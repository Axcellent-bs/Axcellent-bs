"""trend_insight_brief: 기획에 넘기는 근거 있는 인사이트.

실제 네이버·YouTube·LLM·임베딩을 부르지 않는다 (가짜 결과·가짜 LLM).
- insight_id: 같은 날 같은 주제면 같은 id
- 후보: 지금 인기 + 지금 인기에 없는 급상승, 코드 점수 순, 광고 재료가 아니면 맨 아래 참고용
- 한 줄: 근거 숫자만 (지어낸 숫자·단정은 고정 문장), 근거 없으면 고정 문장
- 연령대·상품(임베딩 기준 통과만)·숏츠 형식·예시 아이디어(참고용)·한계
- 하루 캐시 + 규칙 버전, regenerate
"""

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from ax_agent_template.llm import MockLLMClient
from ax_contracts.schemas.data import (
    AgeBandTrend,
    AgeTopic,
    PopularTopic,
    PopularVideo,
    PromoConcept,
    PromoConceptsOutput,
    TrendAgeOutput,
    TrendEvidence,
    TrendInsightBriefRequest,
    TrendPopularOutput,
    TrendVerdict,
)
from data_agent.connectors import CatalogProduct
from data_agent.skills import trend_insight_brief as brief
from data_agent.skills.product_link import CatalogRead
from data_agent.skills.topic_category import _Categories, _Item

NOW = datetime(2026, 11, 4, 3, 0, tzinfo=UTC)
DAY = date(2026, 11, 4)


def _topic(name: str, rank: int, category: str, **kw: Any) -> PopularTopic:
    base = {"rank": rank, "keyword": name, "topic_category": category, "brand_safe": True,
            "youtube_videos": 10, "views_per_hour": 1000.0}  # fmt: skip
    return PopularTopic(**(base | kw))


POPULAR = TrendPopularOutput(
    items=[
        _topic("한국철강", 1, "뉴스·사건", brand_safe=False, youtube_videos=2),
        _topic("리그오브레전드", 2, "게임", real_entity=True, entity_names=["리그오브레전드"],
               videos=[PopularVideo(video_id="v1", title="롤 하이라이트", is_short=True,
                                    shorts_format="챌린지", views_per_subscriber=2.5),
                       PopularVideo(video_id="v2", is_short=True, shorts_format="챌린지")]),
        _topic("문구 하울", 3, "IT", youtube_videos=5),
    ],
)  # fmt: skip


def _rising(name: str, ratio: float) -> TrendVerdict:
    ev = TrendEvidence(baseline_points=56, latest_period=date(2026, 11, 3))
    return TrendVerdict(group_name=name, status="rising", ratio=ratio, evidence=ev)


class BriefLLM(MockLLMClient):
    """한 줄 질문에는 lines, 분류 질문에는 cats(이름 → 분류, 없으면 답하지 않음 = unknown)."""

    def __init__(self, lines: dict[str, str] | None = None,
                 cats: dict[str, str] | None = None) -> None:  # fmt: skip
        super().__init__()
        self.lines = lines or {}
        self.cats = {"다이어리": "IT"} if cats is None else cats
        self.asked = 0
        self.classified = 0
        self.embedded = 0

    async def structured(self, prompt, schema, untrusted=None, images=None, context_id=None):  # noqa: ANN001, ANN201
        if schema is _Categories:
            self.classified += 1
            names = {k[2:]: v for k, v in dict(untrusted or {}).items() if k.startswith("소재")}
            return _Categories(items=[_Item(id=n, category=self.cats[v])
                                      for n, v in names.items() if v in self.cats])  # fmt: skip
        self.asked += 1
        return brief._Lines(
            items=[brief._LineItem(keyword=k, line=v) for k, v in self.lines.items()]
        )

    async def embed(self, texts, context_id=None):  # noqa: ANN001, ANN201
        self.embedded += 1
        # "노트"가 든 글끼리만 닮는다: 노트 [1,0,0], 다른 상품 [0,1,0], 다른 주제 [0,0,1]
        out = []
        for t in texts:
            if "노트" in t:
                out.append([1.0, 0.0, 0.0])
            elif "(" in t:
                out.append([0.0, 1.0, 0.0])
            else:
                out.append([0.0, 0.0, 1.0])
        return out


