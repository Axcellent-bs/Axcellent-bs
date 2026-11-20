"""급상승 검색어 v3: 후보 섞기·묶어 부르기·캐시 먼저·직전 판정·level·why_line·정렬 (가짜 커넥터만).

기준 화면(11/19 23:00): 키워드 30개 전부 "배수 모름(확인 안 함(한도))"인데 가나다순 1~30위.
"""

from __future__ import annotations

import asyncio
import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from ax_contracts.schemas.data import KeywordNews, KeywordTrendInput
from data_agent import keyword_pool as kp
from data_agent.connectors.naver_datalab import TrendResponse, TrendSeries
from data_agent.skills import keyword_candidates as kc
from data_agent.skills import keyword_trend as kt
from data_agent.skills import trend_age
from data_agent.skills.trend_age import AgeStore
from data_agent.skills.trend_scan import judge_series

NOW = datetime(2026, 11, 20, 3, 0, tzinfo=UTC)  # 한국 11-20 12:00
TODAY = date(2026, 11, 20)


def _row(k: str, total: int | None = 100) -> kp.PoolRow:
    return kp.PoolRow(k, total, 0 if total is not None else None, False, False, "low", "related",
                      None)  # fmt: skip


def _shape(name: str, start: date, end: date) -> tuple[tuple[date, float], ...]:
    """"급"으로 시작하면 최근 7일 5배, 아니면 평평 (값 크기는 이름 길이로 다르게)."""
    days = (end - start).days + 1
    base = 5.0 + len(name)
    up = 5.0 if name.startswith("급") else 1.0
    return tuple((start + timedelta(days=i), base * (up if i >= days - 7 else 1.0))
                 for i in range(days))  # fmt: skip


class MultiLab:
    """데이터랩 가짜: 묶음 요청(그룹 여러 개)도 받고, 하루 캐시(`cached`)를 흉내 낸다."""

    daily_limit = 1000

    def __init__(self) -> None:
        self.calls_today = 0
        self.asked: list[list[str]] = []
        self.cache: dict[str, TrendResponse] = {}

    @staticmethod
    def _key(query: Any) -> str:
        return json.dumps([[g.group_name for g in query.keyword_groups], str(query.start_date),
                           str(query.end_date), query.time_unit])  # fmt: skip

    def cached(self, query: Any) -> TrendResponse | None:
        return self.cache.get(self._key(query))

    async def search_trend(self, query: Any, ages: Any = ()) -> TrendResponse:
        self.calls_today += 1
        names = [g.group_name for g in query.keyword_groups]
        self.asked.append(names)
        start, end = query.start_date, query.end_date
        if query.time_unit == "month":
            series = tuple(TrendSeries(n, (n,), ((start, 10.0), (end, 10.0))) for n in names)
        else:
            series = tuple(TrendSeries(n, (n,), _shape(n, start, end)) for n in names)
        got = TrendResponse(start, end, query.time_unit, series)
        self.cache[self._key(query)] = got
        return got


Days = dict[date, list[kp.PoolRow]]


def _env(tmp_path: Path, rows: list[kp.PoolRow], *, days: Days | None = None,
         config: str = "") -> dict[str, str]:  # fmt: skip
    store = kp.PoolStore(tmp_path / "pool.db")
    for day, day_rows in (days or {TODAY: rows}).items():
        store.save(day, day_rows, "ok", None, 1, 1, NOW)
    store.close()
    cfg = tmp_path / "trends.toml"
    cfg.write_text(config, encoding="utf-8")
    return {"DATA_KEYWORD_POOL_DB": str(tmp_path / "pool.db"), "DATA_TREND_CONFIG": str(cfg),
            "DATA_TRENDING_DB": str(tmp_path / "trending.db"),
            "DATA_TREND_AGE_DB": str(tmp_path / "age.db")}  # fmt: skip


class NoLLM:
    async def structured(self, *a: Any, **k: Any) -> Any:
        from ax_agent_template.llm import LLMNotConfiguredError

        raise LLMNotConfiguredError("test")

    complete = embed = structured


def _run(env: dict[str, str], lab: Any, *, now: datetime = NOW, window: str = "day",
         candidates: Any = None) -> Any:  # fmt: skip
    store = AgeStore(env["DATA_TREND_AGE_DB"])
    try:
        return asyncio.run(kt.keyword_trend(
            KeywordTrendInput(window=window), NoLLM(), env=env, connector=lab,
            searchad_configured=True, now=now, age_store=store, candidates=candidates,
        )).output  # fmt: skip
    finally:
        store.close()


STORED = ["known", {"candidates": [
    {"keyword": "유튜브인기", "youtube": {"video_count": 7}},
    {"keyword": "그림책", "youtube": {"video_count": 3}},  # 풀에도 있는 말 → 출처만 더함
], "trending_candidates": [{"keyword": "구글검색어"}]}, {}]


# ---------------------------------------------------------------- 후보 고르기


