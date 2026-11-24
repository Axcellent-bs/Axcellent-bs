"""대상층·천만 계약 (11/24, 추가만): 예전 저장분(새 칸 없음)도 그대로 읽힌다."""

import pytest
from pydantic import ValidationError

from ax_contracts.schemas.data import (
    InstagramRadarOutput,
    InstagramRadarPost,
    MegaViewItem,
    MegaViews,
    RadarAccountMeta,
    TrendContentSection,
    TrendMemesOutput,
)

OLD_POST = {"account": "a", "media_id": "1", "metric": "like_count", "value": 10,
            "account_median": 2.0, "ratio": 5.0}  # fmt: skip


def test_old_saved_results_still_read() -> None:
    post = InstagramRadarPost.model_validate(OLD_POST)
    assert post.account_kind is None and post.audience_ages == [] and post.topic is None
    out = InstagramRadarOutput.model_validate({"status": "known"})
    assert out.accounts_meta == [] and out.audience_posts == [] and out.min_followers is None
    assert TrendMemesOutput().mega_views is None
    section = TrendContentSection()
    assert section.audience_line is None and not section.audience_widened and section.mega == []


def test_meta_values_are_closed_sets() -> None:
    meta = RadarAccountMeta(account="a", kind="meme", ages=["20s", "60plus"], genders=["all"])
    assert meta.tag_source is None and meta.excluded_reason is None
    with pytest.raises(ValidationError):
        RadarAccountMeta(account="a", kind="celebrity")  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        RadarAccountMeta(account="a", ages=["70s"])  # type: ignore[list-item]
    with pytest.raises(ValidationError):
        RadarAccountMeta(account="a", topic="가" * 21)


def test_mega_defaults() -> None:
    mega = MegaViews()
    assert mega.instagram_min == 10_000_000 and mega.youtube_min == 10_000_000 and mega.days == 30
    item = MegaViewItem(platform="youtube", account="c", item_id="v", views=10_000_000)
    assert item.audience_ages == [] and item.account_kind is None
    with pytest.raises(ValidationError):
        MegaViewItem(platform="tiktok", account="c", item_id="v", views=1)  # type: ignore[arg-type]
