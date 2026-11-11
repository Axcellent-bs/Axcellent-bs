"""③ 판매자 정산 테스트: 정산 공식과 보류 사유."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta

import pytest

from finance_agent import books as bk
from finance_agent import finance_config as fc
from finance_agent import settlement as st


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_TEMPLATE_MOCK", "1")


def test_seed_last_week_statements(fixed_now) -> None:
    result = st.statements(bk.seed_books(), None)
    assert (result["period"], result["pay_date"]) == ("2026-W42", "2026-10-24")
    assert (result["from"], result["to"]) == ("2026-10-12T00:00:00Z", "2026-10-19T00:00:00Z")
    rows = {r["seller_id"]: r for r in result["statements"]}
    assert set(rows) == {"sel_0001", "sel_0002", "sel_0003"}
    for r in rows.values():
        assert r["payout"] == r["gross"] - r["commission"] - r["seller_coupon"] - r["refunds"]
        assert set(r) == {
            "seller_id",
            "name",
            "orders",
            "gross",
            "commission_rate",
            "commission",
            "seller_coupon",
            "refunds",
            "payout",
            "hold_reasons",
            "status",
            "review_id",
            "products",
            "pending_confirmation",
        }
    assert {r["status"] for r in rows.values()} == {"ready"}
    assert all(r["hold_reasons"] == [] for r in rows.values())
    assert (
        rows["sel_0001"]["commission_rate"] == 0.10 and rows["sel_0002"]["commission_rate"] == 0.08
    )
    assert 0.08 < rows["sel_0003"]["commission_rate"] < 0.10  # 도서·문구 섞임 → 실효 수수료율
    assert rows["sel_0003"]["seller_coupon"] == 7 * 2000  # 매일 판매자 부담 2,000원


@pytest.mark.parametrize("offset", range(7))
def test_two_sellers_ready_on_any_weekday(monkeypatch: pytest.MonkeyPatch, offset: int) -> None:
    now = datetime(2026, 10, 19, 12, tzinfo=UTC) + timedelta(days=offset)
    monkeypatch.setattr(fc, "now", lambda: now)
    result = st.statements(bk.seed_books(), None)["statements"]
    rows = {r["seller_id"]: r["status"] for r in result}
    assert (rows["sel_0001"], rows["sel_0003"]) == ("ready", "ready")
    codes = {h["code"] for r in result for h in r["hold_reasons"]}
    assert codes <= {"no_confirmed_orders"}  # 처리 중 클레임으로는 보류하지 않는다


def test_hold_reasons(kit, fixed_now) -> None:
    when = kit.at("2026-10-14")
    orders = [
        kit.order("ord_1", when, [("sel_a", "book", 1, 20000)], delivered=False),
        kit.order("ord_2", when, [("sel_b", "book", 1, 10000)]),
    ]
    refunds = [
        {
            "refund_id": "rfd_1",
            "order_id": "ord_2",
            "seller_id": "sel_b",
            "amount": 9500,
            "created_at": when,
        }
    ]
    books = kit.books(orders=orders, refunds=refunds)
    rows = {r["seller_id"]: r for r in st.statements(books, None)["statements"]}
    # 확정 전 주문만 있으면 줄 돈이 없어 이번 정산에서 빠지고, 그 주문은 다음 정산으로 넘어간다
    assert [h["code"] for h in rows["sel_a"]["hold_reasons"]] == ["no_confirmed_orders"]
    assert rows["sel_a"]["payout"] == 0
    assert rows["sel_a"]["pending_confirmation"] == {"orders": 1, "gross": 20000}
    assert rows["sel_b"]["payout"] == 10000 - 1000 - 9500
    assert [h["code"] for h in rows["sel_b"]["hold_reasons"]] == ["negative_payout"]
    assert all(r["status"] == "held" for r in rows.values())


def test_period_parameter_and_quiet_sellers(kit, fixed_now) -> None:
    books = kit.books(
        orders=[kit.order("ord_1", kit.at("2026-10-20"), [("sel_a", "book", 1, 1000)])]
    )
    assert st.statements(books, None)["statements"] == []  # 지난주 거래 없음
    week = st.statements(books, "2026-W43")
    assert [r["seller_id"] for r in week["statements"]] == ["sel_a"]
    with pytest.raises(ValueError):
        st.statements(books, "2026-43")


def test_apply_batch(fixed_now) -> None:
    result = st.statements(bk.seed_books(), None)
    plain = st.apply_batch(dict(result), None)
    assert plain["batch"] == {
        "status": "none",
        "review_id": None,
        "execution_id": None,
        "total_payout": 0,
        "seller_ids": [],
    }
    batch = {
        "status": "requested",
        "review_id": "rev_1",
        "execution_id": "exe_1",
        "total_payout": 10,
        "seller_ids": ["sel_0001"],
        "attempt": 1,
    }
    shown = st.apply_batch(st.statements(bk.seed_books(), None), batch)
    rows = {r["seller_id"]: r for r in shown["statements"]}
    assert (rows["sel_0001"]["status"], rows["sel_0001"]["review_id"]) == ("requested", "rev_1")
    assert rows["sel_0003"]["status"] == "ready"
    assert "attempt" not in shown["batch"] and shown["batch"]["review_id"] == "rev_1"
    assert [r["seller_id"] for r in st.ready(shown)] == ["sel_0002", "sel_0003"]
    assert date.fromisoformat(shown["pay_date"]).weekday() == 5


def test_statement_lists_the_shop_products_the_seller_sold(fixed_now) -> None:
    """내역서의 상품은 쇼핑몰 상품 번호 그대로이고, 상품 판매액 합이 판매액과 같다."""
    books = bk.seed_books()
    books.products = [{"product_id": "prd_x", "name": "쇼핑몰 이름"}]
    for r in st.statements(books, None)["statements"]:
        assert r["products"], r["seller_id"]
        assert sum(p["gross"] for p in r["products"]) == r["gross"]
        assert [p["gross"] for p in r["products"]] == sorted(
            (p["gross"] for p in r["products"]), reverse=True
        )
        for p in r["products"]:
            assert p["product_id"].startswith("prd_") and p["qty"] > 0 and p["name"]
            sold_by = {
                i["seller_id"]
                for o in books.orders
                for i in o["items"]
                if i.get("product_id") == p["product_id"]
            }
            assert sold_by == {r["seller_id"]}
    # 품목에 이름이 없으면 쇼핑몰 상품 목록 이름, 그것도 없으면 상품 번호
    assert books.product_name("prd_x", {}) == "쇼핑몰 이름"
    assert books.product_name("prd_none", {}) == "prd_none"
    assert books.product_name("prd_x", {"title": "품목 이름"}) == "품목 이름"


def test_an_unconfirmed_order_waits_without_holding_the_seller(kit, fixed_now) -> None:
    """확정 전 주문이 있어도 확정된 주문은 정산하고, 확정 전 주문은 확정되는 주로 넘긴다.

    예전에는 한 건만 확정 전이어도 판매자 정산 전체를 보류해서, 주문이 계속 들어오면
    판매자가 영영 지급받지 못했다(251에서 판매자 세 곳 모두 보류).
    """
    sold = kit.at("2026-10-14")
    orders = [
        kit.order("ord_1", sold, [("sel_a", "book", 1, 20000)]),
        kit.order("ord_2", sold, [("sel_a", "book", 1, 10000)], delivered=False),
    ]
    books = kit.books(orders=orders)
    row = st.statements(books, None)["statements"][0]
    assert (row["status"], row["hold_reasons"]) == ("ready", [])
    assert (row["orders"], row["gross"], row["payout"]) == (1, 20000, 18000)
    assert row["pending_confirmation"] == {"orders": 1, "gross": 10000}
    assert [r["seller_id"] for r in st.ready(st.statements(books, None))] == ["sel_a"]

    # 다음 주에 확정되면 그 주 정산에 들어간다 (판매일이 아니라 구매 확정일 기준)
    orders[1]["delivered_at"] = kit.at("2026-10-21")
    later = st.statements(kit.books(orders=orders), "2026-W43")["statements"][0]
    assert (later["orders"], later["gross"]) == (1, 10000)