def test_pick_mixes_sources_with_caps_and_merges_same_words() -> None:
    config = kp.SearchConfig(rising_check=8, cand_new=2, cand_related_up=1, cand_external=2,
                             cand_company=1)  # fmt: skip
    pool = [_row(k, 1000 - i) for i, k in enumerate(["영어", "수학 학원", "그림책", "한국사"])]
    external, had = kc.external_from(STORED)
    got = kc.pick(new=["새말1", "새말2", "새말3"], up=["그림책", "늘어난말"], external=external,
                  company=["캣북스 신간", "그림 책"], pool=pool, config=config)  # fmt: skip
    keys = [c.keyword for c in got]
    assert had and len(keys) == 8
    assert keys[:2] == ["새말1", "새말2"]  # new 상한 2
    assert keys[2] == "그림책" and "늘어난말" not in keys  # related_up 상한 1
    assert keys[3:5] == ["유튜브인기", "구글검색어"]  # external 상한 2 (그림책은 이미 있어 출처만)
    assert keys[5] == "캣북스 신간"  # company 상한 1 ("그림 책"은 같은 말이라 합침)
    assert keys[6:] == ["영어", "수학 학원"]  # 남은 자리는 풀 검색량 순
    by = {c.keyword: c.sources for c in got}
    assert by["그림책"] == ["related_up", "youtube", "company", "pool"]
    counts = kc.counts(got)
    assert counts["new"] == 2 and counts["pool"] == 3 and counts["youtube"] == 2
    assert kc.external_from(None) == ([], False)


def test_no_stored_candidates_is_zero_with_a_limit_line(tmp_path: Path) -> None:
    env = _env(tmp_path, [_row("그림책")])
    out = _run(env, MultiLab())
    assert kc.NO_EXTERNAL in out.limits
    assert out.candidate_counts["youtube"] == 0 and out.candidate_counts["google"] == 0


def test_candidates_are_fixed_for_the_day(tmp_path: Path) -> None:
    env = _env(tmp_path, [_row(f"풀말{i}", 1000 - i) for i in range(40)])
    first = _run(env, MultiLab(), candidates=STORED)
    other = ["known", {"candidates": [{"keyword": "다른후보", "youtube": {"video_count": 9}}]}, {}]
    second = _run(env, MultiLab(), now=NOW + timedelta(hours=1), candidates=other)
    assert {r.keyword for r in first.rising} == {r.keyword for r in second.rising}
    assert "다른후보" not in {r.keyword for r in second.rising}
    assert len(first.rising) == 30


# ---------------------------------------------------------------- 묶어 부르기·캐시·직전 판정


def test_batch_equals_single_judgment() -> None:
    start, end = date(2026, 9, 18), date(2026, 11, 19)
    alone = TrendSeries("급등", ("급등",), _shape("급등", start, end))
    # 같은 묶음 안에서 더 큰 말 때문에 값이 1/4로 눌려도(상대값) 판정은 같다
    squeezed = TrendSeries("급등", ("급등",), tuple((d, v / 4) for d, v in alone.points))
    a, b = judge_series(alone, start, end), judge_series(squeezed, start, end)
    assert (a.status, a.ratio) == (b.status, b.ratio)


def test_thirty_keywords_take_at_most_six_calls_and_zero_the_second_time(tmp_path: Path) -> None:
    env = _env(tmp_path, [_row(f"풀말{i}", 1000 - i) for i in range(40)])
    lab = MultiLab()
    out = _run(env, lab)
    daily = [q for q in lab.asked if q and len(q) >= 1]
    assert len(out.rising) == 30
    rising_calls = [q for q in daily if all(n.startswith("풀말") for n in q) and len(q) > 1]
    assert len(rising_calls) <= 6 and all(len(q) <= 5 for q in rising_calls)
    # 월 검색량이 비슷한 것끼리: 첫 묶음은 검색량 큰 5개
    assert rising_calls[0] == [f"풀말{i}" for i in range(5)]
    before = len(lab.asked)
    week = _run(env, lab, window="week")  # 두 번째 창
    later = _run(env, lab, now=NOW + timedelta(hours=1))  # 다음 미리 계산
    rising_after = [q for q in lab.asked[before:] if len(q) > 1]
    assert rising_after == [] and week.rising and later.rising


def test_cache_hit_is_judged_even_under_the_cap(tmp_path: Path) -> None:
    env = _env(tmp_path, [_row("급상승말", 500)], config="[age]\ndaily_calls = 0\n")
    lab = MultiLab()
    store = AgeStore(env["DATA_TREND_AGE_DB"])
    lab_obj = kt.Datalab(lab, env, NOW, store)
    end = date(2026, 11, 19)
    start = end - timedelta(days=62)
    # 그날 이미 받아 둔 같은 질의
    asyncio.run(lab.search_trend(kt.TrendScanInput(
        keyword_groups=[kt.TrendKeywordGroup(group_name="급상승말", keywords=["급상승말"])],
        start_date=start, end_date=end)))  # fmt: skip
    asked = len(lab.asked)
    got = asyncio.run(lab_obj.series_many(["급상승말"], start, end))
    store.close()
    assert got["급상승말"][0] is not None and len(lab.asked) == asked and lab_obj.capped == 0


