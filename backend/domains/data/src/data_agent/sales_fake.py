"""가짜 판매 출처 (데모 전용): 실제 판매 서버와 같은 계약 모양의 주문·고객을 만든다.

`DATA_SALES_SOURCE=fake`일 때만 수집기(`sales_collect.get_source`)가 쓴다. 기본은 꺼짐이고 공용
서버(251)에서는 켜지 않는다. 테스트 전용 목 모드(`MockSalesSource`)와는 따로다.

판매 서버가 고객 내보내기·장르·가상 회원을 내놓기 전까지 고객·시장 분석을 끝까지 돌려 보려는
것이다. 실제 판매 서버로 바꿀 때는 연결(환경변수)만 바꾼다: 모양(`shop_sales`·`shop_customers`)과
커서 규칙이 같다. 수집기는 sync_state 출처 이름을 `fake`로 따로 써서 커서가 섞이지 않는다.
분석 근거(`SalesBasis.data_source`)에 "가짜 데이터(데모용)"가 보인다.

- 설정: `catalogs/fake/<회사 카탈로그 id>.toml` (회원 수·속성 분포·묶음 선호·기간·하루 주문 수·
  가상 상품·seed). 숫자는 모두 가정 값이다
- 같은 seed면 같은 회원·주문. 하루치 주문은 (seed, 한국 날짜)로 정한 난수로 만들어 실행한 날과
  상관없이 같다(주문 번호도 날짜 + 순번). 기간은 오늘(주입 가능한 시계)까지 `period_days`일
- 아직 오지 않은 시각(`updated_at > 지금`)의 주문·회원은 주지 않는다. 그래서 시간이 지나면 뒤에
  새 주문만 붙고 커서 `(updated_at, id)`가 앞으로만 간다
- 회원 일부는 일부 속성을 비워 둔다(분석에서 "모름")
"""

from __future__ import annotations

import bisect
import random
import tomllib
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta, timezone
from pathlib import Path

from pydantic import BaseModel, ConfigDict, Field, model_validator

from ax_contracts.data_access.shop_customers import CustomerRow, ShopCustomersExportResult
from ax_contracts.data_access.shop_sales import (
    SalesOrderLine,
    SalesOrderRefund,
    SalesOrderRow,
    ShopSalesExportResult,
)
from data_agent.catalog import CATALOG_DIR

FAKE_DIR = CATALOG_DIR / "fake"
KST = timezone(timedelta(hours=9), "KST")
CURSOR_PREFIX = "fake:"
DIRECT = "direct"  # 유입 표시 없음


class FakeAttribute(BaseModel):
    model_config = ConfigDict(extra="forbid")
    missing: float = Field(default=0.0, ge=0, le=1)  # 이 속성을 비워 둔 회원 비율 ("모름")
    values: dict[str, float] = Field(min_length=1)  # 값 code → 무게


class FakePreference(BaseModel):
    model_config = ConfigDict(extra="forbid")
    when: dict[str, str] = Field(min_length=1)  # 속성 key → 값 code (모두 맞아야 함)
    boost: dict[str, float] = Field(min_length=1)  # genre_code → 곱할 무게


class FakeProduct(BaseModel):
    model_config = ConfigDict(extra="forbid")
    product_id: str
    title: str
    category_code: str
    genre_code: str
    price: int = Field(gt=0)


class FakeConfig(BaseModel):
    """`catalogs/fake/<id>.toml` 형식 (데모 전용, 계약이 아니다)."""

    model_config = ConfigDict(extra="forbid")
    seed: int
    members: int = Field(default=3000, ge=1)
    period_days: int = Field(default=90, ge=1)
    orders_per_day: int = Field(default=60, ge=1)
    members_from: date
    members_to: date
    coupon_rate: float = Field(default=0.0, ge=0, le=1)
    coupon_amount: int = Field(default=0, ge=0)
    points_rate: float = Field(default=0.0, ge=0, le=1)
    points_amount: int = Field(default=0, ge=0)
    cancel_rate: float = Field(default=0.0, ge=0, le=1)
    refund_rate: float = Field(default=0.0, ge=0, le=1)
    second_item_rate: float = Field(default=0.0, ge=0, le=1)
    shipping_fee: int = Field(default=0, ge=0)
    free_shipping_over: int = Field(default=0, ge=0)
    sources: dict[str, float] = Field(default_factory=lambda: {DIRECT: 1.0})
    source_medium: dict[str, str] = Field(default_factory=dict)
    attributes: dict[str, FakeAttribute] = Field(default_factory=dict)
    genre_weights: dict[str, float] = Field(min_length=1)
    preferences: list[FakePreference] = Field(default_factory=list)
    products: list[FakeProduct] = Field(min_length=1)

    @model_validator(mode="after")
    def _consistent(self) -> FakeConfig:
        if self.members_from >= self.members_to:
            raise ValueError("members_from가 members_to보다 앞이어야 한다")
        for p in self.products:
            if p.genre_code not in self.genre_weights:
                raise ValueError(f"products {p.product_id}: genre_weights에 없는 장르")
        for pref in self.preferences:
            for key in pref.when:
                if key not in self.attributes:
                    raise ValueError(f"preferences: 없는 속성 {key}")
            for genre in pref.boost:
                if genre not in self.genre_weights:
                    raise ValueError(f"preferences: 없는 장르 {genre}")
        return self


