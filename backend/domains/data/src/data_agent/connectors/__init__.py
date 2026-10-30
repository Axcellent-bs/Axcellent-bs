"""데이터 커넥터: data 엔진이 회사 시스템에서 지표·실패 이벤트·거래량을 읽는 길.

엔진(이상 탐지·영향 분석·감시·리포트의 주문 수)은 이 인터페이스(`Connector`)만 쓴다. 회사 시스템의
응답을 표준 형식으로 바꾸는 일은 커넥터 안에서만 한다. 어떤 커넥터를 쓸지는 회사 카탈로그의
`connector`(캣북스: `shop-api`)다.

구현할 메서드 3개 (읽기만 한다. 숫자를 지어내지 않는다):
- `metric_series(metric_id, from, to, bucket)`: 카탈로그 지표의 5분 시점별 값 [(시각, 값)].
  모르는 지표는 빈 목록이다(기준선이 없어 판정하지 않는다).
- `failure_events(from, to, category=, limit=, tenant_id=)`: 실패 이벤트(`FailureEvent`) 목록.
  원인 쪽 코드(`code`)는 카탈로그 `fault_codes`로 장애 쪽인지 가른다.
- `transactions_volume(from, to)`: 5분 거래 시도 수 [(시각, 값)]. 비율 지표의 분모와 영향 분석의
  "평소 실패"에 쓴다. 주지 않는 커넥터는 None이다(그 비교는 확인할 수 없음).

읽지 못하면 `ConnectorUnavailable`(코드·이유)을 낸다. 0으로 채우지 않는다.

판매자 정보·가격(사기 점수 전용)은 아직 이 인터페이스에 넣지 않았다(`clients.shop_client`).
"""

from __future__ import annotations

import weakref
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol, runtime_checkable

from ax_contracts.schemas.common import TENANT_ID_DEFAULT

Point = tuple[datetime, float]
EVENTS_LIMIT = 5000  # 실패 이벤트 한 번 조회 기본 한도 (shop-api 최대와 같다)


