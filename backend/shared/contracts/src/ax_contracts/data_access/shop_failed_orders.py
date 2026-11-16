"""예시: shop-api의 실패 주문 목록 조회 (data 에이전트가 사용).

각 서비스 담당이 이 파일을 복사해 자기 조회 형식을 같은 방식으로 추가한다.
`buyer_id`는 가명 ID만 쓴다. 금액은 정수 원이다.
"""

from __future__ import annotations

from typing import Literal

from pydantic import ConfigDict, Field

from ax_contracts.schemas.common import TENANT_ID_DEFAULT, AxModel, UtcDatetime


class ShopFailedOrdersQuery(AxModel):
    """입력. `from`은 파이썬 예약어라 별칭으로 받는다."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    from_: UtcDatetime = Field(alias="from")
    to: UtcDatetime
    category: str | None = None
    limit: int = Field(default=1000, ge=1)
    tenant_id: str = TENANT_ID_DEFAULT
    # 고를 결제 상태 (2026-11-16 추가). `failed`(기본): 결제 실패. `timeout`: 결제사 응답이 끊겨
    # 승인됐는지 모르고 아직 다시 확인 전인 결제(응답 모양은 같고 reason=PG_TIMEOUT,
    # failed_at=결제 시도 시각). 판매 서버 `GET /api/shop/failed-orders?status=`
    status: Literal["failed", "timeout"] = "failed"


class FailedOrder(AxModel):
    order_id: str
    buyer_id: str
    amount: int
    category: str
    failed_at: UtcDatetime
    reason: str
    tenant_id: str = TENANT_ID_DEFAULT


class ShopFailedOrdersResult(AxModel):
    """출력. 조회 자체의 근거(쿼리·시각)는 MCP 봉투와 `Evidence`로 붙인다."""

    orders: list[FailedOrder] = Field(default_factory=list)
    collected_at: UtcDatetime | None = None  # 조회 시각. 기존 소비자와 호환되게 선택 필드
    truncated: bool = False  # limit에 걸려 잘렸으면 True
