"""shop-api 연결: 클레임 판정에 쓰는 주문 조회와 현금 환불 (REST).

- 모든 요청에 이 서비스의 연결 키(옛 설치는 `support` 토큰)를 붙인다(`Authorization: Bearer`).
  주문 조회 `GET /api/shop/orders/{id}`도 붙이므로 shop-api가 `SHOP_ORDERS_PUBLIC_READ=off`여도
  읽힌다. 환불 `POST /api/shop/payments/{id}/refund`는 고객지원 쪽 키·토큰만 받는다.
- 주소는 `AX_SHOP_API_URL`(없으면 `http://shop-api:8010`, 템플릿 `agent_url`)이다.
- 주문 출처는 `SUPPORT_ORDER_SOURCE`(`seed` | `shop-api`)로 정한다. 비우면 목 모드는 시드 주문,
  그 밖에는 shop-api다. 목 모드에서 실제 shop-api와 맞춰 볼 때만 `shop-api`로 둔다.
- 환불은 `execute("refund_request")`가 `executed`를 돌려준 뒤에만 부른다. 같은 `idempotency_key`의
  재요청은 shop-api가 이전 환불을 돌려주므로 다시 불러도 중복 지급되지 않는다.
"""

from __future__ import annotations

import logging
import os
import time
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx

from ax_agent_template import company_connection, config, pinned_http
from support_agent import orders
from support_agent.orders import Order, OrderItem

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


def order_source() -> str:
    """`seed` 또는 `shop-api`."""
    explicit = os.getenv("SUPPORT_ORDER_SOURCE", "").strip().lower()
    if explicit in ("seed", "shop-api"):
        return explicit
    if _client is not None:
        return "shop-api"
    return "seed" if config.is_mock("support") else "shop-api"


def _url(path: str) -> str:
    return f"{config.agent_url('shop-api')}/api/shop{path}"


ENV_KEYS = (
    "AX_SHOP_CLIENT_KEYS"  # "inquiry:<키>,claims:<키>,reviews:<키>" 서비스 이름 : 판매 서버 연결 키
)
ENV_SERVICE = "AX_SERVICE_NAME"  # 서비스 이름(없으면 서비스 카드 폴더 이름)
logger = logging.getLogger("ax.support.shop_client")


def client_keys() -> dict[str, str]:
    """AX_SHOP_CLIENT_KEYS를 서비스 이름별 키로 읽는다. 형식이 틀린 항목은 건너뛰고 경고만 남긴다.

    키 값은 로그·오류에 싣지 않는다. 판매 서버(고객사)가 발급하고 설치마다 .env에만 둔다.
    """
    keys: dict[str, str] = {}
    for part in os.getenv(ENV_KEYS, "").split(","):
        part = part.strip()
        if not part:
            continue
        name, sep, key = part.partition(":")
        if not (sep and name.strip() and key.strip()):
            logger.warning("%s의 항목 하나가 서비스:키 형식이 아니라 건너뛴다", ENV_KEYS)
            continue
        keys[name.strip()] = key.strip()
    return keys


def service_name() -> str:
    """이 서버의 서비스 이름(inquiry·claims·reviews…). 연결 키를 고르는 데 쓴다."""
    explicit = os.getenv(ENV_SERVICE, "").strip()
    if explicit:
        return explicit
    card = os.getenv("SUPPORT_AGENT_CARD", "").strip()
    return Path(card).parent.name if card else "support"


def _headers() -> dict[str, str]:
    """이 서비스의 연결 키를 보낸다. 키 목록이 없을 때만(옛 설치) 내부 토큰을 쓴다.

    키 목록은 있는데 이 서비스 이름의 키가 없으면(이름 오타 등) 내부 토큰으로 돌아가지 않는다.
    돌아가면 관찰만 하려던 설치가 쓰기 권한으로 돌거나, 분리 뒤 외부 판매 서버에 내부 토큰이 나간다.
    """
    keys = client_keys()
    if keys:
        key = keys.get(service_name())
        if not key:
            logger.error(
                "%s에 서비스 %r의 키가 없다(설정된 서비스: %s). 요청을 보내지 않는다",
                ENV_KEYS,
                service_name(),
                ", ".join(sorted(keys)),
            )  # 키 값은 싣지 않는다
            raise ShopError("NO_SERVICE_KEY", "이 서비스의 사이트 서버 연결 키가 설정돼 있지 않다")
        return {"Authorization": f"Bearer {key}", **company_connection.caller_headers()}
    return {
        "Authorization": f"Bearer {config.agent_token('support')}",
        **company_connection.caller_headers(),  # 호출 회사(#1649)
    }


