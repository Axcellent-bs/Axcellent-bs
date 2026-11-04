"""기회 가설 카드: 계획대로 사용 가능·승인 출처만, 숫자는 코드, 한 줄만 LLM(숫자 검증).

실제 네이버·YouTube·LLM을 부르지 않는다 (가짜 커넥터·가짜 LLM).
"""

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime, timedelta

from ax_agent_template.llm import MockLLMClient
from ax_contracts.schemas.data import (
    AgeBandTrend,
    AgeTopic,
    DataSource,
    DataSourcesOutput,
    DomainProfileOutput,
    SeedKeyword,
    ShortsFormatCount,
    ShortsGroup,
    ShortsStatsOutput,
    SourcePlanItem,
    SourcePlanOutput,
    TrendAgeOutput,
)
from data_agent.connectors import CatalogProduct, ConnectorUnavailable
from data_agent.connectors.naver_datalab import TrendResponse, TrendSeries
from data_agent.skills import domain_opportunity as do

NOW = datetime(2026, 11, 4, 3, 0, tzinfo=UTC)
END = date(2026, 11, 3)

PROFILE = DomainProfileOutput(
    status="known", industry="출판·교육", industry_tags=["출판·교육"],
    seed_keywords=[
        SeedKeyword(keyword="고양이 그림책", evidence="고양이 그림책", evidence_from="product",
                    product_ids=["p1"]),
        SeedKeyword(keyword="서점", evidence="고양이가 지키는 서점", evidence_from="about"),
        SeedKeyword(keyword="계획 밖", evidence="계획 밖", evidence_from="about"),
    ],
)  # fmt: skip
SOURCES = DataSourcesOutput(sources=[
    DataSource(source_id="naver_search_trend", name="n", kinds=["search_interest"],
               status="available", approved=True, age=True),
    DataSource(source_id="naver_shopping", name="s", kinds=["consumption"],
               status="key_required", status_reason="지금 키로 확인 호출이 실패함"),
    DataSource(source_id="data4library", name="d", kinds=["consumption"], status="available",
               approved=False),
])  # fmt: skip
PLAN = SourcePlanOutput(
    status="known", plan_id="plan_x",
    items=[
        SourcePlanItem(source_id="naver_search_trend", status="available", approved=True,
                       reason="검색", keywords=["고양이 그림책", "서점"], use_age=True),
        SourcePlanItem(source_id="naver_shopping", status="key_required", reason="쇼핑",
                       keywords=["고양이 그림책"], unlocks="API를 쓸 수 있게 되면: 쇼핑 클릭"),
        SourcePlanItem(source_id="data4library", status="available", approved=False,
                       reason="대출"),
    ],
)  # fmt: skip


def _shorts() -> ShortsStatsOutput:
    return ShortsStatsOutput(
        status="known", snapshot_at=NOW, median_views=10000.0, median_views_count=80,
        by_category=[
            ShortsGroup(name="교육·책", count=6, median_views=15000.0,
                        formats=[ShortsFormatCount(name="정보·팁", count=4),
                                 ShortsFormatCount(name="기타", count=2)]),
            ShortsGroup(name="반려동물", status="unknown", count=2, reason="표본 부족"),
        ],
        by_format=[ShortsGroup(name="정보·팁", count=20, median_views=9000.0)],
    )  # fmt: skip


class FakeTrend:
    def __init__(self, fail: bool = False) -> None:
        self.calls = 0
        self.fail = fail

    async def search_trend(self, query, ages=()):  # noqa: ANN001, ANN201
        self.calls += 1
        if self.fail:
            raise ConnectorUnavailable("RATE_LIMITED", "한도")
        start, end = query.start_date, query.end_date
        days = [start + timedelta(days=i) for i in range((end - start).days + 1)]
        series = []
        for g in query.keyword_groups:
            # 기준선 10, 마지막 7일 25 → 평소 대비 2.5배 근처 (그림책), 서점은 평평
            hot = g.group_name == "고양이 그림책"
            pts = tuple((d, 25.0 if hot and (end - d).days < 7 else 10.0) for d in days)
            series.append(TrendSeries(g.group_name, tuple(g.keywords), pts))
        return TrendResponse(start, end, "date", tuple(series))


async def _ages(keywords: list[str]) -> TrendAgeOutput:
    bands = [AgeBandTrend(band="20대", status="rising", ratio=3.1),
             AgeBandTrend(band="40대", status="normal", ratio=1.1)]  # fmt: skip
    return TrendAgeOutput(status="known", items=[
        AgeTopic(keyword="고양이 그림책", status="known", bands=bands, top_band="20대",
                 top_ratio=3.1),
        AgeTopic(keyword="서점", status="unknown", reason="하루 네이버 호출 상한(80번)"),
    ])  # fmt: skip


async def _classify(names: list[str]) -> dict[str, str | None]:
    return {"고양이 그림책": "교육·책", "서점": "반려동물"}


