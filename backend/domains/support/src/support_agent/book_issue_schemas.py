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
    product_ids: list[str] | None = Field(default=None, max_length=100)  # 주면 이 상품만


class BookIssueRow(AxModel):
    """상품 하나의 클레임 집계. 고객 이름·구매자 ID·글은 싣지 않는다."""

    product_id: str
    title: str  # 화면용(가장 최근 클레임의 품목 제목). 묶는 열쇠가 아니다
    claim_count: int  # 이 상품 품목이 든 클레임 수 (한 클레임은 상품마다 한 번)
    claims_by_type: dict[str, int] = Field(default_factory=dict)  # cancel·return·exchange
    defect_claim_count: int  # 사유가 불량·파손·오배송·누락인 클레임 수
    return_qty: int  # 반품이 입고된 수량
    refund_cash_total: int  # 지급이 끝난 클레임의 이 상품 품목 환불액 합 (정수 원)
    # 판매 수량을 아직 읽지 않아 비율은 내지 않는다 (0으로 채우지 않는다)
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
    missing: list[str] = Field(default_factory=list)  # 못 읽은 값과 이유