CATALOG = CatalogRead(
    products=[
        CatalogProduct(product_id="p1", name="무지 노트", category_code="stat", price=3000),
        CatalogProduct(product_id="p2", name="파이썬 입문서", category_code="book", price=18000),
    ],
    categories={"stat": "문구", "book": "도서"},
)


def _ages(seen: list[list[str]], fail: bool = False):  # noqa: ANN202
    async def fn(keywords: list[str]) -> TrendAgeOutput:
        seen.append(list(keywords))
        if fail:
            raise RuntimeError("down")
        items = [
            AgeTopic(keyword=k, status="known", top_band="20대", top_ratio=2.4,
                     bands=[AgeBandTrend(band="20대", status="rising", ratio=2.4)])
            for k in keywords if k == "리그오브레전드"
        ]  # fmt: skip
        return TrendAgeOutput(status="known", items=items)

    return fn


def _env(tmp_path: Path, extra: str = "") -> dict[str, str]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    cfg = tmp_path / "trends.toml"
    cfg.write_text("[brief]\nmax_insights = 3\n" + extra, encoding="utf-8")
    return {"DATA_TREND_CONFIG": str(cfg), "DATA_INSIGHT_BRIEF_DB": str(tmp_path / "brief.db")}


def _run(tmp_path: Path, llm: BriefLLM, *, ages=None, regenerate: bool = False,  # noqa: ANN001
         extra: str = "", promo: PromoConceptsOutput | None = None, rising=None):  # noqa: ANN001, ANN202  # fmt: skip
    seen: list[list[str]] = []
    out = asyncio.run(
        brief.make_brief(
            TrendInsightBriefRequest(regenerate=regenerate), llm, env=_env(tmp_path, extra),
            popular=POPULAR, popular_reason=None,
            rising=rising if rising is not None else [_rising("다이어리", 3.0)],
            age_fn=ages or _ages(seen), promo=promo, catalog=CATALOG, now=NOW,
        )
    ).output  # fmt: skip
    return out, seen


def test_insight_id_is_stable_for_the_same_day_and_topic() -> None:
    a = brief.insight_id(DAY, "리그오브레전드")
    assert a == brief.insight_id(DAY, " 리그 오브레전드 ".replace(" 오", "오"))
    assert a.startswith("ins_20261104_") and a != brief.insight_id(
        date(2026, 11, 5), "리그오브레전드"
    )


def test_brief_orders_by_code_score_and_puts_non_material_last(tmp_path: Path) -> None:
    out, seen = _run(tmp_path, BriefLLM())
    topics = [i.topic for i in out.insights]
    assert len(topics) == 3 and topics[-1] == "한국철강"  # 뉴스·사건 = 참고용, 맨 아래
    last = out.insights[-1]
    assert last.reference_only and last.ad_material is False
    assert last.ad_material_reason == "뉴스·사건 분류는 광고 재료로 쓰지 않음"
    assert [i.rank for i in out.insights] == [1, 2, 3]
    assert len(seen[0]) == 4  # 연령대는 분류된 후보 4개 (인사이트 수 + 2개 이내)


def test_rising_only_topics_go_through_the_shared_classifier(tmp_path: Path) -> None:
    llm = BriefLLM(cats={"다이어리": "IT"})
    out, _ = _run(tmp_path, llm, extra="max_insights = 4\n")
    diary = next(i for i in out.insights if i.topic == "다이어리")
    assert llm.classified == 1  # 지금 인기에 없는 급상승 주제만 묶어서 분류
    assert diary.category == "IT" and diary.ad_material is True
    assert not diary.classification_pending and diary.evidence.naver_ratio == 3.0


def test_unclassified_topics_are_reference_only_and_not_ranked(tmp_path: Path) -> None:
    out, seen = _run(tmp_path, BriefLLM(cats={}))  # 분류기가 답하지 않음 = 분류 대기
    last = out.insights[-1]
    assert last.topic == "다이어리" and last.classification_pending and last.reference_only
    assert last.category is None and last.ad_material is None
    assert (last.ad_material_reason or "").startswith("분류 대기")
    assert "다이어리" not in seen[0]  # 순위·연령대 후보에 넣지 않는다
    ranked = [i for i in out.insights if not i.classification_pending]
    assert len(ranked) == 3


