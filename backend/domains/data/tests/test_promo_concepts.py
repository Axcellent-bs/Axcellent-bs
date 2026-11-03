"""promo_concepts: 홍보 영상 기획안.

실제 네이버·shop-api·LLM을 부르지 않는다 (가짜 커넥터·가짜 LLM).

- 카탈로그에 없는 상품 → 버림, 재료 밖 트렌드 → 버림, 재료에 없는 숫자 → 버림 (이유 기록)
- 게임·연예·스포츠·인물 트렌드 이름이 제목·콘티에 그대로 있으면 경고
- LLM 실패 → 빈 목록 + 이유 (지어내지 않음)
- 하루 캐시, 다시 만들기
- 시즌 재료는 카탈로그(카테고리 이름 + 상품 핵심어)에서 온다
"""

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from ax_agent_template.llm import LLMClient, LLMSettings, MockLLMClient, format_prompt
from ax_contracts.schemas.data import PromoConceptsRequest, PromoTrend, TrendScanInput
from data_agent.connectors import CatalogProduct
from data_agent.connectors.naver_datalab import TrendResponse, TrendSeries
from data_agent.skills.promo_concepts import (
    PromoStore,
    _Concept,
    _Concepts,
    category_groups,
    make_concepts,
    season_trends,
    validate_concepts,
)
from data_agent.skills.topic_category import _Categories
from data_agent.today_board import TodayBoard

NOW = datetime(2026, 11, 3, 3, 0, tzinfo=UTC)  # 한국 11-03 12시

PRODUCTS = [
    CatalogProduct(product_id="p1", name="파이썬 입문서", category_code="book", price=18000),
    CatalogProduct(product_id="p2", name="무지 노트 3권 세트", category_code="stat", price=7500),
]
CATEGORIES = {"book": "도서", "stat": "문구"}


class FakeCatalog:
    async def products(self) -> list[CatalogProduct]:
        return list(PRODUCTS)

    async def categories(self) -> dict[str, str]:
        return dict(CATEGORIES)