ENV_MODE = "SUPPORT_SHOP_MODE"  # write(기본)·read(읽기 전용 설치, 관찰 모드)


_KEY_IS_READ_ONLY = {"seen": False}  # 판매 서버가 read 키라고 알려 준 적이 있다(403 READ_ONLY_KEY)


def read_only() -> bool:
    """읽기 전용 설치(관찰 모드)면 True. 판매 서버를 읽기만 하고 아무것도 쓰지 않는다.

    스위치(SUPPORT_SHOP_MODE=read)를 켰거나, 판매 서버가 이 설치의 키를 read 키로 알려 주면(쓰기에
    403 READ_ONLY_KEY) 자동으로 관찰 모드가 된다.
    """
    if _KEY_IS_READ_ONLY["seen"]:
        return True
    return os.getenv(ENV_MODE, "").strip().lower() in ("read", "readonly", "observe")


def read_only_reason() -> str | None:
    """관찰 모드인 까닭: config(스위치)·key(판매 서버가 read 키라고 알려 옴)·None(쓰기 가능)."""
    if _KEY_IS_READ_ONLY["seen"]:
        return "key"
    return "config" if read_only() else None


async def _send(
    method: str, path: str, *, client: httpx.AsyncClient | None = None, **kwargs: Any
) -> httpx.Response:
    if method.upper() != "GET" and read_only():
        if path.startswith("/admin/"):
            # 읽어 가기의 읽은 표시·되돌려 쓰기·실패 알림: 쓰지 않고 아무 일도 안 한다.
            # 그래서 쓰기 권한이 있는 설치(251)가 처리 상태를 그대로 쥔다
            return httpx.Response(200, json={"data": {}, "meta": {}})
        raise ShopError("READ_ONLY", "읽기 전용 설치라 사이트 서버에 쓰지 않는다")
    shared = client or _client  # 여러 번 부르는 읽기는 하나를 넘겨 재사용한다 (shared_client)
    http = shared or httpx.AsyncClient(
        timeout=TIMEOUT_SECONDS, transport=pinned_http.company_transport()
    )
    try:
        response = await http.request(method, _url(path), headers=_headers(), **kwargs)
        if method.upper() != "GET" and _read_only_key_refused(response):
            _KEY_IS_READ_ONLY["seen"] = True  # 이 설치의 키는 read 키다: 이후 쓰기는 보내지 않는다
            logger.warning("판매 서버가 이 설치의 연결 키를 read 키로 알려 와 관찰 모드로 바뀐다")
            if path.startswith("/admin/"):
                return httpx.Response(200, json={"data": {}, "meta": {}})
            raise ShopError("READ_ONLY", "읽기 전용 키라 사이트 서버에 쓰지 않는다")
        return response
    except httpx.HTTPError as exc:
        raise ShopError("UNAVAILABLE", f"shop-api에 연결할 수 없다 ({type(exc).__name__})") from exc
    finally:
        if shared is None:
            await http.aclose()


@asynccontextmanager
async def shared_client() -> AsyncIterator[httpx.AsyncClient]:
    """같은 서버를 여러 번 읽을 때 쓸 클라이언트 하나.

    호출마다 새로 만들면 연결·TLS 설정 비용이 쌓인다 (주문 4만 건을 읽는 데 18.9초 →
    한 클라이언트로 7.0초, 로컬 Windows 측정).
    """
    if _client is not None:  # 테스트에서 바꿔 끼운 클라이언트
        yield _client
        return
    async with httpx.AsyncClient(
        timeout=TIMEOUT_SECONDS, transport=pinned_http.company_transport()
    ) as client:
        yield client


def _read_only_key_refused(response: httpx.Response) -> bool:
    if response.status_code != 403:
        return False
    try:
        return response.json()["error"]["code"] == "READ_ONLY_KEY"
    except (ValueError, KeyError, TypeError):
        return False


def _error(response: httpx.Response) -> ShopError:
    try:
        error = response.json()["error"]
        return ShopError(str(error["code"]), str(error.get("message", "")))
    except (ValueError, KeyError, TypeError):
        return ShopError("UNAVAILABLE", f"shop-api 응답 오류 ({response.status_code})")


