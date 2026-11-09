"""캠페인 효과(campaign_effect) 계약: 항목마다 "앎(값)" 또는 "모름 + 이유"."""

import pytest
from pydantic import ValidationError

from ax_contracts.schemas import (
    CampaignChange,
    CampaignCoupons,
    CampaignEffectInput,
    CampaignMatchedProduct,
    CampaignProductMeasure,
    CampaignProductsEffect,
)

T0 = "2026-10-14T14:00:00Z"
T1 = "2026-10-14T16:00:00Z"


def test_input_needs_a_forward_window_and_campaign_id_is_optional() -> None:
    data = CampaignEffectInput.model_validate({"window_from": T0, "window_to": T1})
    assert data.campaign_id is None
    with pytest.raises(ValidationError):
        CampaignEffectInput.model_validate({"window_from": T1, "window_to": T0})


def test_unknown_needs_a_reason_and_known_needs_values() -> None:
    with pytest.raises(ValidationError):
        CampaignChange(status="unknown")
    with pytest.raises(ValidationError):
        CampaignChange(status="known", direction="up")  # 증분 없이 안다고 할 수 없다
    assert CampaignChange(status="known", direction="none", excess=0.0).excess == 0.0
    with pytest.raises(ValidationError):
        CampaignCoupons(status="unknown")
    with pytest.raises(ValidationError):
        CampaignCoupons(status="known")  # 사용률 없이 안다고 할 수 없다
    with pytest.raises(ValidationError):
        CampaignCoupons(status="known", use_rate=1.5)


def test_products_are_optional_and_at_most_ten() -> None:
    """#646: 상품 칸을 안 보내면 예전과 같고, 10개까지만 받는다."""
    data = CampaignEffectInput.model_validate({"window_from": T0, "window_to": T1})
    assert data.products == []
    ok = CampaignEffectInput.model_validate(
        {"window_from": T0, "window_to": T1, "products": ["prd_0004", "소설 베스트 3종"]}
    )
    assert ok.products == ["prd_0004", "소설 베스트 3종"]
    with pytest.raises(ValidationError):
        CampaignEffectInput.model_validate(
            {"window_from": T0, "window_to": T1, "products": [f"p{i}" for i in range(11)]}
        )


def test_products_effect_is_known_with_all_three_parts_or_unknown_with_a_reason() -> None:
    with pytest.raises(ValidationError):
        CampaignProductsEffect(status="unknown", unmatched_products=["무지 노트"])
    miss = CampaignProductsEffect(
        status="unknown", unknown_reason="맞는 상품 없음", unmatched_products=["무지 노트"]
    )
    assert miss.matched == [] and miss.orders is None
    product = CampaignMatchedProduct(product_id="prd_0004", name="무지 노트 3권 세트",
                                     asked="prd_0004")  # fmt: skip
    part = CampaignProductMeasure(
        period=47, baseline=18.3, incremental=28.7, incremental_low=20, incremental_high=35,
        direction="up",
    )  # fmt: skip
    with pytest.raises(ValidationError):
        CampaignProductsEffect(status="known", matched=[product], orders=part)  # 수량·판매액 없음
    known = CampaignProductsEffect(
        status="known", matched=[product], orders=part, units=part, gross_krw=part
    )
    assert known.unmatched_products == [] and known.gross_krw is not None
