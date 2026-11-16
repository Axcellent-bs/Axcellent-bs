"""고객지원 비용 기록(`record_support_cost`)과 결산의 관계 (회귀 테스트).

설계: 고객지원 비용 기록은 "고객지원이 무엇을 했나"의 메모다. 돈(환불·쿠폰)은 결산이 판매 장부
(`books.refunds`·`books.coupons`)에서 한 번만 센다. 그래서
- 비용 기록을 남겨도 결산 숫자는 바뀌지 않는다 (두 번 세지 않는다)
- 고객지원이 알린 환불·쿠폰이 장부에 있으면 그날 결산의 환불·쿠폰 줄에 들어 있다
- 월 결산은 같은 장부의 일 결산을 모두 더한 것과 같다
"""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

from ax_agent_template import events
from finance_agent import handlers
from finance_agent.main import app
from finance_agent.sources import MockSources

CODES = ("gmv", "commission_revenue", "coupon_cost", "refund_total", "pg_fee", "net_result")


def _books(kit):
    """10월 장부: 여러 날의 주문, 그중 두 주문의 환불, 회사·판매자 부담 쿠폰."""
    orders = [
        kit.order("ord_1", kit.at("2026-10-05", "09:00"), [("sel_a", "book", 2, 10000)]),
        kit.order(
            "ord_2",
            kit.at("2026-10-05", "13:00"),
            [("sel_b", "stationery", 1, 12345)],
            shipping=3000,
        ),  # fmt: skip
        kit.order("ord_3", kit.at("2026-10-12", "23:30"), [("sel_a", "book", 1, 50000)]),
        kit.order("ord_4", kit.at("2026-10-20"), [("sel_b", "book", 3, 7000)], shipping=3000),
        kit.order(
            "ord_5", kit.at("2026-10-21"), [("sel_a", "book", 1, 9000)], payment_status="failed"
        ),  # fmt: skip
        kit.order("ord_6", kit.at("2026-09-30", "23:59"), [("sel_a", "book", 1, 4000)]),  # 9월
    ]
    refunds = [
        {
            "refund_id": "rfd_1",
            "order_id": "ord_1",
            "seller_id": "sel_a",
            "amount": 10000,
            "created_at": kit.at("2026-10-06", "15:00"),
        },  # fmt: skip
        {
            "refund_id": "rfd_2",
            "order_id": "ord_4",
            "seller_id": "sel_b",
            "amount": 7000,
            "created_at": kit.at("2026-10-20", "18:00"),
        },  # fmt: skip
    ]
    coupons = [
        {
            "coupon_id": "cpn_1",
            "total_value": 3000,
            "funded_by": "company",
            "created_at": kit.at("2026-10-12"),
        },  # fmt: skip
        {
            "coupon_id": "cpn_2",
            "total_value": 2000,
            "funded_by": "seller",
            "seller_id": "sel_a",
            "created_at": kit.at("2026-10-12"),
        },  # fmt: skip
    ]
    return kit.books(orders=orders, refunds=refunds, coupons=coupons)


@pytest.fixture(autouse=True)
def _mock_mode(monkeypatch: pytest.MonkeyPatch, fixed_now, kit) -> None:
    monkeypatch.setenv("AGENT_TEMPLATE_MOCK", "1")
    events.reset_mock_bus()
    handlers.reset_state(MockSources())
    books = _books(kit)

    async def loader():
        return books

    handlers.state().books_loader = loader


def _ask(data: dict[str, Any]) -> dict[str, Any]:
    body = {
        "meta": {
            "context_id": "ctx_support_closing",
            "token": "mock:orchestrator",
            "path": [],
            "deadline": (datetime.now(UTC) + timedelta(seconds=30)).isoformat(),
        },
        "skill": "record_support_cost",
        "input": data,
    }
    response = TestClient(app).post("/a2a", json=body)
    assert response.status_code == 200
    result = response.json()
    assert result["status"] == "completed", result
    return result["output"]


def _closing(period: str, day: str) -> dict[str, int | None]:
    response = TestClient(app).get("/api/finance/closing", params={"period": period, "date": day})
    assert response.status_code == 200, response.text
    return {ln["code"]: ln["amount"] for ln in response.json()["data"]["lines"]}


def _october_days() -> list[str]:
    return [(date(2026, 10, 1) + timedelta(days=i)).isoformat() for i in range(31)]


def _all_closings() -> dict[str, dict[str, int | None]]:
    out = {day: _closing("day", day) for day in _october_days()}
    out["month"] = _closing("month", "2026-10-01")
    return out


