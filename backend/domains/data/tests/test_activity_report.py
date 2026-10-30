"""activity_report 테스트: 공통 로그를 심은 기간의 부서별 숫자가 손으로 센 값과 같은지 본다.

네트워크 없음. 공통 로그는 SQLite 메모리 저장소, 재무는 가짜 A2A, shop-api는 가짜 지표 공급자다.
"""

from __future__ import annotations

import itertools
from datetime import UTC, date, datetime, timedelta

import pytest

from ax_agent_template.common_log import SqliteLogStore
from ax_agent_template.llm import MockLLMClient
from ax_contracts.data_access.common_log import LogRecord
from ax_contracts.schemas import AgentRequest, AgentResult, RequestMeta, TaskStatus
from ax_contracts.schemas.data import ActivityReportInput
from data_agent import handlers
from data_agent.clients.log_client import CommonLogSource
from data_agent.explain import _Summary
from data_agent.skills.activity_report import (
    NOTHING_NOTABLE,
    InvalidActivityInput,
    build_report,
    periods,
)

asyncio = pytest.mark.asyncio
TENANT = "shop-demo"
# 2026-10-29(KST) 하루 = 2026-10-28T15:00Z ~ 2026-10-29T15:00Z
DAY = date(2026, 10, 29)
T0 = datetime(2026, 10, 28, 15, 0, tzinfo=UTC)
PREV0 = T0 - timedelta(days=1)
NOW = datetime(2026, 10, 30, 3, 0, tzinfo=UTC)
_ids = itertools.count()


def _rec(at: datetime, kind: str, source: str, **kw) -> LogRecord:
    return LogRecord(
        log_id=f"log_{next(_ids)}",
        tenant_id=TENANT,
        occurred_at=at,
        source=source,
        kind=kind,
        **kw,
    )


def request(
    at: datetime,
    source: str,
    skill: str,
    status: str = "completed",
    *,
    requester: str = "orchestrator",
    ctx: str = "ctx_req_1",
    took: float = 2.0,
    outcome: str | None = None,
    extra: dict | None = None,
) -> list[LogRecord]:
    """받음 + 보냄 한 쌍."""
    received = _rec(
        at,
        "request_received",
        source,
        skill=skill,
        requester=requester,
        context_id=ctx,
        payload={"skill": skill, "path": []},
    )
    sent = _rec(
        at + timedelta(seconds=took),
        "result_sent",
        source,
        skill=skill,
        requester=requester,
        context_id=ctx,
        task_status=status,
        payload={"skill": skill, "status": status, "outcome": outcome, **(extra or {})},
    )
    return [received, sent]


