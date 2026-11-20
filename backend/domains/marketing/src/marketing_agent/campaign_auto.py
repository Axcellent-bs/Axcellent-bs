"""캠페인 종료 뒤 결과 자동 전달 (태윤 님 검토 11/18 (가)).

배경: 재무 `record_campaign_result`는 마케팅 화면에서 사람이 결과를 저장할 때만 불렸다.
캠페인 기간이
끝나도 재무에 안 가서 손익 칸이 비었다. 시작이 `executed`로 끝난 캠페인이 종료일(`ends_on`, KST)을
주었으면 이 모듈이 종료일 다음 날 + 유예 시간이 지난 뒤 `campaign.record_campaign_result`로 보낸다.

- 대장(`CampaignTracker`): SQLite. `MARKETING_CAMPAIGNS_DB`가 가리키는 파일에 두면 다시 시작해도
  남는다(비면 메모리, 테스트·로컬 기본, `briefs.py`와 같은 방식). 대장 기록은 자동 전달을 켜지
  않아도 항상 한다(나중에 켜면 이미 시작한 캠페인도 대상이 된다).
- 같은 캠페인 중복 전송은 안전하다: 재무 `record_result`는 같은 campaign_id를 덮어쓰고,
  내용이 같으면
  `already_recorded`로 그대로 돌려주며, 빠진 카테고리·제작비·조회수는 앞서 받은 값을 지우지 않는다.
  그래도 이 대장은 성공한 캠페인을 `recorded_auto`로 닫아 한 번만 보낸다.
- 사람이 결과를 저장한(`recorded_human`) 캠페인은 자동으로 덮지 않는다. 자동 전송의 광고비는 실제
  집행액이 아니라 승인 예산이라(광고 플랫폼 연결이 없다)
  사람이 적은 실제 광고비보다 낮은 신뢰의 값이다.
  사람이 나중에 저장하면 재무가 그 값으로 덮어쓴다.
- 취소한 캠페인(`canceled`)은 보내지 않는다.
- 판매액을 데이터가 아직 못 줘서 재무가 `waiting_revenue`로 답하거나 실패하면 `retry_after_h` 뒤에
  다시 하고, `max_attempts`번 해도 안 되면 `gave_up`으로 닫아 사람이 보게 한다(기록은 남는다).
- 자동 전송 기록(`campaign_auto_log`): 시도마다 시각·결과·보낸 값 요약을 남긴다. 조회는
  `CampaignTracker.log(campaign_id)`.
- 외부 게시·돈이 드는 일이 아니라 재무 장부에 손익을 적는 내부 호출뿐이다(실제 광고는 안 나간다).

- 자동 전송 값 표시(`AUTO_SOURCE`): 재무 `CampaignResultInput.source`(숫자의 출처)에 항상
  `marketing.auto.approved_budget`을 보낸다(계약은 그대로). 광고비가 승인 예산이라 재무 화면이
  '예산 기준(잠정)'으로 구분할 수 있다. 사람이 저장하면 `source`를 안 주거나(데이터 값으로 채움)
  자기 값을 주므로, 재무가 같은 campaign_id를 덮어쓰며 이 값이 사라진다(`source`는 안 잇는다).
- 7일 조회수 재확인(11/18): 조회수는 게시 7일 뒤에야 완성돼 종료일 직후 전송엔 3d 등 빠른 값이
  실린다. 영상 캠페인(`format` 있음)은 `recorded_auto` 뒤 하루 한 번 같은 `record_campaign_result`를
  다시 불러(재무는 같은 campaign_id를 덮어써 안전, 내용이 같으면 `already_recorded`) 데이터
  `video_performance`의 가장 늦은 측정값을 싣는다. 그 호출 안에서 이미 읽은 `7d` 상태가 `known`이
  되면 재확인을 닫는다(`views_state=done`). 시작일 + `MARKETING_CAMPAIGN_AUTO_VIEWS_DAYS`일(기본
  14, 0이면 재확인 안 함)이 지나면 포기(`gave_up`).
  사람이 저장(`recorded_human`)·취소한 캠페인은 재확인하지 않는다. 새 호출·계약은 만들지 않는다.

끄는 법·기본값: `MARKETING_CAMPAIGN_AUTO_RECORD`. **기본은 꺼짐**이다. 근거: 자동 전송의 광고비가
승인 예산 값이라 실제 집행액과 다를 수 있고, 재무 손익·교훈·총괄 의견이
이 값으로 계산돼 사람이 모르게
틀린 손익이 쌓이면 되돌리기 번거롭다. 켜는 것은 배포 때 한 번의 환경변수 결정으로 두고(`1`·`true`·
`on`), 켜기 전에도 대장은 쌓인다. 켠 직후에는 이미 종료일이 지난 캠페인이 한꺼번에 한 번씩 간다.
그 밖의 환경변수(모두 선택):
- `MARKETING_CAMPAIGN_AUTO_POLL_S`(기본 600초, 0이면 루프를 켜지 않음)
- `MARKETING_CAMPAIGN_AUTO_GRACE_H`(기본 6시간: 종료일 다음 날 0시(KST) 뒤 데이터 집계 여유)
- `MARKETING_CAMPAIGN_AUTO_RETRY_H`(기본 1시간), `MARKETING_CAMPAIGN_AUTO_MAX_ATTEMPTS`(기본 6)
- `MARKETING_CAMPAIGN_AUTO_VIEWS_DAYS`(기본 14일: 7일 조회수 재확인을 시작일부터 이 날수까지만)
"""

