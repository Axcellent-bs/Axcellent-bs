"""목 데이터만 있던 화면 API의 실제 버전 (사용자·주문·건강 상태·집계·지식·후기).

- 사용자·주문은 고객사 쇼핑몰(shop-api)에서 읽는다. 주소는 `AX_SHOP_API_URL`(없으면 기본 포트 표).
- 집계는 support의 `inquiry_overview`·`list_claims`·`claim_queue`에서 코드로 센다.
- 지식 조각·후기는 아직 실제 출처(API)가 없다. 지어내지 않고 빈 목록을 돌려준다.
"""

from __future__ import annotations

import os
import re
from collections import Counter
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


async def _shop_get(path: str, params: dict[str, Any] | None = None) -> Any:
    try:
        async with httpx.AsyncClient(timeout=TIMEOUT_SECONDS) as client:
            res = await client.get(f"{_shop_url()}{path}", params=params)
    except httpx.HTTPError as error:
        raise ApiError(
            502, "UPSTREAM_UNAVAILABLE", f"shop-api에 연결할 수 없다 ({error})"
        ) from error
    if res.status_code >= 400:
        raise ApiError(502, "UPSTREAM_ERROR", f"shop-api 오류 (HTTP {res.status_code})")
    return res.json().get("data")


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


async def orders(user_id: int) -> list[OrderSummary]:
    items = await _shop_get("/api/shop/orders", {"buyer_id": buyer_id(user_id)}) or []
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
            )
        )
    return out


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
