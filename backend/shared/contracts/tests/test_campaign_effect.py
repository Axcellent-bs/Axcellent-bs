"""캠페인 효과(campaign_effect) 계약: 항목마다 "앎(값)" 또는 "모름 + 이유"."""

import pytest
from pydantic import ValidationError

from ax_contracts.schemas import CampaignChange, CampaignCoupons, CampaignEffectInput

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
