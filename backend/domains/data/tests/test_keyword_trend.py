"""keyword_trend·키워드 풀·trend_memes web_search. 가짜 커넥터만 (실제 API 호출 없음)."""

from __future__ import annotations

import asyncio
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from ax_contracts.schemas.data import KeywordTrendInput, TrendMeme
from data_agent import keyword_pool as kp
from data_agent.connectors import CatalogProduct, ConnectorUnavailable
from data_agent.connectors.naver_datalab import TrendResponse, TrendSeries
from data_agent.connectors.naver_search import NOT_ENABLED, SearchNews, SearchResult
from data_agent.connectors.naver_searchad import KeywordStat
from data_agent.skills import keyword_trend as kt
from data_agent.skills import topic_category
from data_agent.skills.product_link import CatalogRead
from data_agent.skills.topic_category import CategoryResult
from data_agent.skills.trend_age import AgeStore

NOW = datetime(2026, 11, 18, 3, 0, tzinfo=UTC)  # 한국 11-18 12:00
TODAY = date(2026, 11, 18)


# ---------------------------------------------------------------- 순수 계산


def test_estimate_and_day_increase() -> None:
    end = date(2026, 11, 17)
    points = [(end - timedelta(days=i), 10.0) for i in range(40)]
    points = [(d, 30.0 if d == end else v) for d, v in points]
    est = kt.estimates(points, 3000, end)
    # 최근 30일 비율 합 = 29 × 10 + 30 = 320 → 어제 = 3000 × 30 / 320
    assert est[end] == pytest.approx(281.25)
    now_value, inc = kt.increase(est, end, "day")
    assert now_value == pytest.approx(281.25)
    assert inc == pytest.approx(281.25 - 3000 * 10 / 320)
    _, week_inc = kt.increase(est, end, "week")
    assert week_inc == pytest.approx(3000 * 20 / 320)  # 최근 7일만 어제 20 더 많다


def test_increase_without_history_is_none() -> None:
    end = date(2026, 11, 17)
    assert kt.increase({end: 5.0}, end, "day") == (5.0, None)
    assert kt.estimates([(end, 0.0)], 100, end) == {}


def test_peak_window_longest_run_above_line() -> None:
    start = date(2025, 11, 1)
    values = [10.0] * 61
    for i in range(19, 30):  # 11-20 ~ 11-30
        values[i] = 40.0
    values[5] = 40.0  # 짧은 구간
    points = [(start + timedelta(days=i), v) for i, v in enumerate(values)]
    peak = kt.peak_window(points, 1.5)
    assert peak == (date(2025, 11, 20), date(2025, 11, 30))
    assert kt.peak_window([(start, 1.0), (start + timedelta(days=1), 1.0)], 1.5) is None


def _row(k: str, total: int | None = 100, source: str = "related") -> kp.PoolRow:
    return kp.PoolRow(k, total, 0 if total is not None else None, False, False, "low", source, None)


def test_new_keywords_by_day() -> None:
    pools = {
        TODAY - timedelta(days=10): [_row("그림책")],
        TODAY - timedelta(days=1): [_row("그림책"), _row("연말 다이어리", 900), _row("필사", 50)],
        TODAY - timedelta(days=2): [_row("그림책")],
    }
    days = kt.new_keywords(pools, TODAY, 5)
    assert [d.day for d in days] == [TODAY - timedelta(days=k) for k in range(4)]
    assert days[0].status == "pending" and days[0].reason == "아직 집계 전이에요"
    assert [i.keyword for i in days[1].items] == ["연말 다이어리", "필사"]  # 검색량 순
    assert days[2].items == []


# ---------------------------------------------------------------- 키워드 풀


class FakeSearchAd:
    configured = True

    def __init__(self) -> None:
        self.asked: list[list[str]] = []

    async def keywords(self, hints):  # noqa: ANN001, ANN201
        self.asked.append(list(hints))
        return [KeywordStat("고양이 그림책", 500, 1500, False, False, "mid"),
                KeywordStat("고양이 사료", 9000, 30000, False, False, "high"),
                KeywordStat("수능 끝나고 읽을 책", 100, 300, False, False, "low"),
                KeywordStat("희귀", None, None, True, True, None)]  # fmt: skip


def _catalog() -> CatalogRead:
    products = [CatalogProduct(product_id=f"p{i}", name=n, category_code=c)
                for i, (n, c) in enumerate([("고양이 그림책 모음", "c1"), ("고양이 그림책 2", "c1"),
                                            ("하루 에세이", "c2")])]  # fmt: skip
    return CatalogRead(products, {"c1": "그림책", "c2": "에세이"})


def _cats(table):  # noqa: ANN001, ANN202
    async def fake(topics, llm, *, cache=None, context_id=None):  # noqa: ANN001, ANN202
        return {t.keyword: CategoryResult(table[t.keyword], None, 1) for t in topics
                if t.keyword in table}  # fmt: skip

    return fake