def test_insight_fields_come_from_existing_results(tmp_path: Path) -> None:
    idea = PromoConcept(concept_id="c1", title="롤 밈 노트", borrowed_trend="리그오브레전드")
    promo = PromoConceptsOutput(concepts=[idea])
    out, _ = _run(tmp_path, BriefLLM(), promo=promo, extra="max_insights = 4\n")
    lol = next(i for i in out.insights if i.topic == "리그오브레전드")
    assert lol.target_age == "20대" and lol.target_age_ratio == 2.4
    assert lol.target_age_status == "rising"
    assert lol.real_entity is True and lol.ad_material is True
    assert lol.ad_material_reason == "실존 이름·상표가 든 주제라 분위기·형식만 빌릴 것"
    assert lol.shorts_format == "챌린지" and lol.shorts_format_samples == 2
    assert lol.evidence.youtube_views_per_subscriber == 2.5
    assert lol.evidence.links[0] == "https://www.youtube.com/watch?v=v1"
    assert lol.example_ideas == ["롤 밈 노트 (참고용)"]
    assert lol.products == []  # 임베딩 기준(0.65·1등)을 넘는 상품이 없다
    assert any("평가셋" in x for x in lol.limits)
    news = next(i for i in out.insights if i.topic == "한국철강")
    assert news.target_age is None and news.target_age_reason == "연령대 값을 받지 못함"
    assert news.shorts_format is None and news.shorts_format_reason


def test_lines_use_only_evidence_numbers(tmp_path: Path) -> None:
    llm = BriefLLM({
        "리그오브레전드": "YouTube 인기 영상 10개, 20대 평소 대비 2.4배 근거가 있어요",
        "다이어리": "네이버 평소의 7배라 곧 터질 거예요",
    })  # fmt: skip
    out, _ = _run(tmp_path, llm)
    by = {i.topic: i for i in out.insights}
    assert by["리그오브레전드"].line_status == "llm"
    assert by["다이어리"].line_status == "fixed"  # 7은 근거에 없는 숫자
    assert by["다이어리"].line == "네이버 평소 대비 배수 3 근거가 있어요"
    assert llm.asked == 1  # 묶어서 한 번


def test_brief_is_cached_per_day_and_rules(tmp_path: Path) -> None:
    llm = BriefLLM()
    first, _ = _run(tmp_path, llm)
    again, seen = _run(tmp_path, llm)
    assert again.cached and llm.asked == 1 and seen == []
    assert [i.insight_id for i in again.insights] == [i.insight_id for i in first.insights]
    fresh, _ = _run(tmp_path, llm, regenerate=True)
    assert not fresh.cached and llm.asked == 2
    changed, _ = _run(tmp_path, llm, extra="w_naver = 2.0\n")  # 규칙이 바뀌면 다시 묶는다
    assert not changed.cached and changed.rules_version != first.rules_version


def test_age_failure_and_no_candidates_give_reasons(tmp_path: Path) -> None:
    out, _ = _run(tmp_path, BriefLLM(), ages=_ages([], fail=True))
    assert out.status == "known"
    assert all(i.target_age is None and i.target_age_reason for i in out.insights)
    empty = asyncio.run(
        brief.make_brief(
            TrendInsightBriefRequest(), BriefLLM(), env=_env(tmp_path / "e"), popular=None,
            popular_reason="YouTube 스냅샷이 없음", rising=[], age_fn=_ages([]), promo=None,
            catalog=CATALOG, now=NOW,
        )
    ).output  # fmt: skip
    assert empty.status == "unknown" and empty.reason == "YouTube 스냅샷이 없음"


def test_aliases_merge_rising_topics() -> None:
    cands = brief.candidates(POPULAR, [_rising("t1", 3.0), _rising("티원", 2.9)], {"t1": ("티원",)})
    names = [c.topic for c in cands]
    assert names.count("t1") == 1 and "티원" not in names
    t1 = next(c for c in cands if c.topic == "t1")
    assert t1.verdict is not None and t1.verdict.ratio == 3.0 and t1.aliases == ["티원"]


# ---------------------------------------------------------------- 구성 (카테고리 상한·회사 관련)


