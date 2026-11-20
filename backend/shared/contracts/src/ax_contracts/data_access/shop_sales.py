"""shop-api 판매 기록 내보내기 (data 수집기가 사용, 매출 분석의 원천).

`GET /api/shop/sales-export?cursor&limit` → `ShopSalesExportResult`. 읽기 전용이다.

- 순서: `(updated_at, order_id)` 오름차순. `next_cursor`를 다음 요청의 `cursor`로 넘기면
  이어서 받는다. 상태가 바뀐 주문(결제 완료 → 취소, 환불 등)은 `updated_at`이 바뀌어
  다시 나온다. 받는 쪽은 같은 `order_id`를 덮어쓴다(upsert).
- `next_cursor`는 마지막으로 준 주문 위치다. 더 받을 것이 없어도 비우지 않고 돌려준다(다음 수집이
  그 자리부터 시작). 처음부터 받을 때는 `cursor`를 비운다. 커서 글자 모양은 shop-api만 안다.
- 개인정보는 가명 `buyer_id`만. 구매자 이름·연락처·주소는 담지 않는다.
- 금액은 정수 원, 시각은 UTC. 정제(음수 금액·0 이하 수량 버리기)는 받는 쪽(data)이 한다. 그래서
  여기서는 금액·수량에 범위를 걸지 않는다(다른 회사 원본도 같은 모양으로 받는다).
- 새로 더한 형식이라 꼭 있어야 하는 칸(주문 번호·구매자·상태·시각)만 필수이고 나머지는 선택이다.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from ax_contracts.schemas.common import TENANT_ID_DEFAULT, AxModel, UtcDatetime

SALES_EXPORT_LIMIT_MAX = 1000


class ShopSalesExportQuery(AxModel):
    """입력. `cursor`가 없으면 처음부터."""

    cursor: str | None = Field(default=None, max_length=200)
    limit: int = Field(default=500, ge=1, le=SALES_EXPORT_LIMIT_MAX)
    tenant_id: str = TENANT_ID_DEFAULT


class SalesOrderLine(AxModel):
    """주문 안의 상품 줄. `coupon_share`·`points_share`는 주문 할인을 줄마다 나눈 몫(정수 원)."""

    product_id: str
    category_code: str | None = None  # 상품이 지워져 분류를 알 수 없으면 None
    title: str | None = None  # 주문 때 상품 이름 (화면 표시용, 없을 수 있음)
    qty: int
    unit_price: int
    coupon_share: int = 0
    points_share: int = 0
    # 상품 세부 분류 (예: 도서의 장르). 큰 분류는 category_code 그대로. 없으면 None (11/16 추가)
    genre_code: str | None = None


class SalesOrderRefund(AxModel):
    """주문 안의 환불 한 건. 매출에서는 `refunded_at` 기준 기간에 뺀다."""

    refund_id: str | None = None
    amount: int
    refunded_at: UtcDatetime


class SalesOrderRow(AxModel):
    """주문 한 건 (판매 기록 한 줄)."""

    order_id: str
    buyer_id: str  # 가명 ID
    # 주문 상태 (캣북스: pending_payment·paid·payment_failed·cancelled·shipped·delivered 등)
    status: str
    created_at: UtcDatetime
    updated_at: UtcDatetime
    items_amount: int = 0  # 상품 금액 합 = Σ qty × unit_price (할인 전, 배송비 제외)
    shipping_fee: int = 0
    points_used: int = 0
    coupon_discount: int = 0
    src_source: str | None = None  # 유입 출처 (utm_source). 없으면 None
    src_medium: str | None = None
    src_campaign: str | None = None
    # 결제 상태: 성공한 결제가 있으면 succeeded, 아니면 마지막 결제 시도의 상태
    # (캣북스: failed·unknown 등), 결제 시도가 없으면 None
    payment_status: str | None = None
    lines: list[SalesOrderLine] = Field(default_factory=list)
    refunds: list[SalesOrderRefund] = Field(default_factory=list)
    tenant_id: str = TENANT_ID_DEFAULT
    # 취소 주체 (11/20 추가). 손님 취소 → buyer, 판매자·관리자 → seller, 결제 시간 초과 등 자동 →
    # system. 취소가 아니거나 판매 서버가 모르면 None (지어내지 않는다). 변화 감지기가
    # `order.cancelled`의 `reason_code`로 쓰고, 비어 있으면 그 이벤트를 만들지 않는다
    cancel_reason_code: Literal["buyer", "seller", "system"] | None = None


class ShopSalesExportResult(AxModel):
    """출력. `orders`는 `(updated_at, order_id)` 오름차순."""

    orders: list[SalesOrderRow] = Field(default_factory=list)
    next_cursor: str | None = None  # 다음 요청의 cursor (받은 것이 없으면 요청한 cursor 그대로)
    has_more: bool = False  # limit에 걸려 더 받을 것이 있으면 True
    collected_at: UtcDatetime | None = None  # 조회 시각
    tenant_id: str = TENANT_ID_DEFAULT
