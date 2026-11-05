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
- 캠페인 쿠폰(`campaign`, campaign_effect 평가 `eval/campaign/`): 지난 7일보다 먼저 구매자
  `buyers`명에게 캠페인 쿠폰(`campaign_id`, `units_each`장)을 shop-api 쿠폰 발급 코드로 준다.
  구간 안에서 그 구매자의 주문은 `use_fraction` 확률로 쿠폰을 쓴다(shop-api `place_order`).
  `used_after`가 있으면 구간이 끝난 뒤 5분 간격으로 그만큼 더 쓴다
  (조회 시점 누적 사용률에 섞이는 몫). 그 장수는 구간 안에서 쓰지 않게 남겨 둔다.
- 여러 날 캠페인(`eval/campaign/multiday/`): `traffic.history_days`가 있으면 지난 7일 같은
  시간대 대신 구간 시작 `history_days`일 전부터 구간 끝까지 5분마다 빠짐없이 만든다(이어진 기록).
  `traffic.weekday_levels`(월~일 7개)가 있으면 그 요일 배수를 주문 수준에 곱한다(주말 효과).
  둘 다 없으면 예전과 똑같이 만든다.
  쿠폰 결정은 따로 씨앗을 둔 난수로 해서, `campaign`이 없는 시나리오의 데이터는 예전과 같다.
- 상품 하나만 늘림(`product_boost`, campaign_effect 상품 기준 평가 `eval/campaign_products/`):
  구간 안 5분 칸마다 그 상품 주문을 더 넣어 그 상품의 기대 주문 수를 `factor`배로 만든다.
  따로 씨앗을 둔 난수라 `product_boost`가 없는 시나리오의 데이터는 예전과 같다.

정답(실패 주문 수·금액·영향 고객, 이상 시작 시각)은 data 에이전트 코드가 아니라 여기서 만든
결제 기록에서 바로 센다(`Truth`).

- 영향 분석 정답(`impact_*`): 구간 안의 장애 쪽 실패 결제 전부 = 주입한 장애로 실패한 결제 +
  그 구간에 난 잡음 실패. 운영에서는 둘을 구분할 수 없고, 잡음으로 실패한 고객도 실제 결제 실패라
  보상 대상이다. 카드 거절(`PG_DECLINED`)은 결제한 사람 쪽 사유라 넣지 않는다.
- 주입한 장애만(`fault_*`): 예전 영향 분석 정답. 초과분 지표(`impact.excess_*`)의 정답이고,
  결제 장애의 장애 쪽 실패 0건·1건 채점(이상 아님·low 기록)에도 쓴다.

5분마다 주문 수는 시간대 수준을 평균으로 한 포아송 분포다(실제 주문처럼 흔들린다).
난수는 시나리오 id로 씨앗을 고정한 `random.Random`만 쓴다. 같은 시나리오면 늘 같은 데이터다.

캐시: 환경변수 `DATA_EVAL_CACHE_DIR`가 있으면 만든 데이터(DB + 결제 기록)를 그 폴더에 파일로 두고,
키가 같으면 다시 만들지 않고 읽는다. 키 = 생성기 코드(`eval_world.py`·`make_cases.py`) + shop-api
소스(`shared/shop-api/src`) + 시나리오 명세(`expected` 제외)의 해시. 없으면 캐시하지 않는다.
같은 시나리오면 늘 같은 데이터라서 캐시를 써도 결과가 같다(`tests/test_eval_cache.py`).
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import math
import os
import random
import shutil
import sqlite3
from collections.abc import Iterator
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx

from ax_contracts.data_access.shop_failed_orders import ShopFailedOrdersQuery
from ax_contracts.data_access.shop_metric_series import ShopMetricSeriesQuery
from ax_shop_api import service
from ax_shop_api.db import Db
from ax_shop_api.lookups import Lookups
from ax_shop_api.promotions import Promotions
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
            elif path.endswith("/coupons"):  # shop-api `GET /api/shop/coupons` (캠페인 평가)
                data = Promotions(self.db).list_coupons(
                    params.get("buyer_id"), params.get("purpose_code"), params.get("campaign_id")
                )
            elif path.endswith("/orders"):  # shop-api `GET /api/shop/orders` (캠페인 매출 평가)
                data = service.Shop(self.db).list_orders(
                    params.get("buyer_id"), params.get("since"), params.get("until")
                )
            elif path.endswith("/products"):  # shop-api `GET /api/shop/products` (상품 기준 평가)
                data = service.Shop(self.db).list_products(
                    params.get("seller_id"), params.get("category_code")
                )
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