from __future__ import annotations

import asyncio
import logging
import os
import sqlite3
import threading
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta
from typing import Any

from ax_contracts.schemas import AgentRequest, RequestMeta, TaskStatus
from marketing_agent import company_scope
from marketing_agent.campaign import (
    KST_OFFSET_H,
    BriefContent,
    StartedCampaign,
    record_campaign_result,
)

_log = logging.getLogger(__name__)

DB_ENV = "MARKETING_CAMPAIGNS_DB"
ENABLE_ENV = "MARKETING_CAMPAIGN_AUTO_RECORD"
POLL_ENV = "MARKETING_CAMPAIGN_AUTO_POLL_S"
GRACE_ENV = "MARKETING_CAMPAIGN_AUTO_GRACE_H"
RETRY_ENV = "MARKETING_CAMPAIGN_AUTO_RETRY_H"
ATTEMPTS_ENV = "MARKETING_CAMPAIGN_AUTO_MAX_ATTEMPTS"
VIEWS_DAYS_ENV = "MARKETING_CAMPAIGN_AUTO_VIEWS_DAYS"
# 자동 전송의 재무 `source`: 광고비가 승인 예산이라는 표시('예산 기준(잠정)'). 바꾸면 재무와 맞춘다
AUTO_SOURCE = "marketing.auto.approved_budget"
VIEWS_RECHECK_H = 24
CALL_DEADLINE_S = 90

ACTIVE = "active"
RECORDED_AUTO = "recorded_auto"
VIEWS_PENDING = "pending"  # 7d 조회수 재확인 중
VIEWS_DONE = "done"  # 7d known 확인으로 닫음
VIEWS_GAVE_UP = "gave_up"  # 기한까지 7d가 known이 안 돼 닫음
RECORDED_HUMAN = "recorded_human"
CANCELED = "canceled"
GAVE_UP = "gave_up"
# 재무가 손익을 적었다는 답. waiting_revenue(판매액 없음)는 아직 완전하지 않아 다시 한다
_DONE_OUTCOMES = frozenset({"recorded", "already_recorded"})

_SCHEMA = """
CREATE TABLE IF NOT EXISTS campaign_runs (
    campaign_id TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    ends_on TEXT,
    due_at TEXT,
    next_try_at TEXT,
    attempts INTEGER NOT NULL DEFAULT 0,
    started_at TEXT NOT NULL,
    payload TEXT NOT NULL,
    views_state TEXT,
    views_next_at TEXT,
    tenant_id TEXT NOT NULL DEFAULT 'catbooks'
)
"""
_LOG_SCHEMA = """
CREATE TABLE IF NOT EXISTS campaign_auto_log (
    seq INTEGER PRIMARY KEY AUTOINCREMENT,
    campaign_id TEXT NOT NULL,
    at TEXT NOT NULL,
    result TEXT NOT NULL,
    detail TEXT NOT NULL,
    tenant_id TEXT NOT NULL DEFAULT 'catbooks'
)
"""


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name, "").strip().lower()
    if not raw:
        return default
    return raw in {"1", "true", "yes", "on"}


