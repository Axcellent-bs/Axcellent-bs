"""A2A 읽기·기록 skill `record_support_cost` 테스트: 고객지원 처리 결과를 재무 장부에 남긴다."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from ax_agent_template import events
from finance_agent import handlers
from finance_agent.main import app, load_card
from finance_agent.schemas import SupportCostOutput
from finance_agent.sources import MockSources


@pytest.fixture(autouse=True)
def _mock_mode(monkeypatch: pytest.MonkeyPatch, fixed_now) -> None:
    monkeypatch.setenv("AGENT_TEMPLATE_MOCK", "1")
    events.reset_mock_bus()
    handlers.reset_state(MockSources())


def _ask(data: dict, token: str = "mock:orchestrator") -> dict:
    body = {
        "meta": {
            "context_id": "ctx_inc_support",
            "token": token,
            "path": [],
            "deadline": (datetime.now(UTC) + timedelta(seconds=30)).isoformat(),
        },
        "skill": "record_support_cost",
        "input": data,
    }
    response = TestClient(app).post("/a2a", json=body)
    assert response.status_code == 200
    return response.json()


def _out(result: dict) -> SupportCostOutput:
    assert result["status"] == "completed", result
    return SupportCostOutput.model_validate(result["output"])


def test_the_skill_is_on_the_card_as_a_read_skill_for_the_orchestrator() -> None:
    card = load_card()
    skill = next(s for s in card.skills if s.id == "record_support_cost")
    assert skill.grade == "read"
    assert list(skill.allowed_requesters) == ["orchestrator"]


def test_a_refund_is_recorded_with_the_order_shipping_fee() -> None:
    result = _ask(
        {
            "inquiry_id": 7,
            "order_id": "ord_2001",
            "resolution": "claim_decided",
            "claim_id": 3,
            "claim_decision": "approve",
            "refund_amount": 24000,
        }
    )
    assert result["outcome"] == "recorded"
    out = _out(result)
    assert (out.status, out.expense_used, out.refund_amount) == ("recorded", True, 24000)
    assert out.shipping_fee == 3000  # 장부의 주문에서 읽은 값
    assert out.coupon_issued is False
    assert "환불 24,000원" in out.note
    assert handlers.state().ledger.support_costs[7]["order_id"] == "ord_2001"


def test_an_inquiry_without_any_expense_is_still_recorded() -> None:
    """경비를 쓰지 않아도(답변만 한 문의) 상담 처리 결과는 기록한다."""
    out = _out(_ask({"inquiry_id": 8, "order_id": "ord_2002", "resolution": "answered"}))
    assert (out.status, out.expense_used, out.refund_amount) == ("recorded", False, 0)
    assert out.shipping_fee == 0
    assert out.note == "문의 #8 처리 결과 기록: answered, 경비 사용 없음"
    assert 8 in handlers.state().ledger.support_costs


def test_a_coupon_counts_as_an_expense() -> None:
    out = _out(
        _ask(
            {
                "inquiry_id": 9,
                "order_id": "ord_2001",
                "resolution": "compensated",
                "coupon_id": "cpn_0001",
            }
        )
    )
    assert (out.expense_used, out.coupon_issued, out.refund_amount) == (True, True, 0)
    assert "쿠폰 지급" in out.note


def test_the_same_inquiry_is_recorded_once() -> None:
    first = _out(_ask({"inquiry_id": 10, "order_id": "ord_2001", "resolution": "answered"}))
    again = _out(_ask({"inquiry_id": 10, "order_id": "ord_2001", "resolution": "answered"}))
    assert (first.status, again.status) == ("recorded", "already_recorded")
    assert first.record_id == again.record_id
    assert len(handlers.state().ledger.support_costs) == 1


def test_an_unknown_order_leaves_the_shipping_fee_empty_instead_of_guessing() -> None:
    result = _ask({"inquiry_id": 11, "order_id": "ord_9999", "resolution": "answered"})
    out = _out(result)
    assert result["outcome"] == "partial"
    assert out.status == "recorded"  # 처리 결과는 기록한다
    assert out.shipping_fee is None
    assert any("ord_9999" in line for line in out.unavailable)


def test_an_inquiry_without_an_order_is_recorded_without_a_shipping_fee() -> None:
    out = _out(_ask({"inquiry_id": 12, "resolution": "escalated"}))
    assert out.order_id is None and out.shipping_fee is None
    assert any("주문이 연결되지 않아" in line for line in out.unavailable)


def test_other_departments_and_bad_input_are_refused() -> None:
    assert _ask({"inquiry_id": 1, "resolution": "answered"}, token="mock:marketing")["status"] in (
        "rejected",
        "failed",
    )
    bad = _ask({"inquiry_id": 1, "resolution": "invented", "refund_amount": -5})
    assert (bad["status"], bad["outcome"]) == ("failed", "invalid_input")
