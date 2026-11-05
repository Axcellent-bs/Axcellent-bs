"""근거 요약(evidence_highlights): facts 중 센 것 최대 3개, 템플릿 문장, LLM 안 씀."""

from __future__ import annotations

from datetime import UTC, date, datetime

from ax_contracts.schemas.data import (
    AgeBandTrend,
    DomainOpportunity,
    InsightEvidence,
    OpportunityEvidence,
    ShortsFit,
    TrendInsight,
    VideoLink,
)
from data_agent.skills import evidence_highlights as hl
from data_agent.skills.brief_facts import card_facts, insight_facts

AT = datetime(2026, 11, 5, 3, 0, tzinfo=UTC)


def _insight(**kw) -> TrendInsight:  # noqa: ANN003
    base = {"insight_id": "ins_1", "rank": 1, "topic": "만년필", "line": ""}
    return TrendInsight.model_validate({**base, **kw})


def test_ratios_come_first_by_size_then_values_only() -> None:
    i = _insight(
        evidence=InsightEvidence(naver_ratio=1.4237, google_rank=3, youtube_videos=4,
                                 youtube_views_per_hour=1520.4, youtube_at=AT),
        age_bands=[AgeBandTrend(band="20대", status="rising", ratio=3.5, latest_value=70,
                                baseline_value=20),
                   AgeBandTrend(band="40대", status="normal", ratio=0.8)],
    )  # fmt: skip
    got = hl.insight_highlights(i, insight_facts(i))
    assert [h.label for h in got] == ["20대 평소 대비 배수", "네이버 평소 대비 배수",
                                      "Google 인기 검색어 순위"]  # fmt: skip
    top, naver, google = got
    assert (top.source, top.value, top.baseline_value, top.ratio) == ("naver_search", 70, 20, 3.5)
    assert naver.sentence == "네이버 검색이 평소의 1.42배예요 (지난 8주 기준)"
    assert google.baseline_value is None and google.ratio is None and google.source == "google"
    facts = {f.fact_id for f in insight_facts(i)}
    assert all(h.fact_id in facts for h in got)  # 숫자는 facts 그대로


def test_small_samples_go_back_and_falling_ratios_go_last() -> None:
    fit = ShortsFit(level="high", median_views=9000, overall_median_views=3000, ratio=3.0,
                    samples=2, reason="x")  # fmt: skip
    i = _insight(shorts_fit=fit, evidence=InsightEvidence(naver_ratio=1.2, google_rank=7),
                 age_bands=[AgeBandTrend(band="10대", status="normal", ratio=0.5)])  # fmt: skip
    got = [h.label for h in hl.insight_highlights(i, insight_facts(i))]
    assert got == ["네이버 평소 대비 배수", "이 분류 숏츠 중앙값 전체 대비 배수",
                   "Google 인기 검색어 순위"]  # 표본 2개는 1.2배 뒤, 0.5배는 빠짐  # fmt: skip


def test_card_highlights_use_search_shopping_and_ages() -> None:
    c = DomainOpportunity(
        opportunity_id="opp_1", subtopic="파이썬 기초", line="",
        search_interest=[OpportunityEvidence(source_id="naver_search_trend",
                                             kind="search_interest", status="known",
                                             label="네이버 검색 평소 대비 배수", value=1.31,
                                             unit="배", period_start=date(2026, 9, 1),
                                             period_end=date(2026, 11, 4), samples=56)],
        consumption=[OpportunityEvidence(source_id="naver_shopping", kind="consumption",
                                         status="known", label="쇼핑 클릭 평소 대비 배수",
                                         value=2.04, unit="배", samples=56)],
        age_bands=[AgeBandTrend(band="10대", status="rising", ratio=1.7)],
    )  # fmt: skip
    got = hl.card_highlights(c, card_facts(c))
    assert [h.source for h in got] == ["naver_shopping", "naver_search", "naver_search"]
    assert got[0].sentence == "네이버 쇼핑 클릭이 평소의 2.04배예요"
    assert len(got) == 3


def test_shorts_ratio_over_the_cap_is_few_samples_and_goes_back() -> None:
    """2026-11-05 '책추천': 숏츠 조회 117.64배(표본 6개)가 맨 위에 왔다."""
    fit = ShortsFit(level="high", median_views=117640, overall_median_views=1000, ratio=117.64,
                    samples=6, reason="x")  # fmt: skip
    c = DomainOpportunity(
        opportunity_id="opp_2", subtopic="책추천", line="", shorts_fit=fit,
        consumption=[OpportunityEvidence(source_id="naver_shopping", kind="consumption",
                                         status="known", label="쇼핑 클릭 평소 대비 배수 · 10대",
                                         value=1.84, unit="배", samples=59),
                     OpportunityEvidence(source_id="naver_shopping", kind="consumption",
                                         status="known", label="쇼핑 클릭 평소 대비 배수",
                                         value=1.19, unit="배", samples=59)],
        search_interest=[OpportunityEvidence(source_id="naver_search_trend",
                                             kind="search_interest", status="known",
                                             label="네이버 검색 평소 대비 배수", value=1.09,
                                             unit="배", samples=7)],
    )  # fmt: skip
    got = hl.card_highlights(c, card_facts(c), hl.Rule(min_sample=10, max_ratio=10))
    want = ["쇼핑 클릭 평소 대비 배수 · 10대", "쇼핑 클릭 평소 대비 배수",
            "네이버 검색 평소 대비 배수"]  # fmt: skip
    assert [h.label for h in got] == want  # 117배(표본 적음)는 3개 안에 못 듦
    assert got[2].sentence.endswith("(표본 7개)")  # 표본 기준 10 미만: 뒤로 + 표시
    alone = DomainOpportunity(opportunity_id="opp_3", subtopic="x", line="", shorts_fit=fit)
    [few] = hl.card_highlights(alone, card_facts(alone))
    assert few.sentence.endswith("(표본 적음)") and few.ratio == 117.64


def test_evidence_shorts_need_a_thumbnail_and_title_and_follow_views() -> None:
    links = [
        VideoLink(video_id="a", thumbnail_url="t", title="A", views=10),
        VideoLink(video_id="b", thumbnail_url="t", title="B", views=500),
        VideoLink(video_id="c", thumbnail_url=None, title="C", views=900),  # 썸네일 없음
        VideoLink(video_id="d", thumbnail_url="t", title="D"),  # 조회수 모름: 뒤
        VideoLink(video_id="e", thumbnail_url="t", title="E", views=50),
    ]  # fmt: skip
    assert [v.video_id for v in hl.evidence_shorts(links)] == ["b", "e", "a"]
