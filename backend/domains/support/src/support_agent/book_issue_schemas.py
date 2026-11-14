"""책(상품)별 클레임 집계 skill `book_issue_stats`의 입력·출력 모델. 숫자는 모두 코드가 센다."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from ax_contracts.schemas.common import AxModel

BookIssuePeriod = Literal["day", "week", "month"]
PERIOD_DAYS: dict[str, int] = {"day": 1, "week": 7, "month": 30}


class BookIssueInput(AxModel):
    period: BookIssuePeriod = "month"  # 지금부터 거슬러 올라가는 기간 (1일·7일·30일)
    limit: int = Field(default=20, ge=1, le=100)  # 위에서 몇 개까지 (클레임 많은 순)
    min_claims: int = Field(default=1, ge=1)  # 이 건수 이상인 상품만
    min_inquiries: int = Field(default=1, ge=1)  # 클레임이 없어도 문의가 이 건수 이상이면 보인다
    product_ids: list[str] | None = Field(default=None, max_length=100)  # 주면 이 상품만
    include_inquiries: bool = True  # False면 문의 서비스와 판매 서버(주문 상품)를 부르지 않는다
    include_sales: bool = True  # False면 판매 서버 주문 목록을 읽지 않는다 (claim_rate 없음)


class CategoryCount(AxModel):
    category: str  # 문의 분류 코드 (`categories.py`), 분류 전이면 "unknown"
    count: int


class BookIssueRow(AxModel):
    """상품 하나의 클레임 집계. 고객 이름·구매자 ID·글은 싣지 않는다."""

    product_id: str
    title: str  # 화면용(가장 최근 클레임의 품목 제목). 묶는 열쇠가 아니다
    claim_count: int  # 이 상품 품목이 든 클레임 수 (한 클레임은 상품마다 한 번)
    claims_by_type: dict[str, int] = Field(default_factory=dict)  # cancel·return·exchange
    defect_claim_count: int  # 사유가 불량·파손·오배송·누락인 클레임 수
    claim_qty: int = 0  # 클레임으로 접수된 수량 (고객이 요청한 수량의 합)
    return_qty: int  # 반품이 입고된 수량
    refund_cash_total: int  # 지급이 끝난 클레임의 이 상품 품목 환불액 합 (정수 원)
    # 문의는 주문에만 이어져 있어 주문 단위로 센다 (품목이 여럿인 주문은 모든 상품에 센다).
    # 읽지 못했으면 None (0으로 채우지 않는다)
    inquiry_count: int | None = None
    inquiry_count_single_item: int | None = None  # 상품 하나뿐인 주문의 문의만 센 값
    inquiry_attribution: Literal["order_level"] = "order_level"
    top_categories: list[CategoryCount] = Field(default_factory=list)  # 문의 분류 상위 3개
    # 판매 수량을 못 읽었으면 None (0으로 채우지 않는다). 비율 = 클레임 수량 / 같은 기간 판매 수량.
    # 접수 기간 기준이라 오래전 주문의 클레임도 들어가 1을 넘을 수 있다
    sold_qty: int | None = None
    claim_rate: float | None = None
    reason: str  # 코드가 만든 한 줄 근거


class BookIssueReport(AxModel):
    period: BookIssuePeriod
    period_start: str
    period_end: str
    items: list[BookIssueRow] = Field(default_factory=list)
    total_claims: int  # 기간 안의 전체 클레임 수
    claims_without_product: int  # 상품 ID가 없어 상품별로 못 센 클레임 수 (옛 기록 등)
    sales_read: bool = False  # 판매 수량을 끝까지 읽어 합쳤는가
    sales_orders_scanned: int | None = None  # 판매 수량을 세려고 읽은 주문 수
    inquiries_read: bool = False  # 문의 건수를 읽어 합쳤는가
    total_inquiries: int | None = None  # 기간 안의 전체 문의 수 (읽었을 때)
    inquiries_not_attributed: int | None = None  # 결제 전 주문 등으로 상품에 못 붙인 문의 수
    missing: list[str] = Field(default_factory=list)  # 못 읽은 값과 이유


# ---- 문의 서비스 쪽: 주문별 문의 건수 (`inquiry_order_counts`)
class InquiryOrderCountsInput(AxModel):
    period: BookIssuePeriod = "month"


class OrderInquiryCount(AxModel):
    """주문 하나에 이어진 문의 집계. 고객 이름·글은 싣지 않는다."""

    order_id: str
    inquiry_count: int
    categories: dict[str, int] = Field(default_factory=dict)


class InquiryOrderCounts(AxModel):
    period: BookIssuePeriod
    period_start: str
    period_end: str
    total_inquiries: int  # 기간 안의 전체 문의 수
    without_order: int  # 주문이 이어지지 않은 문의 수
    orders: list[OrderInquiryCount] = Field(default_factory=list)  # 문의 많은 순, 최대 200개
    truncated: bool = False  # 주문이 200개를 넘어 잘렸는가
