"""trend_memes 계약: 요즘 유행·밈 (11/18). 새 계약이라 기본값만으로도 읽힌다."""

import pytest
from pydantic import ValidationError

from ax_contracts.schemas.data import (
    MemeSearch,
    MemeSkipped,
    TrendMeme,
    TrendMemesInput,
    TrendMemesOutput,
)


def test_input_defaults_to_week_and_rejects_other_windows() -> None:
    assert TrendMemesInput().window == "week"
    assert TrendMemesInput(window="day").window == "day"
    with pytest.raises(ValidationError):
        TrendMemesInput.model_validate({"window": "month"})


def test_empty_output_parses() -> None:
    out = TrendMemesOutput.model_validate({})
    assert out.status == "unknown" and out.items == [] and out.skipped == []
    assert out.unknown_published == 0 and out.criteria is None


def test_meme_fields() -> None:
    capped = MemeSearch(status="not_checked", reason="확인 안 함(한도)")
    meme = TrendMeme(name="#책장털기", kind="hashtag", channels=3, videos=5, ratio_status="new",
                     search=capped)  # fmt: skip
    assert meme.ratio is None and meme.ad_material == "unknown" and meme.products == []
    assert meme.top_videos == [] and meme.description is None
    with pytest.raises(ValidationError):
        TrendMeme(name="x", kind="word", channels=1, videos=1)  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        TrendMeme(name="x", kind="phrase", channels=-1, videos=1)
    with pytest.raises(ValidationError):
        TrendMeme(name="x", kind="phrase", channels=1, videos=1, ad_material="maybe")  # type: ignore[arg-type]
    with pytest.raises(ValidationError):
        MemeSkipped(name="x", reason="y", kind="other")  # type: ignore[arg-type]
