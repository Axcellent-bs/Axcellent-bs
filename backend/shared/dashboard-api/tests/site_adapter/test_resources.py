"""자원 변환: 누비투어 예약·투어 → shop-api 주문·상품·분류·고객·판매 합계."""

from pathlib import Path
from typing import Any

import pytest

from ax_dashboard_api.site_adapter import mapping as m
from ax_dashboard_api.site_adapter import resources as r


@pytest.fixture
def maps(adapter_dir: Path) -> m.SiteMapping:
    found = m.load_mapping()
    assert found is not None
    return found


@pytest.fixture
def bookings(nubi: Any) -> list[dict]:
    return nubi("nubi_bookings_p1.json")["data"] + nubi("nubi_bookings_p2.json")["data"]


@pytest.fixture
def tours(nubi: Any) -> list[dict]:
    return nubi("nubi_tours.json")["data"]


def test_order_from_nubi_booking(
    maps: m.SiteMapping, bookings: list[dict], tours: list[dict]
) -> None:
    order = r.to_order(bookings[0], maps.resources["orders"])
    assert order["order_id"] == "NB-20261110-0001"
    assert order["status"] == "paid"
    assert order["buyer_id"] == "usr_01"
    assert order["total_amount"] == 2500000
    assert order["refunded_amount"] == 0
    assert order["items"] == [
        {"product_id": "JP-OSA-4D", "qty": 3, "unit_price": 833333, "paid_amount": 900000}
    ]
    assert order["payments"] == [
        {"amount": 900000, "status": "succeeded", "created_at": "2026-11-10T01:00:00.000Z"}
    ]


def test_pending_booking_has_no_payment(
    maps: m.SiteMapping, bookings: list[dict], tours: list[dict]
) -> None:
    order = r.to_order(bookings[2], maps.resources["orders"])
    assert order["status"] == "pending_payment"
    assert order["payments"] == []


def test_unknown_status_kept(maps: m.SiteMapping, bookings: list[dict], tours: list[dict]) -> None:
    row = dict(bookings[0], status="on_hold")
    assert r.to_order(row, maps.resources["orders"])["status"] == "on_hold"


def test_zero_qty_does_not_divide(
    maps: m.SiteMapping, bookings: list[dict], tours: list[dict]
) -> None:
    row = dict(bookings[0], adults=0, children=0)
    item = r.to_order(row, maps.resources["orders"])["items"][0]
    assert item["qty"] == 0 and item["unit_price"] == 0


def test_missing_code_is_mapping_broken(
    maps: m.SiteMapping, bookings: list[dict], tours: list[dict]
) -> None:
    row = {k: v for k, v in bookings[0].items() if k != "code"}
    with pytest.raises(r.MappingBroken) as err:
        r.to_order(row, maps.resources["orders"])
    assert "orders" in str(err.value) and "code" in str(err.value)


def test_product_from_tour(maps: m.SiteMapping, bookings: list[dict], tours: list[dict]) -> None:
    assert r.to_product(tours[1], maps.resources["products"]) == {
        "product_id": "VN-DAD-5D",
        "name": "다낭 3박 5일",
        "price": 749000,
        "category_code": "SEA",
        "active": False,
    }


def test_categories_unique(maps: m.SiteMapping, bookings: list[dict], tours: list[dict]) -> None:
    products = [r.to_product(t, maps.resources["products"]) for t in tours + tours]
    assert r.categories_from(products) == [
        {"category_code": "JP", "name": "JP"},
        {"category_code": "SEA", "name": "SEA"},
    ]


def test_users_skip_guest_orders(
    maps: m.SiteMapping, bookings: list[dict], tours: list[dict]
) -> None:
    orders = [r.to_order(b, maps.resources["orders"]) for b in bookings]
    assert r.users_from(orders) == [
        {"buyer_id": "usr_01", "display_name": None, "points": 0},
        {"buyer_id": "usr_02", "display_name": None, "points": 0},
    ]


def test_sales_summary_skips_cancelled(
    maps: m.SiteMapping, bookings: list[dict], tours: list[dict]
) -> None:
    orders = [r.to_order(b, maps.resources["orders"]) for b in bookings]
    assert r.sales_summary(orders, "product") == [
        {"product_id": "JP-OSA-4D", "gross_qty": 5, "gross_amount": 4388000},
    ]
