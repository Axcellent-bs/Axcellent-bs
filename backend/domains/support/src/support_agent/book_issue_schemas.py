"""책별 집계 `book_issue_stats`의 입력·출력 모델은 계약(`schemas.book_issue`)에 있다.

여기서는 같은 이름으로 다시 내보내 기존 import(`from support_agent.book_issue_schemas import ...`)가
그대로 되게 하고, 고객지원 안에서만 쓰는 문의 서비스 쪽 모델(주문별 문의 건수)만 둔다.
"""

from __future__ import annotations

from pydantic import Field

from ax_contracts.schemas.book_issue import (
    PERIOD_DAYS,
    BookIssueInput,
    BookIssuePeriod,
    BookIssueReport,
    BookIssueRow,
    CategoryCount,
)
from ax_contracts.schemas.common import AxModel

__all__ = [
    "PERIOD_DAYS",
    "BookIssueInput",
    "BookIssuePeriod",
    "BookIssueReport",
    "BookIssueRow",
    "CategoryCount",
    "InquiryOrderCounts",
    "InquiryOrderCountsInput",
    "OrderInquiryCount",
]


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
