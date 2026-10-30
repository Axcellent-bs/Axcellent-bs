"""업무 지표 주기 감시 (DEP-DATA-01).

최근 구간을 anomaly_detection skill과 **같은 판정 함수**(`detect.judge_window`)로 판정하고,
이상인 지표마다 `metric.anomaly` 이벤트를 발행한다. 두 곳의 규칙이 갈라지지 않게 판정은
여기서 다시 쓰지 않는다. 주기 실행이므로 LLM을 부르지 않는다(요청 예산).
사건(context_id)은 통합 에이전트만 열기 때문에 이벤트의 context_id는 None이다.

같은 장애를 여러 번 알리지 않는다.
- idempotency_key는 `metric.anomaly:<지표>:<이상 구간 시작>`이다.
- 직전 감시에서 이상이던 지표가 계속 이상이면(구간 시작이 밀려도) 다시 발행하지 않는다.
  정상·확인할 수 없음으로 돌아온 뒤 다시 이상이 되면 새로 발행한다.

같은 모듈에 업무 리포트 일간 저장(`snapshot_tick`)도 둔다. 데이터 앱은 `DATA_REPORT_DB`가 있으면
저장 주기(`run_snapshots_forever`)만 켠다(지표 감시 `run_forever`는 지금처럼 켜지 않는다).
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
from collections.abc import Awaitable, Callable, Mapping
from dataclasses import replace
from datetime import UTC, date, datetime, time, timedelta, timezone

from ax_agent_template.events import publish
from ax_contracts.schemas import EventEnvelope, EventType
from ax_contracts.schemas.event import MetricAnomalyData
from data_agent.detect import WATCHED_METRICS, MetricSource, judge_window, watched_metrics
from data_agent.metrics.judge import Judgement, Settings, settings_for
from data_agent.report_store import ReportStore, Snapshot

__all__ = [
    "WATCHED_METRICS",
    "MetricSource",
    "backfill_days",
    "days_to_snapshot",
    "reset_published",
    "run_forever",
    "run_once",
    "run_snapshots_forever",
    "snapshot_tick",
]

# 프로세스 안에서 이미 발행한 idempotency_key
_published_keys: set[str] = set()
# 직전 감시에서 이상이던 지표 → 그때 발행한 key (계속 이상이면 다시 발행하지 않는다)
_active: dict[str, str] = {}


def reset_published() -> None:
    """발행 기록을 비운다 (테스트용)."""
    _published_keys.clear()
    _active.clear()


def _envelope(j: Judgement, window_from: datetime, now: datetime) -> EventEnvelope:
    start = j.peak_ts or window_from
    data = MetricAnomalyData(
        metric=j.metric,
        window_from=start,
        window_to=j.peak_to or now,
        observed=j.observed,
        baseline=j.baseline,
        severity=j.severity or "low",
    )
    key = f"metric.anomaly:{j.metric}:{start.isoformat()}"
    return EventEnvelope(
        event_id="evt_" + hashlib.sha256(key.encode()).hexdigest()[:16],
        type=EventType.METRIC_ANOMALY,
        occurred_at=now,
        context_id=None,
        source="data",
        idempotency_key=key,
        data=data.model_dump(mode="json"),
        untrusted={},
    )


async def run_once(
    source: MetricSource,
    now: datetime,
    *,
    lookback: timedelta = timedelta(minutes=30),
    settings: Settings | None = None,
) -> list[EventEnvelope]:
    """한 번 감시해 새로 발행한 `metric.anomaly` 이벤트를 돌려준다."""
    now = now.astimezone(UTC)
    window_from = now - lookback
    settings = settings or settings_for(None, os.environ)
    watched = [m for m, _ in watched_metrics()]  # 회사 카탈로그의 지표
    judged = await judge_window(source, window_from, now, watched, settings)
    emitted: list[EventEnvelope] = []
    for j in judged.judgements:
        if j.status != "anomaly":
            _active.pop(j.metric, None)
            continue
        env = _envelope(j, window_from, now)
        if j.metric in _active or env.idempotency_key in _published_keys:
            continue
        _published_keys.add(env.idempotency_key)
        _active[j.metric] = env.idempotency_key
        await publish(env)
        emitted.append(env)
    return emitted


async def run_forever(
    source: MetricSource,
    interval_seconds: int = 300,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> None:
    """`interval_seconds` 마다 `run_once`를 반복한다."""
    while True:
        await run_once(source, clock())
        await asyncio.sleep(interval_seconds)


# ------------------------------------------------ 업무 리포트 일간 저장 (activity_report)
# 한국 시간 00:30 이후 처음 돌 때 어제를 만들고, 돌 때마다 최근 N일(기본 20일) 중
# 빠진 날을 최근 날짜부터 채운다. N은 `DATA_REPORT_BACKFILL_DAYS`.
# 이미 있는 날은 다시 만들지 않는다. 확인할 수 없음이 있던 날은 다음 날 한 번만 다시 만든다.
# 하루씩 차례로 만든다(재무 호출은 하루에 한 번, 동시 상한 안).
SNAPSHOT_KST = timezone(timedelta(hours=9))
SNAPSHOT_AFTER = time(0, 30)  # 한국 시간. 어제 일간을 만들기 시작하는 시각
BACKFILL_ENV = "DATA_REPORT_BACKFILL_DAYS"
# 기본값. 주간 기본(지난주 월~일)과 그 전 주가 요일과 상관없이 들어가는 길이:
# 일요일이면 지난주 월요일이 13일 전, 그 전 주 월요일이 20일 전이다
SNAPSHOT_BACKFILL_DAYS = 20
MAX_BACKFILL_DAYS = 92  # 잘못 넣어도 재무를 너무 많이 부르지 않게
SNAPSHOT_INTERVAL_SECONDS = 300

logger = logging.getLogger("ax.data.monitor")

BuildDay = Callable[[date], Awaitable[Snapshot]]


def backfill_days(env: Mapping[str, str] | None = None) -> int:
    """채울 범위(일). `DATA_REPORT_BACKFILL_DAYS`가 없거나 숫자가 아니면 기본 20, 1~92로 자른다."""
    raw = (os.environ if env is None else env).get(BACKFILL_ENV, "").strip()
    try:
        value = int(raw)
    except ValueError:
        return SNAPSHOT_BACKFILL_DAYS
    return max(1, min(value, MAX_BACKFILL_DAYS))


def days_to_snapshot(
    store: ReportStore, tenant_id: str, now: datetime, backfill: int | None = None
) -> list[tuple[date, bool]]:
    """만들 날과 다시 만드는 날인지. 최근 날짜부터. 오늘은 만들지 않는다(하루가 끝나지 않았다)."""
    local = now.astimezone(SNAPSHOT_KST)
    today = local.date()
    span = backfill_days() if backfill is None else backfill
    days = [today - timedelta(days=i) for i in range(1, span + 1)]  # 어제, 그제, …
    if local.time() < SNAPSHOT_AFTER:
        days = days[1:]  # 어제는 00:30이 지나야 만든다
    have = store.get_many(tenant_id, days)
    todo: list[tuple[date, bool]] = []
    for day in days:
        saved = have.get(day)
        if saved is None:
            todo.append((day, False))
        elif (
            saved.has_unknown
            and not saved.retried
            and saved.created_at.astimezone(SNAPSHOT_KST).date() < today
        ):
            todo.append((day, True))  # 확인할 수 없음이 있던 날: 다음 날 한 번만
    return todo


async def snapshot_tick(
    store: ReportStore,
    build_day: BuildDay,
    tenant_id: str,
    now: datetime,
    backfill: int | None = None,
) -> list[date]:
    """빠진 날을 최근 날짜부터 하루씩 만들어 저장한다. 만든 날 목록을 돌려준다.

    하루씩 차례로 만들어 재무 호출은 동시 상한(2) 안이다. 하루가 실패해도 다음 날은 계속한다.
    """
    built: list[date] = []
    for day, retry in days_to_snapshot(store, tenant_id, now, backfill):
        try:
            snapshot = await build_day(day)
        except Exception as error:  # noqa: BLE001 - 다음 주기에 다시 시도한다
            logger.warning("업무 리포트 일간 저장 실패 %s: %s", day, type(error).__name__)
            continue
        store.put(replace(snapshot, retried=retry))
        built.append(day)
    return built


async def run_snapshots_forever(
    store: ReportStore,
    build_day: BuildDay,
    tenant_id: str,
    interval_seconds: int = SNAPSHOT_INTERVAL_SECONDS,
    clock: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> None:
    """`interval_seconds`마다 `snapshot_tick`을 돈다. 시작하자마자 한 번 돌아 빠진 날을 채운다.

    데이터 앱이 백그라운드 작업으로 켜서, 채우는 동안에도 요청은 바로 받는다.
    """
    backfill = backfill_days()
    while True:
        try:
            await snapshot_tick(store, build_day, tenant_id, clock(), backfill)
        except Exception as error:  # noqa: BLE001 - 저장소 오류로 서버를 멈추지 않는다
            logger.warning("업무 리포트 일간 저장 주기 오류: %s", type(error).__name__)
        await asyncio.sleep(interval_seconds)
