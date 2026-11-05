"""숏츠 화면 카드 칸 계약: ShortsGroup.videos, ShortsVideo의 분류·구독자 대비·광고 재료 칸.

모두 추가만 한 칸이라 예전 응답(칸 없음)도 그대로 읽힌다.
"""

import pytest
from pydantic import ValidationError

from ax_contracts.schemas.data import ShortsGroup, ShortsVideo


def test_old_shorts_responses_still_parse() -> None:
    video = ShortsVideo.model_validate({"video_id": "v1", "views": 10})
    assert video.category is None and video.views_per_subscriber is None
    assert video.channel_subscribers is None and video.subscriber_reason is None
    assert video.ad_usable is None and video.caution_types == []
    group = ShortsGroup.model_validate({"name": "게임", "count": 3})
    assert group.videos == [] and group.top == []


def test_screen_video_fields() -> None:
    video = ShortsVideo(video_id="v", category="K-pop·음악", channel_subscribers=1000,
                        views_per_subscriber=2.5, ad_usable=False, caution_types=["music"],
                        caution_reason="음원 확인 필요")  # fmt: skip
    group = ShortsGroup(name="K-pop·음악", status="unknown", reason="표본 부족", count=1,
                        videos=[video])  # fmt: skip
    assert group.videos[0].caution_types == ["music"] and group.top == []
    hidden = ShortsVideo(video_id="h", subscriber_reason="채널이 구독자 수를 숨김")
    assert hidden.views_per_subscriber is None
    with pytest.raises(ValidationError):
        ShortsVideo(video_id="v", views_per_subscriber=-1.0)
    with pytest.raises(ValidationError):
        ShortsVideo.model_validate({"video_id": "v", "caution_types": ["copyright"]})
