"""trend_age: 연령대별 상승.

실제 네이버를 부르지 않는다 (연령대마다 다른 시계열을 주는 가짜 커넥터).
- 연령대마다 따로 불러 같은 판정 함수로 평소 대비 배수, 가장 크게 오른 연령대
- 하루 호출 상한: 남은 호출이 모자란 주제만 unknown + 이유
- 데이터 없음(빈 시계열)·호출 실패는 그 연령대만 unknown + 이유
- 대상 고르기: 급상승 → 지금 인기 → 시즌, 설정 수, 같은 이름은 출처만 합침
- 하루 캐시
"""

from __future__ import annotations

import asyncio
from collections.abc import Sequence
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from ax_contracts.schemas.data import TrendAgeRequest, TrendScanInput
from data_agent.connectors import ConnectorUnavailable
from data_agent.connectors.naver_datalab import TrendResponse, TrendSeries
from data_agent.skills import trend_age as age
from data_agent.today_board import TodayBoard

NOW = datetime(2026, 11, 4, 3, 0, tzinfo=UTC)  # 한국 11-04 12시, 판정 마지막 날 11-03


class AgeNaver:
    """연령 코드 묶음마다 다른 시계열. `spike`에 있는 묶음은 마지막 7일이 평소의 3배."""

    def __init__(self, spike: set[tuple[str, ...]], *, empty: set[tuple[str, ...]] = frozenset(),
                 fail: set[tuple[str, ...]] = frozenset()) -> None:  # fmt: skip
        self.spike, self.empty, self.fail = spike, empty, fail
        self.calls: list[tuple[str, tuple[str, ...]]] = []
        self.calls_today = 0

    async def search_trend(self, query: TrendScanInput, ages: Sequence[str] = ()) -> TrendResponse:
        codes = tuple(ages)
        self.calls.append((query.keyword_groups[0].group_name, codes))
        self.calls_today += 1
        if codes in self.fail:
            raise ConnectorUnavailable("RATE_LIMITED", "limit")
        g = query.keyword_groups[0]
        points: list[tuple[date, float]] = []
        if codes not in self.empty:
            day = query.start_date
            while day <= query.end_date:
                late = day > query.end_date - timedelta(days=7)
                wobble = (day.toordinal() % 3) * 0.5
                points.append((day, (30.0 if late and codes in self.spike else 10.0) + wobble))
                day += timedelta(days=1)
        series = TrendSeries(g.group_name, tuple(g.keywords), tuple(points))
        return TrendResponse(query.start_date, query.end_date, "date", (series,))


def _env(tmp_path: Path, extra: str = "") -> dict[str, str]:
    tmp_path.mkdir(parents=True, exist_ok=True)
    cfg = tmp_path / "trends.toml"
    cfg.write_text("[age]\n" + extra, encoding="utf-8")
    return {"DATA_TREND_CONFIG": str(cfg), "DATA_TREND_AGE_DB": str(tmp_path / "age.db")}


def _run(tmp_path: Path, naver: AgeNaver, *, keywords: list[str] | None = None,
         extra: str = "", popular=(), season=(), board: TodayBoard | None = None,
         caller: str = "trend_age", now: datetime = NOW):  # noqa: ANN001, ANN202  # fmt: skip
    return asyncio.run(
        age.trend_age(
            TrendAgeRequest(keywords=keywords or []),
            naver,
            env=_env(tmp_path, extra),
            popular_items=popular,
            season_keywords=season,
            board=board or TodayBoard(),
            now=now,
            caller=caller,
        )  # fmt: skip
    ).output


def test_ratio_per_age_band_and_the_top_band(tmp_path: Path) -> None:
    naver = AgeNaver(spike={("3", "4")})
    out = _run(tmp_path, naver, keywords=["t1"])
    assert [c[1] for c in naver.calls] == [("2",), ("3", "4"), ("5", "6"), ("7", "8"),
                                          ("9", "10", "11")]  # fmt: skip
    [topic] = out.items
    bands = {b.band: b for b in topic.bands}
    assert bands["20대"].status == "rising" and bands["20대"].ratio is not None
    assert bands["20대"].ratio > 2.5 and bands["10대"].status == "normal"
    assert topic.top_band == "20대" and topic.top_ratio == bands["20대"].ratio
    assert out.status == "known" and out.calls_used == 5 and out.call_cap == 200  # 기본 상한 200
    assert "상대값" in out.note and "20대" in out.summary