class ConnectorUnavailable(Exception):
    """회사 시스템에서 데이터를 받지 못했다. `code`는 시스템 오류 코드 또는 UNAVAILABLE."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


class ConnectorUnsupported(ConnectorUnavailable):
    """이 커넥터는 그 자료를 주지 않는다 (예: 목 지표 공급자의 실패 이벤트)."""

    def __init__(self, what: str) -> None:
        super().__init__("UNSUPPORTED", f"이 커넥터는 {what}을 주지 않는다")


@dataclass(frozen=True)
class FailureEvent:
    """실패 이벤트 하나 (표준 형식). 캣북스: 결제가 실패한 주문 하나."""

    at: datetime  # 실패 시각 (UTC)
    code: str  # 원인 쪽 코드 (캣북스: failure_code). 없으면 "" 또는 "UNKNOWN"
    customer_id: str  # 영향받은 사람 (캣북스: buyer_id)
    amount: int  # 금액 (정수, 카탈로그 money_unit)
    category: str  # 분류 (캣북스: 상품 카테고리)
    event_id: str = ""  # 이벤트 id (캣북스: order_id)

    # 예전 이름 (shop-api 실패 주문 `FailedOrder`의 칸 이름)
    @property
    def order_id(self) -> str:
        return self.event_id

    @property
    def buyer_id(self) -> str:
        return self.customer_id

    @property
    def failed_at(self) -> datetime:
        return self.at

    @property
    def reason(self) -> str:
        return self.code


@dataclass(frozen=True)
class FailureEvents:
    events: list[FailureEvent] = field(default_factory=list)
    truncated: bool = False  # 조회 한도에 걸려 일부만 왔는지


@runtime_checkable
class Connector(Protocol):
    async def metric_series(
        self, metric_id: str, window_from: datetime, window_to: datetime, bucket: str = "5m"
    ) -> list[Point]: ...

    async def failure_events(
        self,
        window_from: datetime,
        window_to: datetime,
        *,
        category: str | None = None,
        limit: int = EVENTS_LIMIT,
        tenant_id: str = TENANT_ID_DEFAULT,
    ) -> FailureEvents: ...

    async def transactions_volume(
        self, window_from: datetime, window_to: datetime
    ) -> list[Point] | None: ...


def event_from_order(order: Any) -> FailureEvent:
    """shop-api 실패 주문(`FailedOrder`) → 표준 실패 이벤트."""
    return FailureEvent(
        at=order.failed_at,
        code=order.reason,
        customer_id=order.buyer_id,
        amount=order.amount,
        category=order.category,
        event_id=order.order_id,
    )


def as_events(items: Any) -> list[FailureEvent]:
    """실패 이벤트 목록으로 맞춘다. 표준 이벤트, 실패 주문 목록, 실패 주문 조회 결과를 받는다."""
    if isinstance(items, FailureEvents):
        return list(items.events)
    if hasattr(items, "orders") and not isinstance(items, list | tuple):
        items = items.orders
    return [e if isinstance(e, FailureEvent) else event_from_order(e) for e in items]


class LegacySourceConnector:
    """예전 공급자(`fetch`·`failed_orders`·`list_failed_orders`·`payment_counts`)를 커넥터로 감싼다.

    테스트의 가짜 공급자와 목 모드(`DATA_MOCK`, 테스트 전용)가 쓴다. 각 메서드는 감싼 것에
    그 기능이 없으면 `ConnectorUnsupported`(거래량은 None)다. 실제 모드는 `ShopApiConnector`다.
    """

    def __init__(self, source: Any, transactions_metric: str | None = None) -> None:
        self.source = source
        self.transactions_metric = transactions_metric

    async def metric_series(
        self, metric_id: str, window_from: datetime, window_to: datetime, bucket: str = "5m"
    ) -> list[Point]:
        if not hasattr(self.source, "fetch"):
            raise ConnectorUnsupported("지표 시계열")
        return list(await self.source.fetch(metric_id, window_from, window_to))

    async def failure_events(
        self,
        window_from: datetime,
        window_to: datetime,
        *,
        category: str | None = None,
        limit: int = EVENTS_LIMIT,
        tenant_id: str = TENANT_ID_DEFAULT,
    ) -> FailureEvents:
        if hasattr(self.source, "list_failed_orders"):
            from ax_contracts.data_access.shop_failed_orders import ShopFailedOrdersQuery

            query = ShopFailedOrdersQuery(
                from_=window_from, to=window_to, category=category, limit=limit, tenant_id=tenant_id
            )
            result = await self.source.list_failed_orders(query)
            return FailureEvents(as_events(result), bool(getattr(result, "truncated", False)))
        if hasattr(self.source, "failed_orders"):
            orders = await self.source.failed_orders(window_from, window_to)
            if orders is None:
                raise ConnectorUnavailable("UNAVAILABLE", "실패 이벤트를 읽지 못했다")
            return FailureEvents(as_events(orders))
        raise ConnectorUnsupported("실패 이벤트")

    async def transactions_volume(
        self, window_from: datetime, window_to: datetime
    ) -> list[Point] | None:
        if hasattr(self.source, "payment_counts"):
            return sorted((await self.source.payment_counts(window_from, window_to)).items())
        if self.transactions_metric and hasattr(self.source, "fetch"):
            return list(await self.source.fetch(self.transactions_metric, window_from, window_to))
        return None


# 같은 공급자에는 같은 커넥터를 준다 (감시의 지난 7일 캐시가 공급자마다 이어지게)
_wrapped: weakref.WeakKeyDictionary[Any, LegacySourceConnector] = weakref.WeakKeyDictionary()


def as_connector(source: Any, transactions_metric: str | None = None) -> Connector:
    """커넥터면 그대로, 예전 공급자면 감싼 커넥터. 같은 공급자는 같은 커넥터다."""
    if isinstance(source, Connector):
        return source
    try:
        wrapped = _wrapped.get(source)
    except TypeError:  # 약한 참조를 못 거는 값
        return LegacySourceConnector(source, transactions_metric)
    if wrapped is None or wrapped.transactions_metric != transactions_metric:
        wrapped = LegacySourceConnector(source, transactions_metric)
        try:
            _wrapped[source] = wrapped
        except TypeError:
            pass
    return wrapped
