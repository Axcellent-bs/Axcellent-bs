"""출처 현황의 YouTube 오늘 사용량 칸 (추가만, 예전 응답도 읽힌다)."""

from datetime import date

import pytest
from pydantic import ValidationError

from ax_contracts.schemas import YouTubeDailyUsage
from ax_contracts.schemas.data import DataSourcesOutput


def test_usage_round_trips_and_old_payloads_read() -> None:
    assert DataSourcesOutput().youtube_usage is None
    usage = YouTubeDailyUsage(day=date(2026, 11, 5), units=3500, unit_cap=8000, searches=34,
                              search_stopped=False,
                              by_caller=[{"caller": "video_explorer", "units": 3446,
                                          "searches": 34}])  # fmt: skip
    out = DataSourcesOutput.model_validate({"youtube_usage": usage.model_dump(mode="json")})
    assert out.youtube_usage is not None and out.youtube_usage.by_caller[0].units == 3446
    with pytest.raises(ValidationError):
        YouTubeDailyUsage(day=date(2026, 11, 5), units=-1, unit_cap=8000, searches=0,
                          search_stopped=False)  # fmt: skip