def _continuous_slots(window_from: datetime, window_to: datetime, days: int) -> list[datetime]:
    """구간 시작 `days`일 전부터 구간 끝까지 빠짐없는 5분 버킷 시작 시각 (여러 날 캠페인)."""
    slots: list[datetime] = []
    moment = window_from - timedelta(days=days)
    while moment < window_to:
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


CACHE_ENV = "DATA_EVAL_CACHE_DIR"
_EVAL_DIR = Path(__file__).resolve().parent
_BACKEND = _EVAL_DIR.parents[2]
# 생성 결과를 바꿀 수 있는 코드. 하나라도 바뀌면 캐시 키가 바뀐다
CODE_FILES = (_EVAL_DIR / "eval_world.py", _EVAL_DIR / "make_cases.py")
SHOP_API_SRC = _BACKEND / "shared" / "shop-api" / "src"


def _source_files() -> list[Path]:
    shop = [
        p
        for p in SHOP_API_SRC.rglob("*")
        if p.is_file() and "__pycache__" not in p.parts and p.suffix != ".pyc"
    ]
    return [*CODE_FILES, *sorted(shop)]


_code_hash: str | None = None


def code_hash() -> str:
    """생성기 코드 + shop-api 소스의 해시 (한 프로세스에서 한 번 센다)."""
    global _code_hash
    if _code_hash is None:
        digest = hashlib.sha256()
        for path in _source_files():
            digest.update(path.relative_to(_BACKEND).as_posix().encode())
            digest.update(b"\0")
            digest.update(path.read_bytes())
            digest.update(b"\0")
        _code_hash = digest.hexdigest()[:16]
    return _code_hash


