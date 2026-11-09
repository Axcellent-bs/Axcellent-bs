"""업무 리포트 일간 저장 테스트: 저장한 하루치를 더한 주간이 바로 계산한 주간과 같은지 본다.

네트워크 없음. 공통 로그는 SQLite 메모리 저장소, 재무는 가짜 A2A, shop-api는 가짜 지표 공급자다.
"""

from __future__ import annotations

import itertools
from datetime import UTC, date, datetime, timedelta

import pytest

from ax_agent_template.common_log import SqliteLogStore
from ax_agent_template.llm import MockLLMClient
from ax_contracts.data_access.common_log import LogRecord
from ax_contracts.schemas import AgentRequest, AgentResult, Evidence, RequestMeta, TaskStatus
from ax_contracts.schemas.data import ActivityReportInput
from data_agent.clients.log_client import CommonLogSource
from data_agent.explain import _Summary
from data_agent.monitor import days_to_snapshot, snapshot_tick
from data_agent.report_store import ReportStore, Snapshot
from data_agent.skills.activity_report import build_day_snapshot, build_report

asyncio = pytest.mark.asyncio
TENANT = "shop-demo"
WEEK = date(2026, 10, 19)  # 월요일. 직전 주는 10-12 ~ 10-18
LATER = datetime(2026, 11, 3, 3, 0, tzinfo=UTC)  # 재무 UTC 날짜가 모두 끝난 뒤
_ids = itertools.count()
FINANCE_AT = datetime(2026, 11, 3, 2, 0, tzinfo=UTC)  # 가짜 재무 응답 시각


def kst(day: date, hour: int, minute: int = 0) -> datetime:
    """한국 시간 그 날 hour:minute (UTC로)."""
    return datetime(day.year, day.month, day.day, hour, minute, tzinfo=UTC) - timedelta(hours=9)


def _rec(at: datetime, kind: str, source: str, **kw) -> LogRecord:
    return LogRecord(
        log_id=f"log_{next(_ids)}", tenant_id=TENANT, occurred_at=at, source=source, kind=kind, **kw
    )


def request(
    at: datetime,
    source: str,
    skill: str,
    status: str = "completed",
    *,
    requester: str = "orchestrator",
    ctx: str = "ctx_req_1",
    ms: int = 2000,
    extra: dict | None = None,
) -> list[LogRecord]:
    rid = f"r{next(_ids)}"
    received = _rec(
        at,
        "request_received",
        source,
        skill=skill,
        requester=requester,
        context_id=ctx,
        payload={"skill": skill, "path": [], "request_id": rid},
    )
    sent = _rec(
        at + timedelta(milliseconds=ms),
        "result_sent",
        source,
        skill=skill,
        requester=requester,
        context_id=ctx,
        task_status=status,
        payload={
            "skill": skill,
            "status": status,
            "outcome": None,
            "request_id": rid,
            "duration_ms": ms,
            **(extra or {}),
        },
    )
    return [received, sent]


