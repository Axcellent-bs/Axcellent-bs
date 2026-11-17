"""start_campaign·record_campaign_result: 재무 판단 → 관문 실행, 결과 전달."""

from datetime import UTC, datetime, timedelta

import pytest

from ax_agent_template import events
from ax_contracts.schemas import (
    AgentRequest,
    AgentResult,
    ExecuteDecision,
    HoldReason,
    RequestMeta,
    TaskStatus,
)
from marketing_agent import campaign, handlers
from marketing_agent.main import load_card

pytestmark = pytest.mark.asyncio
CTX = "ctx_cmp_0001"
START = {
    "campaign_id": "cmp_1",
    "campaign_name": "가을 세일",
    "channel": "sns",
    "budget_krw": 300_000,
}
RESULT = {
    "campaign_id": "cmp_1",
    "campaign_name": "가을 세일",
    "channel": "sns",
    "period_from": "2026-10-01",
    "period_to": "2026-10-05",
    "spend_krw": 300_000,
    "source": "data.campaign_effect",
}


@pytest.fixture(autouse=True)
def _mock_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_TEMPLATE_MOCK", "1")
    events.reset_mock_bus()
    handlers.reset_state()


def _req(skill: str, input: dict) -> AgentRequest:
    meta = RequestMeta(
        context_id=CTX,
        token="mock:orchestrator",
        deadline=datetime.now(UTC) + timedelta(seconds=30),
    )
    return AgentRequest(meta=meta, skill=skill, input=input)


def _review(decision: str = "approved", limit: int = 300_000) -> dict:
    return {
        "review_id": "bud_cmp_1_1",
        "campaign_id": "cmp_1",
        "decision": decision,
        "requested_krw": 300_000,
        "approved_limit_krw": limit,
        "reasons": ["근거"],
        "recommendation": "진행",
        "applied_rules": [],
    }


class Finance:
    def __init__(self, status=TaskStatus.COMPLETED, output=None, outcome="approved") -> None:
        self.calls: list[tuple[str, AgentRequest]] = []
        self.answer = (status, output if output is not None else _review(), outcome)

    async def __call__(self, target: str, req: AgentRequest) -> AgentResult:
        self.calls.append((target, req))
        status, output, outcome = self.answer
        return AgentResult(meta=req.meta, status=status, outcome=outcome, output=output)


class Gate:
    def __init__(self, **fields) -> None:
        self.seen: list[dict] = []
        self.decision = ExecuteDecision(
            **({"execution_id": "exe_1", "grade": "B", "status": "executed"} | fields)
        )

    async def __call__(self, action, params, context, evidence):  # noqa: ANN001
        self.seen.append({"action": action, "params": params, "key": context.idempotency_key})
        return self.decision


def _wire(monkeypatch: pytest.MonkeyPatch, finance: Finance, gate: Gate | None = None) -> Gate:
    gate = gate or Gate()
    monkeypatch.setattr(campaign, "call_agent", finance)
    monkeypatch.setattr(campaign, "execute", gate)
    return gate


class Routed:
    """`call_agent` 대역: 데이터는 data, 그 밖(재무)은 finance가 답한다."""

    def __init__(self, finance: Finance, data: Finance) -> None:
        self.finance, self.data = finance, data

    async def __call__(self, target: str, req: AgentRequest) -> AgentResult:
        return await (self.data if target == "data" else self.finance)(target, req)


RECORD = {
    "campaign_id": "cmp_1",
    "period_from": "2026-10-01",
    "period_to": "2026-10-08",
    "gmv_krw": 2_000_000,
    "orders": 40,
    "measured_gmv_krw": 2_000_000,
    "measured_orders": 40,
    "effect_generated_at": "2026-10-08T05:00:00Z",
    "source": "data.campaign_result",
}
# 호출자가 아는 칸만 있다 (기간·매출 효과·출처는 데이터가 안다)
CALLER = {
    "campaign_id": "cmp_1",
    "campaign_name": "가을 세일",
    "channel": "sns",
    "spend_krw": 300_000,
    "coupon_krw": 120_000,
}


def _data(record: dict | None = RECORD, campaign_id: str = "cmp_1") -> Finance:
    out = {"campaign_id": campaign_id, "view": "finance", "finance_record": record}
    return Finance(output=out, outcome="completed")


def _wire_routed(monkeypatch: pytest.MonkeyPatch, finance: Finance, data: Finance) -> None:
    monkeypatch.setattr(campaign, "call_agent", Routed(finance, data))


