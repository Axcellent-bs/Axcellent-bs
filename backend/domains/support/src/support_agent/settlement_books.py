"""판매자 정산 장부: 판매 서버(shop-api)에서 주문·상품·판매자·쿠폰을 읽어 정산에 쓸 모양으로 바꾼다.

2026-11-12 재무(`finance_agent.books`)에서 판매자 정산에 필요한 부분만 옮겨 왔다.

- 주문은 `GET /api/shop/orders`를 1000건씩 `meta.next_cursor`로 끝까지 넘겨 모은다. 한 페이지라도
  실패하면 주문 전체를 "못 받음"으로 둔다(빠진 장부로 정산금을 내지 않는다)
- 평소에는 최근 `RECENT_DAYS`일 주문만 다시 받아 끼우고, `FULL_REFRESH_SECONDS`마다 전체를 다시
  읽어 오래된 주문의 바뀐 점(환불·배송 완료)을 맞춘다. 정규화한 주문과 그 주문의 환불 나눔은
  모아 두고 다시 받은 주문만 새로 계산한다
- 조회는 저장된 장부로 바로 답하고 새로 읽기는 뒤에서 한다. 서버를 켠 뒤 첫 읽기가 끝나기 전에는
  기다리지 않고 "준비 중"(`Preparing`)으로 답한다. 한 번에 읽기 하나만 돈다
- 지급을 요청할 때는 `FRESH_SECONDS`보다 오래된 장부를 쓰지 않는다. 전체가 아니라 최근 주문만 다시
  읽는다(오래된 주문의 바뀐 점은 `FULL_REFRESH_SECONDS`마다 도는 전체 읽기로 맞춘다)
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
RECENT_DAYS = 3
FULL_REFRESH_SECONDS = 600.0
FRESH_SECONDS = 60.0  # 지급 요청은 이보다 오래된 장부를 쓰지 않는다
# 판매자 정산(settlement) 서비스에 판매 서버 연결 키가 따로 없으면 쿠폰 읽기에 이 서비스 키를 쓴다.
# 정산은 클레임(claims) 서비스에 있다가 따로 나왔다(2026-11-12). 쿠폰 읽기는 읽기만 한다
COUPON_KEY_FALLBACK = "claims"


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
    _running: dict[Any, asyncio.Future[Any]] = field(
        default_factory=dict, repr=False, compare=False
    )

    def memo(self, key: Any, make: Callable[[], Any]) -> Any:
        """실제 장부는 모은 뒤 바뀌지 않으므로 계산 결과를 붙여 둔다(테스트 장부는 매번 계산)."""
        if self.mock:
            return make()
        k = (key, len(self.orders))
        if k not in self._memo:
            self._memo[k] = make()
        return self._memo[k]

    async def computed(self, key: Any, make: Callable[[], Any]) -> Any:
        """`make`를 스레드에서 돌린 결과. 실제 장부는 결과를 붙여 두고, 같은 계산이 돌고 있으면
        기다린다.

        스레드 계산은 부른 쪽이 시간 초과로 끊겨도 멈추지 않으므로 같은 열쇠의 계산을 두 번 띄우지
        않는다.
        """
        if self.mock:
            return await asyncio.to_thread(make)
        k = (key, len(self.orders))
        if k in self._memo:
            return self._memo[k]
        running = self._running.get(k)
        if running is None:
            running = asyncio.ensure_future(asyncio.to_thread(self.memo, key, make))
            self._running[k] = running
            running.add_done_callback(lambda _f: self._running.pop(k, None))
        return await asyncio.shield(running)

    def seller_name(self, seller_id: str) -> str:
        names = self.memo("seller_names", self._seller_names)
        return names.get(seller_id, seller_id) if _hashable(seller_id) else seller_id

    def _seller_names(self) -> dict[Any, str]:
        names: dict[Any, str] = {}
        for s in self.sellers:
            sid = s.get("seller_id")
            if _hashable(sid) and sid not in names:  # 같은 번호가 둘이면 앞의 것 (예전과 같다)
                names[sid] = str(s.get("name") or sid)
        return names

    def product_name(self, product_id: str, item: dict[str, Any] | None = None) -> str:
        """품목에 적힌 이름 → 쇼핑몰 상품 목록 이름 → 상품 번호 순으로 찾는다."""
        named = (item or {}).get("title") or (item or {}).get("name")
        if named:
            return str(named)
        names = self.memo("product_names", self._product_names)
        return names.get(product_id, product_id) if _hashable(product_id) else product_id

    def _product_names(self) -> dict[Any, str]:
        names: dict[Any, str] = {}
        for p in self.products:
            pid = p.get("product_id")
            if _hashable(pid) and pid not in names:
                names[pid] = str(p.get("name") or pid)
        return names

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
        times, orders = self.memo("unconfirmed", self._unconfirmed)
        return orders[: bisect.bisect_left(times, end)]

    def _unconfirmed(self) -> tuple[list[datetime], list[dict[str, Any]]]:
        """팔린 주문 중 확정 전인 것만, 팔린 순서 그대로 (주마다 주문 전체를 다시 거르지 않게)."""
        times, orders = self.sold()
        rows = [(t, o) for t, o in zip(times, orders, strict=True) if o.get("delivered_at") is None]
        return [r[0] for r in rows], [r[1] for r in rows]

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
def _hashable(value: Any) -> bool:
    try:
        hash(value)
    except TypeError:
        return False
    return True


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
        self.refunds: dict[str, list[dict[str, Any]]] = {}  # 주문 → 그 주문의 판매자별 환불
        self.skipped = 0
        self.full_at: float | None = None
        self.lock = asyncio.Lock()

    def since(self, full: bool, recent: bool = False) -> datetime | None:
        """None이면 전체를 읽는다. `recent`면 전체 읽기 때가 지나도 최근 주문만(처음 한 번은
        전체)."""
        due = self.full_at is None or time.monotonic() - self.full_at >= FULL_REFRESH_SECONDS
        if full or self.full_at is None or (due and not recent):
            return None
        return (sc.now() - timedelta(days=RECENT_DAYS)).replace(microsecond=0)

    def merge(
        self, raw: list[Any], categories: dict[str, str], since: datetime | None
    ) -> tuple[list[dict[str, Any]], int]:
        """주문 목록과 건너뛴 수. 다시 받은 주문만 정규화하고 환불을 나눈다."""
        fresh: dict[str, dict[str, Any]] = {}
        skipped = 0
        for r in raw:
            norm = normalize_order(r, categories) if isinstance(r, dict) else None
            if norm is None:
                skipped += 1
            else:
                fresh[norm["order_id"]] = norm
        fresh_refunds = {k: refunds_from_orders([o]) for k, o in fresh.items()}
        if since is None:
            self.orders, self.skipped = fresh, skipped
            self.refunds = fresh_refunds
            self.full_at = time.monotonic()
        else:
            kept = {k: o for k, o in self.orders.items() if o["created_at"] < since}
            self.orders = kept | fresh
            self.refunds = {k: self.refunds.get(k) or [] for k in kept} | fresh_refunds
            self.skipped += skipped
        return list(self.orders.values()), self.skipped

    def all_refunds(self) -> list[dict[str, Any]]:
        """`refunds_from_orders(주문 목록)`과 같은 결과(같은 순서)를 모아 둔 나눔으로 만든다."""
        return [row for k in self.orders for row in self.refunds.get(k, ())]


_STORE = OrderStore()


def coupon_headers() -> dict[str, str] | None:
    """쿠폰 목록(읽기)에 보낼 판매 서버 연결 키. 없으면 None(쿠폰을 못 받은 장부가 된다).

    키 목록(`AX_SHOP_CLIENT_KEYS`)에 이 서비스(settlement) 키가 없으면 `COUPON_KEY_FALLBACK`(claims)
    키를 쓴다. 정산이 claims에서 나오면서 서버 설정을 바꾸지 않아도 되게 한다.
    """
    try:
        return shop_client._headers()
    except (shop_client.ShopError, RuntimeError):
        pass
    key = shop_client.client_keys().get(COUPON_KEY_FALLBACK)
    return {"Authorization": f"Bearer {key}"} if key else None


async def live_books(
    http_client: httpx.AsyncClient | None = None,
    store: OrderStore | None = None,
    full: bool = False,
    recent: bool = False,
) -> SettlementBooks:
    """shop-api에서 정산 장부를 모은다.

    `http_client`를 넘기면(테스트) 따로 모아 둔 주문 없이 읽는다. `recent`면 전체 읽기 때가 지났어도
    최근 주문만 다시 읽는다(처음 한 번은 전체).
    """
    client = http_client or httpx.AsyncClient()
    if store is None:
        store = _STORE if http_client is None else OrderStore()
    async with store.lock:
        since = store.since(full, recent)
        shop = config.agent_url("shop-api")
        try:
            orders_raw = await _get_orders(client, shop, since)
            products = await _get(client, f"{shop}/api/shop/products")
            sellers = await _get(client, f"{shop}/api/shop/sellers")
            headers = coupon_headers()
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
            refunds = await asyncio.to_thread(store.all_refunds)
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
# 조회는 저장된 장부로 바로 답한다(오래됐으면 뒤에서 새로 읽기를 하나만 띄운다). 서버를 켠 뒤 첫
# 장부가
# 아직 없으면 `PREPARING_WAIT_SECONDS`만 기다리고 `Preparing`을 낸다(화면은 "준비 중").
# 판매자 200곳·주문 30만 건에서 첫 전체 읽기는 몇 분 걸릴 수 있다. 그동안 30초씩 붙잡지 않는다
PREPARING_WAIT_SECONDS = 5.0
FRESH_WAIT_SECONDS = 20.0  # 지급 요청이 새 장부를 기다리는 최대 시간


class Preparing(Exception):
    """첫 장부를 아직 모으는 중이다(또는 지급 요청에 쓸 새 장부가 아직 안 끝났다)."""


_cache: tuple[float, SettlementBooks] | None = None
_inflight: asyncio.Task[SettlementBooks] | None = None
# 테스트가 장부를 바꿔 끼운다 (None이면 shop-api에서 읽는다)
LOADER: Callable[[bool], Any] | None = None


def clear_cache() -> None:
    global _cache, _inflight, _STORE
    _cache, _inflight, _STORE = None, None, OrderStore()


async def _collect(recent: bool) -> SettlementBooks:
    """장부를 모아 미리 정렬·계산해 두고, 다 되면 캐시를 바꾼다. 못 받으면 직전 장부를 그대로
    둔다."""
    global _cache
    books = await live_books(recent=recent)
    if books.unavailable_core():
        return books
    from support_agent import settlement  # 순환 import를 피한다 (settlement가 이 모듈을 쓴다)

    await asyncio.to_thread(settlement.warm, books)
    _cache = (time.monotonic(), books)
    return books


def _log_failure(task: asyncio.Task[SettlementBooks]) -> None:
    if not task.cancelled() and task.exception() is not None:
        logger.warning("정산 장부를 새로 읽지 못함: %s", type(task.exception()).__name__)


def _start(recent: bool) -> asyncio.Task[SettlementBooks]:
    """새로 읽기를 띄운다. 이미 돌고 있으면 그것을 쓴다(한 번에 하나)."""
    global _inflight
    if _inflight is None or _inflight.done():
        _inflight = asyncio.create_task(_collect(recent))
        _inflight.add_done_callback(_log_failure)
    return _inflight


def prime() -> asyncio.Task[SettlementBooks] | None:
    """서버를 켤 때 한 번: 뒤에서 첫 장부를 모으고 미리 계산해 둔다. 테스트 장부면 띄우지 않는다."""
    if LOADER is not None:
        return None
    return _start(recent=False)


async def _wait(task: asyncio.Task[SettlementBooks], seconds: float) -> SettlementBooks:
    try:
        return await asyncio.wait_for(asyncio.shield(task), seconds)
    except TimeoutError as exc:
        raise Preparing("판매자 정산 장부를 모으는 중이다") from exc


async def load_books(fresh: bool = False) -> SettlementBooks:
    """정산 장부.

    - 조회: 저장된 장부로 바로 답하고, `LIVE_CACHE_SECONDS`가 지났으면 뒤에서 새로 읽는다.
      첫 장부가 아직 없으면 잠깐 기다리고 `Preparing`
    - 지급 요청(`fresh`): `FRESH_SECONDS` 안에 모은 장부만 쓴다. 오래됐으면 최근 주문만 다시 읽는다
      (이미 돌고 있는 읽기가 있으면 그것을 기다린다)
    """
    if LOADER is not None:
        return await LOADER(fresh)
    cached = _cache
    age = time.monotonic() - cached[0] if cached else None
    if fresh:
        if cached and age is not None and age < FRESH_SECONDS:
            return cached[1]
        return await _wait(_start(recent=True), FRESH_WAIT_SECONDS)
    if cached:
        if age is not None and age >= LIVE_CACHE_SECONDS:
            _start(
                recent=False
            )  # 전체 읽기 때가 됐으면 전체, 아니면 최근 주문만 (OrderStore.since)
        return cached[1]
    return await _wait(_start(recent=False), PREPARING_WAIT_SECONDS)
