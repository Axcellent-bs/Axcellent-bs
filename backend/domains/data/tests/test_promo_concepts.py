"""promo_concepts: 광고·홍보 숏츠 예시 아이디어(참고용).

실제 네이버·shop-api·YouTube·LLM을 부르지 않는다 (가짜 커넥터·가짜 LLM).
- 숏츠만: 롱폼·60초 초과·15초 미만 → 버림, 길이에 맞지 않는 컷 수 → 버림
- 카탈로그에 없는 상품·재료 밖 트렌드·재료 밖 숏츠 형식·재료에 없는 숫자 → 버림 (이유 기록)
- 트렌드 없는 초안(상품 + 인기 숏츠 형식) 허용, 맞는 이유 한 줄 필수
- 트렌드 재료: 지금 인기(카테고리별) 중 게임·연예·스포츠·IT만. 뉴스·사건은 재료에 안 들어간다
- 시즌 키워드는 카탈로그(카테고리 이름 + 상품 핵심어)에서 오고, 확인한 키워드와 배율이 응답에 있다
- LLM 실패 → 빈 목록 + 이유, 하루 캐시, 다시 만들기
"""

from __future__ import annotations

import asyncio
import dataclasses
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from ax_agent_template.llm import LLMClient, LLMSettings, MockLLMClient, format_prompt
from ax_contracts.schemas.data import (
    PopularCategory,
    PopularTopic,
    PromoConceptsOutput,
    PromoConceptsRequest,
    PromoShortsFormat,
    PromoTrend,
    TrendScanInput,
)
from data_agent.connectors import CatalogProduct
from data_agent.connectors.naver_datalab import TrendResponse, TrendSeries
from data_agent.skills import promo_concepts as promo
from data_agent.skills.promo_concepts import (
    PromoConfig,
    PromoStore,
    _Concept,
    _Concepts,
    make_concepts,
    season_keywords,
    validate_concepts,
)
from data_agent.today_board import TodayBoard

NOW = datetime(2026, 11, 3, 3, 0, tzinfo=UTC)  # 한국 11-03 12시

PRODUCTS = [
    CatalogProduct(product_id="p1", name="파이썬 입문서", category_code="book", price=18000),
    CatalogProduct(product_id="p2", name="무지 노트 3권 세트", category_code="stat", price=7500),
]
CATEGORIES = {"book": "도서", "stat": "문구"}
FORMATS = {"챌린지": PromoShortsFormat(name="챌린지", avg_views=12718523.7, count=9)}


class FakeCatalog:
    async def products(self) -> list[CatalogProduct]:
        return list(PRODUCTS)

    async def categories(self) -> dict[str, str]:
        return dict(CATEGORIES)