class MonthlyNaver:
    """월 단위 요청에 그룹마다 시계열을 준다. `spike`인 그룹은 작년 12월 값이 크다."""

    def __init__(self, spike: set[str]) -> None:
        self.spike = spike
        self.calls_today = 0
        self.queries: list[TrendScanInput] = []

    async def search_trend(self, query: TrendScanInput) -> TrendResponse:
        self.calls_today += 1
        self.queries.append(query)
        group = query.keyword_groups[0]
        points = []
        month = date(query.start_date.year, query.start_date.month, 1)
        while month <= query.end_date:
            high = group.group_name in self.spike and month == date(2025, 12, 1)
            points.append((month, 100.0 if high else 40.0))
            month = date(month.year + month.month // 12, month.month % 12 + 1, 1)
        series = TrendSeries(group.group_name, tuple(group.keywords), tuple(points))
        return TrendResponse(query.start_date, query.end_date, "month", (series,))


def _item(**kw: Any) -> _Concept:
    base = {
        "title": "겨울 책상 정리",
        "concept": "연말 분위기로 책상을 정리하는 영상",
        "borrowed_trend": "도서 12월 시즌",
        "product_ids": ["p1"],
        "format": "15초 숏폼",
        "storyboard": ["1컷: 책상", "2컷: 파이썬 입문서 18000원", "3컷: 로고"],
        "cautions": ["과장 금지"],
    }
    return _Concept(**(base | kw))


class PromoLLM(MockLLMClient):
    def __init__(self, items: list[_Concept] | None = None, fail: Exception | None = None) -> None:
        super().__init__()
        self.items = items if items is not None else [_item()]
        self.fail = fail
        self.asked = 0

    async def structured(self, prompt, schema, untrusted=None, images=None, context_id=None):  # noqa: ANN001, ANN201
        if schema is _Categories:
            return _Categories(items=[])
        self.asked += 1
        self.calls.append(format_prompt(prompt, untrusted))
        if self.fail is not None:
            raise self.fail
        return _Concepts(items=self.items)


def _env(tmp_path: Path) -> dict[str, str]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    config = tmp_path / "trends.toml"
    config.write_text("[promo]\nconcepts = 3\nseason_min_ratio = 1.3\n", encoding="utf-8")
    return {
        "DATA_TREND_CONFIG": str(config),
        "DATA_YOUTUBE_DB": str(tmp_path / "none.db"),  # 인기 스냅샷 없음
        "DATA_TRENDING_DB": str(tmp_path / "none2.db"),
        "DATA_PROMO_DB": str(tmp_path / "promo.db"),
    }


def _run(tmp_path: Path, llm: MockLLMClient, *, regenerate: bool = False, naver=None):  # noqa: ANN001, ANN202
    return asyncio.run(
        make_concepts(
            PromoConceptsRequest(regenerate=regenerate),
            naver or MonthlyNaver({"도서"}),
            llm,
            env=_env(tmp_path),
            catalog=FakeCatalog(),
            now=NOW,
            board=TodayBoard(),
        )
    ).output


# ---------------------------------------------------------------- 재료


def test_season_material_comes_from_the_catalog() -> None:
    groups = category_groups(PRODUCTS, CATEGORIES)
    assert [(g.group_name, g.keywords) for g in groups] == [
        ("도서", ["도서", "파이썬 입문서"]),
        ("문구", ["문구", "무지 노트"]),  # 핵심어: 숫자가 든 낱말·세트 뺌
    ]


def test_season_trend_needs_last_years_month_above_the_ratio(tmp_path: Path) -> None:
    naver = MonthlyNaver({"도서"})
    out = _run(tmp_path, PromoLLM(), naver=naver)
    assert out.season_status == "known" and out.naver_calls == 2  # 카테고리 수만큼
    assert [g.keyword_groups[0].group_name for g in naver.queries] == ["도서", "문구"]
    names = [t.name for t in out.trends if t.kind == "season"]
    assert names == ["도서 12월 시즌"]
    assert out.popular_status == "unknown"  # 인기 스냅샷이 없으면 그 재료만 없음

    class G:  # 평균의 1.3배 미만이면 시즌 아님
        status = "known"
        group_name = "x"
        points = [type("P", (), {"month": date(2025, 11, 1), "value": 50.0})()] * 3

    assert season_trends([G()], date(2026, 11, 3), 1.3) == []


# ---------------------------------------------------------------- 검증


def _trends() -> list[PromoTrend]:
    return [
        PromoTrend(
            name="도서 12월 시즌", kind="season", detail="작년 12월 '도서' 검색이 평균의 2.35배"
        ),
        PromoTrend(
            name="리그오브레전드",
            kind="popular",
            topic_category="게임",
            ip_risk=True,
            detail="지금 인기 1위 (게임)",
        ),  # fmt: skip
    ]


def test_validation_drops_unknown_products_outside_trends_and_new_numbers() -> None:
    products = {p.product_id: p for p in PRODUCTS}
    facts: dict[str, int | float | str] = {"가격1": 18000, "콘티": "1컷 2컷 3컷"}
    items = [
        _item(),
        _item(title="없는 상품", product_ids=["p9"]),
        _item(title="재료 밖", borrowed_trend="크리스마스"),
        _item(title="숫자", storyboard=["1컷: 책상", "2컷: 50% 할인", "3컷: 로고"]),
        _item(title="형식", format="60초"),
        _item(title="콘티", storyboard=["1컷", "2컷"]),
    ]
    kept, dropped = validate_concepts(items, products, CATEGORIES, _trends(), facts)
    assert [c.title for c in kept] == ["겨울 책상 정리"]
    assert kept[0].products[0].name == "파이썬 입문서" and kept[0].products[0].price == 18000
    assert {d.title: d.reason for d in dropped} == {
        "없는 상품": "카탈로그에 없는 상품 id: p9",
        "재료 밖": "재료 밖 트렌드",
        "숫자": "재료에 없는 숫자: 50",
        "형식": "형식이 목록 밖",
        "콘티": "콘티가 3컷이 아님",
    }


def test_ip_trend_name_in_title_or_storyboard_gets_a_warning() -> None:
    products = {p.product_id: p for p in PRODUCTS}
    facts: dict[str, int | float | str] = {"가격1": 18000, "콘티": "1컷 2컷 3컷"}
    item = _item(title="리그오브레전드처럼 공부", borrowed_trend="리그오브레전드")
    [kept], _ = validate_concepts([item], products, CATEGORIES, _trends(), facts)
    assert kept.warnings == [
        "상표·초상권 확인 필요: '리그오브레전드'이(가) 제목·콘티에 그대로 있음"
    ]
    calm = _item(title="공부 세팅", borrowed_trend="리그오브레전드")
    [ok], _ = validate_concepts([calm], products, CATEGORIES, _trends(), facts)
    assert ok.warnings == [] and ok.trend_kind == "popular"


# ---------------------------------------------------------------- 실행


def test_llm_failure_gives_an_empty_list_with_a_reason(tmp_path: Path) -> None:
    out = _run(tmp_path, PromoLLM(fail=RuntimeError("down")))
    assert out.concepts == [] and out.empty_reason == "LLM 호출 실패 (RuntimeError)"
    no_llm = asyncio.run(
        make_concepts(
            PromoConceptsRequest(),
            MonthlyNaver({"도서"}),
            LLMClient("data", LLMSettings()),
            env=_env(tmp_path / "x"),
            catalog=FakeCatalog(),
            now=NOW,
            board=TodayBoard(),
        )  # fmt: skip
    ).output
    assert no_llm.concepts == [] and no_llm.empty_reason == "LLM 설정이 없어 기획안을 만들지 않음"


def test_concepts_are_cached_for_the_day_and_can_be_regenerated(tmp_path: Path) -> None:
    llm = PromoLLM()
    first = _run(tmp_path, llm)
    assert len(first.concepts) == 1 and not first.cached and llm.asked == 1
    again = _run(tmp_path, llm)
    assert again.cached and llm.asked == 1 and again.run_id == first.run_id
    fresh = _run(tmp_path, llm, regenerate=True)
    assert not fresh.cached and llm.asked == 2 and fresh.run_id != first.run_id
    store = PromoStore(tmp_path / "promo.db")
    latest = store.latest(date(2026, 11, 3))
    store.close()
    assert latest is not None and latest.run_id == fresh.run_id  # 기록이 남는다


def test_materials_go_only_into_the_data_block(tmp_path: Path) -> None:
    llm = PromoLLM()
    _run(tmp_path, llm)
    head, _, block = llm.calls[0].partition("<untrusted")
    assert "도서 12월 시즌" not in head and "도서 12월 시즌" in block
    assert "파이썬 입문서" in head  # 형식 예시는 실제 카탈로그 상품 이름
    assert "p1" in block
