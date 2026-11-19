"""짝 제안: 고객사 응답 첫 줄들의 칸 이름을 보고 shop-api 칸 짝을 고른다.

규칙(동의어 목록·상태 낱말)만 쓴다. dashboard-api는 LLM을 부르지 않는다.
못 찾은 필수 칸은 빈 글자로 두고 사람이 화면에서 채운다. 숫자·값은 만들지 않는다.
"""

from __future__ import annotations

from typing import Any

from ax_dashboard_api.site_adapter.mapping import Resource

# 칸마다 찾아볼 이름(앞이 먼저). 비교는 소문자·`_`·`-`·`.`를 뺀 모양으로 한다
SYNONYMS: dict[Resource, dict[str, tuple[str, ...]]] = {
    "orders": {
        "order_id": ("order_id", "order_no", "booking_id", "reservation_id", "code", "id"),
        "status": ("status", "order_status", "state"),
        "created_at": ("created_at", "ordered_at", "booked_at", "order_date", "created", "date"),
        "buyer_id": (
            "buyer_id",
            "customer_id",
            "member_id",
            "user_id",
            "customer.id",
            "member.id",
            "user.id",
        ),
        "product_id": ("product_id", "product_code", "tour_code", "goods_id", "item_id", "sku"),
        "qty": ("qty", "quantity", "count", "headcount", "people", "persons"),
        "total_amount": ("total_amount", "total_price", "payable", "total", "amount"),
        "paid_amount": ("paid_amount", "amount_paid", "paid", "payment_amount"),
    },
    "products": {
        "product_id": ("product_id", "product_code", "code", "sku", "goods_id", "id"),
        "name": ("name", "title", "product_name"),
        "price": ("price", "base_price", "sale_price", "unit_price"),
        "category_code": ("category_code", "category", "category_id", "region", "type"),
        "active": ("active", "is_active", "on_sale", "enabled", "visible", "published"),
    },
}
# 찾았을 때만 넣는 칸
OPTIONAL: dict[Resource, dict[str, tuple[str, ...]]] = {
    "orders": {
        "updated_at": ("updated_at", "modified_at", "updated"),
        "refunded_amount": ("refunded_amount", "refund_amount", "refunded"),
    },
    "products": {},
}
# 인원 칸이 따로 있으면 더한다(누비투어 예약: 성인 + 아동)
HEAD_PARTS = ("adults", "children")

# 상태 낱말 → shop-api 주문 상태. 위에서부터 처음 맞는 것
STATUS_RULES: tuple[tuple[tuple[str, ...], str], ...] = (
    (("cancel_request", "cancelrequest", "cancel_requested"), "paid"),  # 취소 신청은 아직 결제 상태
    (("partial",), "paid"),  # 부분 환불은 아직 결제 상태(남은 금액은 결제됨)
    (("cancel", "expire", "refund", "void", "fail"), "cancelled"),
    # 부정 낱말(un·not·in-)은 결제 낱말보다 먼저 본다(unconfirmed·not_paid·incomplete)
    (("pending", "wait", "await", "ready", "un", "not", "incomplete"), "pending_payment"),
    (("paid", "complete", "confirm", "done", "settle", "success"), "paid"),
)


def _norm(name: str) -> str:
    return "".join(c for c in name.lower() if c not in "_-.")


def _paths(rows: list[dict[str, Any]]) -> dict[str, str]:
    """줄들에 있는 칸 경로(한 단계 안쪽까지). 비교용 이름 → 실제 경로."""
    found: dict[str, str] = {}
    for row in rows:
        for key, value in row.items():
            found.setdefault(_norm(key), key)
            if isinstance(value, dict):
                for inner in value:
                    path = f"{key}.{inner}"
                    found.setdefault(_norm(path), path)
    return found


def _first(paths: dict[str, str], names: tuple[str, ...]) -> str:
    for name in names:
        hit = paths.get(_norm(name))
        if hit:
            return hit
    return ""


def fields(resource: Resource, rows: list[dict[str, Any]]) -> dict[str, str]:
    """shop-api 칸 → 고객사 짝 식. 못 찾은 필수 칸은 ""."""
    paths = _paths(rows)
    out = {name: _first(paths, names) for name, names in SYNONYMS[resource].items()}
    if resource == "orders" and not out["qty"]:
        parts = [paths[p] for p in HEAD_PARTS if p in paths]
        if len(parts) == len(HEAD_PARTS):
            out["qty"] = "sum:" + ",".join(parts)
    for name, names in OPTIONAL[resource].items():
        hit = _first(paths, names)
        if hit:
            out[name] = hit
    return out


def status_values(rows: list[dict[str, Any]], path: str) -> list[str]:
    """줄들에 나온 상태 값(글자만, 처음 나온 순서)."""
    seen: list[str] = []
    for row in rows:
        value: Any = row
        for key in path.split("."):
            value = value.get(key) if isinstance(value, dict) else None
        if isinstance(value, str) and value not in seen:
            seen.append(value)
    return seen


def statuses(values: list[str]) -> dict[str, str]:
    """상태 값 → shop-api 주문 상태(`pending_payment`·`paid`·`cancelled`). 모르면 ""."""
    out: dict[str, str] = {}
    for value in values:
        low = value.lower()
        out[value] = next((to for words, to in STATUS_RULES if any(w in low for w in words)), "")
    return out


# 주문 줄(목록 칸) 안에서 찾을 이름
LINE_SYNONYMS: dict[str, tuple[str, ...]] = {
    "product_id": (
        "product_id",
        "product_code",
        "course_code",
        "bundle_code",
        "item_code",
        "goods_id",
        "sku",
        "item_id",
    ),
    "qty": ("qty", "quantity", "count"),
    "unit_price": ("unit_price", "price", "sale_price"),
    "paid_amount": ("paid_amount", "amount_paid", "paid", "amount"),
}


def order_items(rows: list[dict[str, Any]]) -> dict[str, Any] | None:
    """주문 안에 줄 목록(객체 목록) 칸이 있으면 줄 짝을 제안한다.

    없으면 None(주문 한 건 = 상품 하나).
    """
    for row in rows:
        for key, value in row.items():
            lines = [v for v in value if isinstance(v, dict)] if isinstance(value, list) else []
            if not lines:
                continue
            paths = _paths(lines)
            ids = [paths[_norm(n)] for n in LINE_SYNONYMS["product_id"] if _norm(n) in paths]
            if not ids:
                continue
            product = ids[0] if len(ids) == 1 else "first:" + ",".join(ids[:2])
            fields = {
                "product_id": product,
                "qty": _first(paths, LINE_SYNONYMS["qty"]) or "const:1",
                "unit_price": _first(paths, LINE_SYNONYMS["unit_price"]),
                "paid_amount": _first(paths, LINE_SYNONYMS["paid_amount"]),
            }
            return {"path": key, "fields": {k: v for k, v in fields.items() if v}}
    return None