class LineLLM(MockLLMClient):
    def __init__(self, lines: dict[str, str]) -> None:
        super().__init__()
        self.lines = lines
        self.asked = 0

    async def structured(self, prompt, schema, untrusted=None, images=None, context_id=None):  # noqa: ANN001, ANN201
        self.asked += 1
        return do._Lines(items=[do._LineItem(keyword=k, line=v) for k, v in self.lines.items()])


def _run(llm=None, trend=None, sources=SOURCES, plan=PLAN, consumption_fn=None):  # noqa: ANN001, ANN202
    return asyncio.run(
        do.make_opportunities(
            env={}, llm=llm or LineLLM({}), profile=PROFILE, plan=plan, sources=sources,
            shorts=_shorts(), catalog_products=[CatalogProduct(product_id="p1",
                                                               name="고양이 그림책")],
            classify=_classify, trend_connector=trend or FakeTrend(), age_fn=_ages,
            consumption_fn=consumption_fn, day=END, end=END,
        )
    )  # fmt: skip


def test_cards_cover_planned_seed_keywords_only_and_carry_sources() -> None:
    res = _run()
    assert [c.subtopic for c in res.cards] == ["고양이 그림책", "서점"]  # 계획 밖 키워드 없음
    book = res.cards[0]
    s = book.search_interest[0]
    assert s.source_id == "naver_search_trend" and s.status == "known" and s.unit == "배"
    assert s.value is not None and s.value > 1.5 and s.samples and s.period_end == END
    assert [b.band for b in book.age_bands] == ["20대", "40대"]
    shop = next(e for e in book.consumption if e.source_id == "naver_shopping")
    assert shop.status == "key_required" and "키 등록 후 가능" in (shop.reason or "")
    lib = next(e for e in book.consumption if e.source_id == "data4library")
    assert lib.status == "not_approved" and "승인" in (lib.reason or "")
    assert book.products[0].product_id == "p1" and res.plan_id == "plan_x"
    assert book.planning_decides and book.opportunity_id.startswith("opp_20261103_")


def test_shorts_fit_levels_formats_and_target_reference() -> None:
    res = _run()
    book, store = res.cards
    f = book.shorts_fit
    assert f is not None and f.level == "high" and f.ratio == 1.5 and f.samples == 6
    assert [x.name for x in book.formats] == ["정보·팁"]  # 기타 제외
    assert book.formats[0].median_views == 9000.0
    assert book.target_views_reference == 15000.0 and "교육·책" in (
        book.target_reference_basis or ""
    )
    g = store.shorts_fit
    assert g is not None and g.level == "uncertain" and "표본 2개" in g.reason
    assert store.target_views_reference == 10000.0  # 표본 부족이면 전체 중앙값
    assert store.age_bands == [] and "상한" in (store.age_reason or "")


def test_line_uses_only_evidence_numbers() -> None:
    llm = LineLLM({"고양이 그림책": "검색이 평소의 9배라 대박이에요", "서점": "근거가 약해요"})
    res = _run(llm)
    book = res.cards[0]
    assert book.line_status == "fixed" and "9" not in book.line  # 지어낸 숫자
    assert llm.asked == 1


def test_unavailable_search_says_why_and_no_search_when_not_planned() -> None:
    res = _run(trend=FakeTrend(fail=True))
    e = res.cards[0].search_interest[0]
    assert e.status == "unknown" and "RATE_LIMITED" in (e.reason or "")
    plan = PLAN.model_copy(update={"items": PLAN.items[1:] + [
        SourcePlanItem(source_id="youtube", status="available", approved=True, reason="숏츠",
                       keywords=["서점"])]})  # fmt: skip
    trend = FakeTrend()
    res2 = _run(trend=trend, plan=plan)
    assert trend.calls == 0 and all(not c.search_interest for c in res2.cards)


def test_approved_consumption_source_is_called() -> None:
    sources = SOURCES.model_copy(update={"sources": [
        *SOURCES.sources[:2],
        SOURCES.sources[2].model_copy(update={"approved": True}),
    ]})  # fmt: skip
    plan = PLAN.model_copy(update={"items": [
        *PLAN.items[:2], PLAN.items[2].model_copy(update={"approved": True})]})  # fmt: skip
    seen: list[str] = []

    async def fetch(item, keyword):  # noqa: ANN001, ANN202
        seen.append(f"{item.source_id}:{keyword}")
        return do.OpportunityEvidence(source_id=item.source_id, kind="consumption",
                                      status="known", label="인기 대출 수", value=12.0,
                                      unit="회", samples=2)  # fmt: skip

    res = _run(sources=sources, plan=plan, consumption_fn=fetch)
    assert seen == ["data4library:고양이 그림책", "data4library:서점"]
    lib = next(e for e in res.cards[0].consumption if e.source_id == "data4library")
    assert lib.status == "known" and lib.value == 12.0


