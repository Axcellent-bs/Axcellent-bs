"""평가 시나리오의 쇼핑몰 데이터를 실제 shop-api 코드로 만든다.

- 판매자·상품·구매자: shop-api 시드(`ax_shop_api.seed.seed`) 그대로. 재고만 넉넉히 늘린다.
- 주문·결제: shop-api `Shop.create_order`·`Shop.pay`로 넣는다. 시각은 shop-api의 `_now`를
  시나리오 시계로 바꿔 끼워 정한다.
- 결제 장애: shop-api 장애 주입(`Shop.inject_fault("failure")`·`release_fault`) 그대로다.
  세기(`fraction`)는 장애 구간 주문 중 장애가 걸린 채 결제한 비율이다.
- 카드 거절(평상시 실패): `simulate="failure"`로 일정 간격마다 하나씩 넣는다 (`PG_DECLINED`).

정답(실패 주문 수·금액·영향 고객, 이상 시작 시각)은 data 에이전트 코드가 아니라 여기서 만든
결제 기록에서 바로 센다(`Truth`).

5분마다 주문 수는 시간대 수준을 평균으로 한 포아송 분포다(실제 주문처럼 흔들린다).
난수는 시나리오 id로 씨앗을 고정한 `random.Random`만 쓴다. 같은 시나리오면 늘 같은 데이터다.
"""

from __future__ import annotations

import contextlib
import json
import math
import random
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx

from ax_contracts.data_access.shop_failed_orders import ShopFailedOrdersQuery
from ax_contracts.data_access.shop_metric_series import ShopMetricSeriesQuery
from ax_shop_api import service
from ax_shop_api.db import Db
from ax_shop_api.lookups import Lookups
from ax_shop_api.seed import BUYER_COUNT, PRODUCTS, seed, seed_buyer_id

BUCKET = timedelta(minutes=5)
HISTORY_DAYS = 7  # 기준선: 지난 7일 같은 시간대
DECLINE_EVERY = 37  # 평상시 카드 거절: 37건에 1건
# 시간대(UTC 시)별 주문 수준. data 목 시드와 같은 모양이다
HOUR_LEVEL = [
    0.3, 0.2, 0.2, 0.2, 0.3, 0.5, 0.8, 1.0, 1.2, 1.4, 1.5, 1.5,
    1.4, 1.3, 1.3, 1.2, 1.2, 1.3, 1.5, 1.6, 1.4, 1.1, 0.8, 0.5,
]  # fmt: skip


def parse(text: str) -> datetime:
    return datetime.fromisoformat(text.replace("Z", "+00:00")).astimezone(UTC)


