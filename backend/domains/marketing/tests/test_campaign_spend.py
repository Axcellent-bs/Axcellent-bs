"""record_campaign_result의 광고비: 호출자 값 우선, 없으면 시작 때 예산(참고값), 모르면 멈춤."""

import sys
from pathlib import Path

import pytest

from ax_contracts.schemas import HoldReason, TaskStatus
from marketing_agent import campaign, handlers

sys.path.insert(0, str(Path(__file__).parent))
from test_campaign import RESULT, START, Finance, Gate, _req, _wire  # noqa: E402

pytestmark = pytest.mark.asyncio

NO_SPEND = {k: v for k, v in RESULT.items() if k not in {"spend_krw", "source"}}


@pytest.fixture(autouse=True)
def _clean(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_TEMPLATE_MOCK", "1")
    handlers.reset_state()


async def _start(monkeypatch: pytest.MonkeyPatch, **gate: str) -> None:
    _wire(monkeypatch, Finance(), Gate(**gate))
    await campaign.start_campaign(_req("start_campaign", START))


async def test_started_then_recorded_uses_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    await _start(monkeypatch)
    finance = Finance(output={"status": "waiting_revenue"}, outcome="waiting_revenue")
    _wire(monkeypatch, finance)
    result = await campaign.record_campaign_result(_req("record_campaign_result", NO_SPEND))
    assert result.outcome == "waiting_revenue"
    sent = finance.calls[0][1].input
    assert sent["spend_krw"] == START["budget_krw"]
    assert sent["source"] == "marketing.budget_krw"
    assert "gmv_krw" not in sent
    assert result.output["spend_basis"] == "budget"
    assert "실제 집행액이 아니라" in result.output["spend_note"]
    assert result.output["status"] == "waiting_revenue"


async def test_caller_spend_wins_over_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    await _start(monkeypatch)
    finance = Finance(output={"status": "recorded"}, outcome="recorded")
    _wire(monkeypatch, finance)
    result = await campaign.record_campaign_result(_req("record_campaign_result", RESULT))
    sent = finance.calls[0][1].input
    assert (sent["spend_krw"], sent["source"]) == (300_000, "data.campaign_effect")
    assert "spend_note" not in result.output


async def test_caller_spend_zero_is_kept(monkeypatch: pytest.MonkeyPatch) -> None:
    await _start(monkeypatch)
    finance = Finance(output={"status": "recorded"}, outcome="recorded")
    _wire(monkeypatch, finance)
    await campaign.record_campaign_result(_req("record_campaign_result", RESULT | {"spend_krw": 0}))
    assert finance.calls[0][1].input["spend_krw"] == 0


async def test_unknown_campaign_is_spend_unknown(monkeypatch: pytest.MonkeyPatch) -> None:
    finance = Finance()
    _wire(monkeypatch, finance)
    result = await campaign.record_campaign_result(_req("record_campaign_result", NO_SPEND))
    assert (result.status, result.outcome) == (TaskStatus.FAILED, "spend_unknown")
    assert finance.calls == []


async def test_restart_forgets_budget(monkeypatch: pytest.MonkeyPatch) -> None:
    await _start(monkeypatch)
    handlers.reset_state()  # 프로세스 재시작과 같다
    finance = Finance()
    _wire(monkeypatch, finance)
    result = await campaign.record_campaign_result(_req("record_campaign_result", NO_SPEND))
    assert result.outcome == "spend_unknown"
    assert finance.calls == []


@pytest.mark.parametrize(
    "fields",
    [
        {
            "status": "held",
            "grade": "C",
            "review_id": "r1",
            "hold_reason": HoldReason.OVER_THRESHOLD,
        },
        {"status": "verifying"},
    ],
)
async def test_not_executed_campaign_is_not_remembered(
    monkeypatch: pytest.MonkeyPatch, fields: dict
) -> None:
    await _start(monkeypatch, **fields)
    assert campaign.BUDGETS == {}
    finance = Finance()
    _wire(monkeypatch, finance)
    result = await campaign.record_campaign_result(_req("record_campaign_result", NO_SPEND))
    assert result.outcome == "spend_unknown"
    assert finance.calls == []


async def test_rejected_by_finance_is_not_remembered(monkeypatch: pytest.MonkeyPatch) -> None:
    rejected = Finance(output={**Finance().answer[1], "decision": "rejected"}, outcome="rejected")
    _wire(monkeypatch, rejected)
    await campaign.start_campaign(_req("start_campaign", START))
    assert campaign.BUDGETS == {}


@pytest.mark.parametrize(
    "bad",
    [
        {"spend_krw": 100},  # source 없이 금액만
        {"source": "x"},  # 금액 없이 출처만
        {"spend_krw": -1, "source": "x"},
    ],
)
async def test_spend_source_must_come_together(monkeypatch: pytest.MonkeyPatch, bad: dict) -> None:
    await _start(monkeypatch)
    finance = Finance()
    _wire(monkeypatch, finance)
    result = await campaign.record_campaign_result(_req("record_campaign_result", NO_SPEND | bad))
    assert result.outcome == "invalid_input"
    assert finance.calls == []
