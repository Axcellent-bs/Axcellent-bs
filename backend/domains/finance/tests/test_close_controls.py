"""마감 통제: 월간 변동 분석, 판매자 미지급금 롤포워드, 정산 기한 알림.

판매자 정산 기한 점검 계산은 고객지원이 한다(2026-11-12, 고객지원 테스트로 옮김).
재무는 고객지원이 준 기한 점검을 그대로 보여 주고 할 일에 알리기만 한다.
"""

from __future__ import annotations

from datetime import date

import pytest
from fastapi.testclient import TestClient

from finance_agent import handlers, payables, variance
from finance_agent.main import app
from finance_agent.sources import MockSources

TODAY = date(2026, 10, 23)  # conftest FIXED_NOW


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch, fixed_now) -> None:
    monkeypatch.setenv("AGENT_TEMPLATE_MOCK", "1")
    handlers.reset_state(MockSources())


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


def test_rollforward_is_unknown_without_support_batches(kit) -> None:
    """고객지원에서 정산 묶음을 못 받으면 지급·기초·기말을 0원으로 두지 않는다."""
    r = payables.rollforward(_payable_books(kit), None, TODAY)
    assert r["balanced"] is None
    assert r["totals"] == {"opening": None, "accrued": None, "paid": None, "closing": None}
    assert all(row["paid"] is None and row["closing"] is None for row in r["sellers"])
    assert r["unavailable"] == ["seller_settlement_batches"]


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


def test_variance_does_not_flag_coupon_or_refund_changes(kit, monkeypatch) -> None:
    prev = {"coupon_cost": 100000, "refund_total": 100000, "pg_fee": 100000}
    cur = {"coupon_cost": 900000, "refund_total": 900000, "pg_fee": 900000}
    books = _variance_books(kit)
    real = variance._values
    monkeypatch.setattr(
        variance,
        "_values",
        lambda b, start, end: {**real(b, start, end), **(cur if start.month == 10 else prev)},
    )
    lines = {x["code"]: x for x in variance.variance_report(books, TODAY)["lines"]}
    assert lines["pg_fee"]["flag"] is True  # 비교 대상: 다른 비용은 그대로 표시한다
    for code in ("coupon_cost", "refund_total"):
        assert lines[code]["current"] == 900000  # 금액은 그대로 적는다
        assert lines[code]["flag"] is False and lines[code]["note"] == ""


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
def test_close_control_apis_and_todo(support) -> None:
    late_row = {
        "seller_id": "sel_0001",
        "name": "가게1",
        "orders": 1,
        "amount": 9000,
        "oldest_confirmed": "2026-09-21",
        "deadline": "2026-10-11",
        "days_left": -12,
        "expected_pay_date": None,
        "status": "overdue",
        "status_label": "기한 지남",
        "hold_reasons": [],
    }
    support.deadlines |= {
        "sellers": [late_row],
        "counts": support.deadlines["counts"] | {"overdue": 1},
    }
    client = TestClient(app)
    dl = client.get("/api/finance/settlements/deadlines").json()["data"]
    assert dl["deadline_days"] == 20 and set(dl["counts"]) == {
        "overdue",
        "at_risk",
        "due_soon",
        "ok",
    }
    assert dl["sellers"] == [late_row]  # 고객지원이 준 것 그대로
    assert client.get("/api/finance/payables").json()["data"]["balanced"] is True
    assert client.get("/api/finance/variance").json()["data"]["month"] == "2026-10"
    assert client.get("/api/finance/variance", params={"date": "x"}).status_code == 400
    todo = client.get("/api/finance/overview").json()["data"]["todo"]
    late = next(t for t in todo if t["kind"] == "settlement_deadline")
    assert late["link"] == "settlements" and late["items"]
    # 재무는 알리기만 한다. 지급 승인 요청·승인 할 일은 없다 (고객지원이 한다)
    assert late["action"] == {"type": "open_tab"} and "고객지원" in late["title"]
    assert late["items"][0]["detail"] == "기한 2026-10-11 (D-12)"
    assert not any(t["kind"] == "settlement_batch" for t in todo)


def test_payables_api_is_unknown_when_support_is_down() -> None:
    data = TestClient(app).get("/api/finance/payables").json()["data"]
    assert data["balanced"] is None and data["totals"]["paid"] is None


def test_variance_summary_says_refunds_do_not_change_the_operating_result() -> None:
    """환불은 영업 결과에서 빼지 않는다. 비용처럼 설명하지 않는다."""
    from finance_agent import variance as variance_module

    refund = variance_module._sentence("refund_total", 228500, 163000, 65500, 0.4)
    assert "판매자 정산에서 차감" in refund and "영업 결과를 줄였" not in refund
    pg = variance_module._sentence("pg_fee", 3282950, 1376449, 1906501, 1.39)
    assert "비용이 늘어 영업 결과를 줄였습니다" in pg