def iso(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class Payment:
    order_id: str
    buyer_id: str
    amount: int
    at: datetime
    failure_code: str | None  # None이면 성공


@dataclass
class World:
    """한 시나리오의 쇼핑몰. `transport`로 data 클라이언트가 실제 조회 코드를 부른다."""

    spec: dict[str, Any]
    db: Db
    payments: list[Payment] = field(default_factory=list)

    @property
    def window(self) -> tuple[datetime, datetime]:
        return parse(self.spec["window"]["from"]), parse(self.spec["window"]["to"])

    def transport(self) -> httpx.MockTransport:
        """shop-api `/api/shop/failed-orders`·`/metric-series`와 같은 응답을 준다.

        응답은 shop-api와 같은 조회 코드(`Lookups`)로 만든다.
        """
        lookups = Lookups(self.db, now=lambda: self.window[1])

        def handle(request: httpx.Request) -> httpx.Response:
            params = request.url.params
            path = request.url.path
            if path.endswith("/failed-orders"):
                query = ShopFailedOrdersQuery(
                    from_=parse(params["from"]),
                    to=parse(params["to"]),
                    category=params.get("category"),
                    limit=int(params.get("limit", 1000)),
                )
                data = lookups.failed_orders(query).model_dump(mode="json")
            elif path.endswith("/metric-series"):
                query = ShopMetricSeriesQuery(
                    metric=params["metric"],
                    from_=parse(params["from"]),
                    to=parse(params["to"]),
                    bucket=params.get("bucket", "5m"),
                )
                data = lookups.metric_series(query).model_dump(mode="json")
            else:
                return httpx.Response(404, json={"error": {"code": "NOT_FOUND"}})
            return httpx.Response(200, content=json.dumps({"data": data, "meta": {}}))

        return httpx.MockTransport(handle)


@contextlib.contextmanager
def _clock() -> Iterator[list[datetime]]:
    """shop-api의 현재 시각(`service._now`)을 시나리오 시계로 바꿔 끼운다."""
    now: list[datetime] = [datetime(2026, 1, 1, tzinfo=UTC)]
    original = service._now
    service._now = lambda: iso(now[0])
    try:
        yield now
    finally:
        service._now = original


def _slots(window_from: datetime, window_to: datetime) -> list[datetime]:
    """분석 구간과, 기준선에 쓰는 지난 7일 같은 시간대 버킷 시작 시각."""
    slots: list[datetime] = []
    for day in range(HISTORY_DAYS, -1, -1):
        moment = window_from - timedelta(days=day)
        end = window_to - timedelta(days=day)
        while moment < end:
            slots.append(moment)
            moment += BUCKET
    return slots


def _in(moment: datetime, block: dict[str, Any] | None) -> bool:
    return bool(block) and parse(block["from"]) <= moment < parse(block["to"])


def _poisson(rng: random.Random, mean: float) -> int:
    """평균 `mean`의 포아송 난수 (Knuth). 같은 씨앗이면 늘 같은 값이다."""
    limit, count, product = math.exp(-mean), 0, rng.random()
    while product > limit:
        count += 1
        product *= rng.random()
    return count


def build(spec: dict[str, Any]) -> World:
    """시나리오 명세로 쇼핑몰 데이터를 만든다."""
    db = Db(":memory:")
    seed(db)
    with db.tx() as conn:  # 재고가 모자라 주문이 막히지 않게 늘린다 (가격·분류는 시드 그대로)
        conn.execute("UPDATE products SET stock = 1000000")
    world = World(spec=spec, db=db)
    window_from, window_to = world.window
    injection = spec.get("injection") or {"type": "none"}
    kind = injection["type"]
    traffic = spec.get("traffic") or {}
    per_bucket = float(traffic.get("orders_per_5m", 5))
    rng = random.Random(spec["id"])

    clock_seconds = [0.0]
    shop = service.Shop(db, clock=lambda: clock_seconds[0])
    order_no = 0
    with _clock() as now:
        for slot in _slots(window_from, window_to):
            in_window = window_from <= slot < window_to
            if kind == "empty" and (in_window or injection.get("history_empty")):
                continue  # 이 버킷에는 주문이 하나도 없다
            level = per_bucket * HOUR_LEVEL[slot.hour]
            if in_window and kind == "order_drop" and _in(slot, injection):
                level *= float(injection["factor"])
            if in_window and kind == "sale" and _in(slot, injection):
                level *= float(injection["factor"])
            count = _poisson(rng, level)  # 실제 주문처럼 5분마다 포아송으로 흔들린다
            for i in range(count):
                moment = slot + timedelta(seconds=int((i + 0.5) * 300 / max(count, 1)))
                now[0] = moment
                clock_seconds[0] = moment.timestamp()
                order_no += 1
                buyer = seed_buyer_id(rng.randint(1, BUYER_COUNT))
                product = PRODUCTS[rng.randrange(len(PRODUCTS))][0]
                order = shop.create_order(buyer, [(product, rng.choice((1, 1, 1, 2)))])
                faulty = (
                    in_window
                    and kind == "payment_failure"
                    and _in(moment, injection)
                    and rng.random() < float(injection.get("fraction", 1.0))
                )
                if faulty:
                    shop.inject_fault("failure")
                simulate = "failure" if order_no % DECLINE_EVERY == 0 else None
                paid = shop.pay(order["order_id"], "card", simulate)
                if faulty:
                    shop.release_fault()
                with db.tx() as conn:  # 방금 넣은 결제 기록 (정답은 여기서 센다)
                    pay = conn.execute(
                        "SELECT amount, status, failure_code FROM payments "
                        "WHERE order_id = ? ORDER BY rowid DESC LIMIT 1",
                        (paid["order_id"],),
                    ).fetchone()
                world.payments.append(
                    Payment(
                        order_id=paid["order_id"],
                        buyer_id=buyer,
                        amount=int(pay["amount"]),
                        at=moment,
                        failure_code=None if pay["status"] == "succeeded" else pay["failure_code"],
                    )
                )
    return world


@dataclass(frozen=True)
class Truth:
    """시나리오가 만든 결제 기록에서 바로 센 정답."""

    fault_failed_orders: int  # 장애(PG_UNAVAILABLE)로 실패한 주문
    fault_customers: int
    fault_amount: int  # 원
    all_failed_payments: int  # 카드 거절 포함 모든 실패 결제 (진단용)
    declined_payments: int  # 카드 거절(PG_DECLINED)
    onset: str | None  # 장애로 실패한 첫 결제가 든 5분 버킷 시작


def truth(world: World) -> Truth:
    window_from, window_to = world.window
    inside = [p for p in world.payments if window_from <= p.at < window_to]
    fault = [p for p in inside if p.failure_code == "PG_UNAVAILABLE"]
    failed = [p for p in inside if p.failure_code is not None]
    onset = None
    if fault:
        first = min(p.at for p in fault)
        onset = iso(first.replace(minute=first.minute - first.minute % 5, second=0))
    return Truth(
        fault_failed_orders=len(fault),
        fault_customers=len({p.buyer_id for p in fault}),
        fault_amount=sum(p.amount for p in fault),
        all_failed_payments=len(failed),
        declined_payments=sum(1 for p in failed if p.failure_code == "PG_DECLINED"),
        onset=onset,
    )