def _env_num(name: str, default: float) -> float:
    try:
        value = float(os.getenv(name, "").strip() or default)
    except ValueError:
        return default
    return value if value >= 0 else default


@dataclass(frozen=True)
class AutoConfig:
    enabled: bool
    poll_s: float
    grace_h: float
    retry_h: float
    max_attempts: int
    views_days: float = 14

    @classmethod
    def from_env(cls) -> AutoConfig:
        return cls(
            enabled=_env_bool(ENABLE_ENV),
            poll_s=_env_num(POLL_ENV, 600),
            grace_h=_env_num(GRACE_ENV, 6),
            retry_h=_env_num(RETRY_ENV, 1),
            max_attempts=max(1, int(_env_num(ATTEMPTS_ENV, 6))),
            views_days=_env_num(VIEWS_DAYS_ENV, 14),
        )


def ends_at(ends_on: str) -> datetime:
    """종료일(KST, 그날 포함)이 끝나는 시각 = 다음 날 0시 KST, UTC로."""
    day = date.fromisoformat(ends_on) + timedelta(days=1)
    midnight_kst = datetime(day.year, day.month, day.day, tzinfo=UTC)
    return midnight_kst - timedelta(hours=KST_OFFSET_H)


class CampaignTracker:
    """시작된 캠페인 대장. `path`가 없으면 환경변수 `MARKETING_CAMPAIGNS_DB`, 없으면 메모리."""

    def __init__(self, path: str | None = None, company_id: str | None = None) -> None:
        self.path = path if path is not None else (os.getenv(DB_ENV, "").strip() or ":memory:")
        self.company_id = company_scope.scope_id(company_id)  # 회사 칸(여러 회사 1-6)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False, timeout=5.0)
        with self._lock, self._conn:
            self._conn.execute(_SCHEMA)
            self._conn.execute(_LOG_SCHEMA)
            have = {r[1] for r in self._conn.execute("PRAGMA table_info(campaign_runs)")}
            for column in ("views_state", "views_next_at"):
                if column not in have:  # 이전 버전 파일 대장
                    self._conn.execute(f"ALTER TABLE campaign_runs ADD COLUMN {column} TEXT")  # noqa: S608
            for table in ("campaign_runs", "campaign_auto_log"):
                company_scope.ensure_tenant_column(self._conn, table)  # 옛 줄은 캣북스

    def close(self) -> None:
        self._conn.close()

    def track(self, started: StartedCampaign, grace_h: float | None = None) -> None:
        """executed로 끝난 캠페인을 올린다. 이미 있으면 그대로 둔다(같은 시작의 재시도)."""
        grace = timedelta(hours=AutoConfig.from_env().grace_h if grace_h is None else grace_h)
        due = (ends_at(started.ends_on) + grace).isoformat() if started.ends_on else None
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT OR IGNORE INTO campaign_runs"
                " (campaign_id, status, ends_on, due_at, started_at, payload, tenant_id)"
                " VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    started.campaign_id,
                    ACTIVE,
                    started.ends_on,
                    due,
                    datetime.now(UTC).isoformat(),
                    started.model_dump_json(),
                    self.company_id,
                ),
            )

    def get(self, campaign_id: str) -> StartedCampaign | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT payload FROM campaign_runs WHERE campaign_id = ? AND tenant_id = ?",
                (campaign_id, self.company_id),
            ).fetchone()
        return StartedCampaign.model_validate_json(row[0]) if row else None

    def status(self, campaign_id: str) -> str | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT status FROM campaign_runs WHERE campaign_id = ? AND tenant_id = ?",
                (campaign_id, self.company_id),
            ).fetchone()
        return row[0] if row else None

    def _set_status(self, campaign_id: str, status: str, *, only_active: bool = False) -> None:
        sql = "UPDATE campaign_runs SET status = ? WHERE campaign_id = ? AND tenant_id = ?"
        if only_active:
            sql += f" AND status = '{ACTIVE}'"  # noqa: S608 - 상수
        with self._lock, self._conn:
            self._conn.execute(sql, (status, campaign_id, self.company_id))

    def mark_human_recorded(self, campaign_id: str) -> None:
        """사람이 결과를 저장했다. 자동 전달은 이 캠페인을 더 보내지 않는다."""
        self._set_status(campaign_id, RECORDED_HUMAN)

    def mark_canceled(self, campaign_id: str) -> None:
        self._set_status(campaign_id, CANCELED, only_active=True)
        self._close_views(campaign_id, VIEWS_DONE, only_pending=True)  # 취소하면 재확인도 멈춘다

    def views_state(self, campaign_id: str) -> str | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT views_state FROM campaign_runs WHERE campaign_id = ? AND tenant_id = ?",
                (campaign_id, self.company_id),
            ).fetchone()
        return row[0] if row else None

    def _set_views(self, campaign_id: str, state: str, next_at: datetime | None) -> None:
        """재확인 칸을 쓴다. 사람이 저장·취소해 `recorded_auto`가 아니면 쓰지 않는다."""
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE campaign_runs SET views_state = ?, views_next_at = ?"
                " WHERE campaign_id = ? AND status = ? AND tenant_id = ?",
                (
                    state,
                    next_at.isoformat() if next_at else None,
                    campaign_id,
                    RECORDED_AUTO,
                    self.company_id,
                ),
            )

    def _close_views(self, campaign_id: str, state: str, *, only_pending: bool) -> None:
        sql = (
            "UPDATE campaign_runs SET views_state = ?, views_next_at = NULL"
            " WHERE campaign_id = ? AND tenant_id = ?"
        )
        if only_pending:
            sql += f" AND views_state = '{VIEWS_PENDING}'"  # noqa: S608 - 상수
        with self._lock, self._conn:
            self._conn.execute(sql, (state, campaign_id, self.company_id))

    def views_due(self, now: datetime) -> list[StartedCampaign]:
        """자동 전송을 마치고 7일 조회수 재확인 시각이 된 캠페인(사람이 저장·취소한 것은 빠진다)."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT payload FROM campaign_runs WHERE status = ? AND views_state = ?"
                " AND views_next_at IS NOT NULL AND views_next_at <= ? AND tenant_id = ?"
                " ORDER BY views_next_at",
                (RECORDED_AUTO, VIEWS_PENDING, now.isoformat(), self.company_id),
            ).fetchall()
        return [StartedCampaign.model_validate_json(r[0]) for r in rows]

    def due(self, now: datetime) -> list[StartedCampaign]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT payload FROM campaign_runs WHERE status = ? AND due_at IS NOT NULL"
                " AND due_at <= ? AND (next_try_at IS NULL OR next_try_at <= ?) AND tenant_id = ?"
                " ORDER BY due_at",
                (ACTIVE, now.isoformat(), now.isoformat(), self.company_id),
            ).fetchall()
        return [StartedCampaign.model_validate_json(r[0]) for r in rows]

    def attempts(self, campaign_id: str) -> int:
        with self._lock:
            row = self._conn.execute(
                "SELECT attempts FROM campaign_runs WHERE campaign_id = ? AND tenant_id = ?",
                (campaign_id, self.company_id),
            ).fetchone()
        return int(row[0]) if row else 0

    def _bump(self, campaign_id: str, next_try: datetime | None) -> int:
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE campaign_runs SET attempts = attempts + 1, next_try_at = ?"
                " WHERE campaign_id = ? AND tenant_id = ?",
                (next_try.isoformat() if next_try else None, campaign_id, self.company_id),
            )
        return self.attempts(campaign_id)

    def add_log(self, campaign_id: str, result: str, detail: dict[str, Any], at: datetime) -> None:
        import json

        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO campaign_auto_log (campaign_id, at, result, detail, tenant_id)"
                " VALUES (?, ?, ?, ?, ?)",
                (
                    campaign_id,
                    at.isoformat(),
                    result,
                    json.dumps(detail, ensure_ascii=False),
                    self.company_id,
                ),
            )

    def log(self, campaign_id: str | None = None) -> list[dict[str, Any]]:
        import json

        sql = "SELECT campaign_id, at, result, detail FROM campaign_auto_log WHERE tenant_id = ?"
        args: tuple[str, ...] = (self.company_id,)
        if campaign_id is not None:
            sql, args = sql + " AND campaign_id = ?", (self.company_id, campaign_id)
        with self._lock:
            rows = self._conn.execute(sql + " ORDER BY seq", args).fetchall()
        return [
            {"campaign_id": c, "at": a, "result": r, "detail": json.loads(d)} for c, a, r, d in rows
        ]


def views_deadline(item: StartedCampaign, cfg: AutoConfig) -> datetime:
    """재확인 마지막 시각 = 시작일(KST 0시) + `views_days`일."""
    start = date.fromisoformat(item.started_on)
    midnight = datetime(start.year, start.month, start.day, tzinfo=UTC)
    return midnight - timedelta(hours=KST_OFFSET_H) + timedelta(days=cfg.views_days)


def _request(item: StartedCampaign, now: datetime, key: str | None = None) -> AgentRequest:
    """자동 전송 요청. 토큰은 `call_agent`가 마케팅 자기 토큰으로 바꾼다."""
    meta = RequestMeta(
        context_id=f"auto_campaign_{item.campaign_id}",
        token="internal:marketing-auto",
        deadline=now + timedelta(seconds=CALL_DEADLINE_S),
        idempotency_key=key or f"auto:result:{item.campaign_id}",
    )
    return AgentRequest(
        meta=meta,
        skill="record_campaign_result",
        input={
            "campaign_id": item.campaign_id,
            "campaign_name": item.campaign_name,
            "channel": item.channel,
            # 실제 집행액을 모른다(광고 플랫폼 연결 없음). 승인 예산을 넣고 사람이 저장하면 덮는다
            "spend_krw": item.budget_krw,
            "source": AUTO_SOURCE,
            "period_from": item.started_on,
            "period_to": item.ends_on,
            **{
                k: v
                for k, v in (
                    ("content_category", item.content_category),
                    ("format", item.format),
                    ("production_cost_krw", item.production_cost_krw),
                )
                if v is not None
            },
        },
    )


_KEEP_OUTPUT = ("write", "status", "profit_krw", "verdict", "views", "views_point", "revision")


async def send_one(
    tracker: CampaignTracker,
    item: StartedCampaign,
    cfg: AutoConfig,
    now: datetime,
    brief_content: BriefContent | None = None,
) -> str:
    """종료된 캠페인 한 건을 재무로 보낸다. 닫힌 상태 이름이나 `retry`를 돌려준다."""
    request = _request(item, now)
    sent = {
        k: request.input[k]
        for k in ("spend_krw", "source", "period_from", "period_to", "content_category", "format")
        if k in request.input
    } | {"production_cost_krw": request.input.get("production_cost_krw")}
    seven_day: list[bool] = []
    try:
        answer = await record_campaign_result(
            request, brief_content=brief_content, on_seven_day=seven_day.append
        )
        finance_status, outcome, out = answer.status, answer.outcome, answer.output
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001 - 한 건의 오류가 다른 캠페인을 막지 않는다
        finance_status, outcome, out = TaskStatus.FAILED, f"exception:{type(exc).__name__}", {}
    done = finance_status == TaskStatus.COMPLETED and outcome in _DONE_OUTCOMES
    detail: dict[str, Any] = {
        "sent": sent,
        "finance_status": finance_status.value,
        "outcome": outcome,
        "finance": {k: out[k] for k in _KEEP_OUTPUT if isinstance(out, dict) and k in out},
    }
    if done:
        # 재무가 받은 뒤에 사람이 먼저 저장했다면 사람 상태를 지우지 않는다
        tracker._set_status(item.campaign_id, RECORDED_AUTO, only_active=True)  # noqa: SLF001
        tracker._bump(item.campaign_id, None)  # noqa: SLF001
        tracker.add_log(item.campaign_id, RECORDED_AUTO, detail, now)
        if item.format is not None:  # 영상 캠페인만 7일 조회수를 기다린다
            _schedule_views(tracker, item, cfg, now, seven_day)
        return RECORDED_AUTO
    attempts = tracker._bump(item.campaign_id, now + timedelta(hours=cfg.retry_h))  # noqa: SLF001
    if attempts >= cfg.max_attempts:
        tracker._set_status(item.campaign_id, GAVE_UP, only_active=True)  # noqa: SLF001
        tracker.add_log(item.campaign_id, GAVE_UP, detail | {"attempts": attempts}, now)
        _log.warning("캠페인 %s 자동 결과 전달 포기(%s회): %s", item.campaign_id, attempts, outcome)
        return GAVE_UP
    tracker.add_log(item.campaign_id, "retry", detail | {"attempts": attempts}, now)
    return "retry"


def _schedule_views(
    tracker: CampaignTracker,
    item: StartedCampaign,
    cfg: AutoConfig,
    now: datetime,
    seven_day: list[bool],
) -> str:
    """전송 뒤 7d 상태로 재확인을 닫거나 다음 날로 잡는다. 닫은 이름 또는 `pending`."""
    if seven_day and seven_day[-1]:
        state = VIEWS_DONE
    elif cfg.views_days <= 0 or now + timedelta(hours=VIEWS_RECHECK_H) > views_deadline(item, cfg):
        state = VIEWS_GAVE_UP
    else:
        tracker._set_views(  # noqa: SLF001
            item.campaign_id, VIEWS_PENDING, now + timedelta(hours=VIEWS_RECHECK_H)
        )
        return VIEWS_PENDING
    tracker._set_views(item.campaign_id, state, None)  # noqa: SLF001
    if state == VIEWS_GAVE_UP:
        _log.warning(
            "캠페인 %s 7일 조회수 재확인 포기(기한 %s일)", item.campaign_id, cfg.views_days
        )
    return state


async def recheck_views(
    tracker: CampaignTracker,
    item: StartedCampaign,
    cfg: AutoConfig,
    now: datetime,
    brief_content: BriefContent | None = None,
) -> str:
    """7일 조회수가 완성될 때까지 같은 결과를 다시 보낸다(재무는 campaign_id로 덮어쓴다)."""
    if tracker.status(item.campaign_id) != RECORDED_AUTO:
        return "skipped"  # 그 사이 사람이 저장하거나 취소했다
    request = _request(item, now, f"auto:views:{item.campaign_id}:{now.date().isoformat()}")
    seven_day: list[bool] = []
    try:
        answer = await record_campaign_result(
            request, brief_content=brief_content, on_seven_day=seven_day.append
        )
        finance_status, outcome = answer.status, answer.outcome
    except asyncio.CancelledError:
        raise
    except Exception as exc:  # noqa: BLE001
        finance_status, outcome = TaskStatus.FAILED, f"exception:{type(exc).__name__}"
    if tracker.status(item.campaign_id) != RECORDED_AUTO:
        return "skipped"
    ok = finance_status == TaskStatus.COMPLETED and outcome in _DONE_OUTCOMES
    state = _schedule_views(tracker, item, cfg, now, seven_day if ok else [])
    tracker.add_log(
        item.campaign_id,
        f"views_{state}" if state != VIEWS_PENDING else "views_retry",
        {"finance_status": finance_status.value, "outcome": outcome, "seven_day": seven_day},
        now,
    )
    return state


async def auto_record_once(
    tracker: CampaignTracker,
    cfg: AutoConfig | None = None,
    now: datetime | None = None,
    brief_content: BriefContent | None = None,
) -> int:
    """종료일이 지난 캠페인을 재무로 보낸다. 꺼져 있으면 아무것도 안 한다. 시도 건수를 돌려준다."""
    cfg = cfg or AutoConfig.from_env()
    if not cfg.enabled:
        return 0
    now = now or datetime.now(UTC)
    items = tracker.due(now)
    for item in items:
        await send_one(tracker, item, cfg, now, brief_content)
    rechecks = tracker.views_due(now)
    for item in rechecks:
        await recheck_views(tracker, item, cfg, now, brief_content)
    return len(items) + len(rechecks)


class Runtime:
    """루프 상태·테스트용 바꿔치기 지점(`sns_publish.publisher.RT`와 같은 방식)."""

    sleep: Callable[[float], Awaitable[None]] = staticmethod(asyncio.sleep)
    loop_state: str = "off"


RT = Runtime()


async def auto_record_loop(
    tracker: CampaignTracker, brief_content: BriefContent | None = None
) -> None:
    while True:
        cfg = AutoConfig.from_env()  # 환경변수를 매 주기 다시 읽어 재시작 없이 끄고 켠다
        await RT.sleep(cfg.poll_s or 600)
        try:
            await auto_record_once(tracker, brief_content=brief_content)
        except asyncio.CancelledError:
            raise
        except Exception:  # noqa: BLE001
            _log.exception("캠페인 결과 자동 전달 실패, 다음 주기에 다시 한다")