class MonthlyNaver:
    """월 단위 요청에 키워드마다 시계열을 준다. `spike`인 키워드는 작년 12월 값이 크다."""

    def __init__(self, spike: set[str], age_spike: set[tuple[str, ...]] | None = None) -> None:
        self.spike = spike
        self.age_spike = age_spike or set()
        self.calls_today = 0
        self.queries: list[TrendScanInput] = []

    async def search_trend(
        self, query: TrendScanInput, ages: tuple[str, ...] = ()
    ) -> TrendResponse:
        self.calls_today += 1
        self.queries.append(query)
        if ages:  # 연령대 조회(trend_age): 날짜 단위, `age_spike` 연령대만 마지막 7일이 3배
            return _age_series(query, tuple(ages) in self.age_spike)
        group = query.keyword_groups[0]
        points = []
        month = date(query.start_date.year, query.start_date.month, 1)
        while month <= query.end_date:
            high = group.group_name in self.spike and month == date(2025, 12, 1)
            points.append((month, 100.0 if high else 40.0))
            month = date(month.year + month.month // 12, month.month % 12 + 1, 1)
        series = TrendSeries(group.group_name, tuple(group.keywords), tuple(points))
        return TrendResponse(query.start_date, query.end_date, "month", (series,))


def _age_series(query: TrendScanInput, spike: bool) -> TrendResponse:
    from datetime import timedelta

    group = query.keyword_groups[0]
    points, day = [], query.start_date
    while day <= query.end_date:
        late = day > query.end_date - timedelta(days=7)
        points.append((day, (30.0 if late and spike else 10.0) + (day.toordinal() % 3) * 0.5))
        day += timedelta(days=1)
    series = TrendSeries(group.group_name, tuple(group.keywords), tuple(points))
    return TrendResponse(query.start_date, query.end_date, "date", (series,))


def _item(**kw: Any) -> _Concept:
    base = {
        "title": "겨울 노트 챌린지",
        "concept": "연말 노트 정리 챌린지",
        "borrowed_trend": "무지 노트 12월 시즌",
        "shorts_format": "챌린지",
        "fit_reason": "연말 정리 시즌과 따라 하기 형식이 노트와 맞는다",
        "product_ids": ["p2"],
        "duration_seconds": 15,
        "storyboard": ["1컷: 책상", "2컷: 무지 노트 3권 세트 7500원", "3컷: 로고"],
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
        self.asked += 1
        self.calls.append(format_prompt(prompt, untrusted))
        if self.fail is not None:
            raise self.fail
        return _Concepts(items=self.items)


def _topic(name: str, category: str, rank: int, *, videos: int = 7,
           trending: int | None = None, person: bool = False) -> PopularTopic:  # fmt: skip
    return PopularTopic(rank=rank, keyword=name, topic_category=category, brand_safe=True,  # type: ignore[arg-type]
                        category_rank=rank, youtube_videos=videos, trending_rank=trending,
                        is_person=person)  # fmt: skip


async def _fake_popular(*args: Any, **kw: Any) -> Any:
    class R:
        class output:  # noqa: N801
            empty_reason = None
            by_category = [
                PopularCategory(
                    category="게임",
                    items=[
                        _topic("페이커", "게임", 1, person=True),  # 사람 이름 → 뺌
                        _topic("리그오브레전드", "게임", 2),
                        _topic("원딜", "게임", 3, videos=4),  # 영상 4개·출처 1개 → 뺌
                    ],
                ),
                PopularCategory(
                    category="뉴스·사건",
                    items=[_topic("한국철강", "뉴스·사건", 1, videos=0, trending=1)],  # fmt: skip
                ),
                PopularCategory(
                    category="연예·스포츠",
                    items=[_topic("경기", "연예·스포츠", 1, videos=0, trending=2)],  # 출처 1개
                ),
                PopularCategory(
                    category="IT", items=[_topic("새 휴대폰", "IT", 1, videos=2, trending=3)]
                ),  # 출처 2개
            ]

    return R()


async def _fake_shorts(*args: Any, **kw: Any) -> Any:
    return dict(FORMATS), "known", None


def _env(tmp_path: Path) -> dict[str, str]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    config = tmp_path / "trends.toml"
    config.write_text("[promo]\nconcepts = 3\nseason_min_ratio = 1.3\n", encoding="utf-8")
    return {
        "DATA_TREND_CONFIG": str(config),
        "DATA_PROMO_DB": str(tmp_path / "promo.db"),
        "DATA_TREND_AGE_DB": str(tmp_path / "age.db"),
    }


def _run(tmp_path: Path, llm: MockLLMClient, monkeypatch: Any, *, regenerate: bool = False,
         naver: MonthlyNaver | None = None):  # noqa: ANN202  # fmt: skip
    monkeypatch.setattr(promo, "popular", _fake_popular)
    monkeypatch.setattr(promo, "shorts_formats", _fake_shorts)
    return asyncio.run(
        make_concepts(
            PromoConceptsRequest(regenerate=regenerate),
            naver or MonthlyNaver({"무지 노트"}),
            llm,
            env=_env(tmp_path),
            catalog=FakeCatalog(),
            now=NOW,
            board=TodayBoard(),
        )
    ).output


# ---------------------------------------------------------------- 재료


def test_season_keywords_come_from_the_catalog() -> None:
    assert season_keywords(PRODUCTS, CATEGORIES, 20) == [
        ("도서", "category"),
        ("문구", "category"),
        ("파이썬 입문서", "product"),
        ("무지 노트", "product"),  # 핵심어: 숫자가 든 낱말·세트 뺌
    ]
    assert len(season_keywords(PRODUCTS, CATEGORIES, 3)) == 3  # 네이버 호출 상한


def test_season_checks_show_keywords_and_ratios(tmp_path: Path, monkeypatch: Any) -> None:
    naver = MonthlyNaver({"무지 노트"})
    out = _run(tmp_path, PromoLLM(), monkeypatch, naver=naver)
    monthly = [q for q in naver.queries if q.time_unit == "month"]  # 시즌 확인 (연령대는 날짜 단위)
    assert [q.keyword_groups[0].group_name for q in monthly] == [
        "도서", "문구", "파이썬 입문서", "무지 노트",
    ]  # fmt: skip
    assert out.naver_calls == 4
    checks = {c.keyword: c for c in out.season_checks}
    note = checks["무지 노트"]
    assert note.next_month == date(2025, 12, 1)
    assert note.next_month_ratio is not None and note.next_month_ratio > 2
    assert checks["도서"].same_month_ratio is not None and checks["도서"].source == "category"
    assert [t.name for t in out.trends if t.kind == "season"] == ["무지 노트 12월 시즌"]


def test_news_trends_never_become_material(tmp_path: Path, monkeypatch: Any) -> None:
    out = _run(tmp_path, PromoLLM(), monkeypatch)
    names = [t.name for t in out.trends if t.kind == "popular"]
    assert names == ["리그오브레전드", "새 휴대폰"]  # 게임·IT만, 뉴스·사건(한국철강)은 뺀다
    assert out.shorts_formats[0].name == "챌린지"


# ---------------------------------------------------------------- 검증


def _validate(items: list[_Concept]):  # noqa: ANN202
    trends = [
        PromoTrend(name="무지 노트 12월 시즌", kind="season", detail="작년 12월 '무지 노트' 2.5배"),
        PromoTrend(
            name="리그오브레전드",
            kind="popular",
            topic_category="게임",
            ip_risk=True,
            entity_names=["리그오브레전드"],
            detail="x",
        ),  # fmt: skip
    ]
    facts: dict[str, int | float | str] = {"가격2": 7500, "상품2": "무지 노트 3권 세트"}
    products = {p.product_id: p for p in PRODUCTS}
    return validate_concepts(items, products, CATEGORIES, trends, facts, formats=FORMATS,
                             config=PromoConfig())  # fmt: skip


def test_longform_and_over_sixty_seconds_are_dropped() -> None:
    six = ["1컷", "2컷", "3컷", "4컷", "5컷", "6컷"]
    kept, dropped = _validate(
        [
            _item(title="롱폼", duration_seconds=180),
            _item(title="61초", duration_seconds=61, storyboard=six[:5]),
            _item(title="10초", duration_seconds=10),
            _item(title="30초 3컷", duration_seconds=30),
            _item(title="60초 6컷", duration_seconds=60, storyboard=six),
        ]
    )
    assert [c.title for c in kept] == ["60초 6컷"]
    assert kept[0].duration_seconds == 60 and kept[0].format is None  # 롱폼 표기 없음
    reasons = {d.title: d.reason for d in dropped}
    assert reasons["롱폼"] == "숏츠 길이 180초가 15~60초 밖 (롱폼 제외)"
    assert reasons["61초"].startswith("숏츠 길이 61초")
    assert reasons["10초"].startswith("숏츠 길이 10초")
    assert reasons["30초 3컷"] == "콘티 3컷이 30초 기준(4~5컷)과 맞지 않음"


def test_concepts_without_a_trend_are_allowed() -> None:
    kept, dropped = _validate([_item(title="트렌드 없음", borrowed_trend="")])
    assert dropped == [] and kept[0].borrowed_trend == "" and kept[0].trend_kind is None
    assert kept[0].shorts_format == "챌린지" and kept[0].shorts_format_avg_views == 12718523.7
    assert kept[0].fit_reason


def test_material_checks_drop_with_reasons() -> None:
    _, dropped = _validate(
        [
            _item(title="없는 상품", product_ids=["p9"]),
            _item(title="재료 밖", borrowed_trend="크리스마스"),
            _item(title="형식 밖", shorts_format="먹방"),
            _item(title="이유 없음", fit_reason=" "),
            _item(title="숫자", storyboard=["1컷(0~3초): 책상", "2컷: 50% 할인", "3컷: 로고"]),
        ]
    )
    assert {d.title: d.reason for d in dropped} == {
        "없는 상품": "카탈로그에 없는 상품 id: p9",
        "재료 밖": "재료 밖 트렌드",
        "형식 밖": "재료 밖 숏츠 형식",
        "이유 없음": "트렌드·형식이 상품과 맞는 이유가 없음",
        "숫자": "재료에 없는 숫자: 50",
    }


def test_ip_name_in_the_title_gets_a_warning() -> None:
    [kept], _ = _validate([_item(title="리그오브레전드처럼 공부", borrowed_trend="리그오브레전드")])
    assert kept.warnings == [
        "상표·초상권 확인 필요: '리그오브레전드'이(가) 제목·콘티에 그대로 있음"
    ]


# ---------------------------------------------------------------- 실행


def test_llm_failure_gives_an_empty_list_with_a_reason(tmp_path: Path, monkeypatch: Any) -> None:
    out = _run(tmp_path, PromoLLM(fail=RuntimeError("down")), monkeypatch)
    assert out.concepts == [] and out.empty_reason == "LLM 호출 실패 (RuntimeError)"
    no_llm = _run(tmp_path / "x", LLMClient("data", LLMSettings()), monkeypatch)
    assert no_llm.concepts == []
    assert no_llm.empty_reason == "LLM 설정이 없어 예시 아이디어를 만들지 않음"


def test_drafts_are_cached_for_the_day_and_can_be_regenerated(
    tmp_path: Path, monkeypatch: Any
) -> None:
    llm = PromoLLM()
    first = _run(tmp_path, llm, monkeypatch)
    assert len(first.concepts) == 1 and not first.cached and llm.asked == 1
    assert "예시 아이디어(참고용)" in first.summary
    again = _run(tmp_path, llm, monkeypatch)
    assert again.cached and llm.asked == 1 and again.run_id == first.run_id
    fresh = _run(tmp_path, llm, monkeypatch, regenerate=True)
    assert not fresh.cached and llm.asked == 2 and fresh.run_id != first.run_id
    store = PromoStore(tmp_path / "promo.db")
    latest = store.latest(date(2026, 11, 3))
    store.close()
    assert latest is not None and latest.run_id == fresh.run_id


def test_materials_go_only_into_the_data_block(tmp_path: Path, monkeypatch: Any) -> None:
    llm = PromoLLM()
    _run(tmp_path, llm, monkeypatch)
    head, _, block = llm.calls[0].partition("<untrusted")
    assert "무지 노트 12월 시즌" not in head and "무지 노트 12월 시즌" in block
    assert "챌린지" in block and "롱폼 금지" in head
    assert "파이썬 입문서" in head  # 형식 예시는 실제 카탈로그 상품 이름


def test_trend_material_needs_two_sources_or_enough_videos_and_no_person(
    tmp_path: Path, monkeypatch: Any
) -> None:
    out = _run(tmp_path, PromoLLM(), monkeypatch)
    assert [t.name for t in out.trends if t.kind == "popular"] == ["리그오브레전드", "새 휴대폰"]
    skipped = {s.name: s.reason for s in out.skipped_trends}
    assert skipped["페이커"] == "사람 이름 주제라 광고 재료에서 뺌 (초상권)"
    assert skipped["원딜"].startswith("출처 1개·YouTube 인기 영상 4개")
    assert skipped["경기"].startswith("출처 1개·YouTube 인기 영상 0개")
    assert "한국철강" not in skipped  # 뉴스·사건은 기준을 보기 전에 늘 뺀다


def test_reason_is_returned_when_no_draft_uses_a_popular_trend(
    tmp_path: Path, monkeypatch: Any
) -> None:
    llm_items = [_item()]

    class WithReason(PromoLLM):
        async def structured(self, prompt, schema, untrusted=None, images=None, context_id=None):  # noqa: ANN001, ANN201
            self.asked += 1
            self.calls.append(format_prompt(prompt, untrusted))
            return _Concepts(
                items=llm_items, no_popular_reason="게임 트렌드와 문구 상품이 맞지 않음"
            )

    out = _run(tmp_path, WithReason(), monkeypatch)
    assert out.popular_trend_note == "게임 트렌드와 문구 상품이 맞지 않음"
    used = _run(tmp_path / "b", PromoLLM([_item(borrowed_trend="리그오브레전드")]), monkeypatch)
    assert used.popular_trend_note is None


def test_prompt_prefers_popular_trends_as_meme_formats(tmp_path: Path, monkeypatch: Any) -> None:
    llm = PromoLLM()
    _run(tmp_path, llm, monkeypatch)
    head = llm.calls[0].partition("<untrusted")[0]
    assert "밈 형식" in head and "1~2개는 지금 인기 트렌드" in head
    assert "no_popular_reason" in head


# ---------------------------------------------------------------- 하루 캐시: 규칙 버전


def _run_with(tmp_path: Path, llm: MockLLMClient, monkeypatch: Any, config_text: str):  # noqa: ANN202
    monkeypatch.setattr(promo, "popular", _fake_popular)
    monkeypatch.setattr(promo, "shorts_formats", _fake_shorts)
    env = _env(tmp_path)
    Path(env["DATA_TREND_CONFIG"]).write_text(config_text, encoding="utf-8")
    return asyncio.run(
        make_concepts(
            PromoConceptsRequest(),
            MonthlyNaver({"무지 노트"}),
            llm,
            env=env,
            catalog=FakeCatalog(),
            now=NOW,
            board=TodayBoard(),
        )
    ).output


BASE = "[promo]\nconcepts = 3\nseason_min_ratio = 1.3\n"


def test_cache_is_reused_when_the_rules_do_not_change(tmp_path: Path, monkeypatch: Any) -> None:
    llm = PromoLLM()
    first = _run_with(tmp_path, llm, monkeypatch, BASE)
    again = _run_with(tmp_path, llm, monkeypatch, BASE)
    assert again.cached and again.run_id == first.run_id and llm.asked == 1


def test_changed_filter_settings_make_a_new_draft_on_the_next_request(
    tmp_path: Path, monkeypatch: Any
) -> None:
    llm = PromoLLM()
    first = _run_with(tmp_path, llm, monkeypatch, BASE)
    changed = _run_with(tmp_path, llm, monkeypatch, BASE + "trend_min_youtube_videos = 7\n")
    assert not changed.cached and changed.run_id != first.run_id and llm.asked == 2
    # 바뀐 규칙으로 만든 것은 다음에 다시 쓴다
    again = _run_with(tmp_path, llm, monkeypatch, BASE + "trend_min_youtube_videos = 7\n")
    assert again.cached and again.run_id == changed.run_id and llm.asked == 2


def test_rules_version_follows_categories_and_prompt(monkeypatch: Any) -> None:
    base = promo.load_config({})
    before = promo.rules_version(base)
    assert promo.rules_version(base) == before  # 같은 규칙이면 같다
    assert promo.rules_version(dataclasses.replace(base, trend_min_sources=3)) != before
    monkeypatch.setattr(promo, "TOPIC_CATEGORIES", (*promo.TOPIC_CATEGORIES, "새분류"))
    assert promo.rules_version(base) != before  # 분류 목록이 바뀌면 다르다
    monkeypatch.undo()
    monkeypatch.setattr(promo, "_prompt", lambda config, example: "다른 프롬프트")
    assert promo.rules_version(base) != before  # 프롬프트가 바뀌면 다르다


def test_old_rows_without_a_rules_version_are_not_used_as_cache(tmp_path: Path) -> None:
    import sqlite3

    store = PromoStore(tmp_path / "promo.db")
    out = PromoConceptsOutput(run_id="old", summary="예전")
    store.save(date(2026, 11, 3), out)  # 규칙 버전 없이 남은 예전 줄
    assert store.latest(date(2026, 11, 3), "abc") is None
    assert store.latest(date(2026, 11, 3)) is not None
    store.close()
    conn = sqlite3.connect(tmp_path / "promo.db")
    assert "rules" in [r[1] for r in conn.execute("PRAGMA table_info(runs)")]
    conn.close()


# ---------------------------------------------------------------- 검사에서 빠지면 한 번 더 묻기


class SequenceLLM(PromoLLM):
    """부를 때마다 다음 답을 준다. 답이 다 떨어지면 마지막 답을 다시 준다."""

    def __init__(self, answers: list[list[_Concept]]) -> None:
        super().__init__()
        self.answers = answers

    async def structured(self, prompt, schema, untrusted=None, images=None, context_id=None):  # noqa: ANN001, ANN201
        self.calls.append(format_prompt(prompt, untrusted))
        items = self.answers[min(self.asked, len(self.answers) - 1)]
        self.asked += 1
        return _Concepts(items=items)


def test_all_dropped_drafts_are_asked_again_once_with_the_reasons(
    tmp_path: Path, monkeypatch: Any
) -> None:
    bad = [_item(title="재료 밖", borrowed_trend="크리스마스")]
    llm = SequenceLLM([bad, [_item(title="다시 쓴 초안")]])
    out = _run(tmp_path, llm, monkeypatch)
    assert llm.asked == 2
    assert [c.title for c in out.concepts] == ["다시 쓴 초안"]
    assert [(d.title, d.reason) for d in out.dropped] == [("재료 밖", "재료 밖 트렌드")]
    head, _, block = llm.calls[1].partition("<untrusted")
    assert "다시 쓰기" in head and "버린 초안1" in block and "재료 밖 트렌드" in block
    assert "한 번 더 물음" in out.summary


def test_asking_again_happens_only_once(tmp_path: Path, monkeypatch: Any) -> None:
    bad = [_item(title="숫자", storyboard=["1컷: 책상", "2컷: 50% 할인", "3컷: 로고"])]
    llm = SequenceLLM([bad])
    out = _run(tmp_path, llm, monkeypatch)
    assert llm.asked == 2 and out.concepts == []
    assert out.empty_reason == "LLM 예시 아이디어가 모두 검증에서 빠짐 (재료에 없는 숫자: 50)"


def test_no_second_ask_when_nothing_was_dropped_or_enough_were_kept(
    tmp_path: Path, monkeypatch: Any
) -> None:
    good = [_item(title=f"초안 {n}") for n in "가나다"]
    llm = SequenceLLM([[*good, _item(title="재료 밖", borrowed_trend="크리스마스")]])
    out = _run(tmp_path, llm, monkeypatch)
    assert llm.asked == 1 and len(out.concepts) == 3 and len(out.dropped) == 1


def test_material_names_match_without_spaces_case_or_by_block_label() -> None:
    kept, dropped = _validate(
        [
            _item(title="공백 다름", borrowed_trend="무지노트 12월시즌", shorts_format=" 챌린지 "),
            _item(title="번호로 답함", borrowed_trend="트렌드1", shorts_format="숏츠형식1"),
            _item(title="따옴표", borrowed_trend="'무지 노트 12월 시즌'"),
        ]
    )
    assert dropped == []
    assert [c.borrowed_trend for c in kept] == ["무지 노트 12월 시즌"] * 3
    assert {c.shorts_format for c in kept} == {"챌린지"}


# ---------------------------------------------------------------- 타깃 연령대 (trend_age)


def test_examples_get_the_target_age_band_of_their_trend(tmp_path: Path, monkeypatch: Any) -> None:
    llm = PromoLLM([
        _item(title="시즌 예시"),  # 무지 노트 12월 시즌 → 시즌 확인 키워드 "무지 노트"
        _item(title="인기 예시", borrowed_trend="리그오브레전드"),
        _item(title="트렌드 없음", borrowed_trend=""),
    ])  # fmt: skip
    naver = MonthlyNaver({"무지 노트"}, age_spike={("3", "4")})
    out = _run(tmp_path, llm, monkeypatch, naver=naver)
    by = {c.title: c for c in out.concepts}
    assert by["시즌 예시"].target_age == "20대" and by["시즌 예시"].target_age_status == "rising"
    assert by["시즌 예시"].target_age_ratio is not None and by["시즌 예시"].target_age_ratio > 2.5
    assert by["인기 예시"].target_age == "20대"
    assert by["트렌드 없음"].target_age is None
    assert by["트렌드 없음"].target_age_reason == "트렌드 없는 예시라 연령대 근거가 없음"
    asked = {q.keyword_groups[0].group_name for q in naver.queries if q.time_unit == "date"}
    assert asked == {"무지 노트", "리그오브레전드"}  # 시즌은 시즌 이름이 아니라 확인 키워드로 본다
    assert "타깃 연령대 있음 2개" in out.summary


def test_target_age_failure_keeps_the_examples_with_a_reason(
    tmp_path: Path, monkeypatch: Any
) -> None:
    class NoAges(MonthlyNaver):
        async def search_trend(self, query, ages=()):  # noqa: ANN001, ANN201
            if ages:
                raise RuntimeError("down")
            return await super().search_trend(query)

    out = _run(tmp_path, PromoLLM(), monkeypatch, naver=NoAges({"무지 노트"}))
    [c] = out.concepts
    assert c.target_age is None and c.target_age_reason == "연령대 조회 실패 (RuntimeError)"


def test_concepts_borrowing_unrelated_popular_trends_are_reference_only() -> None:
    from ax_contracts.schemas.data import PromoConcept, PromoTrendKind

    def concept(trend: str, kind: PromoTrendKind | None) -> PromoConcept:
        return PromoConcept(concept_id=trend, title=trend, borrowed_trend=trend, trend_kind=kind)

    terms = promo.company_terms(
        [CatalogProduct(product_id="p1", name="무지 노트 3권 세트"),
         CatalogProduct(product_id="p2", name="만년필 입문 세트")],
        {"stat": "문구", "book": "도서"},
    )  # fmt: skip
    assert "무지 노트" in terms and "문구" in terms
    items = [concept("골프", "popular"), concept("무지 노트 꾸미기", "popular"),
             concept("만년필 입문 12월 시즌", "season"), concept("", None)]  # fmt: skip
    promo.mark_reference(items, terms)
    golf, note, season, none = items
    assert golf.reference_only and golf.reference_reason == promo.NO_COMPANY_LINK
    assert not note.reference_only  # 회사 상품 핵심어와 겹침
    assert not season.reference_only and not none.reference_only  # 시즌 재료·트렌드 없음은 그대로