def load_config(path: str | Path) -> FakeConfig:
    with Path(path).open("rb") as f:
        return FakeConfig.model_validate(tomllib.load(f))


@dataclass(frozen=True)
class Member:
    row: CustomerRow
    genre_weights: tuple[float, ...]  # FakeSalesSource.genres 순서


def _key(moment: datetime, ident: str) -> tuple[str, str]:
    return (moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"), ident)


def _cursor(key: tuple[str, str]) -> str:
    return f"{CURSOR_PREFIX}{key[0]}|{key[1]}"


def _parse_cursor(cursor: str | None) -> tuple[str, str] | None:
    if not cursor or not cursor.startswith(CURSOR_PREFIX) or "|" not in cursor:
        return None  # 모르는 커서(다른 출처 것)는 처음부터
    stamp, ident = cursor.removeprefix(CURSOR_PREFIX).split("|", 1)
    return (stamp, ident)


def _split(total: int, weights: Sequence[int]) -> list[int]:
    """`total`을 무게 비율로 정수로 나눈다 (나머지는 첫 칸). 합은 늘 `total`."""
    base = sum(weights)
    parts = [total * w // base for w in weights] if base > 0 else [0] * len(weights)
    if parts:
        parts[0] += total - sum(parts)
    return parts


class FakeSalesSource:
    """가짜 판매 서버. `sales_export`·`customers_export`가 실제 판매 서버와 같은 모양·커서 규칙."""

    def __init__(self, config: FakeConfig, *, clock: Callable[[], datetime] | None = None) -> None:
        self.config = config
        self.clock = clock or (lambda: datetime.now(UTC))
        self.genres: tuple[str, ...] = tuple(
            g for g in config.genre_weights if any(p.genre_code == g for p in config.products)
        )
        self._products = {g: [p for p in config.products if p.genre_code == g] for g in self.genres}
        self._members: list[Member] | None = None
        self._days: dict[date, list[SalesOrderRow]] = {}

    @classmethod
    def for_catalog(
        cls, catalog_id: str, *, clock: Callable[[], datetime] | None = None
    ) -> FakeSalesSource:
        """`catalogs/fake/<catalog_id>.toml`로 만든다."""
        return cls(load_config(FAKE_DIR / f"{catalog_id}.toml"), clock=clock)

    # ------------------------------------------------------------ 회원
    def members(self) -> list[Member]:
        """가상 회원 전부 (seed로만 정해진다, 실행한 날과 무관)."""
        if self._members is None:
            self._members = self._make_members()
        return self._members

    def _make_members(self) -> list[Member]:
        cfg = self.config
        rng = random.Random(f"{cfg.seed}:members")
        start = datetime.combine(cfg.members_from, time(), KST)
        span = int((datetime.combine(cfg.members_to, time(), KST) - start).total_seconds())
        out: list[Member] = []
        for i in range(cfg.members):
            joined = (start + timedelta(seconds=rng.randrange(span))).astimezone(UTC)
            attributes: dict[str, str] = {}
            for key, spec in cfg.attributes.items():
                missing = rng.random() < spec.missing
                value = rng.choices(list(spec.values), weights=list(spec.values.values()))[0]
                if not missing:
                    attributes[key] = value
            weights = []
            for genre in self.genres:
                w = cfg.genre_weights[genre]
                for pref in cfg.preferences:
                    if all(attributes.get(k) == v for k, v in pref.when.items()):
                        w *= pref.boost.get(genre, 1.0)
                weights.append(w)
            row = CustomerRow(
                buyer_id=f"fk_b{i + 1:05d}",
                joined_at=joined,
                updated_at=joined,
                attributes=attributes,
            )
            out.append(Member(row, tuple(weights)))
        return out

    # ------------------------------------------------------------ 주문
    def day_orders(self, day: date) -> list[SalesOrderRow]:
        """한국 날짜 `day`에 접수한 주문 전부. (seed, 날짜)로만 정해진다."""
        if day not in self._days:
            self._days[day] = self._make_day(day)
        return self._days[day]

    def _make_day(self, day: date) -> list[SalesOrderRow]:
        cfg = self.config
        members = self.members()
        rng = random.Random(f"{cfg.seed}:orders:{day.isoformat()}")
        low, high = round(cfg.orders_per_day * 0.8), round(cfg.orders_per_day * 1.2)
        count = rng.randint(max(low, 0), max(high, 1))
        midnight = datetime.combine(day, time(), KST)
        seconds = sorted(rng.randrange(86400) for _ in range(count))
        sources = list(cfg.sources)
        source_weights = list(cfg.sources.values())
        rows: list[SalesOrderRow] = []
        for n, sec in enumerate(seconds):
            member = members[rng.randrange(len(members))]
            picks = [self._pick(rng, member)]
            if rng.random() < cfg.second_item_rate:
                picks.append(self._pick(rng, member))
            items = sum(p.price for p in picks)
            coupon = cfg.coupon_amount if rng.random() < cfg.coupon_rate else 0
            points = cfg.points_amount if rng.random() < cfg.points_rate else 0
            if coupon + points >= items:
                coupon = points = 0
            cancelled = rng.random() < cfg.cancel_rate
            refunded = rng.random() < cfg.refund_rate and not cancelled
            source = rng.choices(sources, weights=source_weights)[0]
            created = (midnight + timedelta(seconds=sec)).astimezone(UTC)
            updated = created + timedelta(minutes=1)
            coupons = _split(coupon, [p.price for p in picks])
            pointss = _split(points, [p.price for p in picks])
            lines = [
                SalesOrderLine(
                    product_id=p.product_id,
                    category_code=p.category_code,
                    title=p.title,
                    qty=1,
                    unit_price=p.price,
                    coupon_share=c,
                    points_share=pt,
                    genre_code=p.genre_code,
                )
                for p, c, pt in zip(picks, coupons, pointss, strict=True)
            ]
            refunds: list[SalesOrderRefund] = []
            if refunded:
                at = created + timedelta(hours=2)
                amount = max(lines[0].unit_price - lines[0].coupon_share - lines[0].points_share, 0)
                refunds.append(SalesOrderRefund(refund_id=f"fk_r{day:%Y%m%d}_{n:04d}",
                                                amount=amount, refunded_at=at))  # fmt: skip
                updated = at
            free = cfg.free_shipping_over and items >= cfg.free_shipping_over
            rows.append(
                SalesOrderRow(
                    order_id=f"fk_o{day:%Y%m%d}_{n:04d}",
                    buyer_id=member.row.buyer_id,
                    status="cancelled" if cancelled else "paid",
                    created_at=created,
                    updated_at=updated,
                    items_amount=items,
                    shipping_fee=0 if free else cfg.shipping_fee,
                    points_used=points,
                    coupon_discount=coupon,
                    src_source=None if source == DIRECT else source,
                    src_medium=None if source == DIRECT else cfg.source_medium.get(source),
                    payment_status="succeeded",
                    lines=lines,
                    refunds=refunds,
                )
            )
        return rows

    def _pick(self, rng: random.Random, member: Member) -> FakeProduct:
        genre = rng.choices(self.genres, weights=member.genre_weights)[0]
        return rng.choice(self._products[genre])

    # ------------------------------------------------------------ 내보내기 (계약 모양)
    def _now(self) -> datetime:
        return self.clock().astimezone(UTC)

    def window_days(self, now: datetime | None = None) -> list[date]:
        """주문을 만드는 한국 날짜 (오늘까지 `period_days`일)."""
        today = (now or self._now()).astimezone(KST).date()
        return [today - timedelta(days=i) for i in range(self.config.period_days - 1, -1, -1)]

    def orders(self, now: datetime | None = None) -> list[SalesOrderRow]:
        """지금까지 나온(updated_at ≤ 지금) 기간 안 주문, `(updated_at, order_id)` 순서."""
        now = now or self._now()
        rows = [o for d in self.window_days(now) for o in self.day_orders(d) if o.updated_at <= now]
        return sorted(rows, key=lambda r: _key(r.updated_at, r.order_id))

    def customers(self, now: datetime | None = None) -> list[CustomerRow]:
        """지금까지 가입한 회원, `(updated_at, buyer_id)` 순서."""
        now = now or self._now()
        rows = [m.row for m in self.members() if m.row.updated_at <= now]
        return sorted(rows, key=lambda r: _key(r.updated_at, r.buyer_id))

    async def sales_export(
        self, cursor: str | None = None, limit: int = 500
    ) -> ShopSalesExportResult:
        now = self._now()
        rows = self.orders(now)
        keys = [_key(r.updated_at, r.order_id) for r in rows]
        page, next_cursor, more = _page(keys, rows, cursor, limit)
        return ShopSalesExportResult(
            orders=page, next_cursor=next_cursor, has_more=more, collected_at=now
        )

    async def customers_export(
        self, cursor: str | None = None, limit: int = 500
    ) -> ShopCustomersExportResult:
        now = self._now()
        rows = self.customers(now)
        keys = [_key(r.updated_at, r.buyer_id) for r in rows]
        page, next_cursor, more = _page(keys, rows, cursor, limit)
        return ShopCustomersExportResult(
            customers=page, next_cursor=next_cursor, has_more=more, collected_at=now
        )


def _page[T](
    keys: list[tuple[str, str]], rows: list[T], cursor: str | None, limit: int
) -> tuple[list[T], str | None, bool]:
    """커서 다음부터 `limit`건. 받을 것이 없으면 같은 커서를 돌려준다(계약 규칙)."""
    after = _parse_cursor(cursor)
    start = bisect.bisect_right(keys, after) if after is not None else 0
    page = rows[start : start + limit]
    end = start + len(page)
    next_cursor = _cursor(keys[end - 1]) if page else cursor
    return page, next_cursor, end < len(rows)