# 우선 몫(기본 120)이 상한 12보다 커서 우선 아닌 호출자 몫이 0이 되므로,
# 이 테스트는 우선 몫 0으로 전체 상한만 본다
def test_daily_cap_marks_only_the_topics_that_do_not_fit(tmp_path: Path) -> None:
    naver = AgeNaver(spike=set())
    out = _run(
        tmp_path, naver, keywords=["a", "b", "c"], extra="daily_calls = 12\npriority_reserve = 0\n"
    )
    a, b, c = out.items
    assert a.status == "known" and b.status == "known"
    assert (
        c.status == "unknown"
        and c.reason == "하루 네이버 호출 상한(12번)에 닿아 이 주제는 보지 않음"
    )
    assert len(naver.calls) == 10 and out.calls_used == 10


def test_no_data_or_failure_leaves_that_band_unknown(tmp_path: Path) -> None:
    naver = AgeNaver(spike=set(), empty={("2",)}, fail={("9", "10", "11")})
    [topic] = _run(tmp_path, naver, keywords=["t1"]).items
    bands = {b.band: b for b in topic.bands}
    assert bands["10대"].status == "unknown" and bands["10대"].ratio is None
    assert bands["10대"].reason == "이 연령대 검색량이 적어 네이버가 값을 주지 않음"
    assert bands["50대 이상"].reason == "네이버 검색어 트렌드를 읽지 못함 [RATE_LIMITED]"
    assert topic.status == "known"  # 다른 연령대는 값이 있다
    every = AgeNaver(spike=set(), empty={b for _, b in age.DEFAULT_BANDS})
    out = _run(tmp_path / "b", every, keywords=["없음"])
    assert out.items[0].status == "unknown" and out.status == "unknown"
    assert out.items[0].top_band is None


def test_results_are_cached_for_the_day(tmp_path: Path) -> None:
    naver = AgeNaver(spike={("5", "6")})
    first = _run(tmp_path, naver, keywords=["t1"])
    again = _run(tmp_path, naver, keywords=["t1"])
    assert len(naver.calls) == 5 and again.cached and not first.cached
    assert again.items[0].top_band == "30대"


def test_targets_come_from_rising_then_popular_then_season(tmp_path: Path) -> None:
    board = TodayBoard()
    from ax_contracts.schemas.data import TrendEvidence, TrendVerdict

    verdict = TrendVerdict(
        group_name="티원", status="rising", ratio=2.0, evidence=TrendEvidence(baseline_points=56)
    )
    board.record_verdict(["티원"], verdict)
    popular = [("T1", ["티원"]), ("롤", []), ("배그", []), ("발로란트", [])]
    out = _run(tmp_path, AgeNaver(spike=set()), board=board, popular=popular,
               season=["다이어리", "만년필", "노트"],
               extra="rising_topics = 1\npopular_topics = 2\nseason_topics = 1\n")  # fmt: skip
    assert [(i.keyword, i.sources) for i in out.items] == [
        ("티원", ["rising", "popular"]),  # T1의 별칭이 티원이라 같은 주제
        ("롤", ["popular"]),
        ("배그", ["popular"]),
        ("다이어리", ["season"]),
    ]


def test_same_name_merges_sources() -> None:
    picked = age.pick_targets(age.AgeConfig(), rising=["T 1"], popular=[("t1", [])],
                              season=[], limit=10)  # fmt: skip
    assert len(picked) == 1 and picked[0].sources == ["rising", "popular"]


def test_bands_come_from_settings(tmp_path: Path) -> None:
    extra = '[age.bands]\n"2030" = ["3", "4", "5", "6"]\n"잘못" = ["99"]\n'
    naver = AgeNaver(spike=set())
    out = _run(tmp_path, naver, keywords=["t1"], extra=extra)
    assert out.bands == ["2030"] and naver.calls == [("t1", ("3", "4", "5", "6"))]


