"""cancel_campaign: 재무 취소 전달(예산 기록만). record_campaign_result 재시도·예산 대체 금지."""

from datetime import UTC, datetime, timedelta

import pytest

from ax_agent_template import events
from ax_contracts.schemas import AgentRequest, AgentResult, RequestMeta, TaskStatus
from marketing_agent import campaign, campaign_cancel, handlers
from marketing_agent.main import load_card

pytestmark = pytest.mark.asyncio
CTX = "ctx_cnl_0001"
RESULT = {
    "campaign_id": "cmp_1",
    "campaign_name": "가을 세일",
    "channel": "sns",
    "period_from": "2026-10-01",
    "period_to": "2026-10-05",
    "spend_krw": 300_000,
    "source": "data.campaign_effect",
}
CANCEL = {"campaign_id": "cmp_1", "reason": "고객 요청으로 중단"}
CANCELED = {"campaign_id": "cmp_1", "status": "canceled", "released_krw": 300_000}


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


class Finance:
    def __init__(self, status=TaskStatus.COMPLETED, output=None, outcome="approved") -> None:
        self.calls: list[tuple[str, AgentRequest]] = []
        self.answer = (status, output if output is not None else {}, outcome)

    async def __call__(self, target: str, req: AgentRequest) -> AgentResult:
        self.calls.append((target, req))
        status, output, outcome = self.answer
        return AgentResult(meta=req.meta, status=status, outcome=outcome, output=output)


def _wire_cancel(monkeypatch: pytest.MonkeyPatch, finance: Finance) -> None:
    monkeypatch.setattr(campaign, "call_agent", finance)


async def test_cancel_forwards_contract_and_relays(monkeypatch: pytest.MonkeyPatch) -> None:
    finance = Finance(output=CANCELED, outcome="canceled")
    _wire_cancel(monkeypatch, finance)
    result = await campaign_cancel.cancel_campaign(_req("cancel_campaign", CANCEL))
    assert (result.status, result.outcome) == (TaskStatus.COMPLETED, "canceled")
    assert len(finance.calls) == 1
    target, sent = finance.calls[0]
    assert (target, sent.skill) == ("finance", "cancel_campaign")
    assert sent.input == CANCEL
    assert result.output["released_krw"] == 300_000
    assert "광고를 멈춘 것이 아니다" in result.output["note"]


@pytest.mark.parametrize("outcome", ["already_recorded", "not_started", "starts_unavailable"])
async def test_cancel_relays_finance_failure_unchanged(
    monkeypatch: pytest.MonkeyPatch, outcome: str
) -> None:
    out = {"campaign_id": "cmp_1", "status": outcome, "released_krw": 0}
    finance = Finance(status=TaskStatus.FAILED, output=out, outcome=outcome)
    _wire_cancel(monkeypatch, finance)
    result = await campaign_cancel.cancel_campaign(_req("cancel_campaign", CANCEL))
    assert (result.status, result.outcome, result.output) == (TaskStatus.FAILED, outcome, out)
    assert len(finance.calls) == 1


async def test_cancel_finance_unreachable_is_failed(monkeypatch: pytest.MonkeyPatch) -> None:
    _wire_cancel(monkeypatch, Finance(status=TaskStatus.FAILED, output={}, outcome="unreachable"))
    result = await campaign_cancel.cancel_campaign(_req("cancel_campaign", CANCEL))
    assert (result.status, result.outcome) == (TaskStatus.FAILED, "unreachable")


@pytest.mark.parametrize(
    "output",
    [
        {},
        {"campaign_id": "cmp_1", "status": "canceled"},
        {"campaign_id": "cmp_1", "status": "canceled", "released_krw": -1},
        {"campaign_id": "other", "status": "canceled", "released_krw": 10},
        {"campaign_id": "cmp_1", "status": "not_started", "released_krw": 0},
    ],
)
async def test_cancel_invalid_finance_answer_is_not_cancel(
    monkeypatch: pytest.MonkeyPatch, output: dict
) -> None:
    _wire_cancel(monkeypatch, Finance(output=output, outcome="canceled"))
    result = await campaign_cancel.cancel_campaign(_req("cancel_campaign", CANCEL))
    assert result.status == TaskStatus.FAILED
    assert result.outcome == "finance_cancel_invalid"


@pytest.mark.parametrize(
    "bad",
    [
        {"reason": ""},
        {"reason": "   "},
        {"campaign_id": ""},
        {"campaign_id": " "},
        {"reason": None},
    ],
)
async def test_cancel_invalid_input_never_calls_finance(
    monkeypatch: pytest.MonkeyPatch, bad: dict
) -> None:
    finance = Finance()
    _wire_cancel(monkeypatch, finance)
    result = await campaign_cancel.cancel_campaign(_req("cancel_campaign", CANCEL | bad))
    assert (result.status, result.outcome) == (TaskStatus.FAILED, "invalid_input")
    assert finance.calls == []


async def test_cancel_missing_fields_invalid(monkeypatch: pytest.MonkeyPatch) -> None:
    finance = Finance()
    _wire_cancel(monkeypatch, finance)
    result = await campaign_cancel.cancel_campaign(
        _req("cancel_campaign", {"campaign_id": "cmp_1"})
    )
    assert result.outcome == "invalid_input" and finance.calls == []


async def test_cancel_registered_in_card() -> None:
    assert handlers.HANDLERS["cancel_campaign"] is campaign_cancel.cancel_campaign
    skill = {s.id: s for s in load_card().skills}["cancel_campaign"]
    assert skill.grade == "read"
    assert list(skill.allowed_requesters) == ["orchestrator", "dashboard-api"]


async def test_record_result_not_started_passes_through_once(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    out = {"campaign_id": "cmp_1", "status": "not_started"}
    finance = Finance(status=TaskStatus.FAILED, output=out, outcome="not_started")
    _wire_cancel(monkeypatch, finance)
    full = RESULT | {"gmv_krw": 1_000_000}
    result = await campaign.record_campaign_result(_req("record_campaign_result", full))
    assert (result.status, result.outcome, result.output) == (
        TaskStatus.FAILED,
        "not_started",
        out,
    )
    assert len(finance.calls) == 1  # 재시도 없음


async def test_record_result_without_spend_is_invalid_before_finance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    finance = Finance()
    _wire_cancel(monkeypatch, finance)
    no_spend = {k: v for k, v in RESULT.items() if k != "spend_krw"}
    result = await campaign.record_campaign_result(
        _req("record_campaign_result", no_spend | {"budget_krw": 300_000})
    )
    assert (result.status, result.outcome) == (TaskStatus.FAILED, "invalid_input")
    assert finance.calls == []
