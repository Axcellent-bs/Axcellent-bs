"""판매자 정산 장부: 판매 서버(shop-api)에서 주문·상품·판매자·쿠폰을 읽어 정산에 쓸 모양으로 바꾼다.

2026-11-12 재무(`finance_agent.books`)에서 판매자 정산에 필요한 부분만 옮겨 왔다.

- 주문은 `GET /api/shop/orders`를 1000건씩 `meta.next_cursor`로 끝까지 넘겨 모은다. 한 페이지라도
  실패하면 주문 전체를 "못 받음"으로 둔다(빠진 장부로 정산금을 내지 않는다)
- 평소에는 최근 `RECENT_DAYS`일 주문만 다시 받아 끼우고, `FULL_REFRESH_SECONDS`마다 전체를 다시
  읽어 오래된 주문의 바뀐 점(환불·배송 완료)을 맞춘다. 지급을 요청할 때는 전체를 새로 읽는다
- 환불은 주문 응답의 환불 기록(`refunds`)을 판매자별로 나눈다(품목 결제 금액 비율)
- 금액은 정수 원, 시각은 UTC datetime
"""

from __future__ import annotations

import asyncio
import bisect
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any

import httpx

from ax_agent_template import config
from support_agent import settlement_config as sc
from support_agent import shop_client

logger = logging.getLogger("ax.support.settlement_books")

CALL_SECONDS = 10.0
ORDERS_CALL_SECONDS = 30.0
ORDERS_PAGE = 1000  # shop-api 한 페이지 최대
LIVE_CACHE_SECONDS = 30.0
LIVE_STALE_SECONDS = 300.0  # 캐시가 지나도 이 안이면 직전 장부로 답하고 뒤에서 새로 읽는다
RECENT_DAYS = 3
FULL_REFRESH_SECONDS = 600.0


@dataclass
class SettlementBooks:
    """정산에 쓰는 장부 자료."""

    orders: list[dict[str, Any]] = field(default_factory=list)
    sellers: list[dict[str, Any]] = field(default_factory=list)
    products: list[dict[str, Any]] = field(default_factory=list)
    refunds: list[dict[str, Any]] = field(default_factory=list)
    coupons: list[dict[str, Any]] = field(default_factory=list)
    source: str = "test"
    mock: bool = True
    collected_at: datetime = field(default_factory=sc.now)
    unavailable: list[str] = field(default_factory=list)
    notes: dict[str, str] = field(default_factory=dict)
    _memo: dict[Any, Any] = field(default_factory=dict, repr=False, compare=False)

    def memo(self, key: Any, make: Callable[[], Any]) -> Any:
        """실제 장부는 모은 뒤 바뀌지 않으므로 계산 결과를 붙여 둔다(테스트 장부는 매번 계산)."""
        if self.mock:
            return make()
        k = (key, len(self.orders))
        if k not in self._memo:
            self._memo[k] = make()
        return self._memo[k]

    def seller_name(self, seller_id: str) -> str:
        for s in self.sellers:
            if s.get("seller_id") == seller_id:
                return str(s.get("name") or seller_id)
        return seller_id

    def product_name(self, product_id: str, item: dict[str, Any] | None = None) -> str:
        """품목에 적힌 이름 → 쇼핑몰 상품 목록 이름 → 상품 번호 순으로 찾는다."""
        named = (item or {}).get("title") or (item or {}).get("name")
        if named:
            return str(named)
        for p in self.products:
            if p.get("product_id") == product_id:
                return str(p.get("name") or product_id)
        return product_id

    def sold(self) -> tuple[list[datetime], list[dict[str, Any]]]:
        """팔린 주문(성공한 결제가 있는 주문)을 첫 성공 결제 시각 순으로."""
        return self.memo("sold", self._sold)

    def _sold(self) -> tuple[list[datetime], list[dict[str, Any]]]:
        rows = [(t, o) for o in self.orders if (t := paid_at(o)) is not None]
        rows.sort(key=lambda r: r[0])
        return [r[0] for r in rows], [r[1] for r in rows]

    def sold_between(self, start: datetime, end: datetime) -> list[dict[str, Any]]:
        times, orders = self.sold()
        return orders[bisect.bisect_left(times, start) : bisect.bisect_left(times, end)]

    def confirmed(self) -> tuple[list[datetime], list[dict[str, Any]]]:
        """구매가 확정된(배송 완료) 팔린 주문을 확정 시각 순으로."""
        return self.memo("confirmed", self._confirmed)

    def _confirmed(self) -> tuple[list[datetime], list[dict[str, Any]]]:
        _, orders = self.sold()
        rows = [(o["delivered_at"], o) for o in orders if o.get("delivered_at") is not None]
        rows.sort(key=lambda r: r[0])
        return [r[0] for r in rows], [r[1] for r in rows]

    def confirmed_between(self, start: datetime, end: datetime) -> list[dict[str, Any]]:
        """구매 확정(배송 완료) 시각이 [start, end)인 팔린 주문. 판매자 정산의 기준이다."""
        times, orders = self.confirmed()
        return orders[bisect.bisect_left(times, start) : bisect.bisect_left(times, end)]

    def unconfirmed_until(self, end: datetime) -> list[dict[str, Any]]:
        """`end` 전에 팔렸지만 아직 구매가 확정되지 않은 주문 (다음 정산으로 넘어간다)."""
        times, orders = self.sold()
        upto = orders[: bisect.bisect_left(times, end)]
        return [o for o in upto if o.get("delivered_at") is None]

    def unavailable_core(self) -> bool:
        """정산금을 내는 데 꼭 필요한 자료(주문·쿠폰)를 못 받았는가."""
        return any(name in self.unavailable for name in ("orders", "coupons"))

    def meta(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "mock": self.mock,
            "collected_at": sc.iso(self.collected_at),
            "source": self.source,
            "unavailable": list(self.unavailable),
        }
        if self.notes:
            out["notes"] = dict(self.notes)
        return out


