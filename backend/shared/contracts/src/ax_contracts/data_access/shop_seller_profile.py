"""shop-api의 판매자 정보 조회 (data 에이전트 fraud_score가 사용).

가명 ID만 쓴다. 사업자 정보는 원문 대신 sha256 해시로만 받는다.
- 사업자 해시는 shop-api `sellers` 테이블에 아직 없다. 그동안 shop-api는 `business_hash`·
  `business_hash_duplicated`를 null로 준다(값을 지어내지 않는다). data는 null이면 사업자 중복
  규칙을 "확인할 수 없음"으로 둔다.
"""

from __future__ import annotations

from pydantic import Field

from ax_contracts.schemas.common import TENANT_ID_DEFAULT, AxModel, UtcDatetime


class ShopSellerProfileQuery(AxModel):
    seller_id: str
    tenant_id: str = TENANT_ID_DEFAULT


class ShopSellerProfile(AxModel):
    seller_id: str
    joined_at: UtcDatetime
    business_hash: str | None = None  # 사업자 정보 sha256. 없으면 null
    business_hash_duplicated: bool | None = None  # 같은 해시의 다른 판매자 존재. 모르면 null
    product_count: int = Field(ge=0)  # 판매 중(active) 상품 수
    listings_last_24h: int = Field(ge=0)
    tenant_id: str = TENANT_ID_DEFAULT
