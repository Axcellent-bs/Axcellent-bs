"""data 에이전트가 읽는 조회 API의 업무 규칙(실패 주문 목록·지표 시계열·판매자 정보). 읽기 전용이다.

형식은 `contracts/data-access/`의 `shop_failed_orders`·`shop_metric_series`·`shop_seller_profile`·
`shop_seller_prices`를 따른다.
지표는 DB에서 코드로 계산한다(LLM 없음). 웹 프레임워크에 의존하지 않는다.

- 실패 주문: 결제가 `failed`인 기록 하나가 한 줄이다. 주문의 `category`는 첫 품목의 상품 분류다.
- 시계열: 5분 버킷, 버킷 시작 시각(UTC)이 `ts`다. 구간은 `from <= 시각 < to`이고 `from`은 버킷
  경계로 내림한다. 건수 지표는 값이 없는 버킷도 0으로 채우고, 비율 지표는 결제 시도가 없는
  버킷을 뺀다(0%와 '측정 못 함'을 섞지 않는다).
- 판매자 정보(fraud_score): 기존 `sellers`·`products`만 읽는다(테이블 변경 없음).
  - `joined_at` = `sellers.created_at`, `product_count` = 판매 중(active) 상품 수
  - `listings_last_24h` = `now - 24시간 <= products.created_at <= now`인 상품 수(판매 중지 포함,
    "등록한 수"라서). 정확히 24시간 전에 만든 상품은 센다
  - 사업자 해시는 테이블에 없어 `business_hash`·`business_hash_duplicated`를 null로 준다
- 판매자 가격: 판매자의 판매 중 상품마다 같은 카테고리 판매 중 상품(모든 판매자, 이 판매자 포함)
  가격의 중앙값. 개수가 짝수면 가운데 두 값의 평균을 반올림(0.5는 올림)한다. 카테고리에 상품이
  하나뿐이면 그 상품 가격이 중앙값이다
- 없는 판매자는 404 `SELLER_NOT_FOUND`
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from ax_contracts.data_access.shop_failed_orders import (
    FailedOrder,
    ShopFailedOrdersQuery,
    ShopFailedOrdersResult,
)
from ax_contracts.data_access.shop_metric_series import (
    MetricPoint,
    ShopMetricSeriesQuery,
    ShopMetricSeriesResult,
)
from ax_contracts.data_access.shop_seller_prices import (
    SellerProductPrice,
    ShopSellerPricesQuery,
    ShopSellerPricesResult,
)
from ax_contracts.data_access.shop_seller_profile import ShopSellerProfile, ShopSellerProfileQuery
from ax_shop_api.db import Db
from ax_shop_api.service import ShopError

BUCKET = timedelta(minutes=5)
LISTING_WINDOW = timedelta(hours=24)  # 판매자 정보의 "최근 등록" 구간
MAX_POINTS = 2016  # 5분 버킷 7일치. 넘으면 잘라서 `truncated`로 알린다


def _iso(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse(text: str) -> datetime:
    return datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)


def _floor(moment: datetime) -> datetime:
    seconds = int(BUCKET.total_seconds())
    return datetime.fromtimestamp(int(moment.timestamp()) // seconds * seconds, UTC)


def median_price(prices: list[int]) -> int:
    """정수 원 중앙값. 짝수 개면 가운데 두 값의 평균을 반올림한다(0.5는 올림)."""
    ordered = sorted(prices)
    middle = len(ordered) // 2
    if len(ordered) % 2:
        return ordered[middle]
    return math.floor((ordered[middle - 1] + ordered[middle]) / 2 + 0.5)


class Lookups:
    def __init__(self, db: Db, now: Callable[[], datetime] = lambda: datetime.now(UTC)) -> None:
        self.db = db
        self.now = now

    def failed_orders(self, query: ShopFailedOrdersQuery) -> ShopFailedOrdersResult:
        if query.to <= query.from_:
            raise ShopError("INVALID_RANGE", "to는 from보다 뒤여야 한다")
        sql = (
            "SELECT p.order_id, o.buyer_id, p.amount, p.failure_code, p.created_at, "
            "  (SELECT pr.category_code FROM order_items i JOIN products pr "
            "   ON pr.product_id = i.product_id WHERE i.order_id = p.order_id "
            "   ORDER BY i.rowid LIMIT 1) AS category "
            "FROM payments p JOIN orders o ON o.order_id = p.order_id "
            "WHERE p.status = 'failed' AND p.tenant_id = ? "
            "AND p.created_at >= ? AND p.created_at < ?"
        )
        args: list[Any] = [query.tenant_id, _iso(query.from_), _iso(query.to)]
        if query.category:
            sql += " AND category = ?"
            args.append(query.category)
        sql += " ORDER BY p.created_at, p.payment_id LIMIT ?"
        args.append(query.limit + 1)  # 하나 더 읽어 잘림을 안다
        with self.db.tx() as conn:
            rows = conn.execute(sql, args).fetchall()
        orders = [
            FailedOrder(
                order_id=row["order_id"],
                buyer_id=row["buyer_id"],
                amount=row["amount"],
                category=row["category"] or "unknown",
                failed_at=_parse(row["created_at"]),
                reason=row["failure_code"] or "UNKNOWN",
                tenant_id=query.tenant_id,
            )
            for row in rows[: query.limit]
        ]
        return ShopFailedOrdersResult(
            orders=orders, collected_at=self.now(), truncated=len(rows) > query.limit
        )

    def metric_series(self, query: ShopMetricSeriesQuery) -> ShopMetricSeriesResult:
        if query.to <= query.from_:
            raise ShopError("INVALID_RANGE", "to는 from보다 뒤여야 한다")
        start = _floor(query.from_)
        with self.db.tx() as conn:
            if query.metric == "order_count":
                rows = conn.execute(
                    "SELECT created_at, 1 AS ok FROM orders "
                    "WHERE tenant_id = ? AND created_at >= ? AND created_at < ?",
                    (query.tenant_id, _iso(start), _iso(query.to)),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT created_at, status = 'succeeded' AS ok, order_id FROM payments "
                    "WHERE tenant_id = ? AND created_at >= ? AND created_at < ?",
                    (query.tenant_id, _iso(start), _iso(query.to)),
                ).fetchall()
        buckets: dict[datetime, list[Any]] = defaultdict(list)
        for row in rows:
            buckets[_floor(_parse(row["created_at"]))].append(row)

        points: list[MetricPoint] = []
        truncated = False
        moment = start
        while moment < query.to:
            if len(points) >= MAX_POINTS:
                truncated = True
                break
            value = self._value(query.metric, buckets.get(moment, []))
            if value is not None:
                points.append(MetricPoint(ts=moment, value=value))
            moment += BUCKET
        return ShopMetricSeriesResult(
            metric=query.metric,
            bucket=query.bucket,
            points=points,
            collected_at=self.now(),
            truncated=truncated,
            tenant_id=query.tenant_id,
        )

    @staticmethod
    def _value(metric: str, rows: list[Any]) -> float | None:
        failed = [row for row in rows if not row["ok"]] if metric != "order_count" else []
        if metric == "order_count":
            return float(len(rows))
        if metric == "payment_count":
            return float(len(rows))
        if metric == "order_fail_count":
            return float(len({row["order_id"] for row in failed}))
        if not rows:  # payment_fail_rate: 결제 시도가 없으면 값이 없다
            return None
        return len(failed) / len(rows)

    def _seller(self, conn: Any, seller_id: str, tenant_id: str) -> Any:
        row = conn.execute(
            "SELECT * FROM sellers WHERE seller_id = ? AND tenant_id = ?", (seller_id, tenant_id)
        ).fetchone()
        if row is None:
            raise ShopError("SELLER_NOT_FOUND", "판매자를 찾을 수 없다", 404)
        return row

    def seller_profile(self, query: ShopSellerProfileQuery) -> ShopSellerProfile:
        now = self.now()
        with self.db.tx() as conn:
            seller = self._seller(conn, query.seller_id, query.tenant_id)
            active = conn.execute(
                "SELECT COUNT(*) FROM products "
                "WHERE seller_id = ? AND tenant_id = ? AND active = 1",
                (query.seller_id, query.tenant_id),
            ).fetchone()[0]
            recent = conn.execute(
                "SELECT COUNT(*) FROM products WHERE seller_id = ? AND tenant_id = ? "
                "AND created_at >= ? AND created_at <= ?",
                (query.seller_id, query.tenant_id, _iso(now - LISTING_WINDOW), _iso(now)),
            ).fetchone()[0]
        return ShopSellerProfile(
            seller_id=seller["seller_id"],
            joined_at=_parse(seller["created_at"]),
            business_hash=None,  # sellers 테이블에 사업자 정보가 없다
            business_hash_duplicated=None,
            product_count=active,
            listings_last_24h=recent,
            tenant_id=query.tenant_id,
        )

    def seller_prices(self, query: ShopSellerPricesQuery) -> ShopSellerPricesResult:
        with self.db.tx() as conn:
            self._seller(conn, query.seller_id, query.tenant_id)
            own = conn.execute(
                "SELECT product_id, category_code, price FROM products "
                "WHERE seller_id = ? AND tenant_id = ? AND active = 1 ORDER BY product_id",
                (query.seller_id, query.tenant_id),
            ).fetchall()
            categories = sorted({row["category_code"] for row in own})
            prices: dict[str, list[int]] = defaultdict(list)
            if categories:
                marks = ",".join("?" * len(categories))
                for row in conn.execute(
                    "SELECT category_code, price FROM products WHERE tenant_id = ? AND active = 1 "
                    f"AND category_code IN ({marks})",
                    (query.tenant_id, *categories),
                ).fetchall():
                    prices[row["category_code"]].append(row["price"])
        medians = {category: median_price(values) for category, values in prices.items()}
        return ShopSellerPricesResult(
            items=[
                SellerProductPrice(
                    product_id=row["product_id"],
                    category=row["category_code"],
                    price=row["price"],
                    category_median_price=medians[row["category_code"]],
                )
                for row in own
            ]
        )