# ---------------------------------------------------------------- 공통 변환
def _int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def item_gross(item: dict[str, Any]) -> int:
    return (_int(item.get("qty")) or 0) * (_int(item.get("unit_price")) or 0)


def in_range(dt: datetime | None, start: datetime, end: datetime) -> bool:
    return dt is not None and start <= dt < end


def paid_at(order: dict[str, Any]) -> datetime | None:
    """첫 성공 결제 시각. 성공한 결제가 없으면 None (팔리지 않은 주문)."""
    times = [p["created_at"] for p in order.get("payments", []) if p.get("status") == "succeeded"]
    return min(times) if times else None


def normalize_order(raw: dict[str, Any], categories: dict[str, str]) -> dict[str, Any] | None:
    """shop-api 주문 응답 → 장부 주문. 형식이 다르면 None."""
    created = sc.parse_dt(raw.get("created_at"))
    items, payments = raw.get("items"), raw.get("payments")
    if not isinstance(raw.get("order_id"), str) or created is None:
        return None
    if not isinstance(items, list) or not isinstance(payments, list):
        return None
    norm_items = []
    for i in items:
        if not isinstance(i, dict) or _int(i.get("qty")) is None:
            return None
        if _int(i.get("unit_price")) is None:
            return None
        paid = _int(i.get("paid_amount"))
        norm_items.append(
            i
            | {
                "category_code": i.get("category_code") or categories.get(str(i.get("product_id"))),
                "paid_amount": paid if paid is not None else item_gross(i),
            }
        )
    norm_payments = []
    for p in payments:
        if not isinstance(p, dict):
            return None
        norm_payments.append(p | {"created_at": sc.parse_dt(p.get("created_at")) or created})
    return {
        "order_id": raw["order_id"],
        "buyer_id": raw.get("buyer_id"),
        "status": raw.get("status"),
        "total_amount": _int(raw.get("total_amount")) or 0,
        "refunded_amount": _int(raw.get("refunded_amount")) or 0,
        "created_at": created,
        "delivered_at": sc.parse_dt(raw.get("delivered_at")),
        "items": norm_items,
        "payments": norm_payments,
        "refund_events": _refund_events(raw.get("refunds"), _int(raw.get("refunded_amount")) or 0),
    }


def _refund_events(raw: Any, refunded_amount: int) -> list[dict[str, Any]] | None:
    """주문의 환불 기록 → [{refund_id, payment_id, amount, created_at}]. 어긋나면 None."""
    if not isinstance(raw, list):
        return None
    events = []
    for r in raw:
        if not isinstance(r, dict):
            return None
        amount, at = _int(r.get("amount")), sc.parse_dt(r.get("created_at"))
        if amount is None or amount <= 0 or at is None:
            return None
        events.append(
            {
                "refund_id": r.get("refund_id"),
                "payment_id": r.get("payment_id"),
                "amount": amount,
                "created_at": at,
            }
        )
    if sum(e["amount"] for e in events) != refunded_amount:
        return None
    return events