def spec_hash(spec: dict[str, Any]) -> str:
    """시나리오 명세의 해시. 정답(`expected`)은 생성에 쓰지 않으므로 뺀다."""
    body = {k: v for k, v in spec.items() if k != "expected"}
    text = json.dumps(body, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(text.encode()).hexdigest()[:24]


def cache_path(spec: dict[str, Any], cache_dir: str | Path) -> Path:
    return Path(cache_dir) / code_hash() / f"{spec_hash(spec)}.sqlite"


_pruned: set[Path] = set()


def _prune(cache_dir: Path) -> None:
    """키의 코드 부분이 다른 예전 캐시 폴더를 지운다 (한 프로세스에서 한 번)."""
    if cache_dir in _pruned or not cache_dir.is_dir():
        return
    _pruned.add(cache_dir)
    for child in cache_dir.iterdir():
        if child.is_dir() and child.name != code_hash():
            shutil.rmtree(child, ignore_errors=True)


_PAYMENTS_TABLE = "_eval_cache_payments"


def save(world: World, path: Path) -> None:
    """만든 데이터를 파일 하나로 저장한다 (DB 전체 + 결제 기록). 다 쓴 뒤에 이름을 바꿔 넣는다."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f"{path.name}.{os.getpid()}.tmp")
    tmp.unlink(missing_ok=True)
    out = sqlite3.connect(tmp)
    try:
        with world.db.tx() as conn:
            conn.backup(out)
        rows = [
            (
                p.order_id,
                p.buyer_id,
                p.amount,
                p.at.isoformat(),
                p.failure_code,
                int(p.faulty),
                int(p.noise),
            )
            for p in world.payments
        ]
        out.execute(
            f"CREATE TABLE {_PAYMENTS_TABLE} (seq INTEGER PRIMARY KEY, order_id TEXT, "
            "buyer_id TEXT, amount INTEGER, at TEXT, failure_code TEXT, faulty INTEGER, "
            "noise INTEGER)"
        )
        out.executemany(
            f"INSERT INTO {_PAYMENTS_TABLE} (order_id, buyer_id, amount, at, failure_code, "
            "faulty, noise) VALUES (?, ?, ?, ?, ?, ?, ?)",
            rows,
        )
        out.commit()
    finally:
        out.close()
    os.replace(tmp, path)


def load(spec: dict[str, Any], path: Path) -> World:
    """`save`한 파일에서 데이터를 읽어 새 메모리 DB로 만든다."""
    db = Db(":memory:")
    src = sqlite3.connect(path)
    try:
        rows = src.execute(
            f"SELECT order_id, buyer_id, amount, at, failure_code, faulty, noise "
            f"FROM {_PAYMENTS_TABLE} ORDER BY seq"
        ).fetchall()
        with db.tx() as conn:
            src.backup(conn)
            conn.execute(f"DROP TABLE {_PAYMENTS_TABLE}")
    finally:
        src.close()
    payments = [
        Payment(
            order_id=order_id,
            buyer_id=buyer_id,
            amount=amount,
            at=datetime.fromisoformat(at),
            failure_code=failure_code,
            faulty=bool(faulty),
            noise=bool(noise),
        )
        for order_id, buyer_id, amount, at, failure_code, faulty, noise in rows
    ]
    return World(spec=spec, db=db, payments=payments)


def build(spec: dict[str, Any]) -> World:
    """시나리오 명세로 쇼핑몰 데이터를 만든다. `DATA_EVAL_CACHE_DIR`가 있으면 캐시를 쓴다."""
    cache_dir = os.getenv(CACHE_ENV)
    if not cache_dir:
        return generate(spec)
    _prune(Path(cache_dir))
    path = cache_path(spec, cache_dir)
    if path.is_file():
        try:
            return load(spec, path)
        except sqlite3.Error:  # 깨진 캐시 파일이면 지우고 새로 만든다
            path.unlink(missing_ok=True)
    world = generate(spec)
    save(world, path)
    return world


def generate(spec: dict[str, Any]) -> World:
    """시나리오 명세로 쇼핑몰 데이터를 새로 만든다 (캐시 없음)."""
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
    campaign = spec.get("campaign")  # 캠페인 쿠폰 (없으면 예전과 똑같이 만든다)
    coupons: dict[str, list[Any]] = {}  # 구매자 → [쿠폰 id, 남은 장수]
    if campaign:
        coupons = _issue_campaign_coupons(db, campaign, window_from)
        crng = random.Random(f"{spec['id']}:campaign")  # 기존 난수 순서를 바꾸지 않게 따로
        reserve = int(campaign.get("used_after", 0))
    weekday_levels = traffic.get("weekday_levels")  # 요일 배수 (여러 날 캠페인)
    # 캠페인 매출 평가 (없으면 예전과 똑같이 만든다):
    # - `basket.qty_factor`: 구간 안 주문의 수량에 곱한다 (주문 수는 그대로, 금액만 늘림)
    # - `payment_gap.days_ago`: 그 날(0 = 구간, k = k일 전 같은 시간대)의 주문은 만들고
    #   결제하지 않는다 (결제 기록 공백: shop-api는 결제 대기 주문으로 남긴다)
    basket = int((spec.get("basket") or {}).get("qty_factor", 1))
    # 상품 기준 평가 (`eval/campaign_products/`, 없으면 예전과 똑같이 만든다):
    # `product_boost` = 구간 안에서 그 상품만 `factor`배가 되게 그 상품 주문을 더 넣는다
    boost = spec.get("product_boost")
    prng = random.Random(f"{spec['id']}:product")  # 기존 난수 순서를 바꾸지 않게 따로
    gap_days = [int(d) for d in (spec.get("payment_gap") or {}).get("days_ago", [])]
    gap_ranges = [
        (window_from - timedelta(days=d), window_to - timedelta(days=d)) for d in gap_days
    ]
    with _clock() as now:
        margin = timedelta(minutes=int(traffic.get("history_margin_minutes", 0)))
        if traffic.get("history_days"):  # 이어진 기록 (여러 날 캠페인)
            slots = _continuous_slots(window_from, window_to, int(traffic["history_days"]))
        else:
            slots = _slots(window_from, window_to, margin)
        for slot in slots:
            in_window = window_from <= slot < window_to
            if kind == "empty" and (in_window or injection.get("history_empty")):
                continue  # 이 버킷에는 주문이 하나도 없다
            level = per_bucket * HOUR_LEVEL[slot.hour]
            if weekday_levels:
                level *= float(weekday_levels[slot.weekday()])
            if in_window and kind == "order_drop" and _in(slot, injection):
                level *= float(injection["factor"])
            if in_window and kind == "sale" and _in(slot, injection):
                level *= float(injection["factor"])
            if in_window:
                level *= sale_factor
            count = _poisson(rng, level)  # 실제 주문처럼 5분마다 포아송으로 흔들린다
            for moment, buyer, lines in _slot_orders(slot, count, rng, level, in_window, boost,
                                                     prng):  # fmt: skip
                now[0] = moment
                clock_seconds[0] = moment.timestamp()
                order_no += 1
                if in_window and basket > 1:  # 난수를 더 쓰지 않는다 (다른 케이스와 같은 순서)
                    lines = [(p, q * basket) for p, q in lines]
                if any(s <= moment < e for s, e in gap_ranges):
                    shop.create_order(buyer, lines)  # 결제 기록 공백: 결제하지 않는다
                    continue
                held = coupons.get(buyer) if in_window else None
                # 구간 뒤에 쓸 장수(`used_after`)는 남겨 둔다 (조건과 기록이 늘 같게)
                spare = held is not None and sum(c[1] for c in coupons.values()) > reserve
                if (
                    held
                    and held[1] > 0
                    and spare
                    and crng.random() < float(campaign["use_fraction"])
                ):
                    order = shop.place_order(buyer, lines, coupon_id=held[0])[0]
                    held[1] -= 1
                else:
                    order = shop.create_order(buyer, lines)
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
        if campaign and int(campaign.get("used_after", 0)) > 0:
            _use_after_window(world, shop, coupons, int(campaign["used_after"]), now, clock_seconds)
    return world


def _slot_orders(
    slot: datetime,
    count: int,
    rng: random.Random,
    level: float,
    in_window: bool,
    boost: dict[str, Any] | None,
    prng: random.Random,
) -> Iterator[tuple[datetime, str, list[tuple[str, int]]]]:
    """5분 칸 하나의 주문 (시각, 구매자, 품목).

    평소 주문은 예전과 같은 순서로 `rng`에서 하나씩 뽑는다(뽑는 때도 같다: 주문 하나를 처리한 뒤
    다음을 뽑는다). `boost`가 있으면 구간 안 칸에 그 상품 주문을 평균
    `level ÷ 상품 수 × (factor − 1)`만큼 더 넣는다(따로 둔 난수 `prng`).
    그 상품의 기대 주문 수가 `factor`배가 된다.
    """
    for i in range(count):
        moment = slot + timedelta(seconds=int((i + 0.5) * 300 / max(count, 1)))
        buyer = seed_buyer_id(rng.randint(1, BUYER_COUNT))
        product = PRODUCTS[rng.randrange(len(PRODUCTS))][0]
        yield moment, buyer, [(product, rng.choice((1, 1, 1, 2)))]
    if not (boost and in_window and float(boost["factor"]) > 1):
        return
    extra = _poisson(prng, level / len(PRODUCTS) * (float(boost["factor"]) - 1))
    for i in range(extra):
        moment = slot + timedelta(seconds=int((i + 0.25) * 300 / max(extra, 1)))
        buyer = seed_buyer_id(prng.randint(1, BUYER_COUNT))
        yield moment, buyer, [(str(boost["product_id"]), prng.choice((1, 1, 1, 2)))]


def _issue_campaign_coupons(
    db: Db, campaign: dict[str, Any], window_from: datetime
) -> dict[str, list[Any]]:
    """구매자 1번부터 `buyers`명에게 캠페인 쿠폰을 발급한다 (지난 7일보다 먼저, 기한 없음)."""
    issued_at = window_from - timedelta(days=HISTORY_DAYS + 1)
    promotions = Promotions(db, now=lambda: issued_at)
    out: dict[str, list[Any]] = {}
    for n in range(1, int(campaign["buyers"]) + 1):
        buyer = seed_buyer_id(n)
        coupon, _ = promotions.issue_coupon(
            idempotency_key=f"eval-{campaign['campaign_id']}-{n}",
            purpose_code="campaign",
            buyer_id=buyer,
            count=int(campaign["units_each"]),
            unit_value=int(campaign.get("unit_value", 1000)),
            campaign_id=str(campaign["campaign_id"]),
        )
        out[buyer] = [coupon["coupon_id"], int(campaign["units_each"])]
    return out


def _use_after_window(
    world: World,
    shop: service.Shop,
    coupons: dict[str, list[Any]],
    uses: int,
    now: list[datetime],
    clock_seconds: list[float],
) -> None:
    """구간이 끝난 뒤 5분 간격으로 쿠폰을 `uses`장 더 쓴다 (남은 장수가 있는 구매자를 돌아가며)."""
    moment = world.window[1]
    for _ in range(uses):
        holders = [(b, c) for b, c in coupons.items() if c[1] > 0]
        if not holders:
            break
        buyer, held = holders[0]
        now[0] = moment
        clock_seconds[0] = moment.timestamp()
        order = shop.place_order(buyer, [(PRODUCTS[0][0], 1)], coupon_id=held[0])[0]
        held[1] -= 1
        paid = shop.pay(order["order_id"], "card", None)
        with world.db.tx() as conn:  # 방금 넣은 결제 기록 (구간 안 결제와 같은 방법으로 읽는다)
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
        moment += BUCKET


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
