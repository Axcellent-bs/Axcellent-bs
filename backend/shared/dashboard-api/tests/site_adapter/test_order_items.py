"""주문 한 건에 상품(강의)이 여러 개인 사이트: 짝의 `items`로 주문 줄을 모두 옮긴다.

스터디온에듀가 이렇다.
"""

from typing import Any

from ax_dashboard_api.site_adapter import propose, resources
from ax_dashboard_api.site_adapter.mapping import ResourceMap, pick

ORDER = {
    "code": "SO1001",
    "status": "partially_refunded",
    "member": {"id": "m_7", "name": "가*"},
    "payable": 90000,
    "refunded_amount": 30000,
    "created_at": "2026-11-18T01:00:00Z",
    "updated_at": "2026-11-18T02:00:00Z",
    "items": [
        {
            "kind": "course",
            "course_code": "C-PY",
            "bundle_code": None,
            "unit_price": 30000,
            "paid_amount": 30000,
        },
        {
            "kind": "bundle",
            "course_code": None,
            "bundle_code": "B-CERT",
            "unit_price": 60000,
            "paid_amount": 60000,
        },
    ],
}

MAPPING = ResourceMap.model_validate(
    {
        "path": "/api/v1/orders",
        "fields": {
            "order_id": "code",
            "status": "status",
            "created_at": "created_at",
            "updated_at": "updated_at",
            "buyer_id": "member.id",
            "total_amount": "payable",
            "refunded_amount": "refunded_amount",
        },
        "values": {"status": {"partially_refunded": "paid"}},
        "items": {
            "path": "items",
            "fields": {
                "product_id": "first:course_code,bundle_code",
                "qty": "const:1",
                "unit_price": "unit_price",
                "paid_amount": "paid_amount",
            },
        },
    }
)


def test_every_order_line_is_kept() -> None:
    order = resources.to_order(ORDER, MAPPING)
    assert [i["product_id"] for i in order["items"]] == ["C-PY", "B-CERT"]
    assert [i["paid_amount"] for i in order["items"]] == [30000, 60000]
    assert all(i["qty"] == 1 for i in order["items"])
    assert order["total_amount"] == 90000 and order["refunded_amount"] == 30000
    assert order["status"] == "paid" and order["buyer_id"] == "m_7"
    # 받은 금액은 줄 합계(사이트에 주문 전체 결제액 칸이 없을 때)
    assert order["payments"][0]["amount"] == 90000


def test_first_picks_the_first_value_that_is_there() -> None:
    assert pick({"a": None, "b": "x"}, "first:a,b") == "x"
    assert pick({"a": "y", "b": "x"}, "first:a,b") == "y"
    assert pick({"a": None}, "first:a,b") is None


def test_old_single_item_mappings_still_work(nubi: Any) -> None:
    raw = nubi("nubi_mapping.json")["resources"]["orders"]
    row = nubi("nubi_bookings_p1.json")["data"][0]
    order = resources.to_order(row, ResourceMap.model_validate(raw))
    assert len(order["items"]) == 1


def test_propose_finds_the_order_lines() -> None:
    found = propose.order_items([ORDER])
    assert found is not None
    assert found["path"] == "items"
    assert found["fields"]["product_id"] == "first:course_code,bundle_code"
    assert found["fields"]["qty"] == "const:1"
    assert found["fields"]["paid_amount"] == "paid_amount"


def test_partially_refunded_is_still_paid() -> None:
    assert propose.statuses(["partially_refunded", "refunded"]) == {
        "partially_refunded": "paid",
        "refunded": "cancelled",
    }


def test_setup_proposes_order_lines_and_accepts_them() -> None:
    from ax_dashboard_api.site_adapter import setup
    from ax_dashboard_api.site_adapter.mapping import SiteMapping

    proposed = setup._proposal("orders", "/api/v1/orders", [ORDER])
    assert proposed["items"]["path"] == "items"
    assert "product_id" not in proposed["fields"] and "qty" not in proposed["fields"]
    assert proposed["values"]["status"]["partially_refunded"] == "paid"
    products = {
        "path": "/api/v1/courses",
        "fields": {
            "product_id": "code",
            "name": "title",
            "price": "price",
            "category_code": "category",
            "active": "const:true",
        },
    }
    m = SiteMapping.model_validate(
        {"version": 1, "resources": {"orders": proposed, "products": products}}
    )
    setup._complete(m)  # 빈 칸 없음
