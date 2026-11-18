"""keyword_trend 계약 (11/18)과 MemePlatform 추가 칸. 새 계약이라 기본값만으로도 읽힌다."""

import pytest
from pydantic import ValidationError

from ax_contracts.schemas.data import (
    KeywordSpread,
    KeywordTrendInput,
    KeywordTrendOutput,
    KeywordVolume,
    MemePlatform,
    RisingKeyword,
    SeasonKeyword,
)


def test_input_defaults_to_day() -> None:
    assert KeywordTrendInput().window == "day"
    assert KeywordTrendInput(window="week").window == "week"
    with pytest.raises(ValidationError):
        KeywordTrendInput.model_validate({"window": "month"})


def test_empty_output_parses() -> None:
    out = KeywordTrendOutput.model_validate({})
    assert out.status == "unknown" and out.rising == [] and out.season == []
    assert out.searchad_status == "key_missing" and out.spread_status == "not_checked"


def test_volume_below_10_is_not_a_number() -> None:
    v = KeywordVolume(pc=None, mobile=120, pc_below_10=True, competition="low")
    assert v.total is None  # "<10"은 숫자로 바꾸지 않는다
    assert KeywordVolume(pc=10, mobile=20).total == 30
    with pytest.raises(ValidationError):
        KeywordVolume(competition="very")  # type: ignore[arg-type]


def test_rising_and_season_fields() -> None:
    r = RisingKeyword(keyword="그림책", rank=1, status="rising", ratio=3.2)
    assert r.monthly_volume is None and r.increase is None and r.spread is None
    s = SeasonKeyword(keyword="크리스마스 그림책", ratio=5.8)
    assert s.peak_from is None and s.products is None
    with pytest.raises(ValidationError):
        RisingKeyword(keyword="", rank=1, status="rising")
    assert KeywordSpread().status == "not_checked"


def test_meme_platform_new_fields_are_optional() -> None:
    old = MemePlatform.model_validate({"channels": 3})
    assert old.blog_7d is None and old.monthly_volume is None and not old.volume_below_10
    with pytest.raises(ValidationError):
        MemePlatform(blog_7d=-1)
