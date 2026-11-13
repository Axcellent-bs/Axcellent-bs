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


def test_a_refund_finished_after_closing_updates_the_same_record() -> None:
    """문의를 닫은 뒤 환불이 끝나면 고객지원이 금액을 채워 한 번 더 보낸다 (#343)."""
    base = {"inquiry_id": 20, "order_id": "ord_2001", "claim_id": 3}
    first = _out(_ask(base | {"resolution": "answered"}))
    assert (first.status, first.refund_amount, first.expense_used) == ("recorded", 0, False)
    recorded_at = handlers.state().ledger.support_costs[20]["recorded_at"]

    paid = base | {"resolution": "claim_decided", "claim_decision": "approve"}
    again = _out(_ask(paid | {"refund_amount": 15000}))
    assert (again.status, again.refund_amount, again.expense_used) == ("updated", 15000, True)
    assert again.record_id == first.record_id and "환불 15,000원" in again.note
    record = handlers.state().ledger.support_costs[20]
    assert len(handlers.state().ledger.support_costs) == 1
    assert record["recorded_at"] == recorded_at and record["updated_at"]
    assert (record["resolution"], record["claim_decision"]) == ("claim_decided", "approve")


def test_a_replay_without_new_money_changes_nothing() -> None:
    base = {"inquiry_id": 21, "order_id": "ord_2001"}
    _out(_ask(base | {"resolution": "claim_decided", "refund_amount": 9000}))
    before = dict(handlers.state().ledger.support_costs[21])
    # 닫을 때 이벤트가 늦게 다시 와도 금액을 0원으로 되돌리지 않는다
    late = _out(_ask(base | {"resolution": "answered"}))
    same = _out(_ask(base | {"resolution": "claim_decided", "refund_amount": 9000}))
    assert (late.status, same.status) == ("already_recorded", "already_recorded")
    assert late.refund_amount == 9000
    assert handlers.state().ledger.support_costs[21] == before


def test_a_coupon_after_closing_is_kept_with_the_refund() -> None:
    base = {"inquiry_id": 22, "order_id": "ord_2001", "resolution": "compensated"}
    _out(_ask(base | {"refund_amount": 5000}))
    out = _out(_ask(base | {"coupon_id": "cpn_1"}))
    assert (out.status, out.refund_amount, out.coupon_issued) == ("updated", 5000, True)
    assert "환불 5,000원" in out.note and "쿠폰 지급" in out.note


def test_a_renumbered_inquiry_with_its_own_ref_does_not_overwrite_the_old_record() -> None:
    """고객지원 번호가 다시 1부터 매겨져도 `inquiry_ref`가 다르면 다른 기록이다 (11/13, #1082)."""
    old = _out(_ask({"inquiry_id": 5, "order_id": "ord_2001", "resolution": "answered"}))
    new = _out(
        _ask(
            {
                "inquiry_id": 5,
                "order_id": "ord_2002",
                "resolution": "claim_decided",
                "refund_amount": 9000,
                "inquiry_ref": "ctx_sup_new_5",
            }
        )
    )
    assert new.status == "recorded" and new.record_id == "sc_ctx_sup_new_5"
    rows = handlers.state().ledger.support_costs
    assert rows[5]["order_id"] == "ord_2001" and rows[5]["refund_amount"] == 0  # 예전 기록 그대로
    assert rows["ctx_sup_new_5"]["order_id"] == "ord_2002"
    assert rows["ctx_sup_new_5"]["inquiry_ref"] == "ctx_sup_new_5"
    assert old.record_id == "sc_5"
    # 같은 ref로 다시 오면 같은 기록이다
    again = _out(
        _ask({"inquiry_id": 5, "order_id": "ord_2002", "resolution": "answered",
              "inquiry_ref": "ctx_sup_new_5"})
    )  # fmt: skip
    assert again.status == "already_recorded" and len(rows) == 2


def test_records_keyed_by_ref_survive_a_restart(tmp_path) -> None:
    from finance_agent import persist

    _ask({"inquiry_id": 3, "order_id": "ord_2001", "resolution": "answered"})
    _ask({"inquiry_id": 3, "order_id": "ord_2002", "resolution": "answered",
          "inquiry_ref": "ctx_sup_3b"})  # fmt: skip
    path = tmp_path / "state.json"
    persist.save(handlers.state(), path)
    handlers.reset_state(MockSources())
    assert persist.load(handlers.state(), path)
    assert set(handlers.state().ledger.support_costs) == {3, "ctx_sup_3b"}
