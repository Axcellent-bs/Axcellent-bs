"""마감 통제 3종: 정산 기한 점검, 월간 변동 분석, 판매자 미지급금 롤포워드."""

from __future__ import annotations

from datetime import date

import pytest
from fastapi.testclient import TestClient

from finance_agent import deadline, handlers, payables, variance
from finance_agent.main import app
from finance_agent.sources import MockSources

TODAY = date(2026, 10, 23)  # conftest FIXED_NOW


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch, fixed_now) -> None:
    monkeypatch.setenv("AGENT_TEMPLATE_MOCK", "1")
    handlers.reset_state(MockSources())


# ---------------------------------------------------------------- 정산 기한
def _rows(report):
    return {r["seller_id"]: r for r in report["sellers"]}


def test_old_unpaid_confirmed_sale_is_overdue(kit) -> None:
    books = kit.books(
        orders=[kit.order("ord_1", kit.at("2026-09-20"), [("sel_a", "book", 1, 10000)])]
    )
    report = deadline.deadline_report(books, {}, TODAY)
    row = _rows(report)["sel_a"]
    # 배송 완료(09-21) + 20일 = 10-11. 오늘(10-23) 지났다
    assert (row["deadline"], row["status"], row["days_left"]) == ("2026-10-11", "overdue", -12)
    assert row["amount"] == 9000  # 판매자 몫 (도서 수수료 10% 뺌)
    assert deadline.needs_attention(report) == [row]


def test_recent_sale_paid_on_time_is_ok_and_undelivered_waits(kit) -> None:
    orders = [
        kit.order("ord_1", kit.at("2026-10-14"), [("sel_a", "book", 1, 10000)]),
        kit.order("ord_2", kit.at("2026-10-21"), [("sel_b", "book", 1, 10000)], delivered=False),
    ]
    report = deadline.deadline_report(kit.books(orders=orders), {}, TODAY)
    row = _rows(report)["sel_a"]
    # 2026-W42 지급 예정 10-24, 기한 10-15 + 20 = 11-04
    assert (row["expected_pay_date"], row["deadline"], row["status"]) == (
        "2026-10-24",
        "2026-11-04",
        "ok",
    )
    assert "sel_b" not in _rows(report)  # 배송 전이라 기한이 아직 시작되지 않았다
    assert report["waiting_orders"] == 1


def test_held_week_puts_the_deadline_at_risk(kit) -> None:
    """같은 주에 배송 전 주문이 있으면 판매자 정산이 보류돼 확정된 주문도 예정일이 없다."""
    orders = [
        kit.order("ord_1", kit.at("2026-10-14"), [("sel_a", "book", 1, 10000)]),
        kit.order("ord_2", kit.at("2026-10-15"), [("sel_a", "book", 1, 5000)], delivered=False),
    ]
    row = _rows(deadline.deadline_report(kit.books(orders=orders), {}, TODAY))["sel_a"]
    assert (row["status"], row["expected_pay_date"]) == ("at_risk", None)
    assert [h["code"] for h in row["hold_reasons"]] == ["unconfirmed_purchase"]


def test_approved_batch_counts_as_paid(kit) -> None:
    books = kit.books(
        orders=[kit.order("ord_1", kit.at("2026-09-20"), [("sel_a", "book", 1, 10000)])]
    )
    batches = {"2026-W38": {"status": "approved", "seller_ids": ["sel_a"]}}
    assert deadline.deadline_report(books, batches, TODAY)["sellers"] == []
    requested = {"2026-W38": {"status": "requested", "seller_ids": ["sel_a"]}}
    assert deadline.deadline_report(books, requested, TODAY)["sellers"]  # 승인 전은 미지급


# ---------------------------------------------------------------- 미지급금 롤포워드
def _payable_books(kit):
    orders = [
        kit.order("ord_9", kit.at("2026-09-10"), [("sel_a", "book", 1, 20000)]),  # 기초 18,000
        kit.order("ord_1", kit.at("2026-10-05"), [("sel_a", "book", 2, 10000)]),  # 발생 18,000
        kit.order("ord_2", kit.at("2026-10-06"), [("sel_b", "stationery", 1, 10000)]),  # 9,200
    ]
    coupons = [
        {
            "coupon_id": "c1",
            "total_value": 1000,
            "funded_by": "seller",
            "seller_id": "sel_a",
            "created_at": kit.at("2026-10-07"),
        }
    ]
    refunds = [
        {
            "refund_id": "r1",
            "order_id": "ord_2",
            "seller_id": "sel_b",
            "amount": 2000,
            "created_at": kit.at("2026-10-08"),
        }
    ]
    return kit.books(orders=orders, coupons=coupons, refunds=refunds)


