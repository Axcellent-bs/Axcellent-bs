"""원본 행 → shop-api 모양. 모두 순수 함수다(고객사 호출은 `upstream`).

주문 한 건은 상품 한 줄로 본다(누비투어 예약 = 투어 하나).
결제는 받은 금액이 있으면 한 건으로 만든다.
숫자는 고객사 값을 옮기거나 그 값으로만 계산한다(`unit_price = 총액 // 수량`).
"""

from __future__ import annotations

from typing import Any

from ax_dashboard_api.site_adapter.mapping import MissingField, ResourceMap, pick


class MappingBroken(Exception):
    """짝의 필수 항목이 응답에 없다."""

    def __init__(self, resource: str, field: str) -> None:
        super().__init__(f"{resource} 짝이 응답과 맞지 않아요: {field} 항목이 없어요")
        self.resource = resource
        self.field = field


def _get(row: dict[str, Any], m: ResourceMap, name: str, resource: str) -> Any:
    expr = m.fields.get(name)
    if expr is None:
        raise MappingBroken(resource, name)
    try:
        value = pick(row, expr)
    except MissingField as exc:
        raise MappingBroken(resource, exc.field) from exc
    table = m.values.get(name)
    if table is not None and isinstance(value, str):
        return table.get(value, value)
    return value


def _int(value: Any) -> int:
    return int(value or 0)


def to_order(row: dict[str, Any], m: ResourceMap) -> dict[str, Any]:
    get = lambda name: _get(row, m, name, "orders")  # noqa: E731
    qty = _int(get("qty"))
    total = _int(get("total_amount"))
    paid = _int(get("paid_amount"))
    created_at = get("created_at")
    updated_at = get("updated_at") if "updated_at" in m.fields else created_at
    return {
        "order_id": str(get("order_id")),
        "status": get("status"),
        "created_at": created_at,
        "updated_at": updated_at,
        "buyer_id": get("buyer_id"),
        "total_amount": total,
        "refunded_amount": 0,
        "items": [
            {
                "product_id": get("product_id"),
                "qty": qty,
                "unit_price": total // qty if qty else 0,
                "paid_amount": paid,
            }
        ],
        "payments": (
            [{"amount": paid, "status": "succeeded", "created_at": created_at}] if paid > 0 else []
        ),
    }


def to_product(row: dict[str, Any], m: ResourceMap) -> dict[str, Any]:
    get = lambda name: _get(row, m, name, "products")  # noqa: E731
    price = get("price")
    return {
        "product_id": str(get("product_id")),
        "name": get("name"),
        "price": None if price is None else int(price),
        "category_code": get("category_code"),
        "active": bool(get("active")),
    }


def categories_from(products: list[dict[str, Any]]) -> list[dict[str, Any]]:
    codes = sorted({p["category_code"] for p in products if p.get("category_code")})
    return [{"category_code": c, "name": c} for c in codes]


def users_from(orders: list[dict[str, Any]]) -> list[dict[str, Any]]:
    ids = sorted({o["buyer_id"] for o in orders if o.get("buyer_id")})
    return [{"buyer_id": b, "display_name": None, "points": 0} for b in ids]


def sales_summary(orders: list[dict[str, Any]], group_by: str) -> list[dict[str, Any]]:
    """상품별 판매 수량·금액 합. 결제된 주문(status paid + 성공한 결제)만 센다.

    고객지원 `_is_paid`와 같은 기준이다.

    `group_by`는 지금 `product`만.
    """
    if group_by != "product":
        raise ValueError("group_by는 product만 받아요")
    rows: dict[str, dict[str, Any]] = {}
    for order in orders:
        paid = any(p["status"] == "succeeded" for p in order["payments"])
        if order["status"] != "paid" or not paid:
            continue
        for item in order["items"]:
            pid = item["product_id"]
            row = rows.setdefault(pid, {"product_id": pid, "gross_qty": 0, "gross_amount": 0})
            row["gross_qty"] += item["qty"]
            row["gross_amount"] += order["total_amount"]
    return [rows[k] for k in sorted(rows)]


def _buyer(order: dict[str, Any]) -> str:
    """판매 내보내기는 buyer_id가 필수다. 비회원 주문은 주문 번호로 만든 이름표를 쓴다."""
    return order["buyer_id"] or f"guest-{order['order_id']}"


def to_sales_row(order: dict[str, Any]) -> dict[str, Any]:
    """판매 기록 내보내기 한 줄 (`ax_contracts.data_access.shop_sales.SalesOrderRow`)."""
    paid = any(p["status"] == "succeeded" for p in order["payments"])
    return {
        "order_id": order["order_id"],
        "buyer_id": _buyer(order),
        "status": order["status"],
        "created_at": order["created_at"],
        "updated_at": order["updated_at"],
        "items_amount": order["total_amount"],
        "payment_status": "succeeded" if paid else None,
        "lines": [
            {"product_id": i["product_id"], "qty": i["qty"], "unit_price": i["unit_price"]}
            for i in order["items"]
        ],
    }


def customers_from(orders: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """고객 내보내기 (`CustomerRow`). 가입 시각이 없는 사이트라 첫 주문 시각을 joined_at으로."""
    seen: dict[str, dict[str, Any]] = {}
    for o in orders:
        bid = o.get("buyer_id")
        if not bid:
            continue
        row = seen.setdefault(
            bid, {"buyer_id": bid, "joined_at": o["created_at"], "updated_at": o["updated_at"]}
        )
        row["joined_at"] = min(row["joined_at"], o["created_at"])
        row["updated_at"] = max(row["updated_at"], o["updated_at"])
    return sorted(seen.values(), key=lambda c: (c["updated_at"], c["buyer_id"]))