def test_pool_keeps_book_domain_and_skips_second_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(topic_category, "classify_topics",
                        _cats({"고양이 사료": "생활·취미", "수능 끝나고 읽을 책": "교육·책",
                               "희귀": "기타"}))  # fmt: skip
    env = {"DATA_TREND_CONFIG": str(tmp_path / "none.toml")}
    store = kp.PoolStore(tmp_path / "pool.db")
    ad = FakeSearchAd()
    got = asyncio.run(kp.collect(env, ad, catalog=_catalog(), llm=object(), now=NOW,
                                 store=store))  # fmt: skip
    assert got.status == "ok" and got.out_of_domain == 2
    _, rows = kp.latest(tmp_path / "pool.db")
    assert [r.keyword for r in rows] == ["고양이 그림책", "수능 끝나고 읽을 책"]
    assert all(len(h) <= 5 for h in ad.asked)
    again = asyncio.run(kp.collect(env, ad, catalog=_catalog(), now=NOW, store=store))
    assert again.status == "skipped" and len(ad.asked) == got.calls
    store.close()


def test_pool_without_searchad_keys_keeps_hints_only(tmp_path: Path) -> None:
    class NoKey:
        configured = False

    env = {"DATA_TREND_CONFIG": str(tmp_path / "none.toml")}
    store = kp.PoolStore(tmp_path / "pool.db")
    got = asyncio.run(kp.collect(env, NoKey(), catalog=_catalog(), now=NOW, store=store))
    store.close()
    assert got.status == "hint_only" and "키 등록 후 가능" in (got.reason or "")
    _, rows = kp.latest(tmp_path / "pool.db")
    assert rows and all(r.source == "hint" and r.total is None for r in rows)


# ---------------------------------------------------------------- skill


class FakeLab:
    """데이터랩: 키워드마다 일별(최근 7일 4배)·월별(작년 이달 3배) 값."""

    def __init__(self, fail: set[str] = frozenset()) -> None:
        self.calls_today = 0
        self.fail = set(fail)
        self.asked: list[tuple[str, str]] = []

    async def search_trend(self, query, ages=()):  # noqa: ANN001, ANN201
        self.calls_today += 1
        g = query.keyword_groups[0]
        self.asked.append((g.group_name, query.time_unit))
        if g.group_name in self.fail:
            raise ConnectorUnavailable("UNAVAILABLE", "test")
        start, end = query.start_date, query.end_date
        if query.time_unit == "month":
            months, d = [], start
            while d <= end:
                months.append(d)
                d = (d.replace(day=28) + timedelta(days=4)).replace(day=1)
            points = tuple((m, 30.0 if m == start else 10.0) for m in months)
        else:
            days = (end - start).days + 1
            points = tuple((start + timedelta(days=i), 40.0 if i >= days - 7 else 10.0)
                           for i in range(days))  # fmt: skip
        series = TrendSeries(g.group_name, tuple(g.keywords), points)
        return TrendResponse(start, end, query.time_unit, (series,))


class FakeLLM:
    async def structured(self, *a, **k):  # noqa: ANN002, ANN003, ANN201
        from ax_agent_template.llm import LLMNotConfiguredError

        raise LLMNotConfiguredError("test")

    async def complete(self, *a, **k):  # noqa: ANN002, ANN003, ANN201
        from ax_agent_template.llm import LLMNotConfiguredError

        raise LLMNotConfiguredError("test")

    async def embed(self, *a, **k):  # noqa: ANN002, ANN003, ANN201
        from ax_agent_template.llm import LLMNotConfiguredError

        raise LLMNotConfiguredError("test")


def _pool(tmp_path: Path, rows: list[kp.PoolRow], day: date = TODAY) -> dict[str, str]:
    store = kp.PoolStore(tmp_path / "pool.db")
    store.save(day, rows, "ok", None, 1, 1, NOW)
    store.close()
    return {"DATA_KEYWORD_POOL_DB": str(tmp_path / "pool.db"),
            "DATA_TREND_CONFIG": str(tmp_path / "none.toml"),
            "DATA_TRENDING_DB": str(tmp_path / "trending.db"),
            "DATA_TREND_AGE_DB": str(tmp_path / "age.db")}  # fmt: skip


async def _catalog_provider() -> CatalogRead:
    return _catalog()