def seeded_day(base: datetime) -> list[LogRecord]:
    """손으로 센 값 (아래 test_counts_match_hand_count와 같이 본다).

    고객지원(inquiry·claims): 요청 6건
      - 자동 완료 3 (inquiry summarize_inquiry 2, claims list_claims 1)
      - 사람 확인 후 실행 1 (claims decide_claim → safety held → review_approved)
      - 보류 1 (inquiry send_reply input-required)
      - 확인할 수 없음 1 (inquiry get_inquiry outcome unavailable)
      - 템플릿 대체 1/4 완료 (number_invalid)
      - 응답 시간: 2,2,2,2,4,2초 → 평균 2333.3ms
    마케팅(coupon): 요청 4건
      - 자동 완료 1 (draft_notice), 거절 2 (issue_coupon safety rejected 1 + 요청 거부 1),
        실패 1 (publish_notice failed)
    재무: 서비스 꺼짐 → 요청 0, 사람에게 넘김 2 (처리 1, 반려 1, 처리 시간 30분·60분)
    협업: 총괄→고객지원 5, 고객지원→고객지원 1(claims←support), 고객지원→마케팅 3, 총괄→마케팅 1
    사건: ctx_inc_a(고객지원 summarize_inquiry, 마케팅 draft_notice) MTTR 40분
    """
    h = timedelta(hours=1)
    m = timedelta(minutes=1)
    logs: list[LogRecord] = []
    # 사건 시작 기록 (MTTR 기준)
    logs.append(
        _rec(
            base + h,
            "event_published",
            "orchestrator",
            context_id="ctx_inc_a",
            payload={"event_type": "anomaly.detected"},
        )
    )
    logs += request(base + h + m, "inquiry", "summarize_inquiry", ctx="ctx_inc_a")
    logs += request(
        base + 2 * h,
        "inquiry",
        "summarize_inquiry",
        extra={"fallback": True, "fallback_reason": "number_invalid", "fallback_numbers": [9.0]},
    )
    logs += request(base + 3 * h, "claims", "list_claims")
    # 사람 확인 후 실행: claims가 safety에 실행 요청 → held → 사람이 승인
    logs += request(base + 4 * h, "claims", "decide_claim", requester="support", took=4)
    logs.append(
        _rec(
            base + 4 * h + timedelta(seconds=1),
            "execute_decided",
            "safety",
            context_id="ctx_req_1",
            skill="decide_claim",
            requester="claims",
            task_status="held",
            grade="C",
            payload={"execution_id": "exe_1", "status": "held"},
        )
    )
    logs.append(
        _rec(
            base + 5 * h,
            "task_status",
            "safety",
            context_id="ctx_req_1",
            skill="decide_claim",
            task_status="approved",
            payload={"review_id": "rev_1", "execution_id": "exe_1", "outcome": "review_approved"},
        )
    )
    logs += request(base + 6 * h, "inquiry", "send_reply", "input-required")
    logs += request(base + 7 * h, "inquiry", "get_inquiry", "failed", outcome="unavailable")

    # 마케팅
    logs += request(
        base + h + 2 * m, "coupon", "draft_notice", ctx="ctx_inc_a", requester="inquiry"
    )
    logs += request(base + 8 * h, "coupon", "issue_coupon", requester="inquiry", ctx="ctx_req_2")
    logs.append(
        _rec(
            base + 8 * h + timedelta(seconds=1),
            "execute_decided",
            "safety",
            context_id="ctx_req_2",
            skill="issue_coupon",
            requester="coupon",
            task_status="rejected",
            grade="B",
            payload={"execution_id": "exe_2", "status": "rejected"},
        )
    )
    logs += request(
        base + 9 * h, "coupon", "issue_coupon", "rejected", requester="inquiry", outcome="FORBIDDEN"
    )
    logs += request(base + 10 * h, "coupon", "publish_notice", "failed")
    # 사건 복구
    logs.append(
        _rec(
            base + h + 40 * m,
            "event_published",
            "orchestrator",
            context_id="ctx_inc_a",
            payload={"event_type": "incident.resolved"},
        )
    )

    # 재무 꺼짐 → 사람 할 일함
    for i, (outcome, minutes) in enumerate((("handoff_done", 30), ("handoff_rejected", 60))):
        at = base + 11 * h + i * m
        logs.append(
            _rec(
                at,
                "task_status",
                "claims",
                context_id="ctx_req_3",
                skill="record_support_cost",
                task_status="input-required",
                payload={
                    "skill": "record_support_cost",
                    "to_service": "finance",
                    "outcome": "handed_to_human",
                    "task_id": f"task_{i}",
                },
            )
        )
        logs.append(
            _rec(
                at + timedelta(minutes=minutes),
                "task_status",
                "dashboard-api",
                context_id="ctx_req_3",
                skill="record_support_cost",
                task_status="completed",
                payload={"task_id": f"task_{i}", "to_service": "finance", "outcome": outcome},
            )
        )
    return logs


def small_previous(base: datetime) -> list[LogRecord]:
    """직전 기간: 고객지원 요청 3건(자동 완료), 마케팅 1건."""
    logs: list[LogRecord] = []
    for i in range(3):
        logs += request(base + timedelta(hours=i + 1), "inquiry", "summarize_inquiry")
    logs += request(base + timedelta(hours=5), "coupon", "draft_notice", requester="inquiry")
    return logs


