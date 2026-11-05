"""v2 화면 칸 계약: 영상 광고 재료 여부·확인할 것, 인사이트 숏츠 적합도·목표 참고값, 연령대.

모두 추가만 한 칸이라 예전 응답(칸 없음)도 그대로 읽힌다.
"""

import pytest
from pydantic import ValidationError

from ax_contracts.schemas.data import (
    DomainOpportunity,
    PopularVideo,
    ShortsFit,
    TrendInsight,
    VideoLink,
)


def test_old_responses_still_parse_with_empty_v2_fields() -> None:
    link = VideoLink.model_validate({"video_id": "v1"})
    assert link.ad_usable is None and link.caution_types == [] and link.caution_reason is None
    video = PopularVideo.model_validate({"video_id": "v1"})
    assert video.ad_usable is None and video.caution_types == []
    insight = TrendInsight.model_validate({"insight_id": "i", "rank": 1, "topic": "t", "line": "l"})
    assert insight.shorts_fit is None and insight.shorts_fit_status is None
    assert insight.target_views_reference is None and insight.top_age_band is None
    card = DomainOpportunity.model_validate({"opportunity_id": "o", "subtopic": "s", "line": "l"})
    assert card.top_age_band is None and card.top_age_band_ratio is None


def test_caution_types_are_three_fixed_values() -> None:
    link = VideoLink(video_id="v", ad_usable=False,
                     caution_types=["real_person", "trademark", "music"])  # fmt: skip
    assert link.caution_types == ["real_person", "trademark", "music"]
    with pytest.raises(ValidationError):
        VideoLink.model_validate({"video_id": "v", "caution_types": ["copyright"]})


def test_insight_shorts_fit_and_top_age_band() -> None:
    fit = ShortsFit(level="high", ratio=1.4, samples=12, reason="분류 중앙값 ÷ 전체 중앙값 1.4")
    known = TrendInsight(insight_id="i", rank=1, topic="t", line="l", shorts_fit=fit,
                         shorts_fit_status="known", target_views_reference=52000.0,
                         target_reference_basis="게임 숏츠 조회 중앙값",
                         top_age_band="20대", top_age_band_ratio=2.4)  # fmt: skip
    assert known.shorts_fit is fit and known.top_age_band == "20대"
    few = TrendInsight(insight_id="i", rank=1, topic="t", line="l", shorts_fit_status="unknown",
                       shorts_fit_reason="숏츠 표본 3개 (최소 5개)")  # fmt: skip
    assert few.shorts_fit is None and few.target_views_reference is None
    with pytest.raises(ValidationError):
        TrendInsight(insight_id="i", rank=1, topic="t", line="l", top_age_band_ratio=-1.0)
