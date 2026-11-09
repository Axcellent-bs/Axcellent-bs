"""video_explorer 계약·근거 요약·카테고리 값 추가 (추가만, 예전 응답도 읽힌다)."""

import pytest
from pydantic import ValidationError

from ax_contracts.schemas import VideoExplorerOutput, VideoExplorerRequest, VideoLink
from ax_contracts.schemas.data import (
    DomainOpportunity,
    EvidenceHighlight,
    ExplorerCell,
    ExplorerVideo,
    ShortsStatsOutput,
    TrendInsight,
)


def test_request_defaults_and_choices() -> None:
    req = VideoExplorerRequest()
    assert (req.category, req.period, req.length, req.sort) == (None, "7d", "short", "views")
    req = VideoExplorerRequest.model_validate(
        {"category": "유머", "period": "1d", "length": "long", "sort": "views_per_subscriber"}
    )
    assert req.category == "유머"
    for bad in ({"period": "2d"}, {"length": "mid"}, {"sort": "comments"}, {"category": "x"}):
        with pytest.raises(ValidationError):
            VideoExplorerRequest.model_validate(bad)


def test_output_cells_and_unknown_values() -> None:
    video = ExplorerVideo(video_id="v1", likes=None, likes_reason="좋아요 숨김", category="드라마")
    cell = ExplorerCell(category="드라마", period="3d", length="short", count=1, videos=[video],
                        shortage_reason="이 기간 한국 영상 1개")  # fmt: skip
    out = VideoExplorerOutput(status="known", cells=[cell], categories=["드라마"])
    again = VideoExplorerOutput.model_validate(out.model_dump(mode="json"))
    assert again.cells[0].videos[0].likes_reason == "좋아요 숨김"
    assert again.min_per_cell == 10


def test_before_period_flag_defaults_to_false() -> None:
    assert ExplorerVideo(video_id="v").before_period is False
    assert ExplorerVideo.model_validate({"video_id": "v", "before_period": True}).before_period


def test_new_categories_are_values() -> None:
    for name in ("유머", "드라마", "교양"):
        assert ExplorerVideo(video_id="v", category=name).category == name


def test_old_payloads_still_read() -> None:
    assert VideoLink.model_validate({"video_id": "v"}).title is None
    assert ShortsStatsOutput().subtopic_shorts == 0
    insight = TrendInsight.model_validate({"insight_id": "i", "rank": 1, "topic": "t", "line": ""})
    assert insight.evidence_highlights == [] and insight.evidence_shorts == []
    card = DomainOpportunity.model_validate({"opportunity_id": "o", "subtopic": "s", "line": ""})
    assert card.evidence_highlights == []


def test_highlights_are_at_most_three() -> None:
    h = EvidenceHighlight(source="naver_search", label="네이버 검색 평소 대비", value=1.42,
                          unit="배", sentence="네이버 검색이 평소의 1.42배예요")  # fmt: skip
    with pytest.raises(ValidationError):
        TrendInsight(insight_id="i", rank=1, topic="t", line="", evidence_highlights=[h] * 4)
    with pytest.raises(ValidationError):
        EvidenceHighlight(source="tiktok", label="x", value=1, unit="배", sentence="x")  # type: ignore[arg-type]
