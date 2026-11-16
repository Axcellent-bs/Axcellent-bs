"""책별 집계 모델이 계약으로 옮겨진 뒤에도 고객지원의 기존 import가 같은 모델을 돌려준다."""

from ax_contracts.schemas import book_issue as contract
from support_agent import book_issue_schemas as domain


def test_the_domain_module_re_exports_the_contract_models() -> None:
    for name in (
        "PERIOD_DAYS",
        "BookIssueInput",
        "BookIssuePeriod",
        "BookIssueReport",
        "BookIssueRow",
        "CategoryCount",
    ):
        assert getattr(domain, name) is getattr(contract, name), name


def test_the_inquiry_side_models_stay_in_the_support_domain() -> None:
    """문의 서비스와 claims 사이에서만 쓰는 모델은 계약으로 옮기지 않았다."""
    for name in ("InquiryOrderCountsInput", "OrderInquiryCount", "InquiryOrderCounts"):
        assert hasattr(domain, name) and not hasattr(contract, name), name
