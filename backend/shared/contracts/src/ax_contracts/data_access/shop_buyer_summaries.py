"""회사 서버 구매자 요약 조회 (data 일별 집계 입력, 3단계 3-1 설계 4.3절).

`GET /api/shop/buyer-summaries?as_of&cursor&limit&changed_since` → `ShopBuyerSummariesResult`.
읽기 전용이다.

- 재구매·코호트·떠날 위험·채널 가치에 필요한 구매자마다의 첫·두 번째·마지막 결제 시각과
  결제 주문 수, 첫 주문 유입, 속성을 준다.
- 받는 쪽(data)은 하루 계산 때 메모리에서만 쓰고 숫자만 저장한다. `buyer_id`는 저장하지 않는다.
- "결제 완료" 정의는 판매 기록 내보내기와 같다(`payment_status = succeeded`이고 취소·결제 실패·
  결제 대기·전액 환불이 아님). 부분 환불은 결제 완료에 남는다.
- `changed_since`가 없으면 전체 구매자, 있으면 그 뒤 결제·취소·속성이 바뀐 구매자만.
- 회사 서버가 이 조회를 못 하면 그 회사의 구매자 숫자는 "확인 못 함"이다(지어내지 않는다).
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from ax_contracts.schemas.common import TENANT_ID_DEFAULT, AxModel, UtcDatetime

BUYER_SUMMARIES_LIMIT_MAX = 1000


class ShopBuyerSummariesQuery(AxModel):
    """입력. `cursor`가 없으면 처음부터."""

    as_of: UtcDatetime  # 이 시각 전 결제만 센다 (기준일 24시). 오늘 주문이 섞이지 않게
    # buyer_id 오름차순. 커서 글자 모양은 회사 서버만 안다
    cursor: str | None = Field(default=None, max_length=200)
    limit: int = Field(default=500, ge=1, le=BUYER_SUMMARIES_LIMIT_MAX)
    changed_since: UtcDatetime | None = None  # 있으면 그 뒤 결제·취소·속성이 바뀐 구매자만
    tenant_id: str = TENANT_ID_DEFAULT


class BuyerItemSummary(AxModel):
    """구매자 한 명의 항목(장르·분류·상품)별 결제 요약."""

    level: Literal["genre", "category", "product"]
    code: str
    first_paid_at: UtcDatetime
    last_paid_at: UtcDatetime
    paid_orders: int


class BuyerSummaryRow(AxModel):
    """구매자 한 명의 요약. 결제 완료 주문이 없으면 시각 칸은 None."""

    buyer_id: str  # 가명. 받는 쪽은 메모리에서만 쓰고 저장하지 않는다
    first_paid_at: UtcDatetime | None = None
    second_paid_at: UtcDatetime | None = None
    last_paid_at: UtcDatetime | None = None
    paid_orders: int = 0
    first_src_source: str | None = None
    attributes: dict[str, str] = Field(default_factory=dict)  # 고객 내보내기와 같은 key·값 code
    items: list[BuyerItemSummary] = Field(default_factory=list)
    tenant_id: str = TENANT_ID_DEFAULT


class ShopBuyerSummariesResult(AxModel):
    """출력. `has_more`면 `next_cursor`로 이어 받는다."""

    buyers: list[BuyerSummaryRow] = Field(default_factory=list)
    next_cursor: str | None = None
    has_more: bool = False
    as_of: UtcDatetime  # 회사 서버가 센 기준 (집계 행 source_cursor로 남긴다)
    tenant_id: str = TENANT_ID_DEFAULT