def test_skill_rising_season_products_with_volume(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(topic_category, "classify_topics", _cats({"고양이 그림책": "교육·책"}))
    env = _pool(tmp_path, [_row("고양이 그림책", 3000)])
    lab = FakeLab()
    store = AgeStore(env["DATA_TREND_AGE_DB"])
    out = asyncio.run(kt.keyword_trend(
        KeywordTrendInput(), FakeLLM(), env=env, connector=lab, searchad_configured=True,
        catalog_provider=_catalog_provider, now=NOW, age_store=store,
    )).output  # fmt: skip
    r = out.rising[0]
    assert out.status == "known" and r.status == "rising" and r.ratio and r.ratio > 3
    assert r.monthly_volume == 3000 and r.estimate is not None and r.increase is not None
    # product_link 기준 그대로: 이름 일치 2개 + "그림책" 카테고리 일치(같은 2개라 중복 없음)
    assert r.products == 2 and set(r.product_names) == {"고양이 그림책 2", "고양이 그림책 모음"}
    s = out.season[0]
    # 12개월(작년 11월~지난 10월) 평균 = (30 + 10 × 11) ÷ 12 → 30 ÷ 11.67
    assert s.keyword == "고양이 그림책" and s.ratio == pytest.approx(2.57)
    assert s.month_volume == 9000  # 3000 × 30 ÷ 지난달 10
    assert s.peak_from is not None and s.group == "교육·책"
    assert out.searchad_status == "connected" and out.spread_status == "unavailable"
    assert out.spread_reason == NOT_ENABLED
    assert store.calls_by_caller(TODAY) == {"keyword_trend": out.naver_calls}
    assert out.summary and out.summary_fallback  # LLM 없음 → 템플릿 (숫자 검증 길)
    store.close()


def test_skill_without_searchad_key_gives_ratio_only(tmp_path: Path) -> None:
    env = _pool(tmp_path, [_row("그림책", None, "hint")])
    out = asyncio.run(kt.keyword_trend(KeywordTrendInput(window="week"), FakeLLM(), env=env,
                                       connector=FakeLab(), searchad_configured=False,
                                       now=NOW)).output  # fmt: skip
    r = out.rising[0]
    assert r.ratio is not None and r.status == "rising"
    assert r.monthly_volume is None and r.increase is None
    assert "키 등록 후 가능" in (r.volume_reason or "")
    assert out.searchad_status == "key_missing" and out.window == "week"
    assert out.season and out.season[0].month_volume is None


def test_skill_budget_cap_marks_not_checked(tmp_path: Path) -> None:
    env = _pool(tmp_path, [_row("그림책")])
    cfg = tmp_path / "trends.toml"
    cfg.write_text("[age]\ndaily_calls = 0\n", encoding="utf-8")
    env["DATA_TREND_CONFIG"] = str(cfg)
    lab = FakeLab()
    out = asyncio.run(kt.keyword_trend(KeywordTrendInput(), FakeLLM(), env=env, connector=lab,
                                       now=NOW)).output  # fmt: skip
    assert lab.asked == [] and out.rising[0].reason == "확인 안 함(한도)"
    assert any("확인 안 함" in line for line in out.limits)


def test_no_pool_is_unknown_and_reads_nothing(tmp_path: Path) -> None:
    env = {"DATA_KEYWORD_POOL_DB": str(tmp_path / "none.db"),
           "DATA_TREND_CONFIG": str(tmp_path / "none.toml")}  # fmt: skip
    lab = FakeLab()
    out = asyncio.run(kt.keyword_trend(KeywordTrendInput(), FakeLLM(), env=env, connector=lab,
                                       now=NOW)).output  # fmt: skip
    assert out.status == "unknown" and lab.asked == [] and not (tmp_path / "none.db").exists()


class FakeSearch:
    configured = True

    async def search(self, kind, query, display=100, sort="date"):  # noqa: ANN001, ANN201
        if kind == "blog":
            return SearchResult("blog", 5000, (TODAY, TODAY - timedelta(days=3),
                                               TODAY - timedelta(days=20)))  # fmt: skip
        if kind == "cafearticle":
            return SearchResult("cafearticle", 800, ())
        news = SearchNews("그림책 기사", "news.example.com", "https://news.example.com/1", NOW)
        return SearchResult("news", 3, (), (news,))


def test_spread_with_hub_enabled(tmp_path: Path) -> None:
    env = _pool(tmp_path, [_row("그림책")])
    out = asyncio.run(kt.keyword_trend(KeywordTrendInput(), FakeLLM(), env=env,
                                       connector=FakeLab(), search=FakeSearch(),
                                       now=NOW)).output  # fmt: skip
    sp = out.rising[0].spread
    assert out.spread_status == "connected" and sp is not None and sp.status == "known"
    assert (sp.blog_total, sp.blog_7d, sp.cafe_total, sp.cafe_7d) == (5000, 2, 800, None)
    assert sp.cafe_7d_reason and out.rising[0].news_source == "naver_news"
    assert out.rising[0].news[0].source == "news.example.com"


def test_trend_memes_web_search_fills_only_new_column() -> None:
    from data_agent.skills.trend_memes import fill_web_search

    item = TrendMeme(name="#그림책", kind="hashtag", channels=3, videos=5)
    before = item.search.model_dump()
    asyncio.run(fill_web_search([item], FakeSearch(), FakeSearchAd(), NOW))
    ws = item.web_search
    assert ws is not None and (ws.blog_7d, ws.blog_total, ws.cafe_total) == (2, 5000, 800)
    assert ws.monthly_volume is None and "값이 없음" in (ws.volume_reason or "")
    assert item.search.model_dump() == before  # 네이버 검색 배수 칸은 그대로

    class Off:
        configured = False

    off = TrendMeme(name="그림책", kind="phrase", channels=3, videos=5)
    asyncio.run(fill_web_search([off], Off(), Off(), NOW))
    assert off.web_search is not None and off.web_search.reason == NOT_ENABLED
    assert "키 등록 후 가능" in (off.web_search.volume_reason or "")