def test_no_plan_or_profile_gives_a_reason() -> None:
    res = _run(plan=SourcePlanOutput(status="unknown", reason="LLM 설정이 없음"))
    assert res.cards == [] and "수집 계획이 없음" in (res.reason or "")


def test_cards_are_per_subtopic_and_products_are_only_linked() -> None:
    from ax_contracts.schemas.data import DomainSubtopic, DomainSubtopicsOutput

    subs = DomainSubtopicsOutput(status="known", subtopics=[
        DomainSubtopic(name="그림책 낭독", seed_keyword="고양이 그림책", axis="활용 방식",
                       videos=6, median_views=20000.0),
        DomainSubtopic(name="서점 투어", seed_keyword="서점", axis="같이 묶이는 관심사", videos=2,
                       median_views=500.0),
    ])  # fmt: skip
    res = asyncio.run(
        do.make_opportunities(
            env={}, llm=LineLLM({}), profile=PROFILE, plan=PLAN, sources=SOURCES,
            shorts=_shorts(), catalog_products=[CatalogProduct(product_id="p1",
                                                               name="고양이 그림책")],
            classify=_classify, trend_connector=FakeTrend(), age_fn=_ages,
            consumption_fn=None, day=END, end=END, subtopics=subs,
        )
    )  # fmt: skip
    by = {c.subtopic: c for c in res.cards}
    assert set(by) == {"그림책 낭독", "서점 투어"}  # 상품 이름이 아니라 하위 주제
    book = by["그림책 낭독"]
    assert book.axis == "활용 방식" and book.seed_keyword == "고양이 그림책"
    assert [p.name for p in book.products] == ["고양이 그림책"]  # 연결 상품으로만
    f = book.shorts_fit  # 하위 주제 숏츠 6개(표본 기준 이상) 중앙값 ÷ 전체 10,000
    assert f is not None and f.level == "high" and f.ratio == 2.0 and f.samples == 6
    assert book.target_views_reference == 20000.0
    tour = by[
        "서점 투어"
    ]  # 표본 2개(기준 5개 미만) → 분류 기준으로 되돌아감 (가짜 분류기는 미분류)
    assert tour.shorts_fit is not None and tour.shorts_fit.level == "uncertain"
    assert tour.shorts_fit.reason == "하위 주제 분류 미정"
    assert not any("씨앗 키워드(상품 이름) 단위" in x for x in book.limits)


def test_subtopic_fit_uses_the_seed_search_baseline_and_seeds_share_cards() -> None:
    from ax_contracts.schemas.data import DomainSubtopic, DomainSubtopicsOutput

    subs = DomainSubtopicsOutput(status="known", subtopics=[
        *[DomainSubtopic(name=f"소설{i}", seed_keyword="소설", axis="장르", videos=10 - i,
                         median_views=30000.0) for i in range(4)],
        DomainSubtopic(name="다꾸", seed_keyword="무지 노트", axis="관심사", videos=3,
                       median_views=1000.0),
    ])  # fmt: skip
    topics = do.topics_of(PROFILE, PLAN, subs, 3)
    assert [t.name for t in topics] == ["소설0", "소설1", "다꾸"]  # 씨앗 하나는 2개까지
    fit, target, basis = do.own_shorts(topics[0], None, None, None, _shorts(),
                                       do.OpportunityConfig(), baseline=(20000.0, 120))  # fmt: skip
    # 분모는 인기 차트(숏츠 통계 10,000)가 아니라 같은 방식으로 모은 씨앗 검색 숏츠 전체 20,000
    assert fit is not None and fit.ratio == 1.5 and fit.level == "high"
    assert fit.overall_median_views == 20000.0 and "씨앗 검색 숏츠 전체(120개)" in fit.reason


def test_political_subtopics_are_left_out_with_a_reason() -> None:
    from ax_contracts.schemas.data import DomainSubtopic, DomainSubtopicsOutput

    subs = DomainSubtopicsOutput(status="known", subtopics=[
        DomainSubtopic(name="역대 대통령", seed_keyword="서점", axis="기타", videos=4),
        DomainSubtopic(name="그림책 낭독", seed_keyword="고양이 그림책", axis="활용 방식",
                       videos=3),
    ])  # fmt: skip

    async def classify(names: list[str]) -> dict[str, str | None]:
        return {"역대 대통령": "정치·사회", "그림책 낭독": "교육·책"}

    res = asyncio.run(
        do.make_opportunities(
            env={}, llm=LineLLM({}), profile=PROFILE, plan=PLAN, sources=SOURCES,
            shorts=_shorts(), catalog_products=[], classify=classify,
            trend_connector=FakeTrend(), age_fn=_ages, consumption_fn=None, day=END, end=END,
            subtopics=subs,
        )
    )  # fmt: skip
    assert [c.subtopic for c in res.cards] == ["그림책 낭독"]
    assert any("역대 대통령(정치·사회 분류)" in x for x in res.cards[0].limits)