def _moment(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value.replace("Z", "+00:00")) if value else None


def to_order(data: dict[str, Any]) -> Order | None:
    """shop-api 주문을 판정용 `Order`로 바꾼다. 결제가 끝나지 않은 주문은 None이다."""
    paid = [p for p in data.get("payments", []) if p.get("status") == "succeeded"]
    if data.get("status") != "paid" or not paid:
        return None
    payment = paid[-1]
    delivered_at = _moment(data.get("delivered_at"))
    paid_at = _moment(payment.get("created_at")) or _moment(data["created_at"])
    assert paid_at is not None
    return Order(
        id=data["order_id"],
        order_no=data["order_id"],  # shop-api에는 별도 주문 번호가 없다
        buyer_id=data["buyer_id"],
        status="delivered" if delivered_at else "preparing",
        paid_at=paid_at,
        promised_delivery_at=_moment(data.get("promised_delivery_at")),
        shipped_at=_moment(data.get("shipped_at")),
        delivered_at=delivered_at,
        shipping_fee=data.get("shipping_fee", 0),
        points_used=data.get("points_used", 0),
        coupon_discount=data.get("coupon_discount") or 0,
        payment_id=payment["payment_id"],
        items=[
            OrderItem(
                id=item["order_item_id"],
                title=item["title"],
                qty=item["qty"],
                paid_amount=item["paid_amount"],
                points_share=item.get("points_share", 0),
                coupon_share=item.get("coupon_share") or 0,
                product_id=item.get("product_id"),
            )
            for item in data.get("items", [])
        ],
    )


SALES_PAGE_LIMIT = 1000  # 판매 서버 주문 목록 한 페이지 최대
SALES_MAX_PAGES = 30  # 한 번에 읽는 페이지 상한 (최대 3만 주문)


SALES_SUMMARY_CHUNK_DAYS = (
    7  # 판매 집계를 이 일수씩 나눠 읽는다 (판매 서버가 한 번에 오래 붙잡히지 않게)
)
SALES_CACHE_SECONDS = 300  # 같은 기간의 판매 수량은 몇 분 동안 다시 읽지 않는다
SALES_CACHE_MAX = 32
_SALES_BUCKET = timedelta(
    minutes=5
)  # 기간 끝을 이 단위로 내려 같은 기간의 호출이 같은 열쇠가 되게 한다
_SALES_CACHE: dict[tuple[str, datetime, datetime], tuple[float, SalesRead]] = {}


@dataclass(frozen=True)
class SalesRead:
    qty_by_product: dict[str, int]  # 상품 ID → 결제가 끝난 주문의 판매 수량
    orders_scanned: int | None  # 주문 목록으로 읽었을 때 읽은 주문 수 (집계로 읽었으면 None)
    complete: bool  # False면 페이지 상한·마감 때문에 끝까지 읽지 못했다 (수량이 모자란다)
    source: str = "orders"  # "summary"(판매 집계 읽기 경로) | "orders"(주문 목록)


def clear_sales_cache() -> None:
    """판매 수량 캐시를 비운다 (시험·설정 바꾼 뒤)."""
    _SALES_CACHE.clear()


def _is_paid(data: dict[str, Any]) -> bool:
    return data.get("status") == "paid" and any(
        p.get("status") == "succeeded" for p in data.get("payments", [])
    )


