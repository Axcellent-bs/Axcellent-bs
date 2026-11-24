"""밈 소스 계약 (11/24, 추가만): 예전 저장분(새 칸 없음)도 그대로 읽힌다."""

import pytest
from pydantic import ValidationError

from ax_contracts.schemas.data import (
    InstagramRadarOutput,
    InstagramRadarPost,
    MemeShortsSearch,
    MemeShortsVideo,
    TrendMemesOutput,
)

OLD_POST = {"account": "a", "media_id": "1", "metric": "like_count", "value": 10,
            "account_median": 2.0, "ratio": 5.0}  # fmt: skip


def test_old_radar_results_read_as_benchmark() -> None:
    post = InstagramRadarPost.model_validate(OLD_POST)
    assert post.source_kind == "benchmark" and post.follower_engagement is None
    assert post.followers is None and post.comments is None
    out = InstagramRadarOutput.model_validate({"status": "known"})
    assert out.meme_accounts_total == 0 and out.meme_posts == []


def test_meme_post_fields() -> None:
    meme = {**OLD_POST, "source_kind": "meme", "followers": 1000, "follower_engagement": 0.05}
    post = InstagramRadarPost.model_validate(meme)
    assert post.source_kind == "meme"
    with pytest.raises(ValidationError):
        InstagramRadarPost.model_validate({**OLD_POST, "source_kind": "brand"})
    with pytest.raises(ValidationError):
        InstagramRadarPost.model_validate({**OLD_POST, "followers": -1})


def test_shorts_search_defaults_and_old_memes_output() -> None:
    assert TrendMemesOutput().shorts_search is None  # 예전 저장분
    search = MemeShortsSearch()
    assert search.status == "not_set" and search.items == [] and search.removed == []
    video = MemeShortsVideo(video_id="v", keyword="밈", views=10, subscribers=None)
    assert video.big_channel is False and video.views_per_subscriber is None
    with pytest.raises(ValidationError):
        MemeShortsVideo(video_id="", keyword="밈")