def week_logs(monday: date, scale: int = 1) -> list[LogRecord]:
    """한 주의 기록. 날마다 다르게, 사람에게 넘긴 일과 사건은 자정을 넘긴다."""
    logs: list[LogRecord] = []
    for i in range(7):
        day = monday + timedelta(days=i)
        for n in range((i + 1) * scale):
            logs += request(kst(day, 10, n), "inquiry", "summarize_inquiry", ms=1000 + 100 * n)
        logs += request(kst(day, 11), "coupon", "draft_notice", requester="inquiry")
        if i % 2:
            logs += request(
                kst(day, 12),
                "inquiry",
                "summarize_inquiry",
                extra={"fallback": True, "fallback_reason": "number_invalid"},
            )
            logs += request(kst(day, 13), "coupon", "publish_notice", "failed")
    # 사건: 화요일 23:50 시작 → 수요일 00:20 복구 (자정을 넘긴다)
    tue, wed = monday + timedelta(days=1), monday + timedelta(days=2)
    logs.append(
        _rec(
            kst(tue, 23, 50),
            "event_published",
            "orchestrator",
            context_id="ctx_inc_w",
            payload={"event_type": "anomaly.detected"},
        )
    )
    logs += request(kst(tue, 23, 55), "inquiry", "summarize_inquiry", ctx="ctx_inc_w")
    logs.append(
        _rec(
            kst(wed, 0, 20),
            "event_published",
            "orchestrator",
            context_id="ctx_inc_w",
            payload={"event_type": "incident.resolved"},
        )
    )
    # 사람에게 넘긴 일: 목요일 넘김 → 금요일 처리 (자정을 넘긴다)
    thu, fri = monday + timedelta(days=3), monday + timedelta(days=4)
    logs.append(
        _rec(
            kst(thu, 22),
            "task_status",
            "claims",
            context_id="ctx_req_h",
            skill="record_support_cost",
            task_status="input-required",
            payload={"to_service": "finance", "outcome": "handed_to_human", "task_id": "t1"},
        )
    )
    logs.append(
        _rec(
            kst(fri, 9),
            "task_status",
            "dashboard-api",
            context_id="ctx_req_h",
            skill="record_support_cost",
            task_status="completed",
            payload={"to_service": "finance", "outcome": "handoff_done", "task_id": "t1"},
        )
    )
    return logs


class FakeMetrics:
    """5분 버킷 대신 구간마다 한 점. 날마다 주문 10건, 결제 10번 중 1번 실패."""

    async def fetch(self, metric: str, window_from: datetime, window_to: datetime):
        days = round((window_to - window_from).total_seconds() / 86400)
        points = [window_from + timedelta(days=i) for i in range(days)]
        value = {"order_count": 10.0, "payment_count": 10.0, "payment_fail_rate": 0.1}[metric]
        return [(ts, value) for ts in points]


