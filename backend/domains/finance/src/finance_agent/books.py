"""재무 장부 자료 모으기: 주문·결제·환불·쿠폰·판매자·관문 기록 + 목 PG 정산.

- 목 모드(`FINANCE_MOCK=1` 또는 `AGENT_TEMPLATE_MOCK=1`): 패키지 시드 `finance_books_seed.json`.
  시드의 날짜는 오늘(UTC) 기준 오프셋(`day`)이라 언제 띄워도 최근 2주치가 보인다. 금액은 고정이다.
- 실제 모드: shop-api `GET /api/shop/orders`·`/products`·`/sellers`·`/coupons`(재무 토큰),
  safety `GET /api/safety/executions`. 판매 사이트 DB에는 붙지 않는다.
- 못 받은 자료는 비워 두고 `unavailable`에 이름을 적는다. 추정값을 만들지 않는다(fail-closed).
- 조회는 캐시가 지나도 `LIVE_STALE_SECONDS` 안이면 직전 장부를 주고 뒤에서 새로 읽는다.
  결산 확정·정산 요청처럼 사람이 결정하는 일은 새 장부를 기다린다.
- 실제 모드 장부는 `LIVE_CACHE_SECONDS`초 동안 다시 쓴다. 주문 전체를 매번 읽으면 동시에 여러 번
  부를 때 줄을 서서, 같은 때 들어온 요청은 한 번 모은 결과를 같이 쓴다. 못 받은 자료가 있는
  장부는 캐시에 두지 않는다(다음 요청이 바로 다시 받아 본다). 언제 모은 자료인지는 `collected_at`.
- 주문은 서버가 모아 두고(`OrderStore`) 평소에는 최근 `RECENT_DAYS`일 주문만 다시 받는다.
  오래된 주문의 바뀐 점은 `FULL_REFRESH_SECONDS`마다, 사람이 결정하는 일은 그때 전체를 다시 읽는다.
  주문을 장부 모양으로 바꾸는 일과 화면 계산은 스레드에서 해서, 그동안에도 상태 확인·자기 테스트에
  바로 답한다(2026-11-06 서버: 주문 18만 건에 `/health`가 19초, 자기 테스트가 연결 실패로 떴다).
- PG 정산은 실제 PG가 없어서 두 모드 모두 결제에서 **토스 정산 형식 목**을 만든다
  (`amount`, `fee`, `pay_out_amount`, `sold_date`, `paid_out_date`). 데모용 불일치를 일부러 넣는다.

모든 계산은 코드가 한다. 이 모듈은 자료를 같은 모양으로 맞추기만 한다.
"""

from __future__ import annotations

import asyncio
import bisect
import json
import logging
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from importlib import resources
from typing import Any
from urllib.parse import urlencode

import httpx

from ax_agent_template.config import agent_token, agent_url, is_mock
from finance_agent import finance_config as fc

logger = logging.getLogger("ax.finance.books")

CALL_SECONDS = 5.0
# 주문은 전체를 한 번에 읽어서 쌓일수록 오래 걸린다 (#421: 33,850건 22MB에 8초).
# 부르는 쪽이 먼저 끊어도 끝까지 모아 캐시에 두므로(`cached_live_books`) 다음 요청은 값을 받는다
ORDERS_CALL_SECONDS = 30.0
LIVE_CACHE_SECONDS = 30.0
# 캐시가 지나도 이 시간 안이면 조회는 직전 장부를 바로 주고 뒤에서 새로 읽는다
LIVE_STALE_SECONDS = 300.0
# 평소 새로 읽기는 접수 시각이 최근 이 날수 안인 주문만 받는다(shop-api `since`). 주문이 3초마다
# 쌓여 전체를 30초마다 다시 읽으면 재무 서버가 계산에 묶인다(2026-11-06 서버, 18만 건)
RECENT_DAYS = 3
# 오래된 주문의 바뀐 점(환불·배송 완료)은 이 간격마다 전체를 다시 읽어 맞춘다
FULL_REFRESH_SECONDS = 600.0
SEED_FILE = "finance_books_seed.json"


