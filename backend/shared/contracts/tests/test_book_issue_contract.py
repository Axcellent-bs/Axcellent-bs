"""책별 집계 `book_issue_stats` 계약과 예산 검토 입력의 `product_ids` 선택 칸."""

import pytest
from pydantic import ValidationError

from ax_contracts.schemas.book_issue import (
    PERIOD_DAYS,
    BookIssueInput,
    BookIssueReport,
    BookIssueRow,
    CategoryCount,
)
from ax_contracts.schemas.finance import CampaignBudgetReviewInput

OLD_REQUEST = {
    "campaign_id": "cmp_1",
    "campaign_name": "가을 독서 주간",
    "channel": "instagram",
    "requested_krw": 500000,
}


def test_book_issue_input_defaults_and_limits() -> None:
    q = BookIssueInput()
    assert (q.period, q.limit, q.min_claims, q.min_inquiries) == ("month", 20, 1, 1)
    assert q.product_ids is None and q.include_inquiries and q.include_sales
    assert PERIOD_DAYS == {"day": 1, "week": 7, "month": 30}
    with pytest.raises(ValidationError):
        BookIssueInput(period="year")  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        BookIssueInput(product_ids=[f"prd_{n}" for n in range(101)])
    with pytest.raises(ValidationError):
        BookIssueInput(unknown_field=1)  # type: ignore[call-arg]  # extra="forbid"


def test_book_issue_report_round_trips_with_the_reason_map_and_unread_values() -> None:
    row = BookIssueRow(
        product_id="prd_1",
        title="책",
        claim_count=3,
        claims_by_type={"return": 3},
        claims_by_reason={"defect": 2, "change_of_mind": 1},
        defect_claim_count=2,
        return_qty=1,
        refund_cash_total=10000,
        reason="클레임 3건",
        top_categories=[CategoryCount(category="delivery", count=1)],
    )
    assert row.sold_qty is None and row.claim_rate is None and row.inquiry_count is None
    report = BookIssueReport(
        period="month",
        period_start="2026-10-17T00:00:00Z",
        period_end="2026-11-16T00:00:00Z",
        items=[row],
        total_claims=3,
        claims_without_product=0,
    )
    again = BookIssueReport.model_validate(report.model_dump(mode="json"))
    assert again == report
    assert again.items[0].claims_by_reason == {"defect": 2, "change_of_mind": 1}
    assert (
        again.sales_read is False and again.inquiries_read is False
    )  # 못 읽은 것은 읽었다고 하지 않는다


def test_a_row_without_claim_reasons_keeps_working() -> None:
    """`claims_by_reason`을 모르는 옛 응답도 읽힌다 (기본값은 빈 map)."""
    row = BookIssueRow.model_validate(
        {
            "product_id": "prd_1",
            "title": "책",
            "claim_count": 0,
            "defect_claim_count": 0,
            "return_qty": 0,
            "refund_cash_total": 0,
            "reason": "기간 안 클레임 없음",
        }
    )
    assert row.claims_by_reason == {} and row.claims_by_type == {}


def test_budget_review_input_accepts_old_requests_and_the_new_optional_product_ids() -> None:
    old = CampaignBudgetReviewInput.model_validate(OLD_REQUEST)
    assert old.product_ids == []  # 옛 요청은 그대로 받는다
    new = CampaignBudgetReviewInput.model_validate(
        {**OLD_REQUEST, "product_ids": ["prd_1", "prd_2"]}
    )
    assert new.product_ids == ["prd_1", "prd_2"]
    assert CampaignBudgetReviewInput.model_validate(new.model_dump(mode="json")) == new


def test_budget_review_product_ids_are_limited_and_extra_keys_still_fail() -> None:
    too_many = [f"prd_{n}" for n in range(11)]
    with pytest.raises(ValidationError):
        CampaignBudgetReviewInput.model_validate({**OLD_REQUEST, "product_ids": too_many})
    with pytest.raises(ValidationError):
        CampaignBudgetReviewInput.model_validate({**OLD_REQUEST, "product_id": "prd_1"})
