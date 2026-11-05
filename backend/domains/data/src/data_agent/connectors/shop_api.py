"""캣북스 커넥터: shop-api 읽기 전용 API (`connector = "shop-api"`).

판매 사이트 DB에 직접 쿼리하지 않고 shop-api만 부른다 (`contracts/data-access` `shop_failed_orders`·
`shop_metric_series`). shop-api 응답을 표준 형식(`connectors.FailureEvent`, [(시각, 값)])으로
바꾸는 일은 여기서만 한다.

- 주소는 `AX_SHOP_API_URL`(없으면 `http://shop-api:8010`)이다.
- 토큰: `Authorization: Bearer <data 토큰>`(`DATA_TOKEN`, 목 모드는 `mock:data`).
- 응답 없음·401·403·5xx·형식 오류는 `ShopUnavailable`(= `ConnectorUnavailable`)이다.
  0으로 내지 않는다.
"""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any, get_args

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ax_agent_template import config
from ax_contracts.data_access.shop_failed_orders import (
    ShopFailedOrdersQuery,
    ShopFailedOrdersResult,
)
from ax_contracts.data_access.shop_metric_series import MetricName, ShopMetricSeriesResult
from ax_contracts.schemas.common import TENANT_ID_DEFAULT
from data_agent.connectors import (
    EVENTS_LIMIT,
    CatalogProduct,
    ConnectorUnavailable,
    FailureEvents,
    Point,
    as_events,
)

DEFAULT_SHOP_API_URL = "http://shop-api:8010"
TIMEOUT_SECONDS = 10.0
SHOP_METRICS: frozenset[str] = frozenset(get_args(MetricName))
# shop-api는 한 번에 5분 버킷 2016점(7일)까지 준다. 기준선 7일 + 구간을 하루씩 나눠 읽는다
SERIES_CHUNK = timedelta(days=1)
# 주문 목록은 한 시간씩 나눠 읽는다. 주문이 많은 구간(수만 건)을 한 번에 받으면 shop-api가 그동안
# 묶여 같이 부른 상품·유입 조회가 제한 시간을 넘긴다 (#705)
ORDERS_CHUNK = timedelta(hours=1)
FAILED_ORDERS_LIMIT = EVENTS_LIMIT  # shop-api `/failed-orders`의 최대 limit
TRANSACTIONS_METRIC = "payment_count"  # 거래 시도 수 = 결제 시도 수

# shop-api 오류 이름 (예전 이름). 커넥터 공통 오류와 같은 것이다
ShopUnavailable = ConnectorUnavailable

_client: httpx.AsyncClient | None = None  # 테스트가 `use_client`로 바꿔 끼운다


def use_client(client: httpx.AsyncClient | None) -> None:
    """HTTP 클라이언트를 바꿔 끼운다 (테스트용). None이면 호출마다 새로 만든다."""
    global _client
    _client = client


def _iso(value: datetime) -> str:
    return value.isoformat()


