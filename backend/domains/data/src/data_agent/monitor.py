"""업무 지표 주기 감시 (DEP-DATA-01).

최근 구간을 anomaly_detection skill과 **같은 판정 함수**(`detect.judge_window`)로 판정하고,
이상인 지표마다 `metric.anomaly` 이벤트를 발행한다. 두 곳의 규칙이 갈라지지 않게 판정은
여기서 다시 쓰지 않는다. 주기 실행이므로 LLM을 부르지 않는다(요청 예산).
사건(context_id)은 통합 에이전트만 열기 때문에 이벤트의 context_id는 None이다.

같은 장애를 여러 번 알리지 않는다.
- idempotency_key는 `metric.anomaly:<지표>:<이상 구간 시작>`이다.
- 직전 감시에서 이상이던 지표가 계속 이상이면(구간 시작이 밀려도) 다시 발행하지 않는다.
  정상·확인할 수 없음으로 돌아온 뒤 다시 이상이 되면 새로 발행한다.
"""

from __future__ import annotations

import asyncio
import hashlib
import os
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from ax_agent_template.events import publish
from ax_contracts.schemas import EventEnvelope, EventType
from ax_contracts.schemas.event import MetricAnomalyData
from data_agent.detect import WATCHED_METRICS, MetricSource, judge_window
from data_agent.metrics.judge import Judgement, Settings, settings_for

__all__ = ["WATCHED_METRICS", "MetricSource", "reset_published", "run_forever", "run_once"]

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
    judged = await judge_window(source, window_from, now, [m for m, _ in WATCHED_METRICS], settings)
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