def test_capped_uses_the_last_judgment_within_three_days(tmp_path: Path) -> None:
    rows = [_row("급상승말", 500), _row("평평한말", 300)]
    env = _env(tmp_path, rows, days={TODAY - timedelta(days=2): rows, TODAY: rows})
    old = _run(env, MultiLab(), now=NOW - timedelta(days=2))
    assert any(r.status == "rising" for r in old.rising)
    cfg = Path(env["DATA_TREND_CONFIG"])
    cfg.write_text("[age]\ndaily_calls = 0\n", encoding="utf-8")
    lab = MultiLab()
    out = _run(env, lab)
    assert lab.asked == [q for q in lab.asked if len(q) == 1]  # 급상승 묶음 호출 없음
    assert out.judged_on == TODAY - timedelta(days=2)
    assert out.stale_reason == "데이터랩 하루 몫을 다 써서 11-18 판정을 보여 줘요"
    assert out.rising[0].keyword == "급상승말" and out.rising[0].level == "rising"
    # 4일 뒤에는 3일을 넘어 unknown
    far = _run(env, MultiLab(), now=NOW + timedelta(days=2))
    assert all(r.status == "unknown" for r in far.rising)
    assert all((r.reason or "").startswith(kt.NOT_JUDGED) for r in far.rising)
    assert far.judged_on is None


# ---------------------------------------------------------------- 줄 꾸미기·정렬


def test_level_boundary() -> None:
    assert kc.level("rising", 1.2, 1.5) == "rising"
    assert kc.level("normal", 1.5, 1.5) == "warming"
    assert kc.level("normal", 1.49, 1.5) == "normal"
    assert kc.level("normal", None, 1.5) == "normal"
    assert kc.level("unknown", 9.0, 1.5) == "unknown"


def test_why_line_priority() -> None:
    news = [KeywordNews(title="뉴스 제목 " * 30, source="연합",
                        published_at=datetime(2026, 11, 19, 1, tzinfo=UTC))]  # fmt: skip
    line = kc.why_line(news, 7, ["연관"])
    assert line is not None and line.startswith('"뉴스 제목') and len(line) <= 160
    assert kc.why_line([KeywordNews(title="짧은 뉴스", source="연합",
                                    published_at=datetime(2026, 11, 19, 1, tzinfo=UTC))],
                       7, []) == '"짧은 뉴스" · 연합 · 11-19'  # fmt: skip
    assert kc.why_line([], 7, ["연관"]) == "YouTube 인기 차트 영상 7개"
    assert kc.why_line([], None, ["그림책 추천"]) == '연관어 "그림책 추천"도 같이 오름'
    assert kc.why_line([], None, []) is None


def test_unknown_rows_keep_pool_volume_and_sort_is_not_alphabetical(tmp_path: Path) -> None:
    rows = [_row("가나다", 900), _row("나다라", 5000), _row("급상승말", 100), _row("하하하", 50)]
    env = _env(tmp_path, rows)
    out = _run(env, MultiLab(), candidates=STORED)
    first = out.rising[0]
    assert first.keyword == "급상승말" and first.level == "rising" and first.rank == 1
    assert first.points and len(first.points) == 28 and first.baseline is not None
    assert "pool" in first.candidate_sources
    # 평평한 normal 줄은 월 검색량 큰 순 (가나다 순이 아님)
    normals = [r.keyword for r in out.rising if r.level == "normal"]
    assert normals.index("나다라") < normals.index("가나다")
    youtube = next(r for r in out.rising if r.keyword == "유튜브인기")
    assert youtube.why_line == "YouTube 인기 차트 영상 7개"
    assert [r.rank for r in out.rising] == list(range(1, len(out.rising) + 1))
    # 판정 못 한 줄: 풀 검색량은 채우고 증가 추정 이유를 적는다
    cfg = Path(env["DATA_TREND_CONFIG"])
    cfg.write_text("[age]\ndaily_calls = 0\n", encoding="utf-8")
    capped = _run(env, MultiLab(), now=NOW + timedelta(days=5))
    big = next(r for r in capped.rising if r.keyword == "나다라")
    assert big.status == "unknown" and big.monthly_volume == 5000
    assert big.increase_reason == "판정 못 해 증가 추정 없음"


def test_keyword_trend_has_the_priority_share(tmp_path: Path) -> None:
    store = AgeStore(tmp_path / "age.db")
    config = trend_age.AgeConfig(daily_calls=150, priority_reserve=120)
    store.add_calls(TODAY, 30, "trend_memes")  # 우선 몫 아닌 쪽이 30번(150 − 120)을 다 씀
    assert trend_age._cap_reason(store, TODAY, 1, "trend_memes", config, None) is not None  # noqa: SLF001
    assert trend_age._cap_reason(store, TODAY, 1, "keyword_trend", config, None) is None  # noqa: SLF001
    assert "keyword_trend" in trend_age.PRIORITY_CALLERS
    from data_agent.trend_precompute import SKILLS

    assert SKILLS.index("keyword_trend") == SKILLS.index("trend_candidates") + 1
    store.close()
