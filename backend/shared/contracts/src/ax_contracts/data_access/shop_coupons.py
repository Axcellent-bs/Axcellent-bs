"""shop-api의 쿠폰 조회 (data 에이전트 campaign_effect가 쿠폰 사용률을 구할 때 사용).

`GET /api/shop/coupons[?buyer_id&purpose_code&campaign_id]`의 응답 `data`는 `ShopCoupon` 목록이다.
- 사용 장수(`used_units`)와 남은 장수(`remaining_units`)는 저장하지 않고 주문에서 계산한다.
  취소됐거나 전액 환불된 주문은 장수를 돌려주고, 부분 환불은 돌려주지 않는다.
- 사용률은 `used_units / count`(쿠폰 장수 중 쓴 비율)다. 캠페인 기간 안에 쓴 것만의 사용률은
  이 값으로 구할 수 없다.
- 응답에는 `tenant_id`가 없다(shop-api가 한 회사 안에서만 쓴다).
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from ax_contracts.schemas.common import AxModel, UtcDatetime

CouponPurpose = Literal["campaign", "compensation"]


class ShopCouponsQuery(AxModel):
    """입력(질의 매개변수). 모두 선택이고 주면 그 값과 같은 쿠폰만 돌려준다."""

    buyer_id: str | None = None
    purpose_code: CouponPurpose | None = None
    campaign_id: str | None = None


class ShopCoupon(AxModel):
    coupon_id: str
    buyer_id: str  # 가명 ID
    purpose_code: CouponPurpose
    campaign_id: str | None = None  # 보상 쿠폰이면 null일 수 있다
    count: int = Field(ge=0)  # 발급 장수
    unit_value: int = Field(ge=0)  # 한 장의 값(원)
    total_value: int = Field(ge=0)  # count × unit_value (원)
    context_id: str | None = None  # 발급한 요청의 사건 ID
    created_at: UtcDatetime
    expires_at: UtcDatetime | None = None  # 없으면 무기한
    used_units: int = Field(ge=0)  # 사용한 장수 (조회 시점)
    remaining_units: int  # count - used_units
    expired: bool  # 조회 시점에 만료됐다
