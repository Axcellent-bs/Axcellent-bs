"""shop-api의 지표 시계열 조회 (data 에이전트 anomaly_detection이 사용).

지표는 shop-api가 DB에서 코드로 계산한다 (LLM 없음).
- `order_count`: 버킷 안에 접수된 주문 수
- `order_fail_count`: 버킷 안에 결제가 실패한 주문 수 (`payment.failed` 기준)
- `payment_fail_rate`: 버킷 안 결제 시도 중 실패 비율 `실패 / (성공 + 실패)`
- `payment_count`: 버킷 안 결제 시도 수
- `order_amount`: 버킷 안에 접수된 주문 중 결제가 끝난 주문의 `total_amount` 합계(원, 매출).
  결제 대기·결제 실패·취소 주문은 빼고 환불은 빼지 않는다
- `coupon_order_count`: 버킷 안에 접수된 주문 중 쿠폰을 쓴 주문 수. 쿠폰 장수를 돌려받은
  주문(취소·전액 환불)은 뺀다. 결제 대기·실패 주문은 장수를 잡고 있어 센다
- `coupon_discount_amount`: 위 쿠폰 주문의 `coupon_discount` 합계(원)

조회 시각(`collected_at`)과 잘림 여부(`truncated`)는 MCP 공통 봉투와 같은 형태이며, 기존 소비자와
호환되도록 선택 필드다.
"""

from __future__ import annotations

from typing import Literal

from pydantic import ConfigDict, Field

from ax_contracts.schemas.common import TENANT_ID_DEFAULT, AxModel, UtcDatetime

MetricName = Literal[
    "order_count",
    "order_fail_count",
    "payment_fail_rate",
    "payment_count",
    "order_amount",
    "coupon_order_count",
    "coupon_discount_amount",
]
# 버킷은 5m만 합의됨(#67). 1m·15m·1h는 data 확인 뒤 추가한다(#86)
Bucket = Literal["5m"]


class ShopMetricSeriesQuery(AxModel):
    """입력. `from`은 파이썬 예약어라 별칭으로 받는다."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    metric: MetricName
    from_: UtcDatetime = Field(alias="from")
    to: UtcDatetime
    bucket: Bucket = "5m"
    tenant_id: str = TENANT_ID_DEFAULT


class MetricPoint(AxModel):
    ts: UtcDatetime  # 버킷 시작 시각 (UTC)
    value: float


class ShopMetricSeriesResult(AxModel):
    metric: MetricName
    bucket: Bucket = "5m"
    points: list[MetricPoint] = Field(default_factory=list)
    collected_at: UtcDatetime | None = None
    truncated: bool = False
    tenant_id: str = TENANT_ID_DEFAULT
