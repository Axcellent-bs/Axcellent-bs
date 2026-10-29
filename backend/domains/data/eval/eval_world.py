"""평가 시나리오의 쇼핑몰 데이터를 실제 shop-api 코드로 만든다.

- 판매자·상품·구매자: shop-api 시드(`ax_shop_api.seed.seed`) 그대로. 재고만 넉넉히 늘린다.
- 주문·결제: shop-api `Shop.create_order`·`Shop.pay`로 넣는다. 시각은 shop-api의 `_now`를
  시나리오 시계로 바꿔 끼워 정한다.
- 결제 장애: shop-api 장애 주입(`Shop.inject_fault("failure")`·`release_fault`) 그대로다.
  세기(`fraction`)는 장애 구간 주문 중 장애가 걸린 채 결제한 비율이다.
- 카드 거절(평상시 실패): `simulate="failure"`로 일정 간격마다 하나씩 넣는다 (`PG_DECLINED`).
- 처음 보는 실패 코드(`injection.failure_code`): 장애로 실패한 결제의 `failure_code`를 그 코드로
  바꾼다 (shop-api가 새 코드를 쓰기 시작한 상황).
- 알려진 장애 쪽 코드(`injection.fault_code`, 예 `PG_TIMEOUT`): 장애로 실패한 결제의 코드를 그것으로
  바꾼다. 처음 보는 코드가 아니라서 영향 숫자는 낼 수 있어야 한다.
- 잡음(`type: fault_noise`): 장애 구간 없이 `at_minutes`분마다 결제 하나만 결제 서버 쪽에서
  실패한다(`fault_code`). `history`가 참이면 지난 7일 같은 시각에도 넣는다. 그 5분에 주문이
  없으면 잡음도 없다. 잡음은 장애가 아니라 정답의 장애 실패 수에 넣지 않는다.
- 다른 상황에 얹는 잡음(holdout 생성기): `noise_at_minutes`·`noise_history`·`noise_code`.
- 구간 전체 세일(holdout 생성기): `traffic.sale_factor`배로 주문이 늘어난다.

정답(실패 주문 수·금액·영향 고객, 이상 시작 시각)은 data 에이전트 코드가 아니라 여기서 만든
결제 기록에서 바로 센다(`Truth`).

- 영향 분석 정답(`impact_*`): 구간 안의 장애 쪽 실패 결제 전부 = 주입한 장애로 실패한 결제 +
  그 구간에 난 잡음 실패. 운영에서는 둘을 구분할 수 없고, 잡음으로 실패한 고객도 실제 결제 실패라
  보상 대상이다. 카드 거절(`PG_DECLINED`)은 결제한 사람 쪽 사유라 넣지 않는다.
- 주입한 장애만(`fault_*`): 예전 영향 분석 정답. 초과분 지표(`impact.excess_*`)의 정답이고,
  결제 장애의 장애 쪽 실패 0건·1건 채점(이상 아님·low 기록)에도 쓴다.

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
    faulty: bool = False  # 장애가 걸린 채 결제했는지 (정답은 이것으로 센다)
    noise: bool = False  # 잡음으로 결제 서버 쪽에서 실패했는지 (장애 아님)


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


def _slots(
    window_from: datetime, window_to: datetime, margin: timedelta = timedelta(0)
) -> list[datetime]:
    """분석 구간과, 기준선에 쓰는 지난 7일 같은 시간대 버킷 시작 시각.

    `margin`이 있으면 같은 시간대 앞뒤 `margin`까지 주문을 만든다(#131). 평소 실패율 λ는 같은 시각
    ±1시간을 보므로, 그 범위가 비어 있으면(0건 기록) λ가 실제보다 낮게 나온다. 그날(분석하는 날)은
    앞쪽만 만든다. 구간 뒤는 아직 오지 않은 시간이다. 앞쪽이 없으면 구간 첫 감시가 지난 날과 비교해
    주문 급감처럼 보인다.
    """
    slots: list[datetime] = []
    for day in range(HISTORY_DAYS, -1, -1):
        moment = window_from - timedelta(days=day) - margin
        end = window_to - timedelta(days=day) + (margin if day else timedelta(0))
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


def _noise_targets(injection: dict[str, Any], window_from: datetime) -> list[datetime]:
    """잡음(장애 쪽 실패 1건)을 넣을 시각. 구간 시작부터 `at_minutes`분 뒤.

    `history`가 참이면 지난 7일 같은 시각에도 넣는다 (매일 가끔 나는 결제사 타임아웃).
    """
    days = range(HISTORY_DAYS, -1, -1) if injection.get("history") else [0]
    return sorted(
        window_from - timedelta(days=day) + timedelta(minutes=int(m))
        for day in days
        for m in injection["at_minutes"]
    )


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
    if kind == "fault_noise":
        noise = _noise_targets(injection, window_from)
    elif injection.get("noise_at_minutes"):  # 다른 상황에 얹은 잡음 (holdout 생성기)
        noise = _noise_targets(
            {
                "at_minutes": injection["noise_at_minutes"],
                "history": injection.get("noise_history"),
            },
            window_from,
        )
    else:
        noise = []
    code = injection.get("failure_code") or injection.get("fault_code")
    noise_code = injection.get("noise_code") or code
    sale_factor = float(traffic.get("sale_factor", 1.0))  # 구간 전체 세일 (holdout 생성기)
    with _clock() as now:
        margin = timedelta(minutes=int(traffic.get("history_margin_minutes", 0)))
        for slot in _slots(window_from, window_to, margin):
            in_window = window_from <= slot < window_to
            if kind == "empty" and (in_window or injection.get("history_empty")):
                continue  # 이 버킷에는 주문이 하나도 없다
            level = per_bucket * HOUR_LEVEL[slot.hour]
            if in_window and kind == "order_drop" and _in(slot, injection):
                level *= float(injection["factor"])
            if in_window and kind == "sale" and _in(slot, injection):
                level *= float(injection["factor"])
            if in_window:
                level *= sale_factor
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
                # 잡음: 이 5분 안의 첫 결제 하나만 결제 서버 쪽에서 실패한다 (장애 구간이 아님)
                while noise and noise[0] + BUCKET <= moment:  # 주문이 없던 5분은 잡음도 없다
                    noise.pop(0)
                blip = bool(noise) and noise[0] <= moment
                if blip:
                    noise.pop(0)
                if faulty or blip:
                    shop.inject_fault("failure")
                simulate = "failure" if order_no % DECLINE_EVERY == 0 else None
                paid = shop.pay(order["order_id"], "card", simulate)
                if faulty or blip:
                    shop.release_fault()
                    final = code if faulty else noise_code
                    if final:
                        with db.tx() as conn:
                            conn.execute(
                                "UPDATE payments SET failure_code = ? WHERE order_id = ?",
                                (final, paid["order_id"]),
                            )
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
                        faulty=faulty,
                        noise=blip,
                    )
                )
    return world


@dataclass(frozen=True)
class Truth:
    """시나리오가 만든 결제 기록에서 바로 센 정답."""

    fault_failed_orders: int  # 주입한 장애로 실패한 주문 (잡음 제외)
    fault_customers: int
    fault_amount: int  # 원
    all_failed_payments: int  # 카드 거절 포함 모든 실패 결제 (진단용)
    declined_payments: int  # 카드 거절(PG_DECLINED)
    onset: str | None  # 장애로 실패한 첫 결제가 든 5분 버킷 시작
    noise_failures: int = 0  # 구간 안 잡음(장애 아님) 실패 결제
    # 영향 분석 정답: 구간 안 장애 쪽 실패 전부 (주입한 장애 + 잡음)
    impact_failed_orders: int = 0
    impact_customers: int = 0
    impact_amount: int = 0


USUAL_MARGIN = timedelta(hours=1)  # 평소 실패 수: 같은 시각 ±1시간 (영향 분석 #102와 같음)


def usual_fault_failures(world: World) -> float | None:
    """평가가 직접 센 평소 장애 쪽 실패 수 (구간 길이만큼). 영향 분석 초과분(#102)과 같은 방식이다.

    지난 7일 각 날의 [구간 시작 − 1시간, 구간 끝 + 1시간)에서, 결제가 1건 이상 기록된 5분 구간의
    장애 쪽 실패(주입한 장애 + 잡음) 평균 × 구간 길이. 그런 5분이 없으면 None(확인할 수 없음).
    data 에이전트 코드가 아니라 이 시나리오가 만든 결제 기록에서 센다.
    """
    window_from, window_to = world.window

    def floor(moment: datetime) -> datetime:
        return moment.replace(minute=moment.minute - moment.minute % 5, second=0, microsecond=0)

    ranges = [
        (
            window_from - timedelta(days=day) - USUAL_MARGIN,
            window_to - timedelta(days=day) + USUAL_MARGIN,
        )
        for day in range(1, HISTORY_DAYS + 1)
    ]
    past = [p for p in world.payments if any(start <= p.at < end for start, end in ranges)]
    active = {floor(p.at) for p in past}
    if not active:
        return None
    fails = sum(1 for p in past if p.failure_code is not None and (p.faulty or p.noise))
    return fails / len(active) * ((window_to - window_from) / BUCKET)


def truth(world: World) -> Truth:
    window_from, window_to = world.window
    inside = [p for p in world.payments if window_from <= p.at < window_to]
    fault = [p for p in inside if p.faulty and p.failure_code is not None]
    failed = [p for p in inside if p.failure_code is not None]
    fault_side = [p for p in failed if p.faulty or p.noise]  # 주입한 장애 + 잡음
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
        noise_failures=sum(1 for p in inside if p.noise and p.failure_code is not None),
        impact_failed_orders=len(fault_side),
        impact_customers=len({p.buyer_id for p in fault_side}),
        impact_amount=sum(p.amount for p in fault_side),
    )