async def test_approved_runs_gate_after_review(monkeypatch: pytest.MonkeyPatch) -> None:
    finance = Finance()
    gate = _wire(monkeypatch, finance)
    result = await campaign.start_campaign(_req("start_campaign", START))
    assert (result.status, result.outcome) == (TaskStatus.COMPLETED, "executed")
    target, sent = finance.calls[0]
    assert (target, sent.skill) == ("finance", "review_campaign_budget")
    assert sent.input["requested_krw"] == 300_000
    assert gate.seen == [
        {
            "action": "start_campaign",
            "params": START,
            "key": f"{CTX}:start_campaign:cmp_1:300000",
        }
    ]
    assert result.output["review"]["decision"] == "approved"


async def test_rejected_by_finance_never_calls_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    gate = _wire(monkeypatch, Finance(output=_review("rejected", 0), outcome="rejected"))
    result = await campaign.start_campaign(_req("start_campaign", START))
    assert result.outcome == "rejected_by_finance"
    assert result.output["review"]["decision"] == "rejected"
    assert gate.seen == []


async def test_conditional_within_limit_runs_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    gate = _wire(monkeypatch, Finance(output=_review("conditional", 300_000)))
    result = await campaign.start_campaign(_req("start_campaign", START))
    assert result.outcome == "executed"
    assert result.output["review"]["decision"] == "conditional"
    assert len(gate.seen) == 1