class FinanceSender:
    def __init__(self, unavailable: dict[str, list[str]] | None = None) -> None:
        self.calls: list[dict] = []
        self.unavailable = unavailable or {}
        self.null_dates: set[str] = set()

    async def __call__(self, target: str, req: AgentRequest) -> AgentResult:
        self.calls.append(dict(req.input))
        day = date.fromisoformat(req.input["date"])
        period = req.input["period"]
        # 재무와 같은 뜻: 일간은 1000×날짜, 주간(월~일)은 그 7일의 합, 그 밖은 정하지 않은 값
        if period == "day":
            amount = 1000 * day.day
        elif period == "week":
            amount = sum(1000 * (day + timedelta(days=i)).day for i in range(7))
        else:
            amount = 99999
        if req.input["date"] in self.null_dates:  # #190: 재무가 확인할 수 없다고 답함
            amount = None
        missing = self.unavailable.get(req.input["date"], [])
        lines = [
            {"code": "gmv", "amount": amount},
            {"code": "commission_revenue", "amount": None if amount is None else amount // 10},
        ]
        return AgentResult(
            meta=req.meta,
            status=TaskStatus.COMPLETED,
            output={"lines": lines, "unavailable": missing},
            evidence=Evidence(collected_at=FINANCE_AT),  # 재무는 응답마다 근거 시각을 붙인다
        )


def _meta() -> RequestMeta:
    return RequestMeta(
        context_id="ctx_req_t",
        token="x",
        tenant_id=TENANT,
        deadline=datetime.now(UTC) + timedelta(seconds=60),
    )


def _logs(records: list[LogRecord]) -> CommonLogSource:
    store = SqliteLogStore(":memory:")
    for r in records:
        store.write(r)
    return CommonLogSource(store)


def _llm() -> MockLLMClient:
    return MockLLMClient(responses={_Summary: _Summary(summary="요약")})


async def _fill(store: ReportStore, logs: CommonLogSource, days: list[date], send=None) -> None:
    for d in days:
        snap = await build_day_snapshot(
            d, _meta(), logs, FakeMetrics(), send or FinanceSender(), now=LATER
        )
        store.put(snap)


async def _report(logs, store=None, send=None, now=LATER, **inp):
    return await build_report(
        ActivityReportInput(**inp),
        _meta(),
        _llm(),
        logs=logs,
        metrics=FakeMetrics(),
        send=send or FinanceSender(),
        now=now,
        enabled=lambda s: True,
        owner=lambda s: None,
        store=store,
    )


def _same_parts(a, b) -> dict:
    skip = {"snapshot_at", "stored_days", "live_days", "snapshot_note"}
    return {k: v for k, v in a.output.model_dump().items() if k not in skip}, {
        k: v for k, v in b.output.model_dump().items() if k not in skip
    }


# ----------------------------------------------------- 일간 7개를 더한 주간 == 바로 계산한 주간
@asyncio
async def test_week_from_seven_daily_snapshots_equals_live_week() -> None:
    logs = _logs(week_logs(WEEK - timedelta(days=7)) + week_logs(WEEK, scale=2))
    store = ReportStore(":memory:")
    await _fill(store, logs, [WEEK - timedelta(days=7 - i) for i in range(14)])
    saved = await _report(logs, store=store, period="week", start=WEEK)
    live = await _report(logs, store=None, period="week", start=WEEK)
    a, b = _same_parts(saved, live)
    assert a == b
    assert (saved.output.stored_days, saved.output.live_days) == (14, 0)
    assert saved.output.snapshot_note == "저장된 날 14일 / 바로 계산 0일"
    support = next(d for d in saved.output.departments if d.department == "support")
    assert support.requests.total == 2 * 28 + 3 + 1  # (1+…+7)×2 + 홀수 날 대체 3 + 사건 1
    assert support.incidents.mttr_median_minutes == 30.0  # 자정을 넘긴 사건도 합쳐서 30분
    finance = next(d for d in saved.output.departments if d.department == "finance")
    assert finance.handoffs.avg_close_minutes == 660.0  # 목 22시 넘김 → 금 09시 처리 (11시간)
    assert saved.output.company.gmv.value == float(sum(1000 * (19 + i) for i in range(7)))


@asyncio
async def test_month_from_daily_snapshots_equals_live_month() -> None:
    """월간: 저장 경로는 하루씩 더하고, 바로 계산은 재무를 한 달 한 번 묻는다. 부서 숫자는 같다."""
    oct1 = date(2026, 10, 1)
    logs = _logs(week_logs(date(2026, 10, 5)) + week_logs(date(2026, 9, 7)))
    store = ReportStore(":memory:")
    days = [date(2026, 9, 1) + timedelta(days=i) for i in range(61)]
    await _fill(store, logs, days)
    saved = await _report(logs, store=store, period="month", start=oct1)
    live = await _report(logs, store=None, period="month", start=oct1)
    assert saved.output.departments == live.output.departments
    assert saved.output.collaboration == live.output.collaboration
    assert saved.output.stored_days == 61


# ----------------------------------------------------- 비율은 합친 분자/분모 (평균의 평균 아님)
@asyncio
async def test_rate_is_recomputed_from_summed_counts_not_averaged() -> None:
    mon, tue = WEEK, WEEK + timedelta(days=1)
    records = []
    for n in range(3):  # 월: 3건 모두 자동 완료 (100%)
        records += request(kst(mon, 10, n), "coupon", "draft_notice")
    for n in range(9):  # 화: 9건 모두 실패 (자동 완료 0%)
        records += request(kst(tue, 10, n), "coupon", "publish_notice", "failed")
    logs = _logs(records)
    store = ReportStore(":memory:")
    await _fill(store, logs, [WEEK - timedelta(days=7 - i) for i in range(14)])
    result = await _report(logs, store=store, period="week", start=WEEK)
    marketing = next(d for d in result.output.departments if d.department == "marketing")
    auto = marketing.outcomes.auto_completed
    assert (auto.count, auto.total, auto.rate) == (3, 12, 0.25)  # 평균의 평균이면 0.5
    assert marketing.quality.avg_response_ms == 2000.0  # 합계/개수


# ------------------------------------------------------------- 섞인 기간과 저장값 쓰기
@asyncio
async def test_mixed_week_computes_only_unsaved_days_and_says_so() -> None:
    logs = _logs(week_logs(WEEK - timedelta(days=7)) + week_logs(WEEK))
    store = ReportStore(":memory:")
    await _fill(store, logs, [WEEK - timedelta(days=7 - i) for i in range(12)])  # 마지막 2일 빠짐
    send = FinanceSender()
    mixed = await _report(logs, store=store, send=send, period="week", start=WEEK)
    live = await _report(logs, store=None, period="week", start=WEEK)
    a, b = _same_parts(mixed, live)
    assert a == b
    assert (mixed.output.stored_days, mixed.output.live_days) == (12, 2)
    assert mixed.output.snapshot_note == "저장된 날 12일 / 바로 계산 2일"
    # 주에 빠진 날이 있으면 재무는 그 주를 week로 한 번 묻는다 (저장된 7일인 직전 주는 묻지 않음)
    assert [(c["period"], c["date"]) for c in send.calls] == [("week", "2026-10-19")]
    first = min(s.created_at for s in store.get_many(TENANT, [WEEK]).values())
    assert mixed.output.snapshot_at == first


@asyncio
async def test_saved_day_uses_saved_value_and_today_is_always_live() -> None:
    day = WEEK + timedelta(days=2)
    logs = _logs(week_logs(WEEK))
    store = ReportStore(":memory:")
    await _fill(store, logs, [day - timedelta(days=1), day])
    send = FinanceSender()
    saved = await _report(logs, store=store, send=send, period="day", start=day)
    assert send.calls == [] and saved.output.stored_days == 2  # 저장된 날은 재무를 다시 묻지 않는다
    today_now = kst(day, 15)  # 그 날이 오늘이면 저장값을 쓰지 않는다
    today = await _report(logs, store=store, now=today_now, period="day", start=day)
    assert (today.output.stored_days, today.output.live_days) == (1, 1)


@asyncio
async def test_no_store_means_live_like_before() -> None:
    logs = _logs(week_logs(WEEK))
    result = await _report(logs, store=None, period="day", start=WEEK)
    assert (result.output.stored_days, result.output.live_days) == (0, 2)
    assert result.output.snapshot_at == LATER


# --------------------------------------------------------------------- 저장 주기
async def _builder(logs, send=None, now=LATER):
    async def build(day: date) -> Snapshot:
        return await build_day_snapshot(
            day, _meta(), logs, FakeMetrics(), send or FinanceSender(), now=now
        )

    return build


@asyncio
async def test_tick_fills_missing_last_twenty_days_newest_first_and_skips_saved() -> None:
    logs = _logs(week_logs(WEEK))
    store = ReportStore(":memory:")
    now = kst(date(2026, 10, 30), 1)  # 00:30 이후
    built = await snapshot_tick(store, await _builder(logs), TENANT, now)
    # 기본 20일, 최근 날짜부터: 10-29, 10-28, …, 10-10
    assert built == [date(2026, 10, 29) - timedelta(days=i) for i in range(20)]
    assert await snapshot_tick(store, await _builder(logs), TENANT, now) == []  # 이미 있음


@asyncio
async def test_yesterday_waits_until_0030_kst() -> None:
    store = ReportStore(":memory:")
    early = kst(date(2026, 10, 30), 0, 10)
    days = [d for d, _ in days_to_snapshot(store, TENANT, early)]
    assert date(2026, 10, 29) not in days and len(days) == 19
    assert days[0] == date(2026, 10, 28)  # 최근 날짜부터
    late = kst(date(2026, 10, 30), 0, 31)
    assert date(2026, 10, 29) in [d for d, _ in days_to_snapshot(store, TENANT, late)]


@asyncio
async def test_unknown_day_is_retried_once_on_the_next_day() -> None:
    logs = _logs(week_logs(WEEK))
    store = ReportStore(":memory:")
    day = date(2026, 10, 28)
    bad = FinanceSender(unavailable={"2026-10-28": ["orders"]})  # 재무가 주문을 못 읽음
    first_now = kst(date(2026, 10, 29), 10)
    store.put(await (await _builder(logs, bad, first_now))(day))
    (saved,) = store.get_many(TENANT, [day]).values()
    assert saved.has_unknown and not saved.retried
    assert saved.payload["company"]["gmv"] is None  # 0이 아니라 확인할 수 없음
    assert (day, True) not in days_to_snapshot(
        store, TENANT, kst(date(2026, 10, 29), 12)
    )  # 같은 날
    next_day = kst(date(2026, 10, 30), 1)
    assert (day, True) in days_to_snapshot(store, TENANT, next_day)
    await snapshot_tick(store, await _builder(logs, bad, next_day), TENANT, next_day)
    (again,) = store.get_many(TENANT, [day]).values()
    assert again.retried and again.has_unknown  # 다시 했지만 여전히 확인할 수 없음
    later = kst(date(2026, 10, 31), 1)
    assert (day, True) not in days_to_snapshot(store, TENANT, later)  # 한 번만


@asyncio
async def test_unfinished_utc_finance_day_is_unknown_and_retried() -> None:
    """00:30에 만든 어제: 재무 UTC 날짜가 안 끝나 매출은 확인할 수 없음, 다음 날 다시 만든다."""
    logs = _logs(week_logs(WEEK))
    day = date(2026, 10, 29)
    snap = await build_day_snapshot(
        day, _meta(), logs, FakeMetrics(), FinanceSender(), now=kst(date(2026, 10, 30), 0, 31)
    )
    assert snap.has_unknown
    assert snap.payload["company"]["gmv"] is None
    assert "UTC" in snap.payload["company"]["finance_reason"]
    assert snap.payload["company"]["orders"] == 10.0  # 다른 칸은 그대로


@asyncio
async def test_failed_build_does_not_stop_other_days() -> None:
    logs = _logs(week_logs(WEEK))
    store = ReportStore(":memory:")
    good = await _builder(logs)

    async def flaky(day: date) -> Snapshot:
        if day == date(2026, 10, 25):
            raise RuntimeError("재무 연결 실패")
        return await good(day)

    built = await snapshot_tick(store, flaky, TENANT, kst(date(2026, 10, 30), 1))
    assert date(2026, 10, 25) not in built and len(built) == 19


def test_backfill_range_comes_from_env_with_default_20() -> None:
    from data_agent.monitor import BACKFILL_ENV, backfill_days

    assert backfill_days({}) == 20
    assert backfill_days({BACKFILL_ENV: "21"}) == 21
    assert backfill_days({BACKFILL_ENV: "주간"}) == 20  # 숫자가 아니면 기본값
    assert backfill_days({BACKFILL_ENV: "0"}) == 1
    assert backfill_days({BACKFILL_ENV: "1000"}) == 92  # 재무를 너무 많이 부르지 않게


@asyncio
async def test_backfill_range_from_env_is_used(monkeypatch) -> None:
    from data_agent.monitor import BACKFILL_ENV

    monkeypatch.setenv(BACKFILL_ENV, "3")
    store = ReportStore(":memory:")
    days = [d for d, _ in days_to_snapshot(store, TENANT, kst(date(2026, 10, 30), 1))]
    assert days == [date(2026, 10, 29), date(2026, 10, 28), date(2026, 10, 27)]


# ------------------------------------------------------------- 재무 period=week (#190)
@asyncio
async def test_fully_saved_week_does_not_ask_finance() -> None:
    logs = _logs(week_logs(WEEK - timedelta(days=7)) + week_logs(WEEK))
    store = ReportStore(":memory:")
    await _fill(store, logs, [WEEK - timedelta(days=7 - i) for i in range(14)])
    send = FinanceSender()
    result = await _report(logs, store=store, send=send, period="week", start=WEEK)
    assert send.calls == []  # 7일이 다 저장된 주는 저장값 합계
    assert result.output.company.gmv.value == float(sum(1000 * (19 + i) for i in range(7)))
    assert result.output.company.gmv.collected_at == FINANCE_AT  # 저장할 때 받은 재무 응답 시각


@asyncio
async def test_unsaved_week_asks_finance_week_once() -> None:
    logs = _logs(week_logs(WEEK))
    send = FinanceSender()
    result = await _report(
        logs, store=ReportStore(":memory:"), send=send, period="week", start=WEEK
    )
    assert sorted((c["period"], c["date"]) for c in send.calls) == [
        ("week", "2026-10-12"),
        ("week", "2026-10-19"),
    ]
    assert result.output.company.gmv.value == float(sum(1000 * (19 + i) for i in range(7)))
    assert result.output.company.gmv.collected_at == FINANCE_AT  # 재무 응답 시각


@asyncio
async def test_finance_null_week_is_unknown_not_zero() -> None:
    logs = _logs(week_logs(WEEK))
    send = FinanceSender(unavailable={"2026-10-19": ["orders"]})
    send.null_dates = {"2026-10-19"}
    result = await _report(logs, send=send, period="week", start=WEEK)
    gmv = result.output.company.gmv
    assert gmv.value is None  # 0원이 아니라 확인할 수 없음
    reasons = {u.section: u.reason for u in result.output.unknown_sections}
    assert "orders" in reasons["company.gmv"]


@asyncio
async def test_finance_null_line_without_unavailable_is_unknown() -> None:
    logs = _logs(week_logs(WEEK))
    send = FinanceSender()
    send.null_dates = {"2026-10-19"}
    result = await _report(logs, send=send, period="week", start=WEEK)
    assert result.output.company.gmv.value is None
    reasons = {u.section: u.reason for u in result.output.unknown_sections}
    assert "확인할 수 없다고 답함" in reasons["company.gmv"]


@asyncio
async def test_default_backfill_covers_last_week_and_the_week_before_on_any_weekday() -> None:
    """주간 기본(지난주 + 그 전 주)이 요일과 상관없이 기본 범위(20일) 안에 들어간다."""
    from data_agent.skills.activity_report import periods

    store = ReportStore(":memory:")
    for offset in range(7):  # 월요일 ~ 일요일
        day = date(2026, 10, 26) + timedelta(days=offset)
        now = kst(day, 1)
        p = periods("week", None, now)
        need = {p.previous_start + timedelta(days=i) for i in range(14)}
        covered = {d for d, _ in days_to_snapshot(store, TENANT, now)}
        assert need <= covered, day


@asyncio
async def test_saved_week_with_unknown_revenue_day_asks_finance_week_once() -> None:
    """7일이 다 저장됐어도 저장 때 매출을 확인할 수 없던 날이 있으면 그 주만 week 한 번 묻는다."""
    logs = _logs(week_logs(WEEK - timedelta(days=7)) + week_logs(WEEK))
    store = ReportStore(":memory:")
    await _fill(store, logs, [WEEK - timedelta(days=7 - i) for i in range(14)])
    bad = FinanceSender(unavailable={"2026-10-21": ["orders"]})  # 수요일만 저장 때 실패
    store.put(
        await build_day_snapshot(date(2026, 10, 21), _meta(), logs, FakeMetrics(), bad, now=LATER)
    )
    send = FinanceSender()
    result = await _report(logs, store=store, send=send, period="week", start=WEEK)
    assert [(c["period"], c["date"]) for c in send.calls] == [
        ("week", "2026-10-19")
    ]  # 직전 주는 안 묻는다
    assert result.output.company.gmv.value == float(sum(1000 * (19 + i) for i in range(7)))
    assert result.output.stored_days == 14  # 부서 숫자는 저장값 그대로
