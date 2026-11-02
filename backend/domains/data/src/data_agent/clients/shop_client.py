"""판매 사이트(shop-api) 조회 클라이언트: 판매자 정보·가격, 목 실패 주문, 예전 이름 (이슈 #59).

지표 시계열·실패 주문·거래량은 커넥터(`data_agent.connectors.shop_api.ShopApiConnector`)로 옮겼다.
여기 남은 `HttpMetricSource`·`HttpShopClient`·`ShopApi`·`ShopUnavailable`·`use_client`는
예전 이름이다(같은 커넥터를 쓴다). 판매자 정보·가격(사기 점수 전용)은 아직 여기서 읽는다.
- 응답 없음·401·403·5xx·형식 오류는 `ShopUnavailable`이다. 숫자를 지어내지 않고(0으로 내지 않고)
  핸들러가 "확인할 수 없음"으로 답한다.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from datetime import datetime, timedelta
from typing import Protocol
from urllib.parse import quote

from pydantic import ValidationError

from ax_contracts.data_access.shop_failed_orders import (
    FailedOrder,
    ShopFailedOrdersQuery,
    ShopFailedOrdersResult,
)
from ax_contracts.data_access.shop_seller_prices import ShopSellerPricesResult
from ax_contracts.data_access.shop_seller_profile import ShopSellerProfile
from data_agent import catalog, demo_anchor
from data_agent.connectors import Connector, ConnectorUnavailable, Point
from data_agent.connectors.shop_api import (
    DEFAULT_SHOP_API_URL,
    FAILED_ORDERS_LIMIT,
    SERIES_CHUNK,
    SHOP_METRICS,
    TIMEOUT_SECONDS,
    ShopApi,
    ShopApiConnector,
    ShopUnavailable,
    _iso,
    use_client,
)
from data_agent.seed import DEMO_ALIGN_FROM, INCIDENT_FROM, INCIDENT_TO, SEED_FAILED_ORDERS

__all__ = [
    "DEFAULT_SHOP_API_URL",
    "FAILED_ORDERS_LIMIT",
    "SERIES_CHUNK",
    "SHOP_METRICS",
    "TIMEOUT_SECONDS",
    "_iso",
    "ConnectorUnavailable",
    "HttpMetricSource",
    "HttpSellerClient",
    "HttpShopClient",
    "MockShopClient",
    "Point",
    "SellerClient",
    "ShopApi",
    "ShopApiConnector",
    "ShopClient",
    "ShopUnavailable",
    "get_connector",
    "get_shop_client",
    "shop_api",
    "use_client",
]


class ShopClient(Protocol):
    async def list_failed_orders(self, query: ShopFailedOrdersQuery) -> ShopFailedOrdersResult: ...


class HttpShopClient(ShopApiConnector):
    """예전 이름: shop-api 커넥터 + 실패 주문 원본(`list_failed_orders`)·결제 수."""

    async def payment_counts(self, window_from: datetime, window_to: datetime) -> dict:
        """5분 구간별 결제 수 (`/metric-series` `payment_count`). 초과분의 평소 실패에 쓴다."""
        return dict(await self.transactions_volume(window_from, window_to) or [])

    async def list_failed_orders(self, query: ShopFailedOrdersQuery) -> ShopFailedOrdersResult:
        return await self.failed_orders_raw(query)


class SellerClient(Protocol):
    async def seller_profile(self, seller_id: str) -> ShopSellerProfile: ...

    async def seller_prices(self, seller_id: str) -> ShopSellerPricesResult: ...


class HttpSellerClient:
    """`GET /api/shop/sellers/{id}/profile`·`/prices`로 판매자 정보를 읽는다 (fraud_score).

    없는 판매자는 `ShopUnavailable("SELLER_NOT_FOUND")`이다(shop-api 404).
    """

    def __init__(self, api: ShopApi) -> None:
        self.api = api

    async def seller_profile(self, seller_id: str) -> ShopSellerProfile:
        data = await self.api.get(f"/sellers/{quote(seller_id, safe='')}/profile", {})
        try:
            return ShopSellerProfile.model_validate(data)
        except ValidationError as exc:
            raise ShopUnavailable("BAD_RESPONSE", "판매자 정보 응답 형식이 계약과 다르다") from exc

    async def seller_prices(self, seller_id: str) -> ShopSellerPricesResult:
        data = await self.api.get(f"/sellers/{quote(seller_id, safe='')}/prices", {})
        try:
            return ShopSellerPricesResult.model_validate(data)
        except ValidationError as exc:
            raise ShopUnavailable("BAD_RESPONSE", "판매자 가격 응답 형식이 계약과 다르다") from exc


class HttpMetricSource(ShopApiConnector):
    """예전 이름: shop-api 커넥터 + 지표 시계열(`fetch`)·실패 주문 원본(`failed_orders`)."""

    async def failed_orders(
        self, window_from: datetime, window_to: datetime
    ) -> list[FailedOrder] | None:
        """구간의 실패 결제(실패 코드 포함). 읽지 못하면 None."""
        query = ShopFailedOrdersQuery(from_=window_from, to=window_to, limit=FAILED_ORDERS_LIMIT)
        try:
            result = await self.failed_orders_raw(query)
        except ShopUnavailable:
            return None
        return list(result.orders)

    async def fetch(self, metric: str, window_from: datetime, window_to: datetime) -> list[Point]:
        return await self.metric_series(metric, window_from, window_to)


def demo_shift(window_from: datetime) -> timedelta:
    """시드 장애 시작을 데모 anchor로 옮길 거리. `DEMO_ALIGN_FROM` 전 구간은 0(고정 날짜)이다.

    데모 anchor는 이 경로(impact_analysis)만 정한다(`demo_anchor.anchor_for`). 구간 시작이 저장된
    anchor와 1분 이내면 같은 사건이라 기존 anchor를, 그 밖에는 새 사건이라 새 anchor를 쓴다.
    """
    if window_from < DEMO_ALIGN_FROM:
        return timedelta(0)
    return demo_anchor.anchor_for(window_from) - INCIDENT_FROM


def _demo_order(order: FailedOrder, shift: timedelta) -> FailedOrder:
    """장애 구간 안 주문은 [허용 오차, 장애 길이) 안으로 비례 압축하고, 함정 주문은 그대로 옮긴다.

    같은 사건 재조회는 구간 시작이 anchor보다 최대 1분(허용 오차) 늦을 수 있다. 장애 주문을 그 뒤에
    두어야 재조회 구간에서도 6건이 모두 들어간다.
    """
    offset = order.failed_at - INCIDENT_FROM
    if INCIDENT_FROM <= order.failed_at < INCIDENT_TO:
        lead = demo_anchor.SAME_INCIDENT_TOLERANCE
        span = demo_anchor.incident_length() - lead
        offset = lead + offset * (span / (INCIDENT_TO - INCIDENT_FROM))
    return order.model_copy(update={"failed_at": INCIDENT_FROM + shift + offset})


class MockShopClient:
    """시드 데이터를 구간(from~to, 양끝 포함)과 카테고리로 걸러 돌려준다.

    요청 구간이 `DEMO_ALIGN_FROM` 이후면 시드 장애를 데모 anchor로 옮긴다. 장애 주문 6건은
    [anchor, anchor + 장애 길이) 안에, 함정 3건은 anchor 기준 −5분·+45분·+90분에 놓인다.
    """

    async def list_failed_orders(self, query: ShopFailedOrdersQuery) -> ShopFailedOrdersResult:
        shift = demo_shift(query.from_)
        seeds = [_demo_order(o, shift) if shift else o for o in SEED_FAILED_ORDERS]
        orders = [
            o
            for o in seeds
            if o.tenant_id == query.tenant_id
            and query.from_ <= o.failed_at <= query.to
            and (query.category is None or o.category == query.category)
        ]
        return ShopFailedOrdersResult(orders=orders)


def shop_api(env: Mapping[str, str] = os.environ) -> ShopApi:
    """`AX_SHOP_API_URL`(없으면 `http://shop-api:8010`)의 shop-api."""
    return ShopApi(env.get("AX_SHOP_API_URL") or DEFAULT_SHOP_API_URL)


# 카탈로그 `connector` → 커넥터를 만드는 함수 (지금은 shop-api 하나)
CONNECTORS = {"shop-api": lambda env: HttpMetricSource(shop_api(env))}


def get_connector(env: Mapping[str, str] = os.environ) -> Connector:
    """회사 카탈로그의 `connector`로 커넥터를 만든다. 모르는 이름이면 NotImplementedError."""
    name = catalog.current().connector
    make = CONNECTORS.get(name)
    if make is None:
        raise NotImplementedError(f"커넥터 {name}가 없다 (clients.shop_client.CONNECTORS)")
    return make(env)


def get_shop_client(env: Mapping[str, str] = os.environ) -> ShopClient | Connector:
    """영향 분석이 쓰는 커넥터. `DATA_MOCK=1`(테스트 전용)이면 목, 아니면 카탈로그의 커넥터.

    캣북스(shop-api)는 예전 이름 `HttpShopClient`(같은 `ShopApiConnector`)를 준다.
    """
    if env.get("DATA_MOCK") == "1":
        return MockShopClient()
    if catalog.current().connector == "shop-api":
        return HttpShopClient(shop_api(env))
    return get_connector(env)