class FakeMetrics:
    def __init__(self, broken: bool = False) -> None:
        self.broken = broken

    async def fetch(self, metric: str, window_from: datetime, window_to: datetime):
        if self.broken:
            from data_agent.clients.shop_client import ShopUnavailable

            raise ShopUnavailable("SHOP_DOWN", "shop-api 꺼짐")
        today = window_from == T0
        if metric == "order_count":
            return [
                (window_from, 30.0 if today else 10.0),
                (window_from + timedelta(minutes=5), 10.0),
            ]
        if metric == "payment_count":
            return [(window_from, 10.0), (window_from + timedelta(minutes=5), 10.0)]
        if metric == "payment_fail_rate":
            return [(window_from, 0.1), (window_from + timedelta(minutes=5), 0.0)]
        return []


def finance_sender(gmv: dict[str, int] | None = None, disabled: bool = False):
    calls: list[dict] = []

    async def send(target: str, req: AgentRequest) -> AgentResult:
        calls.append({"target": target, "skill": req.skill, **req.input})
        if disabled:
            return AgentResult(
                meta=req.meta,
                status=TaskStatus.REJECTED,
                outcome="service_disabled",
                output={"to_service": "finance"},
            )
        amount = (gmv or {}).get(req.input["date"], 1000)
        lines = [
            {"code": "gmv", "label": "총거래액", "amount": amount},
            {"code": "commission_revenue", "label": "판매 수수료 매출", "amount": amount // 10},
        ]
        return AgentResult(meta=req.meta, status=TaskStatus.COMPLETED, output={"lines": lines})

    send.calls = calls  # type: ignore[attr-defined]
    return send


def _meta() -> RequestMeta:
    return RequestMeta(
        context_id="ctx_req_report",
        token="x",
        tenant_id=TENANT,
        deadline=datetime.now(UTC) + timedelta(seconds=30),
    )


def _store(records: list[LogRecord]) -> CommonLogSource:
    store = SqliteLogStore(":memory:")
    for r in records:
        store.write(r)
    return CommonLogSource(store)


def _llm(text: str | None = None) -> MockLLMClient:
    return MockLLMClient(responses={_Summary: _Summary(summary=text)} if text else {})


async def _report(
    records: list[LogRecord],
    *,
    llm: MockLLMClient | None = None,
    send=None,
    metrics=None,
    enabled=lambda s: s != "finance",
    **inp,
):
    return await build_report(
        ActivityReportInput(**({"start": DAY} | inp)),
        _meta(),
        llm or _llm(),
        logs=_store(records),
        metrics=metrics or FakeMetrics(),
        send=send or finance_sender(),
        now=NOW,
        enabled=enabled,
        owner=lambda s: {"inquiry": "병환", "claims": "병환", "coupon": "재헌"}.get(s),
    )


def _dept(result, name: str):
    return next(d for d in result.output.departments if d.department == name)


# ---------------------------------------------------------------------------- 기간
def test_periods_default_to_the_previous_finished_period() -> None:
    now = datetime(2026, 10, 30, 3, 0, tzinfo=UTC)  # KST 2026-10-30(금) 12시
    day = periods("day", None, now)
    assert (day.start, day.end, day.previous_start) == (
        date(2026, 10, 29),
        date(2026, 10, 30),
        date(2026, 10, 28),
    )
    assert day.window() == (T0, T0 + timedelta(days=1))
    week = periods("week", None, now)
    assert (week.start, week.end, week.previous_start) == (
        date(2026, 10, 19),
        date(2026, 10, 26),
        date(2026, 10, 12),
    )
    month = periods("month", None, now)
    assert (month.start, month.end, month.previous_start) == (
        date(2026, 9, 1),
        date(2026, 10, 1),
        date(2026, 8, 1),
    )
    assert periods("month", date(2026, 1, 15), now).previous_start == date(2025, 12, 1)


# ------------------------------------------------------------------ 손으로 센 값과 같음
@asyncio
async def test_counts_match_hand_count() -> None:
    result = await _report(seeded_day(T0) + small_previous(PREV0))
    support = _dept(result, "support")
    assert support.label == "고객지원" and support.owners == ["병환"]
    assert support.requests.total == 6
    assert [(s.skill, s.count) for s in support.requests.top_skills] == [
        ("summarize_inquiry", 2),
        ("decide_claim", 1),
        ("get_inquiry", 1),
        ("list_claims", 1),
        ("send_reply", 1),
    ]
    assert support.requests.top_skills[0].label == "문의 요약"
    o = support.outcomes
    assert (o.auto_completed.count, o.human_approved.count, o.held.count) == (3, 1, 1)
    assert (o.rejected.count, o.unknown.count, o.failed.count) == (0, 1, 0)
    assert o.auto_completed.rate == 0.5 and o.auto_completed.total == 6
    assert support.quality.fallback.count == 1 and support.quality.fallback.total == 4
    assert support.quality.fallback.rate == 0.25
    assert support.quality.fallback_reasons == {"number_invalid": 1}
    assert support.quality.avg_response_ms == pytest.approx(2333.3)
    assert support.quality.response_samples == 6
    assert support.incidents.count == 1
    assert support.incidents.mttr_median_minutes == 40.0
    assert [s.skill for s in support.incidents.skills] == ["summarize_inquiry"]

    marketing = _dept(result, "marketing")
    mo = marketing.outcomes
    assert marketing.requests.total == 4
    assert (mo.auto_completed.count, mo.rejected.count, mo.failed.count) == (1, 2, 1)
    assert mo.rejected.rate == 0.5

    finance = _dept(result, "finance")
    assert finance.enabled is False and finance.requests.total == 0
    assert (finance.handoffs.handed, finance.handoffs.done, finance.handoffs.rejected) == (2, 1, 1)
    assert finance.handoffs.avg_close_minutes == 45.0
    assert any(u.section == "departments.finance" for u in result.output.unknown_sections)

    edges = {(e.from_department, e.to_department): e.count for e in result.output.collaboration}
    assert edges == {
        ("orchestrator", "support"): 5,
        ("support", "support"): 1,
        ("support", "marketing"): 3,
        ("orchestrator", "marketing"): 1,
    }
    assert result.output.company.incident_count.value == 1


@asyncio
async def test_rates_with_denominator_under_three_are_null() -> None:
    logs = request(T0 + timedelta(hours=1), "coupon", "draft_notice")
    logs += request(T0 + timedelta(hours=2), "coupon", "draft_notice", "failed")
    result = await _report(logs)
    rates = _dept(result, "marketing").outcomes
    assert rates.auto_completed.count == 1 and rates.auto_completed.total == 2
    assert rates.auto_completed.rate is None and rates.auto_completed.note == "표본 부족"
    assert _dept(result, "marketing").quality.fallback.rate is None


# ------------------------------------------------------------------------ 직전 기간
@asyncio
async def test_previous_period_and_highlights() -> None:
    result = await _report(
        seeded_day(T0) + small_previous(PREV0),
        llm=_llm("고객지원 처리한 요청이 3에서 6으로 100% 늘었습니다."),
    )
    support = _dept(result, "support")
    assert support.previous_period.requests.total == 3
    assert support.previous_period.outcomes.auto_completed.count == 3
    company = result.output.company
    # 재무 날짜 2026-10-29 / 10-28 (UTC 날짜)
    assert company.gmv.value == 1000 and company.gmv.previous_period == 1000
    assert "UTC" in (company.gmv.note or "")
    assert company.order_count.value == 40 and company.order_count.previous_period == 20
    assert company.payment_success_rate.value == 0.95  # 20번 중 1번 실패
    keys = [h.key for h in result.output.highlights]
    # 직전 값 3 이상만: 고객지원 요청 3→6(+100%), 회사 주문 20→40(+100%)
    # 둘 다 +100%면 차이(20 > 3)가 큰 쪽이 먼저다
    assert keys == ["company.order_count", "departments.support.requests.total"]
    assert all(h.previous >= 3 for h in result.output.highlights)
    assert result.output.summary_source == "llm"
    assert result.fallback is False


@asyncio
async def test_weekly_revenue_sums_seven_finance_days() -> None:
    send = finance_sender()
    result = await _report([], send=send, period="week", start=date(2026, 10, 19))
    days = [c["date"] for c in send.calls if c["period"] == "day"]
    assert len(days) == 14  # 이번 주 7일 + 직전 주 7일
    assert result.output.company.gmv.value == 7000


# ----------------------------------------------------------------------- 로그 없음
@asyncio
async def test_period_without_logs() -> None:
    llm = _llm("쓰면 안 된다")
    result = await _report([], llm=llm, metrics=FakeMetrics(broken=True))
    out = result.output
    for d in out.departments:
        assert d.requests.total == 0 and d.outcomes.auto_completed.rate is None
        assert d.quality.avg_response_ms is None and d.incidents.mttr_median_minutes is None
    assert out.collaboration == [] and out.highlights == []
    sections = {u.section for u in out.unknown_sections}
    assert {"departments", "departments.previous_period", "company.order_count"} <= sections
    assert out.company.order_count.value is None
    assert out.summary == NOTHING_NOTABLE and out.summary_source == "none"
    assert llm.calls == []  # 특이사항이 없으면 LLM을 부르지 않는다


# -------------------------------------------------------------------- 부서 하나 꺼짐
@asyncio
async def test_finance_off_means_revenue_unknown_not_zero() -> None:
    result = await _report(seeded_day(T0), send=finance_sender(disabled=True))
    gmv = result.output.company.gmv
    assert gmv.value is None and gmv.previous_period is None
    reasons = {u.section: u.reason for u in result.output.unknown_sections}
    assert "꺼져" in reasons["company.gmv"]


# ---------------------------------------------------------------------- 일간 특이사항 없음
@asyncio
async def test_daily_without_highlights_skips_llm() -> None:
    same = small_previous(T0) + small_previous(PREV0)  # 두 기간이 같다
    llm = _llm("쓰면 안 된다")
    result = await _report(same, llm=llm, metrics=FakeMetrics(broken=True))
    assert result.output.highlights == []
    assert result.output.summary == "특이사항 없음" and result.output.summary_source == "none"
    assert llm.calls == [] and result.fallback is False


# ----------------------------------------------------------------- 지어낸 숫자 → 템플릿
@asyncio
async def test_made_up_number_in_summary_falls_back_to_template() -> None:
    result = await _report(
        seeded_day(T0) + small_previous(PREV0),
        llm=_llm("고객지원 요청이 9876543건 늘었습니다."),
    )
    assert result.fallback is True and result.output.summary_source == "template"
    assert result.fallback_reason == "number_invalid"
    assert result.fallback_numbers == (9876543.0,)
    assert "고객지원 처리한 요청: 직전 3 → 이번 6 (100% 증가)" in result.output.summary
    assert "9876543" not in result.output.summary
    assert len(result.output.summary.splitlines()) <= 3


@asyncio
async def test_llm_unavailable_uses_template_with_reason() -> None:
    result = await _report(seeded_day(T0) + small_previous(PREV0), llm=_llm())
    assert result.output.summary_source == "template"
    assert result.fallback_reason in ("llm_error", "llm_unavailable")


# -------------------------------------------------------------------- 서비스 고르기
@asyncio
async def test_services_filter_limits_departments() -> None:
    result = await _report(seeded_day(T0), services=["coupon"])
    assert [d.department for d in result.output.departments] == ["marketing"]
    assert _dept(result, "marketing").requests.total == 4
    with pytest.raises(InvalidActivityInput):
        await _report([], services=["nope"])


# ------------------------------------------------------------------------ 핸들러
@asyncio
async def test_handler_answers_with_contract_output(monkeypatch) -> None:
    from ax_agent_template import common_log

    store = SqliteLogStore(":memory:")
    for r in seeded_day(T0):
        store.write(r)
    common_log.set_store(store)
    try:
        monkeypatch.delenv("DATA_MOCK", raising=False)
        monkeypatch.setattr(handlers, "get_llm", lambda: _llm())
        monkeypatch.setattr(handlers, "get_activity_sender", lambda: finance_sender())
        monkeypatch.setattr(handlers, "get_metric_source", lambda env: FakeMetrics())
        req = AgentRequest(meta=_meta(), skill="activity_report", input={"start": "2026-10-29"})
        result = await handlers.activity_report(req)
        assert result.status == TaskStatus.COMPLETED
        assert result.output["period"] == "day" and result.output["start"] == "2026-10-29"
        bad = await handlers.activity_report(
            req.model_copy(update={"input": {"services": ["nope"]}})
        )
        assert bad.status == TaskStatus.FAILED and bad.outcome == "invalid_input"
    finally:
        common_log.set_store(None)


# ---------------------------------------------------------------------------- 응답 시간 출처
def _with_id(pair: list[LogRecord], request_id: str, duration_ms: int | None) -> list[LogRecord]:
    """agent-template이 남기는 새 로그 형식: 두 기록에 request_id, 보냄에 duration_ms."""
    received, sent = pair
    extra = {"request_id": request_id}
    if duration_ms is not None:
        extra["duration_ms"] = duration_ms
    return [
        received.model_copy(update={"payload": {**received.payload, "request_id": request_id}}),
        sent.model_copy(update={"payload": {**sent.payload, **extra}}),
    ]


@asyncio
async def test_response_time_uses_duration_ms_when_logged() -> None:
    at = T0 + timedelta(hours=1)
    # 기록 시각 차이는 2초지만 서버가 잰 처리 시간은 0.5초·1.5초 → 평균 1000ms
    records = _with_id(request(at, "inquiry", "summarize_inquiry"), "a" * 32, 500)
    records += _with_id(
        request(at + timedelta(minutes=1), "inquiry", "summarize_inquiry"), "b" * 32, 1500
    )
    support = _dept(await _report(records), "support")
    assert support.quality.avg_response_ms == 1000.0
    assert support.quality.response_samples == 2


@asyncio
async def test_old_logs_without_duration_use_pairing() -> None:
    at = T0 + timedelta(hours=1)
    records = request(at, "inquiry", "summarize_inquiry", took=2.0)
    records += request(at + timedelta(minutes=1), "inquiry", "summarize_inquiry", took=4.0)
    support = _dept(await _report(records), "support")
    assert support.quality.avg_response_ms == 3000.0
    assert support.quality.response_samples == 2


@asyncio
async def test_mixed_period_pairs_old_logs_without_taking_new_ones() -> None:
    at = T0 + timedelta(hours=1)
    old_received, old_sent = request(at, "inquiry", "summarize_inquiry", took=4.0)
    new_received, new_sent = _with_id(
        request(at + timedelta(seconds=1), "inquiry", "summarize_inquiry", took=1.0),
        "c" * 32,
        1000,
    )
    # 시각 순: 예전 받음(0초) → 새 받음(1초) → 새 보냄(2초) → 예전 보냄(4초). 같은 키다.
    # 새 보냄은 duration_ms 1000을 쓰고, 예전 보냄은 예전 받음과 짝지어 4000ms다.
    records = [old_received, new_received, new_sent, old_sent]
    support = _dept(await _report(records), "support")
    assert support.quality.avg_response_ms == 2500.0
    assert support.quality.response_samples == 2


@asyncio
async def test_new_log_with_request_id_but_no_duration_pairs_by_id() -> None:
    at = T0 + timedelta(hours=1)
    first_received, first_sent = _with_id(
        request(at, "inquiry", "summarize_inquiry", took=3.0), "d" * 32, None
    )
    # second는 받음이 기간 앞이라 보냄만 있다 (2초 시점)
    _, second_sent = _with_id(
        request(at - timedelta(seconds=10), "inquiry", "summarize_inquiry", took=12.0),
        "e" * 32,
        None,
    )
    # 순서 짝짓기라면 second 보냄이 first 받음을 가져가 2000ms가 된다. id로 짝지으면 first만 3000ms
    records = [first_received, second_sent, first_sent]
    assert [r.occurred_at for r in records] == sorted(r.occurred_at for r in records)
    support = _dept(await _report(records), "support")
    assert support.quality.avg_response_ms == 3000.0
    assert support.quality.response_samples == 1