def test_each_category_is_capped(tmp_path: Path) -> None:
    games = TrendPopularOutput(items=[_topic(f"게임{n}", n, "게임") for n in range(1, 5)])
    out = asyncio.run(
        brief.make_brief(
            TrendInsightBriefRequest(), BriefLLM(), env=_env(tmp_path, "max_insights = 4\n"),
            popular=games, popular_reason=None, rising=[], age_fn=_ages([]), promo=None,
            catalog=CATALOG, now=NOW,
        )
    ).output  # fmt: skip
    ranked = [i for i in out.insights if not i.company_related and not i.classification_pending]
    assert [i.topic for i in ranked] == ["게임1", "게임2"]  # per_category_max 2


def _season_promo(keyword: str, ratio: float):  # noqa: ANN202
    from ax_contracts.schemas.data import PromoSeasonCheck

    check = PromoSeasonCheck(keyword=keyword, status="known", same_month=date(2025, 12, 1),
                             same_month_ratio=ratio, next_month=date(2026, 1, 1),
                             next_month_ratio=1.0)  # fmt: skip
    return PromoConceptsOutput(season_checks=[check])


def test_company_insight_is_reserved_when_a_season_keyword_rose(tmp_path: Path) -> None:
    llm = BriefLLM(cats={"다이어리": "IT", "무지 노트": "기타", "파이썬 입문서": "기타"})
    out, seen = _run(
        tmp_path, llm, promo=_season_promo("무지 노트", 1.5), extra="max_insights = 4\n"
    )
    company = [i for i in out.insights if i.company_related]
    note = company[0]
    assert note.topic == "무지 노트" and len(company) <= 2
    assert note.ad_material is True and not note.reference_only
    assert note.evidence.season_ratio == 1.5 and note.evidence.season_keyword == "무지 노트"
    assert "작년 12월 검색이 기간 평균의 1.5배" in (note.company_reason or "")
    assert "무지 노트" in seen[0]  # 회사 후보도 연령대를 본다
    assert note.products and note.products[0].name == "무지 노트"
    trend = [i for i in out.insights if not i.company_related and not i.reference_only]
    assert len(trend) <= 3  # 4개 중 1자리는 회사 관련


def test_company_insight_can_rise_by_age_band_without_season(tmp_path: Path) -> None:
    async def ages(keywords: list[str]) -> TrendAgeOutput:
        items = [AgeTopic(keyword=k, status="known", top_band="30대", top_ratio=1.4,
                          bands=[AgeBandTrend(band="30대", status="normal", ratio=1.4)])
                 for k in keywords if k == "파이썬 입문서"]  # fmt: skip
        return TrendAgeOutput(status="known", items=items)

    llm = BriefLLM(cats={"다이어리": "IT", "무지 노트": "기타", "파이썬 입문서": "기타"})
    out, _ = _run(tmp_path, llm, ages=ages)
    company = [i for i in out.insights if i.company_related]
    assert [i.topic for i in company] == ["파이썬 입문서"]
    assert "30대 평소 대비 1.4배" in (company[0].company_reason or "")


def test_no_company_insight_is_said_and_no_product_is_explicit(tmp_path: Path) -> None:
    out, _ = _run(tmp_path, BriefLLM(cats={"다이어리": "IT"}))
    assert not any(i.company_related for i in out.insights)
    assert any(x.startswith("회사 관련 인사이트 없음") for x in out.limits)
    lol = next(i for i in out.insights if i.topic == "리그오브레전드")
    assert lol.products == [] and (lol.products_reason or "").startswith(
        "회사 상품과 직접 연결 없음"
    )


def test_score_counts_products_and_season(tmp_path: Path) -> None:
    config = brief.BriefConfig()
    plain = brief.Candidate("a", [], popular=_topic("a", 1, "게임"))
    linked = brief.Candidate("b", [], popular=_topic("b", 1, "게임"), has_products=True,
                             season=("노트", 1.6, date(2025, 12, 1)))  # fmt: skip
    a = brief.score(plain, config, 10)
    b = brief.score(linked, config, 10)
    assert round(b - a, 4) == round(config.w_product + config.w_season * 0.6, 4)