def _floor(moment: datetime) -> datetime:
    seconds = int(_SALES_BUCKET.total_seconds())
    return datetime.fromtimestamp(int(moment.timestamp()) // seconds * seconds, UTC)


async def fetch_sold_qty(
    since: datetime,
    until: datetime,
    *,
    stop_at: datetime | None = None,
    max_pages: int = SALES_MAX_PAGES,
) -> SalesRead:
    """접수 시각이 `since` 이상 `until` 미만인 결제 끝난 주문의 상품별 판매 수량.

    1) 판매 서버의 판매 집계 읽기 경로(`GET /sales-summary?group_by=product`)를 7일씩 나눠 읽는다.
       주문을 하나씩 읽지 않아 주문이 많아도 판매 서버를 오래 붙잡지 않는다.
    2) 그 경로가 없는 옛 판매 서버(404)면 주문 목록(`GET /orders`)을 페이지로 읽는다. 주문 목록은
       order_id 순이라 중간에 멈추면 최근 주문만 읽은 게 아니라 일부만 읽은 것이다.

    기간 끝을 5분 단위로 내려 같은 기간의 호출이 같은 열쇠가 되게 하고, 끝까지 읽은 결과는 몇 분
    캐시한다(요청마다 판매 서버를 다시 읽지 않는다). `complete=False`일 때는 수량을 쓰지 않는다.
    연결 실패·거절은 `ShopError`다.
    """
    span = until - since
    until_q = _floor(until)
    since_q = until_q - span
    key = (_url("/sales"), since_q, until_q)
    now_mono = time.monotonic()
    hit = _SALES_CACHE.get(key)
    if hit is not None and now_mono - hit[0] < SALES_CACHE_SECONDS:
        return hit[1]
    async with shared_client() as client:  # 집계 시도와 주문 목록 읽기가 클라이언트 하나를 쓴다
        read = await _read_summary(client, since_q, until_q, stop_at=stop_at)
        if read is None:  # 판매 집계 읽기 경로가 없다(옛 판매 서버): 주문 목록으로 읽는다
            read = await _read_order_list(
                client, since_q, until_q, stop_at=stop_at, max_pages=max_pages
            )
    if read.complete:
        if len(_SALES_CACHE) >= SALES_CACHE_MAX:
            _SALES_CACHE.pop(min(_SALES_CACHE, key=lambda k: _SALES_CACHE[k][0]), None)
        _SALES_CACHE[key] = (time.monotonic(), read)
    return read


def _iso(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


async def _read_summary(
    client: httpx.AsyncClient, since: datetime, until: datetime, *, stop_at: datetime | None
) -> SalesRead | None:
    """판매 집계 읽기 경로로 상품별 `gross_qty`(결제가 끝난 주문의 판매 수량)를 합친다.

    경로가 없으면(404·405) None을 돌려줘 주문 목록으로 읽게 한다. 그 밖의 거절·연결 실패는
    `ShopError`다. 마감을 넘기면 끝까지 읽지 못한 것으로 돌려준다.
    """
    qty: dict[str, int] = {}
    chunk = timedelta(days=SALES_SUMMARY_CHUNK_DAYS)
    start = since
    while start < until:
        end = min(start + chunk, until)
        timeout = TIMEOUT_SECONDS
        if stop_at is not None:
            remaining = (stop_at - datetime.now(UTC)).total_seconds()
            if remaining <= 0:
                return SalesRead(qty, None, False, "summary")
            timeout = min(TIMEOUT_SECONDS, max(remaining, 0.5))
        response = await _send(
            "GET",
            "/sales-summary",
            params={"from": _iso(start), "to": _iso(end), "group_by": "product"},
            client=client,
            timeout=timeout,
        )
        if response.status_code in (404, 405):
            return None
        if response.status_code != 200:
            raise _error(response)
        for row in response.json()["data"]["rows"]:
            product_id = row.get("product_id")
            if product_id:
                qty[product_id] = qty.get(product_id, 0) + int(row["gross_qty"])
        start = end
    return SalesRead(qty, None, True, "summary")


async def _read_order_list(
    client: httpx.AsyncClient,
    since: datetime,
    until: datetime,
    *,
    stop_at: datetime | None,
    max_pages: int,
) -> SalesRead:
    """주문 목록(`GET /orders?since&until&cursor`)을 페이지로 읽어 상품별 판매 수량을 센다."""
    qty: dict[str, int] = {}
    scanned = 0
    cursor: str | None = None
    for _ in range(max_pages):
        timeout = TIMEOUT_SECONDS
        if stop_at is not None:
            remaining = (stop_at - datetime.now(UTC)).total_seconds()
            if remaining <= 0:
                return SalesRead(qty, scanned, False)
            timeout = min(TIMEOUT_SECONDS, max(remaining, 0.5))  # 페이지 하나가 마감을 넘기지 않게
        params: dict[str, Any] = {
            "since": since.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "until": until.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "limit": SALES_PAGE_LIMIT,
        }
        if cursor:
            params["cursor"] = cursor
        response = await _send("GET", "/orders", params=params, client=client, timeout=timeout)
        if response.status_code != 200:
            raise _error(response)
        body = response.json()
        for order in body["data"]:
            scanned += 1
            if not _is_paid(order):
                continue
            for item in order.get("items", []):
                if item.get("product_id"):
                    qty[item["product_id"]] = qty.get(item["product_id"], 0) + int(item["qty"])
        cursor = (body.get("meta") or {}).get("next_cursor")
        if not cursor:
            return SalesRead(qty, scanned, True)
    return SalesRead(qty, scanned, False)


async def fetch_order(order_id: str, *, client: httpx.AsyncClient | None = None) -> Order | None:
    """주문을 읽는다. 없거나 결제가 끝나지 않았으면 None, 연결 실패는 `ShopError`.

    여러 주문을 이어서 읽을 때는 `shared_client()`의 클라이언트를 `client`로 넘겨 재사용한다.
    """
    response = await _send("GET", f"/orders/{order_id}", client=client)
    if response.status_code == 404:
        return None
    if response.status_code != 200:
        raise _error(response)
    return to_order(response.json()["data"])


async def fetch_buyer_orders(buyer_id: str, limit: int = 20) -> tuple[list[Order], bool]:
    """구매자의 결제가 끝난 주문을 결제 시각 최근 순으로 읽는다. (주문들, 더 있어 잘렸는가)

    `GET /orders?buyer_id=`로 번호를 받고 주문을 하나씩 읽는다(최대 `limit`번). 판매 서버가
    `buyer_id` 조건을 무시해도 남의 주문이 섞이지 않게, 목록 줄과 읽은 주문 모두 구매자를 다시 본다.
    연결 실패는 `ShopError`.
    """
    found: list[Order] = []
    async with shared_client() as client:
        response = await _send(
            "GET", "/orders", params={"buyer_id": buyer_id, "limit": limit}, client=client
        )
        if response.status_code != 200:
            raise _error(response)
        body = response.json()
        truncated = bool((body.get("meta") or {}).get("next_cursor"))
        for row in body.get("data", [])[:limit]:
            if row.get("buyer_id") not in (None, buyer_id) or not row.get("order_id"):
                continue
            order = await fetch_order(row["order_id"], client=client)
            if order is not None and order.buyer_id == buyer_id:
                found.append(order)
    found.sort(key=lambda o: o.paid_at, reverse=True)
    return found, truncated


async def load_buyer_orders(buyer_id: str, limit: int = 20) -> tuple[list[Order], bool]:
    """구매자의 주문들. 출처는 `order_source()`가 정한다(`load_order`와 같다)."""
    if order_source() == "seed":
        mine = [o.model_copy(deep=True) for o in orders.SEED_ORDERS if o.buyer_id == buyer_id]
        mine.sort(key=lambda o: o.paid_at, reverse=True)
        return mine[:limit], len(mine) > limit
    return await fetch_buyer_orders(buyer_id, limit)


async def fetch_order_facts(order_id: str) -> dict[str, Any] | None:
    """주문 원본(결제 시도 포함)을 읽는다. 없으면 None, 연결 실패는 `ShopError`.

    `fetch_order`는 결제가 끝난 주문만 돌려주지만, 결제 문제를 보려면 실패한 주문도 필요하다.
    """
    response = await _send("GET", f"/orders/{order_id}")
    if response.status_code == 404:
        return None
    if response.status_code != 200:
        raise _error(response)
    return response.json()["data"]


async def load_order(order_id: str, *, client: httpx.AsyncClient | None = None) -> Order | None:
    """클레임 판정에 쓸 주문. 출처는 `order_source()`가 정한다. `client`는 `fetch_order`와 같다."""
    if order_source() == "seed":
        return orders.get_order(order_id)
    return await fetch_order(order_id, client=client)


async def refund(
    payment_id: str,
    amount: int,
    idempotency_key: str,
    context_id: str,
    *,
    cancel_order: bool = False,
) -> dict[str, Any]:
    """현금 환불을 반영한다. 같은 키의 재요청은 이전 환불을 돌려준다 (`meta.replayed`).

    `cancel_order`는 취소 클레임 지급이다. 판매 서버(catbooks-demo #60)는 이 환불로 결제가 전액
    환불되고 아직 발송 전이면 주문도 취소하고(`order.cancelled`) 응답에 `order_cancelled`를 붙인다.
    그 전 판매 서버는 모르는 칸을 무시하고 환불만 한다.
    """
    body: dict[str, Any] = {
        "refund_amount": amount,
        "idempotency_key": idempotency_key,
        "context_id": context_id,
    }
    if cancel_order:
        body["cancel_order"] = True
    response = await _send("POST", f"/payments/{payment_id}/refund", json=body)
    if response.status_code not in (200, 201):
        raise _error(response)
    return response.json()["data"]


async def _read_inbox(path: str, limit: int) -> tuple[list[dict[str, Any]], int, int]:
    response = await _send("GET", path, params={"state": "new", "limit": limit})
    if response.status_code != 200:
        raise _error(response)
    body = response.json()
    meta = body.get("meta", {})
    return body["data"], int(meta.get("total", len(body["data"]))), int(meta.get("failed", 0))


async def fetch_inquiry_inbox(limit: int = 20) -> tuple[list[dict[str, Any]], int, int]:
    """판매 서버 문의함에서 읽을 차례가 된 문의를 읽는다 (`support` 토큰).

    문의 원문과 사진이 들어 있다. 돌려주는 것은 (문의 목록, 읽기를 기다리는 전체 수,
    판매 서버가 실패로 빼 둔 수)다. 연결 실패·거절은 `ShopError`다.
    """
    return await _read_inbox("/admin/inquiries", limit)


async def mark_inquiry_synced(seq: int, support_inquiry_id: int) -> None:
    """고객지원이 이 문의를 받았다고 판매 서버 문의함에 알린다 (사진은 판매 서버가 지운다)."""
    response = await _send(
        "POST", f"/admin/inquiries/{seq}/synced", json={"support_inquiry_id": support_inquiry_id}
    )
    if response.status_code != 200:
        raise _error(response)


async def report_inquiry_failed(seq: int, reason: str) -> None:
    """이 문의를 처리하지 못했다고 알린다 (판매 서버가 다시 읽는 시각을 늦추고 횟수를 센다)."""
    response = await _send("POST", f"/admin/inquiries/{seq}/failed", json={"reason": reason})
    if response.status_code != 200:
        raise _error(response)


async def fetch_claim_inbox(limit: int = 20) -> tuple[list[dict[str, Any]], int, int]:
    """판매 서버 클레임함에서 읽을 차례가 된 신청을 읽는다 (`support` 토큰).

    돌려주는 것은 (신청 목록, 읽기를 기다리는 전체 수, 판매 서버가 실패로 빼 둔 수)다.
    연결 실패·거절은 `ShopError`다.
    """
    return await _read_inbox("/admin/claims", limit)


async def fetch_open_claims(after: int = 0) -> tuple[list[dict[str, Any]], int | None]:
    """읽어 갔지만 아직 끝나지 않은 신청 한 쪽(`seq`·`support_claim_id`·`status`·`result_digest`).

    `after` 뒤부터 읽고, 돌려주는 두 번째 값이 다음 쪽의 `after`다(더 없으면 None).
    """
    response = await _send(
        "GET", "/admin/claims", params={"state": "open", "after": after, "limit": 100}
    )
    if response.status_code != 200:
        raise _error(response)
    body = response.json()
    return body["data"], body.get("meta", {}).get("next_after")


async def write_claim_result(
    seq: int, support_claim_id: int | None, status: str, result: dict[str, Any]
) -> None:
    """고객지원이 판정한 결과를 판매 서버 클레임함에 적는다 (고객은 판매 서버에서 읽는다)."""
    response = await _send(
        "POST",
        f"/admin/claims/{seq}/result",
        json={"support_claim_id": support_claim_id, "status": status, "result": result},
    )
    if response.status_code != 200:
        raise _error(response)


async def report_claim_failed(seq: int, reason: str) -> None:
    """이 신청을 처리하지 못했다고 알린다 (횟수를 넘기면 판매 서버가 접수 불가로 닫는다)."""
    response = await _send("POST", f"/admin/claims/{seq}/failed", json={"reason": reason})
    if response.status_code != 200:
        raise _error(response)


async def fetch_stuck_intake() -> list[dict[str, Any]]:
    """판매 서버가 실패로 빼 둔 문의·클레임 접수(번호·사유·횟수·시각, 고객 글 없음)."""
    response = await _send("GET", "/admin/intake/stuck")
    if response.status_code != 200:
        raise _error(response)
    return response.json()["data"]


async def retry_stuck_intake(kind: str, seq: int) -> dict[str, Any]:
    """실패로 뺀 접수를 다시 읽는 차례로 돌린다 (`kind`는 inquiry·claim)."""
    if read_only():  # 쓰기 없이 가짜 200으로 받으면 "이유 없는 실패"로 보인다. 이유를 분명히 낸다
        raise ShopError("READ_ONLY", "읽기 전용 설치라 사이트 서버의 접수를 다시 돌리지 않는다")
    path = f"/admin/{'inquiries' if kind == 'inquiry' else 'claims'}/{seq}/retry"
    response = await _send("POST", path)
    if response.status_code != 200:
        raise _error(response)
    return response.json()["data"]


async def _get_list(
    path: str, params: dict[str, Any]
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    response = await _send("GET", path, params=params)
    if response.status_code != 200:
        raise _error(response)
    body = response.json()
    return body["data"], body.get("meta", {})


async def _post(path: str, payload: dict[str, Any]) -> dict[str, Any]:
    response = await _send("POST", path, json=payload)
    if response.status_code != 200:
        raise _error(response)
    return response.json()["data"]


async def fetch_open_inquiry_views(
    after: int = 0, sweep: bool = False
) -> tuple[list[dict[str, Any]], int | None]:
    """읽어 갔지만 고객 화면 값이 아직 끝나지 않은 문의 한 쪽(seq·번호·view_digest).

    sweep이면 해결·평가가 끝난 문의까지 고객 화면에 남은 문의를 모두 준다(가끔 한 번, 삭제 반영용).
    """
    params: dict[str, Any] = {"after": after, "limit": 100}
    if sweep:
        params["sweep"] = "true"
    items, meta = await _get_list("/admin/inquiries/open", params)
    return items, meta.get("next_after")


async def write_inquiry_view(seq: int, view: dict[str, Any], view_digest: str) -> None:
    """고객이 볼 문의 값(상태·답변)을 판매 서버에 되돌려 쓴다 (고객은 판매 서버에서 읽는다)."""
    await _post(f"/admin/inquiries/{seq}/view", {"view": view, "view_digest": view_digest})


async def fetch_feedback_inbox(limit: int = 20) -> tuple[list[dict[str, Any]], int]:
    """판매 서버에 저장된, 아직 읽어 가지 않은 문의 평가와 남은 전체 수."""
    items, meta = await _get_list("/admin/feedback", {"limit": limit})
    return items, int(meta.get("total", len(items)))


async def mark_feedback_synced(seq: int, outcome: str) -> None:
    await _post(f"/admin/inquiries/{seq}/feedback-synced", {"outcome": outcome})


async def fetch_idea_inbox(limit: int = 20) -> tuple[list[dict[str, Any]], int]:
    """판매 서버 아이디어함에서 아직 읽어 가지 않은 아이디어와 남은 전체 수."""
    items, meta = await _get_list("/admin/ideas", {"limit": limit})
    return items, int(meta.get("total", len(items)))


async def mark_idea_synced(
    seq: int, outcome: str, ref: str | None = None, notice: str | None = None
) -> None:
    await _post(f"/admin/ideas/{seq}/synced", {"outcome": outcome, "ref": ref, "notice": notice})


async def fetch_return_inbox(limit: int = 20) -> tuple[list[dict[str, Any]], int]:
    """판매 서버에 기록된, 아직 읽어 가지 않은 반품 입고와 남은 전체 수."""
    items, meta = await _get_list("/admin/returns", {"limit": limit})
    return items, int(meta.get("total", len(items)))


async def mark_return_synced(claim_id: int, outcome: str) -> None:
    await _post(f"/admin/returns/{claim_id}/synced", {"outcome": outcome})


async def mark_inquiry_gone(seq: int) -> None:
    """고객지원에서 찾을 수 없는 문의를 고객 화면에서 빼 달라고 판매 서버에 알린다."""
    await _post(f"/admin/inquiries/{seq}/gone", {})


async def mark_inquiry_gone_by_support(support_inquiry_id: int) -> None:
    """고객지원이 지운 문의를 고객 화면에서 바로 빼 달라고 판매 서버에 알린다 (없는 번호는 무시)."""
    response = await _send("POST", f"/admin/inquiries/by-support/{support_inquiry_id}/gone")
    if response.status_code not in (200, 404):
        raise _error(response)
