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
    finance = Finance(output={"status": "waiting_revenue"}, outcome="waiting_revenue")
    _wire(monkeypatch, finance)
    result = await campaign.record_campaign_result(_req("record_campaign_result", RESULT))
    assert result.outcome == "waiting_revenue"
    target, sent = finance.calls[0]
    assert (target, sent.skill) == ("finance", "record_campaign_result")
    assert "gmv_krw" not in sent.input  # 모르는 판매액은 채우지 않는다
    assert sent.input["spend_krw"] == 300_000
    assert sent.input["coupon_krw"] == 0 and sent.input["source"] == "data.campaign_effect"


async def test_record_result_keeps_known_gmv(monkeypatch: pytest.MonkeyPatch) -> None:
    finance = Finance(output={"status": "recorded"}, outcome="recorded")
    _wire(monkeypatch, finance)
    await campaign.record_campaign_result(
        _req("record_campaign_result", RESULT | {"gmv_krw": 2_000_000, "orders": 40})
    )
    assert finance.calls[0][1].input["gmv_krw"] == 2_000_000


async def test_record_result_relays_finance_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    _wire(monkeypatch, Finance(status=TaskStatus.FAILED, output={}, outcome="unreachable"))
    result = await campaign.record_campaign_result(_req("record_campaign_result", RESULT))
    assert (result.status, result.outcome) == (TaskStatus.FAILED, "unreachable")


@pytest.mark.parametrize(
    "bad",
    [
        {"spend_krw": -1},
        {"gmv_krw": -5},
        {"source": None},
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
    assert set(skills) == set(handlers.HANDLERS)
    assert (skills["start_campaign"].grade, skills["start_campaign"].verifier) == ("B", "finance")
    assert skills["record_campaign_result"].grade == "read"