def test_shorts_format_values_come_from_shorts_stats_at_the_same_time(tmp_path: Path) -> None:
    from ax_contracts.schemas.data import ShortsGroup, ShortsStatsOutput

    shorts = ShortsStatsOutput(status="known", snapshot_at=NOW, by_format=[
        ShortsGroup(name="챌린지", count=9, avg_views=90000.0, median_views=12000.0),
    ])  # fmt: skip
    out = asyncio.run(
        brief.make_brief(
            TrendInsightBriefRequest(), BriefLLM(), env=_env(tmp_path), popular=POPULAR,
            popular_reason=None, rising=[], age_fn=_ages([]), promo=None, catalog=CATALOG,
            shorts=shorts, now=NOW,
        )
    ).output  # fmt: skip
    lol = next(i for i in out.insights if i.topic == "리그오브레전드")
    assert lol.shorts_format == "챌린지" and lol.shorts_format_samples == 9  # 숏츠 통계 표본 수
    assert lol.shorts_format_median_views == 12000.0 and lol.shorts_format_at == NOW


# ------------------------------------------------ 숏츠 형식 단계 (주제 → 카테고리 → 전체)


def _shorts_stats(cat_formats: dict[str, int] | None, cat_status: str = "known"):  # noqa: ANN202
    from ax_contracts.schemas.data import ShortsFormatCount, ShortsGroup, ShortsStatsOutput

    by_category = []
    if cat_formats is not None:
        by_category.append(ShortsGroup(
            name="IT", status=cat_status, reason=None if cat_status == "known" else "표본 부족",
            count=sum(cat_formats.values()),
            formats=[ShortsFormatCount(name=k, count=v) for k, v in cat_formats.items()],
        ))  # fmt: skip
    return ShortsStatsOutput(status="known", snapshot_at=NOW, by_category=by_category, by_format=[
        ShortsGroup(name="리액션", count=4, median_views=500.0),
        ShortsGroup(name="정보·팁", count=7, median_views=300.0),
        ShortsGroup(name="unknown", count=20),
        ShortsGroup(name="기타", count=30),  # 목록 밖 형식은 카테고리·전체 단계에서 고르지 않는다
    ])  # fmt: skip


def test_shorts_format_stage_topic_first() -> None:
    p = POPULAR.items[1]  # 리그오브레전드: 근거 숏츠 2개 모두 챌린지
    got = brief.shorts_format(p, "게임", _shorts_stats({"정보·팁": 5}))
    assert (got.format, got.source, got.count, got.total) == ("챌린지", "topic", 2, 2)
    assert got.reason is None


def test_shorts_format_falls_back_to_category_then_all() -> None:
    p = POPULAR.items[2]  # 문구 하울(IT): 근거 숏츠 없음
    got = brief.shorts_format(p, "IT", _shorts_stats({"정보·팁": 5, "리액션": 2}))
    assert (got.format, got.source, got.count, got.total) == ("정보·팁", "category", 5, 7)
    assert "카테고리 숏츠 통계에서 고름" in (got.reason or "")
    # 카테고리 표본이 부족하면 전체에서 가장 많은 형식 (분류 못 한 형식은 빼고)
    low = brief.shorts_format(p, "IT", _shorts_stats({"리액션": 2}, cat_status="unknown"))
    assert (low.format, low.source, low.count, low.total) == ("정보·팁", "all", 7, 41)
    assert "IT 숏츠 표본 부족" in (low.reason or "")
    none = brief.shorts_format(None, None, _shorts_stats(None))  # 지금 인기에 없는 주제·분류 미정
    assert none.source == "all" and "카테고리 미정" in (none.reason or "")


def test_shorts_format_without_stats_says_why() -> None:
    got = brief.shorts_format(POPULAR.items[2], "IT", None)
    assert got.format is None and got.source is None
    assert "숏츠 통계로 채우지 못함" in (got.reason or "")