@dataclass
class Books:
    """한 번 모은 장부 자료. 금액은 정수 원, 시각은 UTC datetime."""

    orders: list[dict[str, Any]] = field(default_factory=list)
    sellers: list[dict[str, Any]] = field(default_factory=list)
    refunds: list[dict[str, Any]] = field(default_factory=list)
    coupons: list[dict[str, Any]] = field(default_factory=list)
    executions: list[dict[str, Any]] | None = None
    pg_overrides: dict[str, dict[str, Any]] = field(default_factory=dict)
    source: str = "seed"
    mock: bool = True
    collected_at: datetime = field(default_factory=fc.now)
    unavailable: list[str] = field(default_factory=list)
    notes: dict[str, str] = field(default_factory=dict)
    # 같은 장부로 여러 번 하는 무거운 계산(결제 목록·PG 정산)을 한 번만 한다. 실제 모드만 쓴다
    _memo: dict[Any, Any] = field(default_factory=dict, repr=False, compare=False)

    def memo(self, key: Any, make: Callable[[], Any]) -> Any:
        """실제 모드 장부는 모은 뒤 바뀌지 않으므로 계산 결과를 장부에 붙여 둔다.

        목 모드 장부는 테스트가 고쳐 쓰기도 하고 작아서 매번 새로 계산한다. 주문 수도 키에 넣어
        누가 주문을 덧붙여도 예전 결과를 쓰지 않는다. 돌려준 값은 읽기만 한다.
        """
        if self.mock:
            return make()
        k = (key, len(self.orders))
        if k not in self._memo:
            self._memo[k] = make()
        return self._memo[k]

    # ---- 찾기
    def seller_name(self, seller_id: str) -> str:
        for s in self.sellers:
            if s.get("seller_id") == seller_id:
                return str(s.get("name") or seller_id)
        return seller_id

    def order(self, order_id: str) -> dict[str, Any] | None:
        return next((o for o in self.orders if o["order_id"] == order_id), None)

    def payments(self) -> list[dict[str, Any]]:
        """성공한 결제 목록 (주문 정보를 붙여서). 읽기만 한다."""
        return self.memo("payments", self._payments)

    def _payments(self) -> list[dict[str, Any]]:
        rows = []
        for o in self.orders:
            for p in o.get("payments", []):
                if p.get("status") == "succeeded" and isinstance(p.get("amount"), int):
                    rows.append(p | {"order": o})
        return rows

    def sold(self) -> tuple[list[datetime], list[dict[str, Any]]]:
        """팔린 주문(성공한 결제가 있는 주문)을 첫 성공 결제 시각 순으로. (시각 목록, 주문 목록)."""
        return self.memo("sold", self._sold)

    def _sold(self) -> tuple[list[datetime], list[dict[str, Any]]]:
        rows = [(t, o) for o in self.orders if (t := paid_at(o)) is not None]
        rows.sort(key=lambda r: r[0])
        return [r[0] for r in rows], [r[1] for r in rows]

    def sold_between(self, start: datetime, end: datetime) -> list[dict[str, Any]]:
        """첫 성공 결제 시각이 [start, end)인 팔린 주문. 결제 시각 순이다.

        `order_sold(o) and in_range(paid_at(o), start, end)`로 전체를 훑는 것과 같은 주문을 고른다.
        """
        times, orders = self.sold()
        return orders[bisect.bisect_left(times, start) : bisect.bisect_left(times, end)]

    def unavailable_core(self) -> bool:
        """주문·쿠폰처럼 숫자를 내는 데 꼭 필요한 자료를 못 받았는가."""
        return any(name in self.unavailable for name in ("orders", "coupons"))

    def meta(self, extra_unavailable: list[str] | None = None) -> dict[str, Any]:
        missing = list(dict.fromkeys([*self.unavailable, *(extra_unavailable or [])]))
        meta: dict[str, Any] = {
            "mock": self.mock,
            "collected_at": fc.iso(self.collected_at),
            "source": self.source,
            "unavailable": missing,
        }
        if self.notes:
            meta["notes"] = dict(self.notes)
        return meta