SUPPORT_RECORDS = [
    # 장부에 있는 환불을 고객지원이 알림 (문의 닫은 뒤 환불 금액을 채워 다시 보냄)
    {"inquiry_id": 1, "order_id": "ord_1", "resolution": "answered"},
    {
        "inquiry_id": 1,
        "order_id": "ord_1",
        "resolution": "claim_decided",
        "claim_decision": "approve",
        "refund_amount": 10000,
    },  # fmt: skip
    {
        "inquiry_id": 2,
        "order_id": "ord_4",
        "resolution": "claim_decided",
        "claim_decision": "approve",
        "refund_amount": 7000,
        "inquiry_ref": "ctx_sup_2",
    },  # fmt: skip
    # 쿠폰 보상
    {"inquiry_id": 3, "order_id": "ord_3", "resolution": "compensated", "coupon_id": "cpn_1"},
    # 장부에 아직 없는 환불(판매 장부에 들어오기 전), 없는 주문
    {"inquiry_id": 4, "order_id": "ord_2", "resolution": "claim_decided", "refund_amount": 5555},
    {
        "inquiry_id": 5,
        "order_id": "ord_9999",
        "resolution": "claim_decided",
        "refund_amount": 8888,
    },  # fmt: skip
    # 경비 없는 문의
    {"inquiry_id": 6, "order_id": "ord_2", "resolution": "answered"},
]


def test_recording_support_costs_never_changes_the_closing_numbers() -> None:
    """비용 기록은 메모다. 같은 돈을 결산에서 한 번 더 세지 않는다."""
    before = _all_closings()
    for record in SUPPORT_RECORDS:
        _ask(record)
    assert len(handlers.state().ledger.support_costs) == 6
    assert sum(r["refund_amount"] for r in handlers.state().ledger.support_costs.values()) == (
        10000 + 7000 + 5555 + 8888
    )
    assert _all_closings() == before
    # 장부에 없는 환불(5,555원·8,888원)은 결산에 들어가지 않는다
    assert before["month"]["refund_total"] == -(10000 + 7000)


def test_a_reported_refund_that_is_in_the_books_is_in_that_days_closing() -> None:
    for record in SUPPORT_RECORDS:
        _ask(record)
    # ord_1 환불은 환불한 날(10-06)에 잡힌다. 주문한 날(10-05)이 아니다
    assert _closing("day", "2026-10-06")["refund_total"] == -10000
    assert _closing("day", "2026-10-05")["refund_total"] == 0
    # ord_4는 주문·환불이 같은 날
    day20 = _closing("day", "2026-10-20")
    assert day20["refund_total"] == -7000 and day20["gmv"] == 21000
    # 고객지원이 알린 회사 부담 쿠폰은 발급한 날 쿠폰 줄에 (판매자 부담 쿠폰은 빼고)
    assert _closing("day", "2026-10-12")["coupon_cost"] == -3000
    # 결산의 환불 줄은 장부에 있는 고객지원 기록의 환불 합과 같다
    books_orders = {"ord_1", "ord_4"}
    reported = sum(
        r["refund_amount"]
        for r in handlers.state().ledger.support_costs.values()
        if r["order_id"] in books_orders
    )
    assert _closing("month", "2026-10-01")["refund_total"] == -reported


def test_the_month_closing_is_the_sum_of_its_day_closings() -> None:
    for record in SUPPORT_RECORDS:
        _ask(record)
    closings = _all_closings()
    month = closings.pop("month")
    for code in CODES:
        days = [c[code] for c in closings.values()]
        assert all(isinstance(v, int) for v in days), code
        assert month[code] == sum(days), code  # type: ignore[arg-type]
    # 9월 30일 주문·결제 실패 주문은 10월 결산에 없다
    assert month["gmv"] == 20000 + 12345 + 50000 + 21000


def test_the_support_cost_list_keeps_every_record_with_its_own_amount() -> None:
    for record in SUPPORT_RECORDS:
        _ask(record)
    response = TestClient(app).get("/api/finance/support-costs")
    rows = response.json()["data"]
    ids = {"sc_1", "sc_ctx_sup_2", "sc_3", "sc_4", "sc_5", "sc_6"}
    assert {r["record_id"] for r in rows} == ids
    by_id = {r["record_id"]: r for r in rows}
    assert (by_id["sc_1"]["refund_amount"], by_id["sc_1"]["shipping_fee"]) == (10000, 0)
    assert (by_id["sc_4"]["refund_amount"], by_id["sc_4"]["shipping_fee"]) == (5555, 3000)
    assert by_id["sc_5"]["shipping_fee"] is None  # 장부에 없는 주문
    assert by_id["sc_3"]["coupon_issued"] and not by_id["sc_6"]["expense_used"]