def test_brief_fills_shorts_format_from_category_with_source_and_samples(tmp_path: Path) -> None:
    out = asyncio.run(
        brief.make_brief(
            TrendInsightBriefRequest(), BriefLLM(), env=_env(tmp_path, "max_insights = 4\n"),
            popular=POPULAR, popular_reason=None, rising=[_rising("다이어리", 3.0)],
            age_fn=_ages([]), promo=None, catalog=CATALOG, now=NOW,
            shorts=_shorts_stats({"정보·팁": 5, "리액션": 2}),
        )
    ).output  # fmt: skip
    by = {i.topic: i for i in out.insights}
    lol, haul, diary = by["리그오브레전드"], by["문구 하울"], by["다이어리"]
    assert lol.shorts_format_source == "topic" and lol.shorts_format_source_count == 2
    assert haul.shorts_format == "정보·팁" and haul.shorts_format_source == "category"
    assert (haul.shorts_format_source_count, haul.shorts_format_source_total) == (5, 7)
    assert haul.shorts_format_samples == 7 and haul.shorts_format_median_views == 300.0
    assert diary.shorts_format_source == "category"  # 급상승 주제도 분류(IT)로 카테고리 단계
    news = by["한국철강"]  # 뉴스·사건 숏츠 통계 없음 → 전체
    assert news.shorts_format_source == "all" and news.shorts_format == "정보·팁"


# ------------------------------------------------ 연령대 전체 (trend_age 결과 재사용)


def test_every_insight_carries_all_age_bands_without_extra_calls(tmp_path: Path) -> None:
    calls: list[list[str]] = []

    async def ages(keywords: list[str]) -> TrendAgeOutput:
        calls.append(list(keywords))
        bands = [
            AgeBandTrend(band="10대", status="normal", ratio=1.1),
            AgeBandTrend(band="20대", status="rising", ratio=2.4),
            AgeBandTrend(band="30대", status="unknown", reason="빈 시계열"),
        ]
        items = [AgeTopic(keyword=k, status="known", top_band="20대", top_ratio=2.4, bands=bands)
                 for k in keywords if k == "리그오브레전드"]  # fmt: skip
        return TrendAgeOutput(status="known", items=items)

    out, _ = _run(tmp_path, BriefLLM(), ages=ages)
    assert len(calls) == 1  # 연령대 전체도 브리프가 이미 부른 trend_age 한 번에서
    lol = next(i for i in out.insights if i.topic == "리그오브레전드")
    assert [(b.band, b.ratio, b.status) for b in lol.age_bands] == [
        ("10대", 1.1, "normal"), ("20대", 2.4, "rising"), ("30대", None, "unknown"),
    ]  # fmt: skip
    assert lol.age_bands[2].reason == "빈 시계열" and lol.target_age == "20대"
    news = next(i for i in out.insights if i.topic == "한국철강")
    assert news.age_bands == [] and news.target_age_reason  # 연령대를 받지 못하면 빈 목록 + 이유


# ------------------------------------------------ 기회 가설 카드 (선택 칸)


def test_brief_attaches_opportunity_cards_or_says_why(tmp_path: Path) -> None:
    from data_agent.skills.domain_opportunity import OpportunityResult

    async def none_fn():  # noqa: ANN202
        return OpportunityResult([], "수집 계획이 없음 (LLM 설정이 없음)", None, [])

    async def broken():  # noqa: ANN202
        raise RuntimeError("down")

    def run(fn, sub: str):  # noqa: ANN001, ANN202
        return asyncio.run(
            brief.make_brief(
                TrendInsightBriefRequest(), BriefLLM(), env=_env(tmp_path / sub), popular=POPULAR,
                popular_reason=None, rising=[], age_fn=_ages([]), promo=None, catalog=CATALOG,
                now=NOW, opportunities_fn=fn,
            )
        ).output  # fmt: skip

    out = run(none_fn, "a")
    assert out.opportunities == [] and out.opportunities_reason.startswith("수집 계획이 없음")
    assert "source_plan" in out.sources and out.insights  # 인사이트는 그대로
    bad = run(broken, "b")
    assert bad.opportunities_reason == "기회 가설 카드를 만들지 못함 (RuntimeError)"
    plain = run(None, "c")
    assert plain.opportunities_reason == "기회 가설 카드를 요청하지 않음"


def test_brief_is_rebuilt_when_the_code_changes(tmp_path: Path, monkeypatch) -> None:  # noqa: ANN001
    llm = BriefLLM()
    first, _ = _run(tmp_path, llm)
    again, _ = _run(tmp_path, llm)
    assert again.cached and llm.asked == 1
    monkeypatch.setattr(brief, "CODE_VERSION", "changed")  # 코드가 바뀐 것과 같다 (소스 해시)
    changed, _ = _run(tmp_path, llm)
    assert not changed.cached and llm.asked == 2 and changed.rules_version != first.rules_version
