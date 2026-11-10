"""목 데이터만 있던 화면 API의 실제 버전 (사용자·주문·건강 상태·집계·지식·후기).

- 사용자·주문은 고객사 쇼핑몰(shop-api)에서 읽는다. 주소는 `AX_SHOP_API_URL`(없으면 기본 포트 표).
- 집계는 support의 `inquiry_overview`·`list_claims`·`claim_queue`에서 코드로 센다.
- 지식 조각·후기는 아직 실제 출처(API)가 없다. 지어내지 않고 빈 목록을 돌려준다.
"""

from __future__ import annotations

import os
import re
from collections import Counter
from datetime import datetime
from typing import Any

import httpx

from ax_agent_template.config import agent_url
from ax_dashboard_api import support_client
from ax_dashboard_api.envelope import ApiError, or_unavailable
from ax_dashboard_api.schemas import (
    Dashboard,
    DashboardTotals,
    Health,
    Me,
    OrderDetail,
    OrderLine,
    OrderListRow,
    OrderPayment,
    OrderRefund,
    OrderSummary,
)

TIMEOUT_SECONDS = 10.0
TIMEZONE = "Asia/Seoul"

# shop-api 주문 상태 → 화면 표시
STATUS_KO = {
    "pending_payment": "결제 대기",
    "payment_failed": "결제 실패",
    "paid": "결제 완료",
    "cancelled": "취소",
}


def _shop_url() -> str:
    return agent_url("shop-api")


async def _shop_get(
    path: str, params: dict[str, Any] | None = None, *, not_found: str | None = None
) -> Any:
    """`not_found`를 주면 판매 서버의 404를 그 문구의 404로 돌려준다(502로 안 보이게)."""
    data, _ = await _shop_get_page(path, params, not_found=not_found)
    return data


async def _shop_get_page(
    path: str, params: dict[str, Any] | None = None, *, not_found: str | None = None
) -> tuple[Any, dict[str, Any]]:
    """판매 서버 응답의 `(data, meta)`. 페이지를 넘기는 목록(`meta.next_cursor`)에 쓴다."""
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:
            res = await client.get(f"{_shop_url()}{path}", params=params)
    except httpx.HTTPError as error:
        raise ApiError(
            502, "UPSTREAM_UNAVAILABLE", f"shop-api에 연결할 수 없다 ({error})"
        ) from error
    if res.status_code == 404 and not_found:
        raise ApiError(404, "NOT_FOUND", not_found)
    if res.status_code >= 400:
        raise ApiError(502, "UPSTREAM_ERROR", f"shop-api 오류 (HTTP {res.status_code})")
    body = res.json()
    meta = body.get("meta")
    return body.get("data"), meta if isinstance(meta, dict) else {}


# 판매 서버 주문 목록 한 페이지(최대 1000). 더 있으면 meta.next_cursor로 넘긴다
ORDERS_PAGE = 1000


async def _shop_orders(params: dict[str, Any], max_rows: int) -> list[dict[str, Any]]:
    """`GET /api/shop/orders`를 cursor로 끝까지 넘겨 모은다. `max_rows`를 넘으면 거기서 멈춘다.

    멈췄을 때는 `max_rows + 1`건 이상을 돌려주므로 부르는 쪽이 "너무 많다"를 알 수 있다.
    """
    query = {**params, "limit": ORDERS_PAGE}
    found: list[dict[str, Any]] = []
    while True:
        items, meta = await _shop_get_page("/api/shop/orders", query)
        if items is None:
            items = []
        if not isinstance(items, list):
            raise ApiError(502, "UPSTREAM_ERROR", "shop-api가 목록 모양이 아닌 응답을 돌려줬다")
        found.extend(items)
        cursor = meta.get("next_cursor")
        if not cursor or len(found) > max_rows:
            return found
        query = {**query, "cursor": cursor}


def _number(identifier: str) -> int:
    """`buy_0007`·`ord_00012` 같은 ID의 숫자 부분. 숫자가 없으면 0."""
    digits = re.sub(r"\D", "", identifier)
    return int(digits) if digits else 0


def buyer_id(user_id: int) -> str:
    """화면의 숫자 사용자 ID → shop-api 가명 ID (support와 같은 규칙: 7 → buy_0007)."""
    return f"buy_{user_id:04d}"


async def users() -> list[Me]:
    items = await _shop_get("/api/shop/users") or []
    return [
        Me(
            id=_number(u["buyer_id"]),
            name=u.get("display_name") or u["buyer_id"],
            email="",  # shop-api는 이메일을 갖지 않는다(가명 ID만)
            points=int(u.get("points") or 0),
        )
        for u in items
    ]


