"""shop-api 연결: 쿠폰 발급 (REST).

- 발급 `POST /api/shop/coupons`는 `marketing` 토큰만 받는다(`Authorization: Bearer`).
  `idempotency_key`가 필수이고 같은 키·같은 내용의 재요청은 이전 쿠폰을 돌려주므로,
  실패한 건을 다시 불러도 쿠폰이 두 번 생기지 않는다.
- 주소는 `AX_SHOP_API_URL`(없으면 `http://shop-api:8010`, 템플릿 `agent_url`)이다.
- 발급 출처는 `MARKETING_COUPON_SOURCE`(`memory` | `shop-api`)로 정한다. 비우면 목 모드는
  메모리에만 기록하고, 그 밖에는 shop-api다. 쿠폰 번호도 shop-api가 정하므로 재시작해도
  번호가 겹치지 않는다.
- `execute("issue_coupon")`이 `executed`를 돌려준 뒤에만 부른다.
"""

from __future__ import annotations

import os
from typing import Any

import httpx

from ax_agent_template import config

TIMEOUT_SECONDS = 10.0
_client: httpx.AsyncClient | None = None  # 테스트가 `use_client`로 바꿔 끼운다


class ShopError(Exception):
    """shop-api가 거절했거나 응답이 없다. `code`는 shop-api 오류 코드(연결 실패는 UNAVAILABLE)."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def use_client(client: httpx.AsyncClient | None) -> None:
    """HTTP 클라이언트를 바꿔 끼운다 (테스트용). None이면 호출마다 새로 만든다."""
    global _client
    _client = client


def coupon_source() -> str:
    """`memory` 또는 `shop-api`."""
    explicit = os.getenv("MARKETING_COUPON_SOURCE", "").strip().lower()
    if explicit in ("memory", "shop-api"):
        return explicit
    if _client is not None:
        return "shop-api"
    return "memory" if config.is_mock("marketing") else "shop-api"


def _error(response: httpx.Response) -> ShopError:
    try:
        error = response.json()["error"]
        return ShopError(str(error["code"]), str(error.get("message", "")))
    except (ValueError, KeyError, TypeError):
        return ShopError("UNAVAILABLE", f"shop-api 응답 오류 ({response.status_code})")


async def issue_coupon(
    *,
    idempotency_key: str,
    purpose_code: str,
    buyer_id: str,
    count: int,
    unit_value: int,
    campaign_id: str | None,
    context_id: str | None,
) -> dict[str, Any]:
    """쿠폰을 발급하고 shop-api가 돌려준 쿠폰(`coupon_id` 등)을 돌려준다."""
    body: dict[str, Any] = {
        "idempotency_key": idempotency_key,
        "purpose_code": purpose_code,
        "buyer_id": buyer_id,
        "count": count,
        "unit_value": unit_value,
        "campaign_id": campaign_id,
        "context_id": context_id,
    }
    url = f"{config.agent_url('shop-api')}/api/shop/coupons"
    headers = {"Authorization": f"Bearer {config.agent_token('marketing')}"}
    client = _client or httpx.AsyncClient(timeout=TIMEOUT_SECONDS)
    try:
        response = await client.post(url, headers=headers, json=body)
    except httpx.HTTPError as exc:
        raise ShopError("UNAVAILABLE", f"shop-api에 연결할 수 없다 ({type(exc).__name__})") from exc
    finally:
        if _client is None:
            await client.aclose()
    if response.status_code not in (200, 201):
        raise _error(response)
    try:
        coupon = response.json()["data"]
        coupon["coupon_id"]
    except (ValueError, KeyError, TypeError):
        raise ShopError("UNAVAILABLE", "shop-api 응답에 쿠폰이 없다") from None
    return coupon