def _split_refund(order: dict[str, Any], amount: int) -> list[tuple[dict[str, Any], int]]:
    """환불 금액을 품목 결제 금액 비율로 판매자에게 나눈다(내림, 나머지는 마지막 품목)."""
    paid_items = [i for i in order["items"] if i.get("paid_amount")]
    total_paid = sum(i["paid_amount"] for i in paid_items) or 1
    left, out = amount, []
    for idx, item in enumerate(paid_items):
        share = left if idx == len(paid_items) - 1 else amount * item["paid_amount"] // total_paid
        left -= share
        out.append((item, share))
    return out


def refunds_from_orders(orders: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """주문의 환불을 판매자별 환불 기록으로 옮긴다.

    환불 기록(`refunds`)이 있으면 한 건씩 환불 시각으로, 없으면(예전 판매 서버) 주문 시각에 둔다.
    """
    rows = []
    for o in orders:
        if not o["refunded_amount"]:
            continue
        events = o.get("refund_events")
        if events is None:
            events = [
                {
                    "refund_id": f"rfd_of_{o['order_id']}",
                    "payment_id": next(
                        (p["payment_id"] for p in o["payments"] if p.get("payment_id")), None
                    ),
                    "amount": o["refunded_amount"],
                    "created_at": o["created_at"],
                }
            ]
        for e in events:
            for idx, (item, share) in enumerate(_split_refund(o, e["amount"])):
                if share:
                    rows.append(
                        {
                            "refund_id": f"{e['refund_id']}_{idx}",
                            "order_id": o["order_id"],
                            "payment_id": e["payment_id"],
                            "seller_id": item.get("seller_id"),
                            "amount": share,
                            "created_at": e["created_at"],
                        }
                    )
    return rows


def refund_basis(orders: list[dict[str, Any]]) -> str:
    kinds = {o.get("refund_events") is not None for o in orders if o.get("refunded_amount")}
    if kinds == {False}:
        return "order_created_at"
    if kinds == {True, False}:
        return "mixed"
    return "refund_created_at"


# ---------------------------------------------------------------- 실제 (shop-api)
async def _get(client: httpx.AsyncClient, url: str, headers: dict[str, str] | None = None) -> Any:
    """`{data, meta}` 응답의 data. 실패하면 None."""
    try:
        response = await client.get(url, headers=headers or {}, timeout=CALL_SECONDS)
    except httpx.HTTPError:
        return None
    if response.status_code != 200:
        return None
    try:
        body = response.json()
    except ValueError:
        return None
    return body.get("data") if isinstance(body, dict) else None


async def _get_orders(
    client: httpx.AsyncClient, shop: str, since: datetime | None
) -> list[dict[str, Any]] | None:
    params: dict[str, Any] = {"limit": ORDERS_PAGE}
    if since is not None:
        params["since"] = sc.iso(since)
    orders: list[dict[str, Any]] = []
    while True:
        try:
            response = await client.get(
                f"{shop}/api/shop/orders", params=params, timeout=ORDERS_CALL_SECONDS
            )
        except httpx.HTTPError:
            return None
        if response.status_code != 200:
            return None
        try:
            body = response.json()
        except ValueError:
            return None
        data = body.get("data") if isinstance(body, dict) else None
        if not isinstance(data, list):
            return None
        orders.extend(data)
        meta = body.get("meta")
        cursor = meta.get("next_cursor") if isinstance(meta, dict) else None
        if not cursor:
            return orders
        params = {**params, "cursor": cursor}


class OrderStore:
    """모은 주문. 평소에는 최근 주문만 다시 받아 같은 구간을 통째로 바꿔 끼운다."""

    def __init__(self) -> None:
        self.orders: dict[str, dict[str, Any]] = {}
        self.skipped = 0
        self.full_at: float | None = None
        self.lock = asyncio.Lock()

    def since(self, full: bool) -> datetime | None:
        if full or self.full_at is None or time.monotonic() - self.full_at >= FULL_REFRESH_SECONDS:
            return None
        return (sc.now() - timedelta(days=RECENT_DAYS)).replace(microsecond=0)

    def merge(
        self, raw: list[Any], categories: dict[str, str], since: datetime | None
    ) -> tuple[list[dict[str, Any]], int]:
        fresh: dict[str, dict[str, Any]] = {}
        skipped = 0
        for r in raw:
            norm = normalize_order(r, categories) if isinstance(r, dict) else None
            if norm is None:
                skipped += 1
            else:
                fresh[norm["order_id"]] = norm
        if since is None:
            self.orders, self.skipped = fresh, skipped
            self.full_at = time.monotonic()
        else:
            kept = {k: o for k, o in self.orders.items() if o["created_at"] < since}
            self.orders = kept | fresh
            self.skipped += skipped
        return list(self.orders.values()), self.skipped


_STORE = OrderStore()


async def live_books(
    http_client: httpx.AsyncClient | None = None,
    store: OrderStore | None = None,
    full: bool = False,
) -> SettlementBooks:
    """shop-api에서 정산 장부를 모은다.

    `http_client`를 넘기면(테스트) 따로 모아 둔 주문 없이 읽는다.
    """
    client = http_client or httpx.AsyncClient()
    if store is None:
        store = _STORE if http_client is None else OrderStore()
    async with store.lock:
        since = store.since(full)
        shop = config.agent_url("shop-api")
        try:
            orders_raw = await _get_orders(client, shop, since)
            products = await _get(client, f"{shop}/api/shop/products")
            sellers = await _get(client, f"{shop}/api/shop/sellers")
            try:
                headers = shop_client._headers()
            except (shop_client.ShopError, RuntimeError):
                headers = None
            coupons_raw = (
                await _get(client, f"{shop}/api/shop/coupons", headers) if headers else None
            )
        finally:
            if http_client is None:
                await client.aclose()

        unavailable: list[str] = []
        categories = {
            str(p.get("product_id")): str(p.get("category_code"))
            for p in (products if isinstance(products, list) else [])
            if isinstance(p, dict)
        }
        orders: list[dict[str, Any]] = []
        refunds: list[dict[str, Any]] = []
        if isinstance(orders_raw, list):
            orders, skipped = await asyncio.to_thread(store.merge, orders_raw, categories, since)
            refunds = await asyncio.to_thread(refunds_from_orders, orders)
            if skipped:
                unavailable.append("orders_partial")
        else:
            unavailable.append("orders")
    if not isinstance(products, list):
        unavailable.append("products")
    if not isinstance(sellers, list):
        unavailable.append("sellers")
    coupons: list[dict[str, Any]] = []
    if isinstance(coupons_raw, list):
        for c in coupons_raw:
            if isinstance(c, dict):
                coupons.append(
                    c
                    | {
                        "created_at": sc.parse_dt(c.get("created_at")),
                        "funded_by": c.get("funded_by") or "company",
                        "seller_id": c.get("seller_id"),
                    }
                )
    else:
        unavailable.append("coupons")
    return SettlementBooks(
        orders=orders,
        sellers=sellers if isinstance(sellers, list) else [],
        products=[p for p in products if isinstance(p, dict)] if isinstance(products, list) else [],
        refunds=refunds,
        coupons=coupons,
        source="shop-api",
        mock=False,
        collected_at=sc.now(),
        unavailable=unavailable,
        notes={"refund_basis": refund_basis(orders)},
    )


# ---------------------------------------------------------------- 캐시
_cache: tuple[float, SettlementBooks] | None = None
_inflight: asyncio.Task[SettlementBooks] | None = None
# 테스트가 장부를 바꿔 끼운다 (None이면 shop-api에서 읽는다)
LOADER: Callable[[bool], Any] | None = None


def clear_cache() -> None:
    global _cache, _inflight, _STORE
    _cache, _inflight, _STORE = None, None, OrderStore()


async def _collect(full: bool) -> SettlementBooks:
    global _cache
    books = await live_books(full=full)
    _cache = None if books.unavailable_core() else (time.monotonic(), books)
    return books


def _log_failure(task: asyncio.Task[SettlementBooks]) -> None:
    if not task.cancelled() and task.exception() is not None:
        logger.warning("정산 장부를 새로 읽지 못함: %s", type(task.exception()).__name__)


async def load_books(fresh: bool = False) -> SettlementBooks:
    """정산 장부. 조회는 캐시를 쓰고, 지급을 요청할 때(`fresh`)는 주문 전체를 새로 읽는다."""
    global _inflight
    if LOADER is not None:
        return await LOADER(fresh)
    if fresh:
        _inflight = asyncio.create_task(_collect(full=True))
        _inflight.add_done_callback(_log_failure)
        return await asyncio.shield(_inflight)
    cached = _cache
    age = time.monotonic() - cached[0] if cached else None
    if cached and age is not None and age < LIVE_CACHE_SECONDS:
        return cached[1]
    if _inflight is None or _inflight.done():
        _inflight = asyncio.create_task(_collect(full=False))
        _inflight.add_done_callback(_log_failure)
    if cached and age is not None and age < LIVE_STALE_SECONDS:
        return cached[1]
    return await asyncio.shield(_inflight)
