"""campaign_effect 유입경로 계약 (#648): 알면 숫자, 모르면 이유. 예전 응답(칸 없음)도 읽힌다."""

import pytest
from pydantic import ValidationError

from ax_contracts.schemas import (
    CampaignAttribution,
    CampaignAttributionProducts,
    CampaignEffectOutput,
    CampaignSourceRow,
    DataSourcesOutput,
    PendingSource,
)


def test_pending_sources_are_listed_apart_from_plan_sources() -> None:
    assert DataSourcesOutput().pending_sources == []
    ad = PendingSource(name="광고 데이터", reason="#647 광고 부분 대기")
    out = DataSourcesOutput(pending_sources=[ad])
    assert out.sources == [] and out.pending_sources[0].name == "광고 데이터"

T0, T1 = "2026-10-14T14:00:00Z", "2026-10-14T16:00:00Z"


def test_old_responses_have_no_attribution() -> None:
    out = CampaignEffectOutput.model_validate({
        "window_from": T0, "window_to": T1, "summary": "",
        "orders": {"status": "unknown", "unknown_reason": "x"},
        "revenue": {"status": "unknown", "unknown_reason": "x"},
        "coupons": {"status": "unknown", "unknown_reason": "x"},
    })  # fmt: skip
    assert out.attribution_effect is None and out.attribution_and_products is None


def test_attribution_is_known_with_numbers_or_unknown_with_a_reason() -> None:
    with pytest.raises(ValidationError):
        CampaignAttribution(status="unknown")
    with pytest.raises(ValidationError):
        CampaignAttribution(status="known", orders=3)  # 판매액 없음
    with pytest.raises(ValidationError):
        CampaignAttribution(status="known", orders=1, gross_krw=1, tagged_share=1.5)
    none = CampaignAttribution(status="unknown", unknown_reason="유입 기록 없음")
    assert none.orders is None and none.by_source == []
    row = CampaignSourceRow(source="instagram", mediums=["social"], orders=3, gross_krw=45000)
    known = CampaignAttribution(status="known", campaign_id="cmp_1", orders=3, gross_krw=45000,
                                by_source=[row], window_orders=40, tagged_orders=10,
                                tagged_share=0.25)  # fmt: skip
    assert known.by_source[0].source == "instagram" and known.basis
    with pytest.raises(ValidationError):
        CampaignAttributionProducts(status="known", orders=1)
    both = CampaignAttributionProducts(status="known", matched=["prd_0004"], orders=1, units=2,
                                       gross_krw=15000)  # fmt: skip
    assert both.matched == ["prd_0004"]
