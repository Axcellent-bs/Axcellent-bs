"""끝난 급상승(faded, 11/26): 정점이 판정 구간(7일) 안에만 있으면 끝났어도 rising이던 것.

증거: 수능(11/19)이 11/25에도 감지 탭에 "수능시간표 110.8배"로 떴다. 판정(status)·배수·점수는
그대로 두고 evidence에 정점 구간과 faded를 붙인다. 실제 네이버를 부르지 않는다.
"""

from __future__ import annotations

import math
from datetime import date, timedelta
from types import SimpleNamespace

from ax_contracts.schemas.data import RisingKeyword, TrendEvidence, TrendVerdict
from data_agent.connectors.naver_datalab import TrendSeries
from data_agent.skills import keyword_candidates as kc
from data_agent.skills.content_recommend import rank_topics
from data_agent.skills.keyword_trend import faded_of
from data_agent.skills.trend_scan import (
    FADED_ENV,
    REQUEST_DAYS,
    Settings,
    _facts,
    faded_after_days_env,
    judge_series,
)
from data_agent.today_board import TodayBoard

END = date(2026, 11, 25)
START = END - timedelta(days=REQUEST_DAYS - 1)


def _calm(day: date) -> float:
    return 50.0 * (1 + 0.05 * math.sin(day.toordinal() * 2.1))


def _series(spikes: dict[date, float], name: str = "수능시간표") -> TrendSeries:
    points = []
    day = START
    while day <= END:
        points.append((day, _calm(day) * spikes.get(day, 1.0)))
        day += timedelta(days=1)
    top = max(v for _, v in points)
    return TrendSeries(name, (name,), tuple((d, round(v * 100 / top, 5)) for d, v in points))


def test_peak_six_days_ago_and_back_to_normal_is_faded() -> None:
    peak = END - timedelta(days=6)  # 11/19
    v = judge_series(_series({peak: 110.0}), START, END)
    assert v.status == "rising"  # 판정은 그대로
    assert v.ratio is not None and v.ratio > 50  # 배수·점수도 그대로 남긴다
    assert v.evidence.score is not None and v.evidence.score > 3.2
    assert v.evidence.peak_start == peak and v.evidence.peak_end == peak
    assert v.evidence.latest_period == END
    assert v.evidence.latest_value <= v.evidence.band_high
    assert v.evidence.faded is True


def test_peak_yesterday_is_not_faded() -> None:
    peak = END - timedelta(days=1)
    v = judge_series(_series({peak: 110.0}), START, END)
    assert v.status == "rising"
    assert v.evidence.peak_end == peak
    assert v.evidence.faded is False  # 1일 차이 ≤ 2일


def test_old_peak_but_still_above_band_is_not_faded() -> None:
    peak = END - timedelta(days=6)
    v = judge_series(_series({peak: 110.0, END: 3.0}), START, END)
    assert v.status == "rising"
    assert v.evidence.peak_end == peak
    assert v.evidence.latest_value > v.evidence.band_high  # 지금도 평소 범위 위
    assert v.evidence.faded is False


def test_normal_is_never_faded() -> None:
    v = judge_series(_series({}), START, END)
    assert v.status == "normal"
    assert v.evidence.faded is False
    assert v.evidence.peak_end is not None  # 정점 구간은 판정했으면 늘 남긴다


def test_faded_days_setting_and_env() -> None:
    peak = END - timedelta(days=6)
    strict = judge_series(_series({peak: 110.0}), START, END, Settings(faded_after_days=6))
    assert strict.evidence.faded is False  # 6일 차이는 "6일 넘게"가 아니다
    loose = judge_series(_series({peak: 110.0}), START, END, Settings(faded_after_days=5))
    assert loose.evidence.faded is True
    assert faded_after_days_env({}) == 2
    assert faded_after_days_env({FADED_ENV: "4"}) == 4
    assert faded_after_days_env({FADED_ENV: "x"}) == 2
    assert faded_after_days_env({FADED_ENV: "-3"}) == 0


def test_old_responses_without_fields_still_parse() -> None:
    e = TrendEvidence.model_validate({"latest_period": "2026-11-25", "baseline_points": 56})
    assert e.faded is False and e.peak_start is None and e.peak_end is None
    r = RisingKeyword.model_validate({"keyword": "수능", "rank": 1, "status": "rising"})
    assert r.faded is False and r.peak_end is None


def _verdict(name: str, *, faded: bool, ratio: float = 10.0) -> TrendVerdict:
    return TrendVerdict(group_name=name, status="rising", ratio=ratio,
                        evidence=TrendEvidence(faded=faded, score=9.0))  # fmt: skip


def test_scan_summary_numbers_skip_faded() -> None:
    facts = _facts([_verdict("수능시간표", faded=True, ratio=110.8),
                    _verdict("기말고사", faded=False)])  # fmt: skip
    assert facts["급상승 수"] == 1
    assert facts["끝난 급상승 수"] == 1
    assert facts["키워드1"] == "기말고사"
    assert "수능시간표" not in facts.values()


def test_board_rising_names_skip_faded(tmp_path) -> None:  # noqa: ANN001
    board = TodayBoard(today=lambda: END, path=tmp_path / "board.db")
    board.record_verdict(["수능시간표"], _verdict("수능시간표", faded=True))
    board.record_verdict(["기말고사"], _verdict("기말고사", faded=False))
    # 인사이트 브리프·홍보 콘셉트가 쓰는 목록에서 빠진다 (판정 자체는 남는다)
    assert board.rising_names() == ["기말고사"]
    assert board.verdict(["수능시간표"]).evidence.faded is True


def test_recommend_topics_skip_faded() -> None:
    topics = [SimpleNamespace(keyword="수능시간표", verdict=_verdict("수능시간표", faded=True),
                              two_sources=False),
              SimpleNamespace(keyword="기말고사", verdict=_verdict("기말고사", faded=False),
                              two_sources=False)]  # fmt: skip
    config = SimpleNamespace(z_weight=1.0, two_source_bonus=0.0)
    picked = rank_topics(topics, config, 5)  # type: ignore[arg-type]
    assert [t.keyword for t in picked] == ["기말고사"]


def test_detect_rows_recompute_faded_from_stored_points_and_sort_last() -> None:
    peak = END - timedelta(days=6)
    points = list(_series({peak: 110.0}).points)
    assert faded_of("수능시간표", points, END) == (peak, True)
    fresh = list(_series({END - timedelta(days=1): 110.0}).points)
    assert faded_of("수능시간표", fresh, END)[1] is False
    rows = [
        SimpleNamespace(level="rising", faded=True, ratio=110.8, monthly_volume=1000),
        SimpleNamespace(level="rising", faded=False, ratio=3.0, monthly_volume=10),
        SimpleNamespace(level="warming", faded=False, ratio=1.8, monthly_volume=10),
    ]
    ordered = sorted(rows, key=kc.sort_key)
    assert [r.ratio for r in ordered] == [3.0, 110.8, 1.8]  # 같은 rising 칸 안에서 faded는 뒤로