def test_no_targets_gives_a_reason(tmp_path: Path) -> None:
    out = _run(tmp_path, AgeNaver(spike=set()))
    assert out.status == "unknown" and out.items == []
    assert out.reason == "볼 주제가 없음 (오늘 급상승 판정·지금 인기·시즌 키워드가 모두 없음)"


class LimitedNaver(AgeNaver):
    """네이버 전체 하루 상한(daily_limit)과 오늘 호출 수(calls_today)를 흉내 낸다."""

    def __init__(self, daily_limit: int, already: int) -> None:
        super().__init__(set())
        self.daily_limit = daily_limit
        self.calls_today = already


def test_cache_is_reused_within_cache_days_and_expires_after(tmp_path: Path) -> None:
    naver = AgeNaver(spike={("5", "6")})
    _run(tmp_path, naver, keywords=["t1"])
    assert len(naver.calls) == 5
    two_days = _run(tmp_path, naver, keywords=["t1"], now=NOW + timedelta(days=2))
    assert len(naver.calls) == 5 and two_days.cached  # 3일 안이면 다시 부르지 않는다
    three_days = _run(tmp_path, naver, keywords=["t1"], now=NOW + timedelta(days=3))
    assert len(naver.calls) == 10 and not three_days.cached  # 3일이 지나면 새로 부른다
    one = "cache_days = 1\n"
    _run(tmp_path / "s", naver, keywords=["t1"], extra=one)
    again = _run(tmp_path / "s", naver, keywords=["t1"], extra=one, now=NOW + timedelta(days=1))
    assert len(naver.calls) == 20 and not again.cached  # 설정한 일수를 따른다


def test_non_priority_callers_get_only_the_share_left_after_the_reserve(tmp_path: Path) -> None:
    naver = AgeNaver(spike=set())
    extra = "daily_calls = 20\npriority_reserve = 10\n"  # 우선 아닌 몫 = 10번 = 주제 2개
    out = _run(tmp_path, naver, keywords=["a", "b", "c"], extra=extra, caller="promo_concepts")
    assert [i.status for i in out.items] == ["known", "known", "unknown"]
    assert "하루 네이버 호출 상한(10번)에 닿아" in (out.items[2].reason or "")
    assert "우선 몫" in (out.items[2].reason or "")
    assert len(naver.calls) == 10
    # 우선 호출자는 남은 몫(10번)을 마저 쓴다
    top = _run(tmp_path, naver, keywords=["d", "e", "f"], extra=extra, caller="trend_insight_brief")
    assert [i.status for i in top.items] == ["known", "known", "unknown"]
    assert top.items[2].reason == "하루 네이버 호출 상한(20번)에 닿아 이 주제는 보지 않음"
    assert top.calls_used == 20


def test_calls_by_caller_and_the_limits_line(tmp_path: Path) -> None:
    naver = AgeNaver(spike=set())
    _run(tmp_path, naver, keywords=["a", "b"], caller="trend_insight_brief")
    out = _run(tmp_path, naver, keywords=["c"], caller="promo_concepts")
    assert out.calls_by_caller == {"promo_concepts": 5, "trend_insight_brief": 10}
    assert out.limits == [
        "연령대 하루 상한 200번 중 promo_concepts 5번·trend_insight_brief 10번 사용"
        " (브리프·카드 우선 몫 120번)"
    ]
    cached = _run(tmp_path, naver, keywords=["a"], caller="trend_age")  # 캐시 히트도 채워진다
    assert cached.cached and cached.calls_by_caller == out.calls_by_caller


def test_naver_overall_limit_keeps_a_reserve_for_other_skills(tmp_path: Path) -> None:
    naver = LimitedNaver(daily_limit=1000, already=690)  # 남기는 몫 300 -> 앞으로 10번만
    out = _run(tmp_path, naver, keywords=["a", "b", "c"], caller="trend_insight_brief")
    a, b, c = out.items
    assert a.status == "known" and b.status == "known"
    assert c.reason is not None and "하루 네이버 호출 상한(1000번)에 닿아" in c.reason
    assert "다른 기능 몫 300번" in c.reason
    none = _run(tmp_path / "r", LimitedNaver(1000, 0), keywords=["x"],
                extra="naver_reserve = 1000\n", caller="trend_insight_brief")  # fmt: skip
    assert none.items[0].status == "unknown"
