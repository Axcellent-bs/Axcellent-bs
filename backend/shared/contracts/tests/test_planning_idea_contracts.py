"""기획 방향 선택지 계약 (11/18): 새 칸은 모두 선택 칸이라 예전 요청도 그대로 받는다."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from ax_contracts.schemas.marketing_channels import (
    CHANNEL_FOLLOWERS_SKILL,
    ChannelFollowers,
    ChannelFollowersOutput,
)
from ax_contracts.schemas.planning import CampaignBriefInput, InsightPast

BRIEF = {"proposal_id": "PROP-1", "title": "t", "idea": "i", "why": "w", "approved_by": "사람"}


def test_new_fields_default_and_old_payload_still_parses() -> None:
    past = InsightPast(campaign_id="c")
    assert past.views is None and past.views_at is None
    brief = CampaignBriefInput.model_validate(BRIEF)
    assert brief.idea_angle is None and brief.trend_refs == []
    assert ChannelFollowersOutput(status="unknown").channels == []
    assert CHANNEL_FOLLOWERS_SKILL == "channel_followers"
    assert ChannelFollowers(channel="instagram", followers=300).followers == 300
    with pytest.raises(ValidationError):
        CampaignBriefInput.model_validate({**BRIEF, "trend_refs": ["a", "b", "c", "d"]})
    with pytest.raises(ValidationError):
        CampaignBriefInput.model_validate({**BRIEF, "idea_angle": "other"})
