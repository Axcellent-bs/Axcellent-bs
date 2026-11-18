"""인스타그램 계약 (11/18)과 MemePlatform 인스타 칸. 새 계약이라 기본값만으로도 읽힌다."""

import pytest
from pydantic import ValidationError

from ax_contracts.schemas.data import (
    InstagramAudioInput,
    InstagramAudioOutput,
    InstagramMemoAddInput,
    InstagramMemoListOutput,
    InstagramRadarOutput,
    InstagramSource,
    MemePlatform,
)


def test_audio_input_and_empty_outputs() -> None:
    assert InstagramAudioInput().audio_type == "music"
    with pytest.raises(ValidationError):
        InstagramAudioInput.model_validate({"audio_type": "podcast"})
    out = InstagramAudioOutput.model_validate({})
    assert out.status == "unknown" and out.items == [] and "재생 수" in out.limitation
    radar = InstagramRadarOutput.model_validate({})
    assert radar.groups == [] and radar.accounts_failed == []
    assert InstagramMemoListOutput().can_add is False


def test_memo_input_limits() -> None:
    ok = InstagramMemoAddInput(kind="audio", name="a" * 60, memo="b" * 200, author="재현")
    assert ok.linked_trend is None
    with pytest.raises(ValidationError):
        InstagramMemoAddInput(kind="audio", name="a" * 61, author="재현")
    with pytest.raises(ValidationError):
        InstagramMemoAddInput(kind="audio", name="a", memo="b" * 201, author="재현")
    with pytest.raises(ValidationError):
        InstagramMemoAddInput(kind="video", name="a", author="재현")  # type: ignore[arg-type]


def test_source_status_and_meme_platform_fields() -> None:
    src = InstagramSource(source_id="ig_audio", name="Instagram Audio", status="permission")
    assert src.reason is None
    with pytest.raises(ValidationError):
        InstagramSource(source_id="ig_reels", name="x", status="connected")  # type: ignore[arg-type]
    old = MemePlatform.model_validate({"posts": 3})
    assert old.status is None and old.top_like_median is None and not old.posts_24h_capped
    assert MemePlatform(status="not_hashtag").status == "not_hashtag"