def _period(value: str | None, name: str) -> str | None:
    """`since`·`until` 검사. 시간대가 없으면 판매 서버가 UTC로 본다."""
    if not value:
        return None
    try:
        datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise ApiError(400, "INVALID_PERIOD", f"{name}은 ISO 8601 시각이어야 한다") from None
    return value


async def orders(
    user_id: int, since: str | None = None, until: str | None = None
) -> list[OrderSummary]:
    """구매자의 주문 목록, 최신순. `since` 이상·`until` 미만(주문 시각)만 읽는다."""
    params: dict[str, Any] = {"buyer_id": buyer_id(user_id)}
    if start := _period(since, "since"):
        params["since"] = start
    if end := _period(until, "until"):
        params["until"] = end
    # 한 구매자의 주문이라 기간이 없어도("전체") 페이지를 넘겨 다 읽는다. 상한은 주문 관리와 같다
    items = (await _shop_orders(params, MAX_ORDERS))[:MAX_ORDERS]
    out: list[OrderSummary] = []
    for o in items:
        lines = o.get("items") or []
        first = lines[0].get("title") if lines else None
        title = (first or "상품") + (f" 외 {len(lines) - 1}건" if len(lines) > 1 else "")
        out.append(
            OrderSummary(
                id=_number(o["order_id"]),
                order_no=o["order_id"],
                status=o["status"],
                status_ko=STATUS_KO.get(o["status"], o["status"]),
                title=title,
                created_at=o.get("created_at"),
                total_amount=o.get("total_amount"),
                refunded_amount=o.get("refunded_amount"),
                item_count=len(lines),
            )
        )
    # 시각이 없는 주문은 맨 뒤로 보낸다
    out.sort(key=lambda x: (x.created_at is not None, x.created_at or "", x.id), reverse=True)
    return out


ORDER_TABS = (
    "pending_payment",
    "paid",
    "partial_refund",
    "full_refund",
    "cancelled",
    "payment_failed",
)


# 기간 안 주문을 한 번에 받아 나누는 상한. 넘으면 기간을 줄여 달라고 한다
MAX_ORDERS = 20_000


def _title(lines: list[dict[str, Any]]) -> str:
    first = lines[0].get("title") if lines else None
    return (first or "상품") + (f" 외 {len(lines) - 1}건" if len(lines) > 1 else "")


def _row(o: dict[str, Any]) -> OrderListRow:
    return OrderListRow(
        id=_number(o["order_id"]),
        order_no=o["order_id"],
        status=o["status"],
        status_ko=STATUS_KO.get(o["status"], o["status"]),
        title=_title(o.get("items") or []),
        created_at=o.get("created_at"),
        total_amount=o.get("total_amount"),
        refunded_amount=o.get("refunded_amount"),
        item_count=len(o.get("items") or []),
        buyer_id=o.get("buyer_id"),
        shipped_at=o.get("shipped_at"),
        delivered_at=o.get("delivered_at"),
    )


def order_tab(o: dict[str, Any]) -> str:
    """주문 관리 탭. 환불이 있으면 상태 대신 환불 합계로 나눈다.

    결제액 이상이면 전체 환불, 그보다 적으면 부분 환불이다. 환불이 없으면 판매 서버 상태 그대로다.
    """
    refunded = o.get("refunded_amount") or 0
    total = o.get("total_amount")
    if refunded > 0 and total is not None:
        return "full_refund" if refunded >= total else "partial_refund"
    return str(o.get("status") or "")


async def order_page(
    *,
    status: str | None,
    since: str | None,
    until: str | None,
    q: str | None,
    buyer: str | None,
    limit: int,
    offset: int,
) -> tuple[list[OrderListRow], int, dict[str, int]]:
    """주문 관리 화면의 한 페이지(최신순)와 전체 건수·탭별 건수. 읽기만 한다.

    판매 서버에 원래 있는 기간 목록(`GET /api/shop/orders`)만 쓴다. 판매 서버는 고치지 않고,
    최신순 정렬·탭 나누기·주문 번호 검색·페이지는 여기서 한다.
    기간 안 주문이 `MAX_ORDERS`를 넘으면 나누지 않고 422로 기간을 줄여 달라고 한다.
    """
    params: dict[str, Any] = {}
    if buyer:
        params["buyer_id"] = buyer
    if start := _period(since, "since"):
        params["since"] = start
    if end := _period(until, "until"):
        params["until"] = end
    # cursor로 넘기다 MAX_ORDERS를 넘으면 거기서 멈춘다(25만 건을 다 읽지 않는다)
    items = await _shop_orders(params, MAX_ORDERS)
    if len(items) > MAX_ORDERS:
        raise ApiError(
            422,
            "PERIOD_TOO_LARGE",
            f"이 기간 주문이 {MAX_ORDERS:,}건을 넘어 한 번에 볼 수 없다. 기간을 줄여 주세요",
        )
    needle = (q or "").strip().lower()
    found = [o for o in items if not needle or needle in str(o.get("order_id", "")).lower()]
    counts: dict[str, int] = {"all": len(found)}
    for o in found:
        tab = order_tab(o)
        if tab in ORDER_TABS:
            counts[tab] = counts.get(tab, 0) + 1
    picked = [o for o in found if not status or order_tab(o) == status]
    # 최신순, 같은 시각이면 주문 번호 역순
    picked.sort(key=lambda o: (o.get("created_at") or "", o.get("order_id") or ""), reverse=True)
    return [_row(o) for o in picked[offset : offset + limit]], len(picked), counts


