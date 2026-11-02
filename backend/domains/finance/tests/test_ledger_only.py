"""재무는 쿠폰·환불을 끝난 금액만 장부에 적는다 (담당 결정 2026-11-02).

쿠폰·환불이 아무리 수상해 보여도 감사 결과·경보·할 일을 만들지 않고, 장부 합계는 그대로다.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

from ax_agent_template import events
from ax_contracts.schemas import EventEnvelope, EventType
from finance_agent import audit, handlers, incidents, settlement
from finance_agent import books as bk
from finance_agent import finance_config as fc
from finance_agent.closing import compute_totals
from finance_agent.main import app
from finance_agent.sources import MockSources


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch, fixed_now) -> None:
    monkeypatch.setenv("AGENT_TEMPLATE_MOCK", "1")
    events.reset_mock_bus()
    handlers.reset_state(MockSources())


def _suspicious_books(kit) -> bk.Books:
    """예전 감사·경보에 모두 걸리던 장부: 쪼개기·중복 쿠폰, 큰 환불, 잦은 보류·거절."""
    now = kit.at("2026-10-22", "10:00")
    coupons = [
        {
            "coupon_id": f"cpn_{i}",
            "buyer_id": "buy_0981",
            "unit_value": 19900,
            "count": 1,
            "total_value": 19900,
            "purpose_code": "compensation",
            "context_id": "ctx_inc_1",
            "created_at": now + timedelta(minutes=i),
            "funded_by": "company",
        }
        for i in range(3)
    ]
    coupons.append(
        {
            "coupon_id": "cpn_seller",
            "buyer_id": "buy_0982",
            "unit_value": 2000,
            "count": 1,
            "total_value": 2000,
            "seller_id": "sel_a",
            "created_at": now,
            "funded_by": "seller",
        }
    )
    orders = [kit.order("ord_1", now, [("sel_a", "book", 1, 100000)])]
    refunds = [
        {
            "refund_id": f"rfd_{i}",
            "order_id": "ord_1",
            "buyer_id": "buy_0982",
            "seller_id": "sel_a",
            "amount": 95000,
            "created_at": now + timedelta(hours=i),
        }
        for i in range(2)
    ]
    executions = [
        {
            "execution_id": f"exe_{action}_{status}",
            "created_at": now,
            "action": action,
            "requester": "marketing",
            "context_id": "ctx_inc_1",
            "params": {"count": 2, "unit_value": 5000, "total_value": 12000},  # 금액도 틀리다
            "status": status,
            "computed_amount": 12000,
        }
        for action in ("issue_coupon", "refund_request")
        for status in ("held", "rejected", "executed")
    ]
    return kit.books(orders=orders, refunds=refunds, coupons=coupons, executions=executions)


def test_coupons_and_refunds_make_no_findings(kit) -> None:
    data, missing = asyncio.run(audit.audit_report(_suspicious_books(kit), 7))
    assert data["findings"] == [] and missing == []
    assert data["stats"] == {"gate_held": 0, "gate_rejected": 0}  # 쿠폰·환불은 세지 않는다
    assert set(data["stats"]).isdisjoint({"coupon_count", "coupon_amount"})


def test_other_actions_are_still_counted_beside_coupons_and_refunds(kit) -> None:
    books = _suspicious_books(kit)
    assert books.executions is not None
    books.executions += [
        {
            "execution_id": f"exe_settle_{i}",
            "created_at": kit.at("2026-10-22"),
            "action": "confirm_settlement",
            "status": "held",
            "params": {},
        }
        for i in range(3)
    ]
    data, _ = asyncio.run(audit.audit_report(books, 7))
    [found] = data["findings"]
    assert found["code"] == "gate_hold_trend" and data["stats"]["gate_held"] == 3
    assert all(r.startswith("exe_settle_") for r in found["refs"])  # 쿠폰·환불 기록을 섞지 않는다


def test_coupons_and_refunds_make_no_todo_or_alert(kit) -> None:
    books = _suspicious_books(kit)

    async def load() -> bk.Books:
        return books

    handlers.state().books_loader = load
    client = TestClient(app)
    kinds = {t["kind"] for t in client.get("/api/finance/overview").json()["data"]["todo"]}
    assert kinds.isdisjoint({"audit_finding", "policy_alert"})
    assert handlers.state().records.anomalies == {}
    assert client.get("/api/finance/anomalies").json()["data"]["open_count"] == 0
    assert client.get("/api/finance/policy").status_code == 404


def test_ledger_totals_stay_as_they_were(kit) -> None:
    """결산의 쿠폰 비용·환불 합계와 정산의 판매자 부담 쿠폰·환불 차감은 그대로 장부에서 나온다."""
    books = _suspicious_books(kit)
    day = kit.at("2026-10-22").date()
    totals = compute_totals(books, fc.day_start(day), fc.day_start(day + timedelta(days=1)))
    assert totals.refund_total == 190000
    assert totals.coupon_cost == 3 * 19900  # 회사 부담 쿠폰만 비용이다
    [row] = settlement.statements(books, "2026-W43")["statements"]
    assert (row["gross"], row["seller_coupon"], row["refunds"]) == (100000, 2000, 190000)
    assert row["payout"] == 100000 - row["commission"] - 2000 - 190000


def test_events_still_add_up_in_the_ledger(kit) -> None:
    """`coupon.issued`·`payment.refunded`는 그대로 누적하고 의심 건을 만들지 않는다."""

    def event(n: int, kind: EventType, ctx: str | None, data: dict[str, Any]) -> EventEnvelope:
        return EventEnvelope(
            event_id=f"evt_{n}",
            type=kind,
            occurred_at=kit.now,
            context_id=ctx,
            source="marketing",
            idempotency_key=f"key_{n}",
            data=data,
        )

    coupon = {"purpose_code": "compensation", "count": 1, "total_value": 2000, "coupon_id": "cpn_1"}
    refund = {"order_id": "ord_7", "refund_amount": 18000}
    for envelope in (
        event(1, EventType.COUPON_ISSUED, "ctx_inc_1", coupon),
        event(2, EventType.COUPON_ISSUED, "ctx_inc_1", coupon | {"coupon_id": "cpn_2"}),
        event(3, EventType.PAYMENT_REFUNDED, None, refund),
        event(3, EventType.PAYMENT_REFUNDED, None, refund),  # 같은 이벤트는 한 번만
    ):
        asyncio.run(handlers.on_event(envelope))
    ledger = handlers.state().ledger
    assert ledger.compensation("ctx_inc_1") == {"count": 2, "total": 4000}
    assert ledger.refunds == {"ord_7": 18000}
    assert handlers.state().records.anomalies == {}


def test_incident_keeps_amounts_and_rows_without_limits_or_checks(kit) -> None:
    rows = [
        {
            "execution_id": "exe_1",
            "created_at": kit.at("2026-10-22"),
            "action": "issue_coupon",
            "requester": "marketing",
            "context_id": "ctx_inc_scn_a_0001",
            "params": {
                "buyer_id": "buy_0999",  # 영향 고객 목록에 없어도 내역에 적기만 한다
                "purpose_code": "compensation",
                "count": 1,
                "unit_value": 2000,
                "total_value": 2000,
            },
            "status": "executed",
            "grade": "A",
            "computed_amount": 2000,
        }
    ]
    data, unavailable = asyncio.run(
        incidents.incident_view("ctx_inc_scn_a_0001", handlers.lg.ExecutionFeed(rows, "seed"))
    )
    comp = data["compensation"]
    assert (comp["total_executed"], comp["count_executed"]) == (2000, 1)
    assert [c["buyer_id"] for c in comp["coupons"]] == ["buy_0999"]
    assert "in_impact" not in comp["coupons"][0]
    assert "limits" not in data and "checks" not in data and "limits" not in unavailable


def test_settlement_does_not_ask_for_claims(kit) -> None:
    books = _suspicious_books(kit)
    assert not hasattr(books, "claims")
    [row] = settlement.statements(books, "2026-W43")["statements"]
    assert {h["code"] for h in row["hold_reasons"]}.isdisjoint({"open_claim", "claims_unknown"})