def test_rollforward_balances_and_splits_by_seller(kit) -> None:
    batches = {
        "2026-W37": {
            "status": "approved",
            "approved_at": "2026-10-02T01:00:00Z",
            "total_payout": 18000,
            "seller_ids": ["sel_a"],
            "payouts": {"sel_a": 18000},
        }
    }
    r = payables.rollforward(_payable_books(kit), batches, TODAY)
    assert r["month"] == "2026-10"
    assert r["totals"] == {
        "opening": 18000,
        "accrued": 17000 + 7200,
        "paid": 18000,
        "closing": 24200,
    }
    assert r["balanced"] is True
    rows = {x["seller_id"]: x for x in r["sellers"]}
    assert (rows["sel_a"]["opening"], rows["sel_a"]["accrued"], rows["sel_a"]["paid"]) == (
        18000,
        17000,
        18000,
    )
    assert rows["sel_b"]["closing"] == 7200
    assert r["overpaid"] == []


def test_rollforward_flags_overpaid_and_unallocated(kit) -> None:
    batches = {
        "a": {
            "status": "approved",
            "approved_at": "2026-10-03T00:00:00Z",
            "total_payout": 50000,
            "seller_ids": ["sel_a"],
            "payouts": {"sel_a": 50000},
        },
        "b": {
            "status": "approved",
            "requested_at": "2026-10-04T00:00:00Z",
            "total_payout": 700,
            "seller_ids": ["sel_b"],
        },
        "c": {
            "status": "requested",
            "total_payout": 999,
            "seller_ids": ["sel_b"],
        },  # 승인 전은 지급 아님
    }
    r = payables.rollforward(_payable_books(kit), batches, TODAY)
    assert r["overpaid"] == ["sel_a"]  # 18,000 + 17,000 − 50,000 < 0
    rows = {x["seller_id"]: x for x in r["sellers"]}
    assert rows[payables.UNALLOCATED]["paid"] == 700  # 판매자별 금액이 없는 예전 묶음
    assert r["totals"]["paid"] == 50700


def test_rollforward_is_unknown_without_orders(kit) -> None:
    books = _payable_books(kit)
    books.unavailable.append("orders")
    r = payables.rollforward(books, {}, TODAY)
    assert r["totals"]["closing"] is None and r["balanced"] is None


# ---------------------------------------------------------------- 변동 분석
def _variance_books(kit):
    orders = [
        kit.order(
            "ord_s1", kit.at("2026-09-10"), [("sel_a", "book", 1, 100000)]
        ),  # 9월 수수료 10,000
        kit.order("ord_o1", kit.at("2026-10-10"), [("sel_a", "book", 1, 100000)]),
        kit.order("ord_o2", kit.at("2026-10-11"), [("sel_b", "stationery", 1, 100000)]),  # 8,000
    ]
    return kit.books(orders=orders)


def test_variance_splits_commission_into_volume_and_rate_mix(kit) -> None:
    r = variance.variance_report(_variance_books(kit), TODAY)
    lines = {x["code"]: x for x in r["lines"]}
    assert (lines["gmv"]["previous"], lines["gmv"]["current"], lines["gmv"]["pct"]) == (
        100000,
        200000,
        1.0,
    )
    assert lines["commission_revenue"]["diff"] == 8000
    assert lines["take_rate"]["current"] == 0.09
    d = r["drivers"]
    assert d["commission_change"] == 8000
    assert d["volume_effect"] == 10000  # 총거래액 +100,000 × 지난달 10%
    assert d["volume_effect"] + d["rate_mix_effect"] == d["commission_change"]
    assert lines["gmv"]["flag"] and "총거래액이 100,000원 (100%) 늘었습니다" in lines["gmv"]["note"]
    cats = {c["category"]: c for c in r["categories"]}
    assert cats["stationery"]["commission_diff"] == 8000
    assert any("거래량 효과" in s for s in r["summary"])


def test_variance_without_orders_is_unknown(kit) -> None:
    books = _variance_books(kit)
    books.unavailable.append("orders")
    r = variance.variance_report(books, TODAY)
    lines = {x["code"]: x for x in r["lines"]}
    assert lines["gmv"]["current"] is None and lines["commission_revenue"]["diff"] is None
    assert r["drivers"] is None and r["categories"] == []


def test_korean_particle_follows_the_last_syllable() -> None:
    assert variance._ga("수수료 매출") == "수수료 매출이"
    assert variance._ga("영업 결과") == "영업 결과가"
    assert variance._ga("PG 수수료") == "PG 수수료가"


# ---------------------------------------------------------------- API
def test_close_control_apis_and_todo() -> None:
    client = TestClient(app)
    dl = client.get("/api/finance/settlements/deadlines").json()["data"]
    assert dl["deadline_days"] == 20 and set(dl["counts"]) == {
        "overdue",
        "at_risk",
        "due_soon",
        "ok",
    }
    assert client.get("/api/finance/payables").json()["data"]["balanced"] is True
    assert client.get("/api/finance/variance").json()["data"]["month"] == "2026-10"
    assert client.get("/api/finance/variance", params={"date": "x"}).status_code == 400
    todo = client.get("/api/finance/overview").json()["data"]["todo"]
    late = next(t for t in todo if t["kind"] == "settlement_deadline")
    assert late["link"] == "settlements" and late["items"]