async def order_detail(order_no: str) -> OrderDetail:
    """판매 서버의 주문 한 건을 화면 모델로 맞춘다. 없는 값은 None이다."""
    o = await _shop_get(f"/api/shop/orders/{order_no}", not_found="주문을 찾을 수 없다")
    if not isinstance(o, dict) or "order_id" not in o:
        raise ApiError(502, "UPSTREAM_ERROR", "shop-api가 주문 모양이 아닌 응답을 돌려줬다")
    status = str(o.get("status") or "")
    return OrderDetail(
        order_no=o["order_id"],
        status=status,
        status_ko=STATUS_KO.get(status, status),
        created_at=o.get("created_at"),
        total_amount=o.get("total_amount"),
        refunded_amount=o.get("refunded_amount"),
        shipping_fee=o.get("shipping_fee"),
        points_used=o.get("points_used"),
        coupon_discount=o.get("coupon_discount"),
        promised_delivery_at=o.get("promised_delivery_at"),
        shipped_at=o.get("shipped_at"),
        delivered_at=o.get("delivered_at"),
        items=[
            OrderLine(
                title=i.get("title"),
                qty=i.get("qty"),
                unit_price=i.get("unit_price"),
                paid_amount=i.get("paid_amount"),
            )
            for i in o.get("items") or []
        ],
        payments=[
            OrderPayment(
                amount=p.get("amount"),
                method_code=p.get("method_code"),
                status=p.get("status"),
                failure_code=p.get("failure_code"),
                created_at=p.get("created_at"),
            )
            for p in o.get("payments") or []
        ],
        refunds=(
            None
            if not isinstance(o.get("refunds"), list)
            else [
                OrderRefund(amount=r.get("amount"), created_at=r.get("created_at"))
                for r in o["refunds"]
            ]
        ),
    )


def health() -> Health:
    model = os.getenv("LLM_MODEL") or "미설정"
    return Health(status="ok", llm_provider=model, kb_chunks=0, demo_tools=False)


async def metrics(days: int) -> Dashboard:
    """support의 문의·클레임 데이터로 센 집계. 일별 추이는 아직 support가 주지 않아 비운다."""
    # 꺼진 서비스(문의·클레임)의 몫은 비우고 meta.unavailable에 "확인할 수 없음"을 남긴다
    overview = await or_unavailable(support_client.call("inquiry_overview"), {})
    claims = (await or_unavailable(support_client.call("list_claims"), {})).get("items", [])
    queue = await or_unavailable(support_client.call("claim_queue"), {})
    kpis = overview.get("kpis", {})
    waiting = overview.get("waiting", [])
    by_status = Counter(str(c.get("status")) for c in claims)
    reasons = Counter(str(c.get("reason")) for c in claims if c.get("reason"))
    queue_items = queue.get("queue") or queue.get("items") or []
    return Dashboard.model_validate(
        {
            "days": days,
            "timezone": TIMEZONE,
            "daily": [],
            "totals": DashboardTotals(
                inquiries=int(kpis.get("conversations", 0)),
                open_waiting=len(waiting),
                high_waiting=sum(1 for w in waiting if w.get("urgency") == "high"),
                claims=len(claims),
                auto_rate=None,
                claims_rejected=by_status.get("rejected", 0),
                needs_review=by_status.get("needs_review", 0),
                awaiting_return=by_status.get("awaiting_return", 0),
                queue_waiting=len(queue_items),
                queue_ready=0,
                paid_cash=0,
                paid_points=0,
                agent_reviews=int(kpis.get("agent_replies", 0)),
                agent_overrides=int(kpis.get("edited", 0)),
                flagged_reviews=0,
            ).model_dump(),
            "by_category": kpis.get("by_category", {}),
            "claim_reasons": dict(reasons),
            "guard_findings": kpis.get("guard_findings", {}),
            "queue": [],
            "inquiry_queue": waiting,
        }
    )