class ShopApi:
    """shop-api 읽기 호출 공통부: 주소, Bearer 토큰, `{data}` 풀기, 오류 변환."""

    def __init__(
        self,
        base_url: str = DEFAULT_SHOP_API_URL,
        *,
        token: str | None = None,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._token = token
        self._client = client

    def _headers(self) -> dict[str, str]:
        try:
            token = self._token or config.agent_token("data")
        except RuntimeError as error:  # DATA_TOKEN 없음: 부르지 않고 확인할 수 없음
            raise ShopUnavailable(
                "UNAUTHORIZED", f"data 토큰이 없어 shop-api를 부를 수 없다 ({error})"
            ) from error
        return {"Authorization": f"Bearer {token}"}

    async def get(self, path: str, params: dict[str, Any]) -> Any:
        """`GET /api/shop{path}`의 `data`를 돌려준다. 200이 아니면 `ShopUnavailable`."""
        headers = self._headers()
        client = self._client or _client or httpx.AsyncClient(timeout=TIMEOUT_SECONDS)
        try:
            response = await client.get(
                f"{self.base_url}/api/shop{path}", params=params, headers=headers
            )
        except httpx.TimeoutException as exc:
            raise ShopUnavailable(
                "UNAVAILABLE",
                f"shop-api가 {TIMEOUT_SECONDS:g}초 안에 답하지 않음 ({type(exc).__name__})",
            ) from exc
        except httpx.HTTPError as exc:
            raise ShopUnavailable(
                "UNAVAILABLE", f"shop-api에 연결할 수 없다 ({type(exc).__name__})"
            ) from exc
        finally:
            if client is not self._client and client is not _client:
                await client.aclose()
        if response.status_code != 200:
            raise _error(response)
        try:
            return response.json()["data"]
        except (ValueError, KeyError, TypeError) as exc:
            raise ShopUnavailable("BAD_RESPONSE", "shop-api 응답에 data가 없다") from exc


def _error(response: httpx.Response) -> ShopUnavailable:
    status = response.status_code
    try:
        code = str(response.json()["error"]["code"])
    except (ValueError, KeyError, TypeError):
        code = "UNAVAILABLE"
    return ShopUnavailable(code, f"shop-api 응답 {status} ({code})")


class ShopApiConnector:
    """shop-api 커넥터 (`Connector` 구현).

    - `metric_series`: `GET /api/shop/metric-series` (5분 버킷, 하루씩 나눠 읽는다). shop-api가
      계산하지 않는 지표(`inquiry_count` 등)는 빈 시계열이다(기준선이 없어 판정하지 않는다).
    - `failure_events`: `GET /api/shop/failed-orders` → 표준 실패 이벤트
      (실패 주문 하나 = 이벤트 하나)
    - `transactions_volume`: 결제 시도 수(`payment_count`) 시계열
    - `campaign_coupons`: `GET /api/shop/coupons?campaign_id=` (캠페인 효과 쿠폰 사용률). 회사 공통
      인터페이스가 아니라 캣북스 커넥터에만 있다
    - `products`·`categories`: `GET /api/shop/products`(판매 중 상품)·`/categories`(코드 → 이름).
      영상 소재 ↔ 실제 상품 연결(content_recommend)에 쓴다. 읽기만 한다
    """

    def __init__(self, api: ShopApi) -> None:
        self.api = api

    async def metric_series(
        self, metric_id: str, window_from: datetime, window_to: datetime, bucket: str = "5m"
    ) -> list[Point]:
        if metric_id not in SHOP_METRICS:
            return []
        points: dict[datetime, float] = {}
        start = window_from
        while start < window_to:
            end = min(start + SERIES_CHUNK, window_to)
            data = await self.api.get(
                "/metric-series",
                {"metric": metric_id, "from": _iso(start), "to": _iso(end), "bucket": bucket},
            )
            try:
                result = ShopMetricSeriesResult.model_validate(data)
            except ValidationError as exc:
                raise ShopUnavailable("BAD_RESPONSE", "지표 응답 형식이 계약과 다르다") from exc
            for point in result.points:
                if window_from <= point.ts < window_to:
                    points[point.ts] = point.value
            start = end
        return sorted(points.items())

    async def failed_orders_raw(self, query: ShopFailedOrdersQuery) -> ShopFailedOrdersResult:
        """shop-api 실패 주문 원본 (계약 `ShopFailedOrdersResult`)."""
        params: dict[str, Any] = {
            "from": _iso(query.from_),
            "to": _iso(query.to),
            "limit": query.limit,
        }
        if query.category is not None:
            params["category"] = query.category
        data = await self.api.get("/failed-orders", params)
        try:
            return ShopFailedOrdersResult.model_validate(data)
        except ValidationError as exc:
            raise ShopUnavailable("BAD_RESPONSE", "실패 주문 응답 형식이 계약과 다르다") from exc

    async def failure_events(
        self,
        window_from: datetime,
        window_to: datetime,
        *,
        category: str | None = None,
        limit: int = EVENTS_LIMIT,
        tenant_id: str = TENANT_ID_DEFAULT,
    ) -> FailureEvents:
        query = ShopFailedOrdersQuery(
            from_=window_from, to=window_to, category=category, limit=limit, tenant_id=tenant_id
        )
        result = await self.failed_orders_raw(query)
        return FailureEvents(as_events(result), result.truncated)

    async def transactions_volume(
        self, window_from: datetime, window_to: datetime
    ) -> list[Point] | None:
        return await self.metric_series(TRANSACTIONS_METRIC, window_from, window_to)

    async def campaign_coupons(self, campaign_id: str) -> list[CampaignCoupon]:
        """그 캠페인 쿠폰 목록 (발급 장수·조회 시점 사용 장수). 없으면 빈 목록."""
        data = await self.api.get("/coupons", {"campaign_id": campaign_id})
        try:
            return [CampaignCoupon.model_validate(item) for item in data]
        except (ValidationError, TypeError) as exc:
            raise ShopUnavailable("BAD_RESPONSE", "쿠폰 응답 형식이 예상과 다르다") from exc

    async def orders(self, window_from: datetime, window_to: datetime) -> list[ShopOrder]:
        """접수 시각이 [from, to)인 주문 (`GET /api/shop/orders?since&until`). 금액·결제·쿠폰 칸.

        구간을 `ORDERS_CHUNK`(한 시간)씩 나눠 차례로 읽는다. 같은 주문 번호는 한 번만 센다.
        """
        found: dict[str, ShopOrder] = {}
        start = window_from
        while start < window_to:
            end = min(start + ORDERS_CHUNK, window_to)
            data = await self.api.get("/orders", {"since": _iso(start), "until": _iso(end)})
            try:
                for item in data:
                    order = ShopOrder.model_validate(item)
                    found.setdefault(order.order_id, order)
            except (ValidationError, TypeError) as exc:
                raise ShopUnavailable("BAD_RESPONSE", "주문 응답 형식이 예상과 다르다") from exc
            start = end
        return list(found.values())

    async def attributed_orders(
        self, window_from: datetime, window_to: datetime, campaign_id: str | None = None,
        source: str | None = None, *, _depth: int = 0,
    ) -> list[AttributedOrder]:  # fmt: skip
        """유입 표시(`src_*`)가 있는 주문 (`GET /api/shop/orders/attributed`, #655).

        취소·전액 환불은 shop-api가 뺀다. 한 번에 1000건을 넘으면(`truncated`) 구간을 반으로 나눠
        다시 읽는다(최대 6번 나눔, 그래도 넘으면 `ShopUnavailable("TRUNCATED")`).
        """
        params: dict[str, Any] = {"from": _iso(window_from), "to": _iso(window_to),
                                  "limit": ATTRIBUTED_LIMIT}  # fmt: skip
        if campaign_id:
            params["campaign_id"] = campaign_id
        if source:
            params["source"] = source
        data = await self.api.get("/orders/attributed", params)
        try:
            truncated = bool(data.get("truncated"))
            rows = [AttributedOrder.model_validate(item) for item in data.get("orders", [])]
        except (ValidationError, TypeError, AttributeError) as exc:
            raise ShopUnavailable("BAD_RESPONSE", "유입 주문 응답 형식이 예상과 다르다") from exc
        if not truncated:
            return rows
        if _depth >= ATTRIBUTED_SPLITS:
            raise ShopUnavailable("TRUNCATED", "유입 주문이 너무 많아 다 읽지 못함")
        mid = window_from + (window_to - window_from) / 2
        first = await self.attributed_orders(window_from, mid, campaign_id, source,
                                             _depth=_depth + 1)  # fmt: skip
        second = await self.attributed_orders(mid, window_to, campaign_id, source,
                                              _depth=_depth + 1)  # fmt: skip
        return first + second

    async def products(self) -> list[CatalogProduct]:
        """판매 중 상품 (shop-api가 판매 중만 준다). 형식이 다르면 `ShopUnavailable`."""
        data = await self.api.get("/products", {})
        try:
            return [CatalogProduct.model_validate(item) for item in data]
        except (ValidationError, TypeError) as exc:
            raise ShopUnavailable("BAD_RESPONSE", "상품 응답 형식이 예상과 다르다") from exc

    async def categories(self) -> dict[str, str]:
        """카테고리 코드 → 이름 (이름이 없는 코드는 뺀다)."""
        data = await self.api.get("/categories", {})
        try:
            return {
                str(item["category_code"]): str(item["name"])
                for item in data
                if item.get("category_code") and item.get("name")
            }
        except (KeyError, TypeError, AttributeError) as exc:
            raise ShopUnavailable("BAD_RESPONSE", "카테고리 응답 형식이 예상과 다르다") from exc


class ShopOrderItem(BaseModel):
    model_config = ConfigDict(extra="ignore")
    product_id: str | None = None  # 상품 기준 성과(#646). 예전 응답에 없으면 None
    qty: int = Field(ge=0)
    unit_price: int = Field(ge=0)


class ShopOrderPayment(BaseModel):
    model_config = ConfigDict(extra="ignore")
    amount: int = Field(ge=0)
    status: str  # succeeded | failed


class ShopOrder(BaseModel):
    """shop-api 주문 한 건에서 캠페인 판매액이 쓰는 칸 (응답 모양은 shop-api 코드에만 있다)."""

    model_config = ConfigDict(extra="ignore")
    order_id: str
    status: str  # pending_payment | paid | payment_failed | ...
    created_at: datetime
    coupon_id: str | None = None
    coupon_discount: int = Field(default=0, ge=0)
    refunded_amount: int = Field(default=0, ge=0)
    items: list[ShopOrderItem] = Field(default_factory=list)
    payments: list[ShopOrderPayment] = Field(default_factory=list)

    @property
    def gross(self) -> int:
        """주문 금액 = 상품가 × 수량 합 (할인 전)."""
        return sum(i.qty * i.unit_price for i in self.items)

    @property
    def paid(self) -> int:
        """성공한 결제 금액 합."""
        return sum(p.amount for p in self.payments if p.status == "succeeded")

    @property
    def settled(self) -> bool:
        """결제 완료 주문 (성공한 결제가 하나라도 있음)."""
        return any(p.status == "succeeded" for p in self.payments)


ATTRIBUTED_LIMIT = 1000  # shop-api 유입 주문 조회 한 번 상한
ATTRIBUTED_SPLITS = 6  # 넘으면 구간을 반으로 나눠 읽는 최대 횟수


class AttributedOrder(BaseModel):
    """유입 표시가 있는 주문 한 건 (shop-api `orders/attributed`, 개인 식별값 없음)."""

    model_config = ConfigDict(extra="ignore")
    order_id: str
    created_at: datetime
    status: str  # paid | pending_payment | payment_failed | ...
    items: list[ShopOrderItem] = Field(default_factory=list)
    src_source: str | None = None
    src_medium: str | None = None
    src_campaign: str | None = None

    @property
    def gross(self) -> int:
        """주문 금액 = 상품가 × 수량 합 (할인 전)."""
        return sum(i.qty * i.unit_price for i in self.items)


class CampaignCoupon(BaseModel):
    """shop-api 쿠폰 한 장에서 캠페인 효과가 쓰는 칸 (응답 모양은 shop-api 코드에만 있다)."""

    model_config = ConfigDict(extra="ignore")
    coupon_id: str
    count: int = Field(ge=0)  # 발급 장수
    used_units: int = Field(ge=0)  # 사용 장수 (조회 시점, 취소·전액 환불은 빼고 셈)