# ---------------------------------------------------------------- 공통 변환
def _int(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def item_gross(item: dict[str, Any]) -> int:
    return (_int(item.get("qty")) or 0) * (_int(item.get("unit_price")) or 0)


def order_sold(order: dict[str, Any]) -> bool:
    """성공한 결제가 있는 주문만 매출로 본다."""
    return any(p.get("status") == "succeeded" for p in order.get("payments", []))


def paid_at(order: dict[str, Any]) -> datetime | None:
    """첫 성공 결제 시각. 성공한 결제가 없으면 None (팔리지 않은 주문)."""
    times = [p["created_at"] for p in order.get("payments", []) if p.get("status") == "succeeded"]
    return min(times) if times else None


def in_range(dt: datetime | None, start: datetime, end: datetime) -> bool:
    return dt is not None and start <= dt < end


# ---------------------------------------------------------------- 목 (시드)
def _at(today: date, day: int, hhmm: str) -> datetime:
    hour, minute = (int(x) for x in hhmm.split(":"))
    return fc.day_start(today + timedelta(days=day)) + timedelta(hours=hour, minutes=minute)


def read_seed() -> dict[str, Any]:
    return json.loads(resources.files("finance_agent").joinpath(SEED_FILE).read_text("utf-8"))


def seed_books(today: date | None = None, seed: dict[str, Any] | None = None) -> Books:
    """시드를 오늘 기준 날짜로 펼친다. 같은 날짜면 결과가 항상 같다."""
    today = today or fc.today()
    seed = seed if seed is not None else read_seed()
    refunds_by_order: dict[str, int] = {}
    refunds = []
    for r in seed.get("refunds", []):
        row = {k: v for k, v in r.items() if k not in ("day", "time")}
        row["created_at"] = _at(today, r["day"], r["time"])
        refunds.append(row)
        refunds_by_order[r["order_id"]] = refunds_by_order.get(r["order_id"], 0) + r["amount"]

    orders = []
    for o in seed.get("orders", []):
        created = _at(today, o["day"], o["time"])
        items = [
            i | {"paid_amount": item_gross(i) - (_int(i.get("points_share")) or 0)}
            for i in o["items"]
        ]
        total = sum(i["paid_amount"] for i in items) + o.get("shipping_fee", 0)
        delivered = o.get("delivered_day")
        orders.append(
            {
                "order_id": o["order_id"],
                "buyer_id": o["buyer_id"],
                "status": "paid",
                "total_amount": total,
                "refunded_amount": refunds_by_order.get(o["order_id"], 0),
                "shipping_fee": o.get("shipping_fee", 0),
                "points_used": o.get("points_used", 0),
                "created_at": created,
                "delivered_at": _at(today, delivered, "08:00") if delivered is not None else None,
                "items": items,
                "payments": [
                    {
                        "payment_id": o["payment_id"],
                        "amount": total,
                        "method_code": "card",
                        "status": "succeeded",
                        "created_at": created,
                    }
                ],
            }
        )

    coupons = []
    for c in seed.get("coupons", []):
        row = {k: v for k, v in c.items() if k not in ("day", "time")}
        row["created_at"] = _at(today, c["day"], c["time"])
        coupons.append(row)

    executions = []
    for e in seed.get("executions", []):
        executions.append(
            {
                "execution_id": e["execution_id"],
                "created_at": _at(today, e["day"], e["time"]),
                "action": e["action"],
                "requester": e["requester"],
                "context_id": e["context_id"],
                "params": e["params"],
                "status": e["status"],
                "grade": e["grade"],
                "hold_reason": e.get("hold_reason"),
                "computed_amount": e.get("computed_amount"),
                "review_id": e.get("review_id"),
                "result_ref": e.get("result_ref"),
                "idempotency_key": e.get("idempotency_key"),
            }
        )

    return Books(
        orders=orders,
        sellers=list(seed.get("sellers", [])),
        refunds=refunds,
        coupons=coupons,
        executions=executions,
        pg_overrides=dict(seed.get("pg_overrides", {})),
        source="seed",
        mock=True,
        collected_at=fc.now(),
    )


def seed_incidents(today: date | None = None) -> dict[str, dict[str, Any]]:
    """목 모드 사건 정보(시나리오 A 시연용): context_id → {component, window_from, window_to}."""
    today = today or fc.today()
    out: dict[str, dict[str, Any]] = {}
    for row in read_seed().get("incidents", []):
        out[row["context_id"]] = {
            "component": row.get("component"),
            "window_from": _at(today, row["day"], row["from"]),
            "window_to": _at(today, row["day"], row["to"]),
        }
    return out


# ---------------------------------------------------------------- 실제 (shop-api, safety)
async def _get(
    client: httpx.AsyncClient,
    url: str,
    headers: dict | None = None,
    timeout: float = CALL_SECONDS,
) -> Any:
    """`{data, meta}` 응답의 data. 실패하면 None."""
    try:
        response = await client.get(url, headers=headers or {}, timeout=timeout)
    except httpx.HTTPError:
        return None
    if response.status_code != 200:
        return None
    try:
        body = response.json()
    except ValueError:
        return None
    return body.get("data") if isinstance(body, dict) else None


def normalize_order(raw: dict[str, Any], categories: dict[str, str]) -> dict[str, Any] | None:
    """shop-api 주문 응답 → 장부 주문. 형식이 다르면 None (그 주문은 빼고 unavailable에 적는다)."""
    created = fc.parse_dt(raw.get("created_at"))
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
        norm_payments.append(p | {"created_at": fc.parse_dt(p.get("created_at")) or created})
    return {
        "order_id": raw["order_id"],
        "buyer_id": raw.get("buyer_id"),
        "status": raw.get("status"),
        "total_amount": _int(raw.get("total_amount")) or 0,
        "refunded_amount": _int(raw.get("refunded_amount")) or 0,
        "shipping_fee": _int(raw.get("shipping_fee")) or 0,
        "points_used": _int(raw.get("points_used")) or 0,
        "created_at": created,
        "delivered_at": fc.parse_dt(raw.get("delivered_at")),
        "items": norm_items,
        "payments": norm_payments,
    }


def refunds_from_orders(orders: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """shop-api 주문의 `refunded_amount`를 환불 기록으로 옮긴다.

    shop-api 주문 응답에는 환불 시각이 없어서 주문 시각으로 둔다(`notes.refund_basis`).
    판매자는 품목 결제 금액 비율로 나눈다(나머지는 마지막 품목).
    """
    rows = []
    for o in orders:
        amount = o["refunded_amount"]
        if not amount:
            continue
        paid_items = [i for i in o["items"] if i.get("paid_amount")]
        total_paid = sum(i["paid_amount"] for i in paid_items) or 1
        left = amount
        for idx, item in enumerate(paid_items):
            share = (
                left if idx == len(paid_items) - 1 else amount * item["paid_amount"] // total_paid
            )
            left -= share
            if share:
                rows.append(
                    {
                        "refund_id": f"rfd_of_{o['order_id']}_{idx}",
                        "order_id": o["order_id"],
                        "payment_id": next(
                            (p["payment_id"] for p in o["payments"] if p.get("payment_id")), None
                        ),
                        "buyer_id": o.get("buyer_id"),
                        "seller_id": item.get("seller_id"),
                        "amount": share,
                        "created_at": o["created_at"],
                        "execution_id": None,
                    }
                )
    return rows


def normalize_execution(raw: dict[str, Any]) -> dict[str, Any] | None:
    """safety `ExecutionRecord` → 장부 관문 기록."""
    request, decision = raw.get("request"), raw.get("decision")
    created = fc.parse_dt(raw.get("created_at"))
    if not isinstance(request, dict) or not isinstance(decision, dict) or created is None:
        return None
    return {
        "execution_id": raw.get("execution_id"),
        "created_at": created,
        "action": request.get("action"),
        "requester": request.get("requester"),
        "context_id": request.get("context_id"),
        "params": request.get("params") or {},
        "status": decision.get("status"),
        "grade": decision.get("grade"),
        "hold_reason": decision.get("hold_reason"),
        "computed_amount": decision.get("computed_amount"),
        "review_id": decision.get("review_id"),
        "result_ref": decision.get("result_ref"),
        "idempotency_key": request.get("idempotency_key"),
    }


class OrderStore:
    """실제 모드 주문을 모아 두고, 평소에는 최근 `RECENT_DAYS`일 주문만 다시 받아 바꿔 끼운다.

    - 전체 읽기: 처음, `FULL_REFRESH_SECONDS`가 지났을 때, 사람이 결정하는 일(`full=True`)
    - 최근 읽기: 접수 시각이 `since` 이후인 주문을 받아, 모아 둔 주문 중 같은 구간을 통째로 바꾼다
      (그사이 지워진 주문도 빠진다). 그보다 오래된 주문의 바뀐 점은 다음 전체 읽기 때 맞춘다
    - 주문을 장부 모양으로 바꾸는 일은 스레드에서 한다(서버가 다른 요청에 바로 답하도록)
    """

    def __init__(self) -> None:
        self.orders: dict[str, dict[str, Any]] = {}
        self.skipped: list[datetime | None] = []  # 모양이 달라 뺀 주문의 접수 시각
        self.full_at: float | None = None
        self.lock = asyncio.Lock()

    def since(self, full: bool) -> datetime | None:
        """이번에 받을 구간의 시작. None이면 전체."""
        if full or self.full_at is None or time.monotonic() - self.full_at >= FULL_REFRESH_SECONDS:
            return None
        return (fc.now() - timedelta(days=RECENT_DAYS)).replace(microsecond=0)

    def merge(
        self, raw: list[Any], categories: dict[str, str], since: datetime | None
    ) -> tuple[list[dict[str, Any]], int]:
        """받은 주문을 끼워 넣고 (전체 주문, 뺀 주문 수). 스레드에서 부른다."""
        fresh: dict[str, dict[str, Any]] = {}
        skipped: list[datetime | None] = []
        for r in raw:
            norm = normalize_order(r, categories) if isinstance(r, dict) else None
            if norm is None:
                skipped.append(fc.parse_dt(r.get("created_at")) if isinstance(r, dict) else None)
            else:
                fresh[norm["order_id"]] = norm
        if since is None:
            self.orders, self.skipped = fresh, skipped
            self.full_at = time.monotonic()
        else:
            kept = {k: o for k, o in self.orders.items() if o["created_at"] < since}
            self.orders = kept | fresh
            self.skipped = [t for t in self.skipped if t is not None and t < since] + skipped
        return list(self.orders.values()), len(self.skipped)


_STORE = OrderStore()


async def live_books(
    http_client: httpx.AsyncClient | None = None,
    store: OrderStore | None = None,
    full: bool = False,
) -> Books:
    """shop-api·safety에서 장부를 모은다.

    `http_client`를 넘기면(테스트·한 번 쓰기) 따로 모아 둔 주문 없이 전체를 읽는다. 넘기지 않으면
    서버가 모아 둔 주문(`_STORE`)에 최근 주문만 받아 끼운다.
    """
    client = http_client or httpx.AsyncClient()
    unavailable: list[str] = []
    if store is None:
        store = _STORE if http_client is None else OrderStore()
    async with store.lock:
        return await _live_books(client, http_client is None, store, full, unavailable)


async def _live_books(
    client: httpx.AsyncClient,
    own_client: bool,
    store: OrderStore,
    full: bool,
    unavailable: list[str],
) -> Books:
    since = store.since(full)
    try:
        shop, safety = agent_url("shop-api"), agent_url("safety")
        query = "" if since is None else "?" + urlencode({"since": fc.iso(since)})
        orders_raw = await _get(
            client, f"{shop}/api/shop/orders{query}", timeout=ORDERS_CALL_SECONDS
        )
        products = await _get(client, f"{shop}/api/shop/products")
        sellers = await _get(client, f"{shop}/api/shop/sellers")
        try:
            headers = {"Authorization": f"Bearer {agent_token('finance')}"}
        except RuntimeError:
            headers = {}
        coupons_raw = await _get(client, f"{shop}/api/shop/coupons", headers) if headers else None
        executions_raw = await _get(client, f"{safety}/api/safety/executions")
    finally:
        if own_client:
            await client.aclose()

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
    coupons = []
    if isinstance(coupons_raw, list):
        for c in coupons_raw:
            if isinstance(c, dict):
                coupons.append(
                    c
                    | {
                        "created_at": fc.parse_dt(c.get("created_at")),
                        "funded_by": c.get("funded_by") or "company",
                        "seller_id": c.get("seller_id"),
                    }
                )
    else:
        unavailable.append("coupons")
    executions = None
    if isinstance(executions_raw, list):
        executions = [e for e in map(_norm_exec, executions_raw) if e is not None]
    else:
        unavailable.append("executions")
    return Books(
        orders=orders,
        sellers=sellers if isinstance(sellers, list) else [],
        refunds=refunds,
        coupons=coupons,
        executions=executions,
        source="shop-api",
        mock=False,
        collected_at=fc.now(),
        unavailable=unavailable,
        notes={"refund_basis": "order_created_at"},
    )


def _norm_exec(raw: Any) -> dict[str, Any] | None:
    return normalize_execution(raw) if isinstance(raw, dict) else None


_live_cache: tuple[float, Books] | None = None  # (모은 시각 monotonic, 장부)
_live_inflight: asyncio.Task[Books] | None = None


def clear_live_cache() -> None:
    global _live_cache, _live_inflight, _STORE
    _live_cache, _live_inflight, _STORE = None, None, OrderStore()


def warm(books: Books) -> None:
    """새 장부로 여러 화면이 같이 쓰는 계산(팔린 주문 순서·결제·PG 정산·최근 7일 대사)을 미리 한다.

    스레드에서 부른다. 캐시에 넣기 전에 해 두면 화면 요청은 이미 만든 결과를 쓴다.
    """
    from finance_agent import reconciliation  # 순환 import를 피한다

    books.sold()
    books.payments()
    pg_settlements(books)
    reconciliation.reconcile(books, None, {})


async def off(fn: Callable[..., Any], /, *args: Any, **kwargs: Any) -> Any:
    """무거운 계산을 스레드에서 돌린다. 그동안 서버는 다른 요청(상태 확인·자기 테스트)에 답한다."""
    return await asyncio.to_thread(fn, *args, **kwargs)


async def _collect_live(full: bool = False) -> Books:
    global _live_cache
    books = await (live_books(full=True) if full else live_books())
    if books.unavailable_core():
        _live_cache = None
    else:
        await off(warm, books)
        _live_cache = (time.monotonic(), books)
    return books


def _log_refresh_failure(task: asyncio.Task[Books]) -> None:
    """아무도 기다리지 않는 뒤쪽 새로 읽기의 실패도 남긴다. 다음 요청은 다시 읽어 본다."""
    if not task.cancelled() and task.exception() is not None:
        logger.warning("재무 장부를 새로 읽지 못함: %s", type(task.exception()).__name__)


async def cached_live_books(fresh: bool = False) -> Books:
    """실제 모드 장부. 캐시가 살아 있으면 그대로, 모으는 중이면 같은 결과를 기다린다.

    주문이 쌓이면 새로 읽는 데 몇 초가 걸린다. 조회(`fresh=False`)는 캐시가 지나도
    `LIVE_STALE_SECONDS` 안이면 직전 장부를 바로 주고 뒤에서 새로 읽는다. 언제 모은 자료인지는
    `collected_at`에 나온다. 사람이 결정하는 일(`fresh=True`)은 주문 전체를 새로 읽은 장부를
    기다린다.
    """
    global _live_inflight
    if fresh:
        _live_inflight = asyncio.create_task(_collect_live(full=True))
        _live_inflight.add_done_callback(_log_refresh_failure)
        return await asyncio.shield(_live_inflight)
    cached = _live_cache
    age = time.monotonic() - cached[0] if cached else None
    if cached and age is not None and age < LIVE_CACHE_SECONDS:
        return cached[1]
    if _live_inflight is None or _live_inflight.done():
        _live_inflight = asyncio.create_task(_collect_live())
        _live_inflight.add_done_callback(_log_refresh_failure)
    if cached and not fresh and age is not None and age < LIVE_STALE_SECONDS:
        return cached[1]
    return await asyncio.shield(_live_inflight)


async def load_books(fresh: bool = False) -> Books:
    """모드에 맞게 장부를 모은다. `fresh`면 직전 장부로 답하지 않고 새로 읽은 것을 기다린다."""
    if is_mock("finance"):
        return seed_books()
    return await cached_live_books(fresh)


# ---------------------------------------------------------------- 목 PG 정산 (토스 정산 형식)
def _live_overrides(settled_ids: list[str]) -> dict[str, dict[str, Any]]:
    """실제 모드에서 데모 불일치를 넣을 결제. 결제 번호 순서의 정해진 자리라 매번 같다."""
    plan = {3: {"kind": "mismatch", "delta": -2000}, 7: {"kind": "missing"}}
    plan |= {11: {"kind": "fee", "fee_rate": 0.035}, 13: {"kind": "mismatch", "delta": 1000}}
    ordered = sorted(settled_ids)
    return {ordered[i]: spec for i, spec in plan.items() if i < len(ordered)}


def pg_settlements(books: Books, today: date | None = None) -> dict[str, dict[str, Any]]:
    """결제별 목 PG 정산 기록. PG에 없는 결제는 키가 없다.

    - `sold_date` = 결제일, `paid_out_date` = 판매일 + `payout_days`(지급 예정일)
    - 지급 예정일이 오늘 이후면 아직 정산 전(`paid_out_date`는 예정일, `settled` False)
    - 같은 장부·같은 날이면 한 번만 만든다(결산·대사·현금 흐름이 같은 요청 안에서 여러 번 부른다).
      돌려준 값은 읽기만 한다
    """
    cfg = fc.load_config().pg
    today = today or fc.today()
    return books.memo(("pg", today, id(cfg)), lambda: _pg_settlements(books, today, cfg))


def _pg_settlements(books: Books, today: date, cfg: Any) -> dict[str, dict[str, Any]]:
    payments = books.payments()
    settled_ids = [
        p["payment_id"]
        for p in payments
        if p["created_at"].date() + timedelta(days=cfg.payout_days) <= today
    ]
    overrides = books.pg_overrides if books.mock else _live_overrides(settled_ids)
    out: dict[str, dict[str, Any]] = {}
    for p in payments:
        sold = p["created_at"].date()
        payout = sold + timedelta(days=cfg.payout_days)
        settled = payout <= today
        spec = overrides.get(p["payment_id"], {}) if settled else {}
        if spec.get("kind") == "missing":
            continue
        amount = (
            p["amount"] + int(spec.get("delta", 0))
            if spec.get("kind") == "mismatch"
            else p["amount"]
        )
        fee = fc.rate_amount(amount, float(spec.get("fee_rate", cfg.fee_rate)))
        out[p["payment_id"]] = {
            "payment_id": p["payment_id"],
            "order_id": p["order"]["order_id"],
            "amount": amount,
            "fee": fee,
            "pay_out_amount": amount - fee,
            "sold_date": sold.isoformat(),
            "paid_out_date": payout.isoformat(),
            "settled": settled,
        }
    return out
