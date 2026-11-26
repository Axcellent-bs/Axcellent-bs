"""약한 근거를 뒤로 (11/26, trend_report_insight.pick_facts · is_weak).

TR-BB81: 기획에 넘긴 근거 맨 앞이 "음악방송 검색 −54.9%"(줄어드는 숫자)와
"#책추천 2곳"(작은 표본)이었다.
약한 근거는 빼지 않고(FACTS_MAX 12 그대로) 같은 무리 안 순서를 지킨 채 맨 뒤로 보낸다.
"방향" 줄은 그대로 맨 앞. 가짜 보고서만 쓴다(LLM·외부 호출 없음).
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest

from ax_contracts.schemas.data import (
    TrendHashtagRow,
    TrendMaterial,
    TrendReportEvidence,
    TrendSearchRow,
    TrendThemeRow,
)
from data_agent.skills import trend_report_insight as ti


def _load(name: str, file: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(file))
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


H = _load("trend_report_helpers_weak_facts", "test_trend_report.py")


def _material(*ids: str) -> TrendMaterial:
    return TrendMaterial(kind="format", text="재료",
                         evidence_items=[TrendReportEvidence(id=i, text="근거", short="근거")
                                         for i in ids])  # fmt: skip


@pytest.fixture
def bb81(tmp_path: Path):  # noqa: ANN201
    """TR-BB81 모양: 결론 근거가 −54.9% 검색 → 2곳 해시태그 → 양수 검색 순서."""
    got = H._filled(tmp_path)  # noqa: SLF001
    s = got.sections
    s.theme.status = "waiting"
    s.search.rows = [
        TrendSearchRow(keyword="음악방송", search_change_pct=-54.9, basis="filtered"),
        TrendSearchRow(keyword="공연 티켓", search_change_pct=12.3, basis="filtered"),
    ]
    s.hashtags.rows = [
        TrendHashtagRow(tag="#책추천", accounts=2, previous_accounts=1, burst_posts=0),
    ]
    s.content.posts, s.content.mega, s.content.shorts, s.content.memes = [], [], [], []
    s.competitors.rows = []
    rec = s.recommendation
    rec.beats = []
    rec.materials = [_material("search:1", "hashtag:1", "search:2")]
    return got


def _labels(report: Any) -> list[str]:
    return [f.label for f in ti.pick_facts(report)]


def test_is_weak_reads_the_raw_numbers(bb81: Any) -> None:
    assert ti.is_weak(bb81, "search:1") is True  # −54.9%
    assert ti.is_weak(bb81, "search:2") is False  # +12.3%
    assert ti.is_weak(bb81, "hashtag:1") is True  # 2곳 < 5
    assert ti.HASHTAG_MIN_ACCOUNTS == 5
    bb81.sections.hashtags.rows[0].accounts = 5
    assert ti.is_weak(bb81, "hashtag:1") is False  # 5곳은 약하지 않음 (미만만)
    # 쇼핑·주제 후보 변화율도 음수면 약함, 없으면(None) 약하지 않음
    bb81.sections.search.rows[1].shopping_change_pct = -3.0
    assert ti.is_weak(bb81, "shopping:2") is True
    assert ti.is_weak(bb81, "shopping:1") is False
    # 모르는 근거·번호가 맞지 않는 근거는 약하지 않음
    assert ti.is_weak(bb81, "mega:1") is False
    assert ti.is_weak(bb81, "search:9") is False
    assert ti.is_weak(bb81, "search:x") is False


def test_is_weak_theme_change(bb81: Any) -> None:
    t = bb81.sections.theme
    t.status, t.theme = "known", "공연"
    t.rows = [TrendThemeRow(name="뮤지컬", level=100, change_pct=-1.0),
              TrendThemeRow(name="콘서트", level=60, change_pct=4.0),
              TrendThemeRow(name="연극", level=30)]  # fmt: skip
    assert ti.is_weak(bb81, "theme:1") is True
    assert ti.is_weak(bb81, "theme:2") is False
    assert ti.is_weak(bb81, "theme:3") is False  # 변화율 모름 → 약하지 않음
    # 주제 후보 무리도 약한 것(뮤지컬)이 뒤로
    labels = _labels(bb81)
    assert labels.index(next(x for x in labels if "콘서트" in x)) < labels.index(
        next(x for x in labels if "뮤지컬" in x)
    )


def test_bb81_positive_search_is_the_first_fact_after_direction(bb81: Any) -> None:
    labels = _labels(bb81)
    assert labels[0] == "방향"  # 방향 줄은 그대로 맨 앞
    assert "공연 티켓" in labels[1]  # 양수 검색이 첫 근거
    # 약한 둘은 빼지 않고 뒤로, 원래 순서(검색 −54.9% → 해시태그 2곳) 그대로
    assert "음악방송" in labels[2] and "#책추천" in labels[3]
    assert len(labels) == 4
    # SOURCE 값은 그대로
    assert {f.source for f in ti.pick_facts(bb81)} == {ti.SOURCE}


def test_only_weak_facts_keep_their_order(bb81: Any) -> None:
    bb81.sections.search.rows[1].search_change_pct = -1.0  # 양수 검색도 줄어드는 숫자로
    labels = _labels(bb81)[1:]
    assert ["음악방송" in labels[0], "#책추천" in labels[1], "공연 티켓" in labels[2]] == [True] * 3


def test_cap_stays_twelve(bb81: Any) -> None:
    s = bb81.sections
    s.search.rows = [TrendSearchRow(keyword=f"k{i}", search_change_pct=-float(i + 1),
                                    basis="filtered") for i in range(8)]  # fmt: skip
    s.search.rows += [TrendSearchRow(keyword=f"p{i}", search_change_pct=float(i + 1),
                                     basis="filtered") for i in range(8)]  # fmt: skip
    ids = [f"search:{n}" for n in range(1, 17)]
    s.recommendation.materials = [_material(*ids)]
    labels = _labels(bb81)
    assert len(labels) == ti.FACTS_MAX
    # 양수 8개가 방향 다음에 먼저, 남은 3칸은 약한 근거(빼지 않음)
    assert all("'p" in x for x in labels[1:9]) and all("'k" in x for x in labels[9:])
