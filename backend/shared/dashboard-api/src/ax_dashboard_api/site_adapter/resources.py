"""원본 행 → shop-api 모양. 모두 순수 함수다(고객사 호출은 `upstream`).

주문 한 건은 상품 한 줄로 본다(누비투어 예약 = 투어 하나).
결제는 받은 금액이 있으면 한 건으로 만든다.
판매 기록(`sales_row`)의 환불 줄·취소 주체·결제 실패는 짝에 그 칸이 있을 때만 채운다.
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


def _lines(row: dict[str, Any], m: ResourceMap) -> list[dict[str, Any]]:
    """주문 줄 목록 짝(`m.items`)으로 줄마다 상품·수량·단가·결제액을 옮긴다."""
    assert m.items is not None
    try:
        raw = pick(row, m.items.path)
    except MissingField as exc:
        raise MappingBroken("orders", exc.field) from exc
    if not isinstance(raw, list):
        raise MappingBroken("orders", m.items.path)
    out = []
    for line in raw:
        if not isinstance(line, dict):
            continue

        def get(name: str, default: Any = None, line: dict[str, Any] = line) -> Any:
            expr = m.items.fields.get(name) if m.items else None  # type: ignore[union-attr]
            if expr is None:
                if default is None:
                    raise MappingBroken("orders", f"{m.items.path}.{name}")  # type: ignore[union-attr]
                return default
            try:
                return pick(line, expr)
            except MissingField as exc:
                raise MappingBroken("orders", exc.field) from exc

        qty = _int(get("qty", 1)) or 1
        paid = _int(get("paid_amount"))
        unit = _int(get("unit_price", paid // qty if qty else 0))
        out.append(
            {"product_id": get("product_id"), "qty": qty, "unit_price": unit, "paid_amount": paid}
        )
    return out


def to_order(row: dict[str, Any], m: ResourceMap) -> dict[str, Any]:
    get = lambda name: _get(row, m, name, "orders")  # noqa: E731
    if m.items is not None:
        return _to_order_lines(row, m)
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
        "refunded_amount": _int(get("refunded_amount")) if "refunded_amount" in m.fields else 0,
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


def _to_order_lines(row: dict[str, Any], m: ResourceMap) -> dict[str, Any]:
    """주문 줄이 여러 개인 주문.

    받은 금액은 주문 칸(`paid_amount`)이 있으면 그것, 없으면 줄 합계.
    """
    get = lambda name: _get(row, m, name, "orders")  # noqa: E731
    lines = _lines(row, m)
    created_at = get("created_at")
    paid = (
        _int(get("paid_amount"))
        if "paid_amount" in m.fields
        else sum(line["paid_amount"] for line in lines)
    )
    return {
        "order_id": str(get("order_id")),
        "status": get("status"),
        "created_at": created_at,
        "updated_at": get("updated_at") if "updated_at" in m.fields else created_at,
        "buyer_id": get("buyer_id"),
        "total_amount": _int(get("total_amount")),
        "refunded_amount": _int(get("refunded_amount")) if "refunded_amount" in m.fields else 0,
        "items": lines,
        "payments": (
            [{"amount": paid, "status": "succeeded", "created_at": created_at}] if paid > 0 else []
        ),
    }


def order_detail(order: dict[str, Any]) -> dict[str, Any]:
    """주문 하나 읽기(`GET /orders/{id}`)에 고객지원 클레임 판정이 읽는 칸을 더한다 (W5).

    고객지원 `shop_client.to_order`는 결제의 `payment_id`, 줄의 `order_item_id`·`title`, 구매자를
    읽는다. 사이트에 없는 값은 지어내지 않고 주문의 값으로 만든 번호만 붙인다.

    - `payment_id` = 주문 번호: 사이트 환불은 주문(예약) 단위라 결제 하나를 가리킬 필요가 없다.
      환불 쓰기 짝은 이 번호로 신청을 찾는다(`link:payments.payment_id`, `claims.number`)
    - `order_item_id` = `<주문 번호>-<줄 순번>`, `title` = 상품 번호(사이트 예약에 상품 이름이 없다)
    - 구매자가 비면(회원 아님) `guest-<주문 번호>` (판매 기록 내보내기·신청 받은함과 같은 이름표)
    """
    order_id = order["order_id"]
    return {
        **order,
        "buyer_id": _buyer(order),
        "items": [
            {
                **item,
                "order_item_id": f"{order_id}-{n}",
                "title": str(item.get("product_id") or order_id),
            }
            for n, item in enumerate(order["items"], start=1)
        ],
        "payments": [{**p, "payment_id": order_id} for p in order["payments"]],
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


CANCEL_REASONS = frozenset({"buyer", "seller", "system"})


def _optional(row: dict[str, Any], m: ResourceMap, name: str) -> Any:
    """짝에 칸이 있으면 그 값, 없으면 None (사이트에 없는 값은 지어내지 않는다)."""
    return _get(row, m, name, "orders") if name in m.fields else None


def sales_row(row: dict[str, Any], m: ResourceMap) -> dict[str, Any]:
    """원본 주문 → 판매 기록 한 줄. 주문 모양에 없는 칸(취소 주체·환불 시각)을 짝에서 더 읽는다."""
    order = to_order(row, m)
    reason = _optional(row, m, "cancel_reason_code")
    return to_sales_row(
        order,
        cancel_reason_code=reason if order["status"] == "cancelled" else None,
        refunded_at=_optional(row, m, "refunded_at"),
    )


def to_sales_row(
    order: dict[str, Any],
    *,
    cancel_reason_code: Any = None,
    refunded_at: Any = None,
) -> dict[str, Any]:
    """판매 기록 내보내기 한 줄 (`ax_contracts.data_access.shop_sales.SalesOrderRow`).

    - 결제 상태: 받은 금액이 있으면 `succeeded`, 상태 값 표가 `payment_failed`로 옮긴
      주문이면 `failed`, 그 밖은 None
    - 환불: 사이트의 환불 누적 금액(`refunded_amount`)이 있으면 한 줄. 사이트에 환불 id가 없어
      `refund_id`는 비운다(감지기가 누적 금액으로 만든다). 시각은 짝의 `refunded_at`, 없으면
      주문이 마지막으로 바뀐 시각(고객사 값)
    - 취소 주체: `buyer`·`seller`·`system` 중 하나일 때만(값 표로 옮긴 값). 그 밖은 None
    """
    paid = any(p["status"] == "succeeded" for p in order["payments"])
    failed = order["status"] == "payment_failed"
    refunded = order.get("refunded_amount") or 0
    reason = cancel_reason_code if cancel_reason_code in CANCEL_REASONS else None
    return {
        "order_id": order["order_id"],
        "buyer_id": _buyer(order),
        "status": order["status"],
        "created_at": order["created_at"],
        "updated_at": order["updated_at"],
        "items_amount": order["total_amount"],
        "payment_status": "succeeded" if paid else ("failed" if failed else None),
        "lines": [
            {"product_id": i["product_id"], "qty": i["qty"], "unit_price": i["unit_price"]}
            for i in order["items"]
        ],
        "refunds": (
            [
                {
                    "refund_id": None,
                    "amount": refunded,
                    "refunded_at": refunded_at or order["updated_at"],
                }
            ]
            if refunded > 0
            else []
        ),
        "cancel_reason_code": reason,
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