async def test_finance_opinion_passes_through_and_does_not_block(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """재무는 의견만 낸다: 권하지 않음이어도 막지 않고 의견 필드를 그대로 싣는다."""
    review = _review("approved", 300_000) | {
        "advisory": True,
        "opinion": "not_recommended",
        "suggested_krw": 0,
    }
    gate = _wire(monkeypatch, Finance(output=review))
    result = await campaign.start_campaign(_req("start_campaign", START))
    assert (result.status, result.outcome) == (TaskStatus.COMPLETED, "executed")
    assert len(gate.seen) == 1
    sent = result.output["review"]
    assert (sent["advisory"], sent["opinion"], sent["suggested_krw"]) == (
        True,
        "not_recommended",
        0,
    )


async def test_conditional_over_limit_stops(monkeypatch: pytest.MonkeyPatch) -> None:
    gate = _wire(monkeypatch, Finance(output=_review("conditional", 100_000)))
    result = await campaign.start_campaign(_req("start_campaign", START))
    assert result.outcome == "over_approved_limit"
    assert gate.seen == []


@pytest.mark.parametrize(
    ("fields", "status", "outcome"),
    [
        (
            {
                "status": "held",
                "grade": "C",
                "review_id": "rev_1",
                "hold_reason": HoldReason.OVER_THRESHOLD,
            },
            TaskStatus.INPUT_REQUIRED,
            "held",
        ),
        ({"status": "verifying"}, TaskStatus.WORKING, "verifying"),
        (
            {"status": "rejected", "hold_reason": HoldReason.VERIFY_REJECTED},
            TaskStatus.COMPLETED,
            "rejected_by_review",
        ),
        ({"status": "rejected", "grade": "C"}, TaskStatus.FAILED, "execute_rejected"),
        ({"status": "failed"}, TaskStatus.FAILED, "execute_failed"),
    ],
)
async def test_gate_outcomes_reported_faithfully(
    monkeypatch: pytest.MonkeyPatch, fields: dict, status: TaskStatus, outcome: str
) -> None:
    _wire(monkeypatch, Finance(), Gate(**fields))
    result = await campaign.start_campaign(_req("start_campaign", START))
    assert (result.status, result.outcome) == (status, outcome)
    assert result.output["review"]["review_id"] == "bud_cmp_1_1"


@pytest.mark.parametrize(
    "answer",
    [
        {"status": TaskStatus.FAILED, "output": {}, "outcome": "unreachable"},
        {"status": TaskStatus.CANCELED, "output": {}, "outcome": "TIMEOUT"},
        {"status": TaskStatus.COMPLETED, "output": {"x": 1}, "outcome": "approved"},
    ],
)
async def test_finance_unavailable_is_fail_closed(
    monkeypatch: pytest.MonkeyPatch, answer: dict
) -> None:
    gate = _wire(monkeypatch, Finance(**answer))
    result = await campaign.start_campaign(_req("start_campaign", START))
    assert result.outcome in {"finance_review_unavailable", "finance_review_invalid"}
    assert result.status != TaskStatus.COMPLETED
    assert gate.seen == []


@pytest.mark.parametrize(
    "bad",
    [
        {"budget_krw": 0},
        {"budget_krw": -1},
        {"budget_krw": 1000.5},
        {"budget_krw": "1000"},
        {"budget_krw": True},
        {"campaign_id": ""},
        {"extra": 1},
    ],
)
async def test_invalid_start_input_calls_nothing(
    monkeypatch: pytest.MonkeyPatch, bad: dict
) -> None:
    finance = Finance()
    gate = _wire(monkeypatch, finance)
    result = await campaign.start_campaign(_req("start_campaign", START | bad))
    assert (result.status, result.outcome) == (TaskStatus.FAILED, "invalid_input")
    assert finance.calls == [] and gate.seen == []


async def test_record_result_passes_contract_through(monkeypatch: pytest.MonkeyPatch) -> None:
    # 호출자가 기간·출처와 잰 값을 모두 줬으면 데이터를 부르지 않는다
    finance = Finance(output={"status": "recorded"}, outcome="recorded")
    data = _data()
    _wire_routed(monkeypatch, finance, data)
    full = RESULT | {"gmv_krw": 1_000_000}
    result = await campaign.record_campaign_result(_req("record_campaign_result", full))
    assert result.outcome == "recorded"
    # 매출 효과는 데이터에서 읽지 않는다(조회수 읽기 video_performance는 따로)
    assert [c for _, c in data.calls if c.skill == "campaign_result"] == []
    target, sent = finance.calls[0]
    assert (target, sent.skill) == ("finance", "record_campaign_result")
    assert sent.input["gmv_krw"] == 1_000_000 and sent.input["spend_krw"] == 300_000
    assert sent.input["coupon_krw"] == 0 and sent.input["source"] == "data.campaign_effect"


async def test_record_result_fills_effect_from_data(monkeypatch: pytest.MonkeyPatch) -> None:
    finance = Finance(output={"status": "recorded"}, outcome="recorded")
    data = _data()
    _wire_routed(monkeypatch, finance, data)
    result = await campaign.record_campaign_result(_req("record_campaign_result", CALLER))
    assert result.outcome == "recorded"
    target, asked = data.calls[0]
    assert (target, asked.skill) == ("data", "campaign_result")
    assert asked.input == {"campaign_id": "cmp_1", "view": "finance"}
    sent = finance.calls[0][1].input
    # 데이터가 아는 값은 그대로, 집행액·이름·채널·쿠폰은 호출자 값
    assert sent["period_from"] == "2026-10-01" and sent["period_to"] == "2026-10-08"
    assert (sent["gmv_krw"], sent["orders"]) == (2_000_000, 40)
    assert (sent["measured_gmv_krw"], sent["measured_orders"]) == (2_000_000, 40)
    assert sent["effect_generated_at"] == "2026-10-08T05:00:00Z"
    assert sent["source"] == "data.campaign_result"
    assert (sent["spend_krw"], sent["coupon_krw"]) == (300_000, 120_000)
    assert (sent["campaign_name"], sent["channel"]) == ("가을 세일", "sns")
    assert result.evidence.queries == ["data.campaign_result", "finance.record_campaign_result"]


async def test_record_result_keeps_caller_effect_and_fills_period(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # 기간만 비었으면 기간만 데이터로 채운다. 호출자가 준 매출 효과·출처는 덮어쓰지 않는다
    finance = Finance(output={"status": "recorded"}, outcome="recorded")
    _wire_routed(monkeypatch, finance, _data())
    caller = CALLER | {"gmv_krw": 5_000, "source": "marketing.manual"}
    await campaign.record_campaign_result(_req("record_campaign_result", caller))
    sent = finance.calls[0][1].input
    assert sent["period_from"] == "2026-10-01" and sent["period_to"] == "2026-10-08"
    assert sent["gmv_krw"] == 5_000 and sent["source"] == "marketing.manual"
    assert "orders" not in sent and "measured_gmv_krw" not in sent  # 두 기준을 섞지 않는다


async def test_record_result_effect_unknown_leaves_gmv_blank(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    # 측정 중이면 데이터 값이 null이다. 0으로 채우지 않고 재무가 판단을 미룬다
    unknown = {
        "campaign_id": "cmp_1",
        "period_from": "2026-10-01",
        "period_to": "2026-10-08",
        "source": "data.campaign_result",
    }
    finance = Finance(output={"status": "waiting_revenue"}, outcome="waiting_revenue")
    _wire_routed(monkeypatch, finance, _data(unknown))
    result = await campaign.record_campaign_result(_req("record_campaign_result", CALLER))
    assert result.outcome == "waiting_revenue"
    sent = finance.calls[0][1].input
    assert "gmv_krw" not in sent and "orders" not in sent


async def test_record_result_no_finance_record_uses_caller_period(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    finance = Finance(output={"status": "waiting_revenue"}, outcome="waiting_revenue")
    _wire_routed(monkeypatch, finance, _data(None))
    caller = CALLER | {"period_from": "2026-10-01", "period_to": "2026-10-05", "source": "x.y"}
    await campaign.record_campaign_result(_req("record_campaign_result", caller))
    sent = finance.calls[0][1].input
    assert "gmv_krw" not in sent and sent["source"] == "x.y"


async def test_record_result_no_period_anywhere_fails_before_finance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    finance = Finance()
    _wire_routed(monkeypatch, finance, _data(None))
    result = await campaign.record_campaign_result(_req("record_campaign_result", CALLER))
    assert (result.status, result.outcome) == (TaskStatus.FAILED, "data_period_missing")
    assert finance.calls == []


@pytest.mark.parametrize(
    ("data", "outcome"),
    [
        (Finance(status=TaskStatus.FAILED, output={}, outcome="unreachable"), "data_unavailable"),
        (Finance(status=TaskStatus.CANCELED, output={}, outcome="TIMEOUT"), "data_unavailable"),
        (Finance(output={"x": 1}, outcome="completed"), "data_result_invalid"),
        (_data(campaign_id="cmp_other"), "data_result_invalid"),
        (_data(RECORD | {"campaign_id": "cmp_other"}), "data_result_invalid"),
    ],
)
async def test_record_result_data_unusable_never_calls_finance(
    monkeypatch: pytest.MonkeyPatch, data: Finance, outcome: str
) -> None:
    finance = Finance()
    _wire_routed(monkeypatch, finance, data)
    result = await campaign.record_campaign_result(_req("record_campaign_result", CALLER))
    assert result.outcome == outcome
    assert result.status != TaskStatus.COMPLETED
    assert finance.calls == []


async def test_record_result_keeps_known_gmv(monkeypatch: pytest.MonkeyPatch) -> None:
    finance = Finance(output={"status": "recorded"}, outcome="recorded")
    _wire(monkeypatch, finance)
    await campaign.record_campaign_result(
        _req("record_campaign_result", RESULT | {"gmv_krw": 2_000_000, "orders": 40})
    )
    sent = [r for t, r in finance.calls if r.skill == "record_campaign_result"]
    assert sent[0].input["gmv_krw"] == 2_000_000


async def test_record_result_relays_finance_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    _wire(monkeypatch, Finance(status=TaskStatus.FAILED, output={}, outcome="unreachable"))
    full = RESULT | {"gmv_krw": 1_000_000}
    result = await campaign.record_campaign_result(_req("record_campaign_result", full))
    assert (result.status, result.outcome) == (TaskStatus.FAILED, "unreachable")


@pytest.mark.parametrize(
    "bad",
    [
        {"spend_krw": -1},
        {"gmv_krw": -5},
        {"period_from": "2026-10-09"},
        {"period_to": "10/05"},
    ],
)
async def test_record_result_invalid_input(monkeypatch: pytest.MonkeyPatch, bad: dict) -> None:
    finance = Finance()
    _wire(monkeypatch, finance)
    result = await campaign.record_campaign_result(_req("record_campaign_result", RESULT | bad))
    assert result.outcome == "invalid_input"
    assert finance.calls == []


async def test_card_lists_campaign_skills() -> None:
    skills = {s.id: s for s in load_card().skills}
    assert set(skills) == set(handlers.HANDLERS) | {"receive_improvement_note"}  # 공통 skill
    assert (skills["start_campaign"].grade, skills["start_campaign"].verifier) == ("B", "safety")
    assert skills["record_campaign_result"].grade == "read"


# ------------------------------------------- 상품 ID를 재무 예산 의견에 싣기 (B안 ③)
async def test_product_ids_in_the_request_go_to_the_finance_review(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    finance = Finance()
    _wire(monkeypatch, finance)
    asked = {**START, "product_ids": ["prd_1", "prd_2"]}
    result = await campaign.start_campaign(_req("start_campaign", asked))
    assert result.status == TaskStatus.COMPLETED
    (target, sent) = finance.calls[0]
    assert target == "finance" and sent.skill == campaign.REVIEW_SKILL
    assert sent.input["product_ids"] == ["prd_1", "prd_2"]


async def test_requests_without_products_still_ask_finance_with_an_empty_list(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    finance = Finance()
    _wire(monkeypatch, finance)
    await campaign.start_campaign(_req("start_campaign", START))
    assert finance.calls[0][1].input["product_ids"] == []  # 옛 요청은 그대로(상품 신호 없음)


async def test_more_than_ten_products_are_refused_like_any_other_bad_input(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    finance = Finance()
    _wire(monkeypatch, finance)
    asked = {**START, "product_ids": [f"prd_{n}" for n in range(11)]}
    result = await campaign.start_campaign(_req("start_campaign", asked))
    assert result.status != TaskStatus.COMPLETED and finance.calls == []


async def test_the_skill_takes_the_products_from_the_received_brief_when_the_request_has_none(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from types import SimpleNamespace

    finance = Finance()
    _wire(monkeypatch, finance)
    looked: list[str] = []

    def fake_get(proposal_id: object, campaign_id: str) -> object:
        looked.append(campaign_id)
        return SimpleNamespace(
            brief=SimpleNamespace(
                product_ids=["prd_7", "prd_8"], content_category=None, content_format=None
            )
        )

    monkeypatch.setattr(handlers.STATE.briefs, "get", fake_get)
    result = await handlers.start_campaign_skill(_req("start_campaign", START))
    assert result.status == TaskStatus.COMPLETED and looked == ["cmp_1"]
    assert finance.calls[0][1].input["product_ids"] == ["prd_7", "prd_8"]


async def test_products_in_the_request_win_over_the_brief(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    finance = Finance()
    _wire(monkeypatch, finance)

    def never(proposal_id: object, campaign_id: str) -> object:
        raise AssertionError("요청이 상품·카테고리·형식을 다 줬으면 기획안을 찾지 않는다")

    monkeypatch.setattr(handlers.STATE.briefs, "get", never)
    # 카테고리·형식도 기획안에서 이어 받으므로, 기획안을 안 찾는 경우는 셋 다 준 요청이다
    asked = {**START, "product_ids": ["prd_1"], **CATEGORY}
    await handlers.start_campaign_skill(_req("start_campaign", asked))
    assert finance.calls[0][1].input["product_ids"] == ["prd_1"]


async def test_no_brief_means_no_products_and_the_start_still_goes_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    finance = Finance()
    _wire(monkeypatch, finance)
    result = await handlers.start_campaign_skill(_req("start_campaign", START))
    assert result.status == TaskStatus.COMPLETED
    assert finance.calls[0][1].input["product_ids"] == []


async def test_a_broken_brief_store_does_not_break_the_campaign_start(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from marketing_agent.briefs import BriefStorageError

    finance = Finance()
    _wire(monkeypatch, finance)

    def broken(proposal_id: object, campaign_id: str) -> object:
        raise BriefStorageError("저장소를 읽지 못함")

    monkeypatch.setattr(handlers.STATE.briefs, "get", broken)
    result = await handlers.start_campaign_skill(_req("start_campaign", START))
    assert result.status == TaskStatus.COMPLETED  # 상품 신호만 빼고 재무 의견을 받는다
    assert finance.calls[0][1].input["product_ids"] == []


# ------------------------------------------- 콘텐츠 카테고리·형식·제작비 (재무 #1203 계약)
CATEGORY = {"content_category": "아재개그", "format": "shorts"}


async def test_category_and_format_go_to_the_finance_review(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    finance = Finance()
    _wire(monkeypatch, finance)
    result = await campaign.start_campaign(_req("start_campaign", START | CATEGORY))
    assert result.status == TaskStatus.COMPLETED
    sent = finance.calls[0][1].input
    assert sent["content_category"] == "아재개그" and sent["format"] == "shorts"


async def test_review_without_category_sends_no_category_keys(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    finance = Finance()
    _wire(monkeypatch, finance)
    await campaign.start_campaign(_req("start_campaign", START))
    sent = finance.calls[0][1].input
    assert "content_category" not in sent and "format" not in sent  # 지어내지 않는다


@pytest.mark.parametrize(
    "bad",
    [{"format": "tiktok"}, {"content_category": ""}, {"content_category": "가" * 41}],
)
async def test_bad_category_or_format_is_refused_on_start(
    monkeypatch: pytest.MonkeyPatch, bad: dict
) -> None:
    finance = Finance()
    _wire(monkeypatch, finance)
    result = await campaign.start_campaign(_req("start_campaign", START | bad))
    assert result.outcome == "invalid_input" and finance.calls == []


async def test_record_result_passes_category_format_and_production_cost(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from ax_contracts.schemas.finance import CampaignResultInput

    finance = Finance(output={"status": "recorded"}, outcome="recorded")
    _wire_routed(monkeypatch, finance, _data())
    asked = CALLER | CATEGORY | {"production_cost_krw": 150_000}
    result = await campaign.record_campaign_result(_req("record_campaign_result", asked))
    assert result.outcome == "recorded"
    sent = finance.calls[0][1].input
    assert sent["content_category"] == "아재개그" and sent["format"] == "shorts"
    assert sent["production_cost_krw"] == 150_000
    # 데이터가 조회수를 못 주면(이 대역은 다른 모양을 답함) 비워 둔다
    assert "views" not in sent and "views_point" not in sent
    parsed = CampaignResultInput.model_validate(sent)  # 재무 계약이 그대로 받는다
    assert parsed.production_cost_krw == 150_000 and parsed.spend_krw == 300_000


async def test_record_result_without_category_leaves_them_out(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    finance = Finance(output={"status": "recorded"}, outcome="recorded")
    _wire_routed(monkeypatch, finance, _data())
    await campaign.record_campaign_result(_req("record_campaign_result", CALLER))
    sent = finance.calls[0][1].input
    for name in ("content_category", "format", "production_cost_krw"):
        assert name not in sent  # 모르면 비워 둔다(0·빈 값으로 채우지 않는다)


async def test_record_result_keeps_zero_production_cost(monkeypatch: pytest.MonkeyPatch) -> None:
    # 0원은 "제작비가 없었다"는 호출자의 값이다. 빈 값(모름)과 다르다
    finance = Finance(output={"status": "recorded"}, outcome="recorded")
    _wire_routed(monkeypatch, finance, _data())
    asked = CALLER | {"production_cost_krw": 0}
    await campaign.record_campaign_result(_req("record_campaign_result", asked))
    assert finance.calls[0][1].input["production_cost_krw"] == 0


@pytest.mark.parametrize(
    "bad",
    [{"format": "tiktok"}, {"production_cost_krw": -1}, {"content_category": ""}],
)
async def test_record_result_bad_category_fields_never_reach_finance(
    monkeypatch: pytest.MonkeyPatch, bad: dict
) -> None:
    finance = Finance()
    _wire_routed(monkeypatch, finance, _data())
    result = await campaign.record_campaign_result(_req("record_campaign_result", CALLER | bad))
    assert result.outcome == "invalid_input" and finance.calls == []


# ---- 영상 등록(start_campaign)과 조회수 채우기(record_campaign_result) ----


class VideoData:
    """`call_agent` 대역: video_performance는 `video`, campaign_result는 `result`가 답한다."""

    def __init__(self, finance: Finance, video: Finance, result: Finance | None = None) -> None:
        self.finance, self.video, self.result = finance, video, result or _data()

    async def __call__(self, target: str, req: AgentRequest) -> AgentResult:
        if target == "data" and req.skill == "video_performance":
            return await self.video(target, req)
        if target == "data":
            return await self.result(target, req)
        return await self.finance(target, req)


def _checkpoint(name: str, views: int | None, reason: str | None = None) -> dict:
    return {
        "checkpoint": name,
        "status": "known" if views is not None else "unknown",
        "views": views,
        "reason": reason,
    }


def _video(checkpoints: list[dict], **fields) -> Finance:
    out = {
        "status": "known",
        "campaign_id": "cmp_1",
        "video_id": "abc123",
        "tracking": True,
        "checkpoints": checkpoints,
    } | fields
    return Finance(output=out, outcome="completed")


URL = "https://youtube.com/shorts/abc123"


def _wire_video(
    monkeypatch: pytest.MonkeyPatch, finance: Finance, video: Finance, gate: Gate | None = None
) -> Gate:
    gate = gate or Gate()
    monkeypatch.setattr(campaign, "call_agent", VideoData(finance, video))
    monkeypatch.setattr(campaign, "execute", gate)
    return gate


async def test_start_registers_video_after_executed(monkeypatch: pytest.MonkeyPatch) -> None:
    video = _video([])
    _wire_video(monkeypatch, Finance(), video)
    asked = START | {"video_url": URL, "test": False}
    result = await campaign.start_campaign(_req("start_campaign", asked))
    assert result.outcome == "executed"
    sent = video.calls[0][1]
    assert sent.skill == "video_performance"
    assert sent.input == {"campaign_id": "cmp_1", "video_url": URL, "test": False}
    assert result.output["video"] == {"registered": True, "video_id": "abc123", "test": False}


async def test_start_passes_test_true(monkeypatch: pytest.MonkeyPatch) -> None:
    video = _video([])
    _wire_video(monkeypatch, Finance(), video)
    await campaign.start_campaign(_req("start_campaign", START | {"video_url": URL, "test": True}))
    assert video.calls[0][1].input["test"] is True


async def test_start_without_video_url_does_not_call_data(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    video = _video([])
    _wire_video(monkeypatch, Finance(), video)
    result = await campaign.start_campaign(_req("start_campaign", START))
    assert result.outcome == "executed" and not video.calls
    assert "video" not in result.output


async def test_video_url_without_test_flag_is_refused_before_anything(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    finance, video = Finance(), _video([])
    gate = _wire_video(monkeypatch, finance, video)
    result = await campaign.start_campaign(_req("start_campaign", START | {"video_url": URL}))
    assert result.outcome == "invalid_input"
    assert not finance.calls and not video.calls and not gate.seen


async def test_held_campaign_does_not_register_video(monkeypatch: pytest.MonkeyPatch) -> None:
    video = _video([])
    gate = Gate(status="held", hold_reason=HoldReason.OVER_THRESHOLD)
    _wire_video(monkeypatch, Finance(), video, gate)
    result = await campaign.start_campaign(
        _req("start_campaign", START | {"video_url": URL, "test": False})
    )
    assert result.outcome == "held" and not video.calls


@pytest.mark.parametrize(
    "answer",
    [
        Finance(status=TaskStatus.FAILED, output={}, outcome="data_down"),
        Finance(output={"status": "unknown", "reason": "영상 못 찾음", "tracking": False}),
        Finance(output={"nonsense": 1}),
    ],
)
async def test_video_registration_failure_never_blocks_the_campaign(
    monkeypatch: pytest.MonkeyPatch, answer: Finance
) -> None:
    gate = _wire_video(monkeypatch, Finance(), answer)
    result = await campaign.start_campaign(
        _req("start_campaign", START | {"video_url": URL, "test": False})
    )
    assert result.status == TaskStatus.COMPLETED and result.outcome == "executed"
    assert gate.seen and result.output["video"]["registered"] is False
    assert result.output["video"]["warning"]


async def test_record_fills_views_from_latest_known_checkpoint(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    finance = Finance(output={"status": "recorded"}, outcome="recorded")
    video = _video(
        [
            _checkpoint("1h", 120),
            _checkpoint("1d", 3_000),
            _checkpoint("3d", 9_500),
            _checkpoint("7d", None, "측정 중"),
        ]
    )
    monkeypatch.setattr(campaign, "call_agent", VideoData(finance, video))
    result = await campaign.record_campaign_result(_req("record_campaign_result", CALLER))
    assert result.outcome == "recorded"
    sent = finance.calls[0][1].input
    assert sent["views"] == 9_500 and sent["views_point"] == "3d"
    assert video.calls[0][1].input == {"campaign_id": "cmp_1"}


async def test_record_sends_7d_when_measured(monkeypatch: pytest.MonkeyPatch) -> None:
    finance = Finance(output={"status": "recorded"}, outcome="recorded")
    video = _video([_checkpoint("3d", 9_500), _checkpoint("7d", 20_000)])
    monkeypatch.setattr(campaign, "call_agent", VideoData(finance, video))
    await campaign.record_campaign_result(_req("record_campaign_result", CALLER))
    sent = finance.calls[0][1].input
    assert sent["views"] == 20_000 and sent["views_point"] == "7d"


@pytest.mark.parametrize(
    "video",
    [
        _video([_checkpoint("1h", None, "측정 중"), _checkpoint("1d", None, "놓침")]),
        _video([], status="unknown", tracking=False, reason="등록된 영상 없음"),
        _video([_checkpoint("7d", 20_000)], test=True),
        _video([_checkpoint("7d", 20_000)], campaign_id="cmp_other"),
        Finance(status=TaskStatus.FAILED, output={}, outcome="data_down"),
        Finance(output={"nonsense": 1}),
    ],
)
async def test_record_leaves_views_blank_when_unmeasured_or_unusable(
    monkeypatch: pytest.MonkeyPatch, video: Finance
) -> None:
    finance = Finance(output={"status": "recorded"}, outcome="recorded")
    monkeypatch.setattr(campaign, "call_agent", VideoData(finance, video))
    result = await campaign.record_campaign_result(_req("record_campaign_result", CALLER))
    assert result.outcome == "recorded"  # 조회수 때문에 결과 전달이 막히지 않는다
    sent = finance.calls[0][1].input
    assert "views" not in sent and "views_point" not in sent


async def test_record_keeps_caller_views_and_skips_the_data_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    finance = Finance(output={"status": "recorded"}, outcome="recorded")
    video = _video([_checkpoint("7d", 20_000)])
    monkeypatch.setattr(campaign, "call_agent", VideoData(finance, video))
    asked = CALLER | {"views": 777, "views_point": "1d"}
    await campaign.record_campaign_result(_req("record_campaign_result", asked))
    sent = finance.calls[0][1].input
    assert sent["views"] == 777 and sent["views_point"] == "1d" and not video.calls


async def test_record_bad_views_point_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    finance = Finance(output={"status": "recorded"}, outcome="recorded")
    monkeypatch.setattr(campaign, "call_agent", VideoData(finance, _video([])))
    asked = CALLER | {"views": 1, "views_point": "2d"}
    result = await campaign.record_campaign_result(_req("record_campaign_result", asked))
    assert result.outcome == "invalid_input" and not finance.calls


# ------------------------------------------- 기획안의 카테고리·형식 잇기 (기획 #1212)
def _brief_with(monkeypatch: pytest.MonkeyPatch, **fields: object) -> list[str]:
    """받아 둔 기획안 대역. `content_format`(기획 칸)이 재무·마케팅 `format`으로 이어지는지 본다."""
    from types import SimpleNamespace

    looked: list[str] = []

    def fake_get(proposal_id: object, campaign_id: str) -> object:
        looked.append(campaign_id)
        return SimpleNamespace(brief=SimpleNamespace(product_ids=[], **fields))

    monkeypatch.setattr(handlers.STATE.briefs, "get", fake_get)
    return looked


BRIEF_CONTENT = {"content_category": "아재개그", "content_format": "reels"}


async def test_start_fills_category_and_format_from_the_brief(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    finance = Finance()
    _wire(monkeypatch, finance)
    looked = _brief_with(monkeypatch, **BRIEF_CONTENT)
    result = await handlers.start_campaign_skill(_req("start_campaign", START))
    assert result.status == TaskStatus.COMPLETED and looked == ["cmp_1"]  # 기획안은 한 번만 읽는다
    sent = finance.calls[0][1].input
    # 기획 칸 content_format -> 재무·마케팅 칸 format
    assert sent["content_category"] == "아재개그" and sent["format"] == "reels"
    assert "content_format" not in sent


async def test_start_caller_values_win_over_the_brief(monkeypatch: pytest.MonkeyPatch) -> None:
    finance = Finance()
    _wire(monkeypatch, finance)
    _brief_with(monkeypatch, **BRIEF_CONTENT)
    asked = START | {"content_category": "퀴즈", "format": "shorts"}
    await handlers.start_campaign_skill(_req("start_campaign", asked))
    sent = finance.calls[0][1].input
    assert sent["content_category"] == "퀴즈" and sent["format"] == "shorts"


async def test_start_fills_only_the_missing_field(monkeypatch: pytest.MonkeyPatch) -> None:
    finance = Finance()
    _wire(monkeypatch, finance)
    _brief_with(monkeypatch, **BRIEF_CONTENT)
    await handlers.start_campaign_skill(_req("start_campaign", START | {"format": "shorts"}))
    sent = finance.calls[0][1].input
    assert sent["content_category"] == "아재개그" and sent["format"] == "shorts"


async def test_start_brief_without_category_sends_no_keys(monkeypatch: pytest.MonkeyPatch) -> None:
    finance = Finance()
    _wire(monkeypatch, finance)
    _brief_with(monkeypatch, content_category=None, content_format=None)
    result = await handlers.start_campaign_skill(_req("start_campaign", START))
    assert result.status == TaskStatus.COMPLETED
    sent = finance.calls[0][1].input
    assert "content_category" not in sent and "format" not in sent


async def test_start_without_a_brief_sends_no_category_keys(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    finance = Finance()
    _wire(monkeypatch, finance)
    await handlers.start_campaign_skill(_req("start_campaign", START))
    sent = finance.calls[0][1].input
    assert "content_category" not in sent and "format" not in sent


async def test_start_broken_brief_store_leaves_category_blank_and_goes_on(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from marketing_agent.briefs import BriefStorageError

    finance = Finance()
    _wire(monkeypatch, finance)

    def broken(proposal_id: object, campaign_id: str) -> object:
        raise BriefStorageError("저장소를 읽지 못함")

    monkeypatch.setattr(handlers.STATE.briefs, "get", broken)
    result = await handlers.start_campaign_skill(_req("start_campaign", START))
    assert result.status == TaskStatus.COMPLETED
    sent = finance.calls[0][1].input
    assert "content_category" not in sent and "format" not in sent


async def test_start_does_not_read_the_brief_when_caller_gave_both(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    finance = Finance()
    _wire(monkeypatch, finance)
    looked = _brief_with(monkeypatch, **BRIEF_CONTENT)
    asked = START | CATEGORY | {"product_ids": ["prd_1"]}
    await handlers.start_campaign_skill(_req("start_campaign", asked))
    assert looked == []  # 호출자가 다 줬으면 기획안을 읽지 않는다


async def test_record_result_fills_category_and_format_from_the_brief(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    finance = Finance(output={"status": "recorded"}, outcome="recorded")
    _wire_routed(monkeypatch, finance, _data())
    _brief_with(monkeypatch, **BRIEF_CONTENT)
    result = await handlers.record_campaign_result_skill(_req("record_campaign_result", CALLER))
    assert result.outcome == "recorded"
    sent = finance.calls[0][1].input
    assert sent["content_category"] == "아재개그" and sent["format"] == "reels"


async def test_record_result_caller_values_win_over_the_brief(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    finance = Finance(output={"status": "recorded"}, outcome="recorded")
    _wire_routed(monkeypatch, finance, _data())
    _brief_with(monkeypatch, **BRIEF_CONTENT)
    asked = CALLER | {"content_category": "퀴즈", "format": "shorts"}
    await handlers.record_campaign_result_skill(_req("record_campaign_result", asked))
    sent = finance.calls[0][1].input
    assert sent["content_category"] == "퀴즈" and sent["format"] == "shorts"


async def test_record_result_without_brief_or_with_broken_store_leaves_them_out(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from marketing_agent.briefs import BriefStorageError

    finance = Finance(output={"status": "recorded"}, outcome="recorded")
    _wire_routed(monkeypatch, finance, _data())
    await handlers.record_campaign_result_skill(_req("record_campaign_result", CALLER))

    def broken(proposal_id: object, campaign_id: str) -> object:
        raise BriefStorageError("저장소를 읽지 못함")

    monkeypatch.setattr(handlers.STATE.briefs, "get", broken)
    result = await handlers.record_campaign_result_skill(_req("record_campaign_result", CALLER))
    assert result.outcome == "recorded"
    for sent in (call[1].input for call in finance.calls):
        assert "content_category" not in sent and "format" not in sent
