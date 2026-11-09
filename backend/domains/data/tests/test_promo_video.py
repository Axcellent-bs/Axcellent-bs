"""홍보 영상 인사이트(kind promo)와 기획에 넘기는 기준 (`skills/promo_video.py`)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from ax_agent_template.llm import LLMNotConfiguredError
from ax_contracts.schemas.data import (
    InsightEvidence,
    ShortsGroup,
    ShortsStatsOutput,
    TrendInsight,
)
from data_agent.skills import promo_video as pv
from data_agent.skills import trend_insight_brief as brief
from data_agent.skills.shorts_stats import ShortsConfig, _Short, group_stats

AT = datetime(2026, 11, 9, 6, 0, tzinfo=UTC)
GATE = pv.Gate()


def _shorts(*groups: ShortsGroup, base: float = 100.0) -> ShortsStatsOutput:
    return ShortsStatsOutput(status="known", snapshot_at=AT, by_format=list(groups),
                             median_views_per_hour=base, median_views_per_hour_count=40,
                             recent_days=3)  # fmt: skip


def _group(name: str, vph: float | None, recent: int | None, status: str = "known") -> ShortsGroup:
    reason = None if status == "known" else "표본 부족 (3개, 기준 5개)"
    return ShortsGroup(name=name, status=status, count=12, median_views=5000.0,  # type: ignore[arg-type]
                       median_views_per_hour=vph, recent_count=recent, reason=reason)  # fmt: skip


def _trend(topic: str, *, fmt: str | None = None, naver: float | None = None,
           season: float | None = None) -> TrendInsight:  # fmt: skip
    ev = InsightEvidence(naver_ratio=naver, season_ratio=season)
    return TrendInsight(insight_id=f"ins_{topic}", rank=1, topic=topic, line="x",
                        shorts_format=fmt, evidence=ev)  # fmt: skip


# ------------------------------------------------------------------ 기준


def test_gate_passes_rising_views_with_enough_new_shorts() -> None:
    ok, why = pv.judge(GATE, vs_all_ratio=1.6, search_ratio=None, recent=6, recent_days=3)
    assert ok
    assert why == "이 형식 1시간 조회가 숏츠 전체 대비 1.6배 · 최근 3일 게시된 같은 형식 숏츠 6개"
    assert "평소" not in why  # 숏츠 전체 대비 값이다 (지난 기간 대비가 아님)


def test_gate_naver_alone_is_enough_when_new_shorts_are_there() -> None:
    ok, why = pv.judge(GATE, vs_all_ratio=1.1, search_ratio=1.4, recent=5, recent_days=3)
    assert ok and "네이버 검색이 평소의 1.4배" in why


def test_gate_holds_back_a_one_off_spike() -> None:
    ok, why = pv.judge(GATE, vs_all_ratio=1.6, search_ratio=None, recent=2, recent_days=3)
    assert not ok and why == "최근 3일 같은 형식 숏츠 2개 (기준 5개, 반짝)"


def test_gate_period_text_comes_from_the_window_value() -> None:
    """기간 문구는 숏츠 통계가 준 값(= 검색 기간). 모르면 숫자를 쓰지 않는다."""
    ok, why = pv.judge(GATE, vs_all_ratio=1.6, search_ratio=None, recent=2, recent_days=7)
    assert not ok and why == "최근 7일 같은 형식 숏츠 2개 (기준 5개, 반짝)"
    ok, why = pv.judge(GATE, vs_all_ratio=1.6, search_ratio=None, recent=2, recent_days=None)
    assert not ok and why == "최근 검색 기간 같은 형식 숏츠 2개 (기준 5개, 반짝)"
    assert pv.recent_period(7) == "최근 7일" and pv.recent_period(None) == "최근 검색 기간"


def test_gate_says_how_many_shorts_had_no_publish_time() -> None:
    ok, why = pv.judge(GATE, vs_all_ratio=1.6, search_ratio=None, recent=2, recent_days=3,
                       recent_unknown=3)  # fmt: skip
    assert not ok and why == "최근 3일 같은 형식 숏츠 2개 (기준 5개, 게시 시각 모름 3개 제외)"
    ok, why = pv.judge(GATE, vs_all_ratio=1.6, search_ratio=None, recent=6, recent_days=3,
                       recent_unknown=3)  # fmt: skip
    assert ok and why.endswith("최근 3일 게시된 같은 형식 숏츠 6개 (게시 시각 모름 3개 제외)")


def test_gate_falling_search_is_not_evidence() -> None:
    label = "검색이 작년 같은 달의"
    ok, why = pv.judge(GATE, vs_all_ratio=None, search_ratio=0.8, search_label=label,
                       recent=9, recent_days=3)  # fmt: skip
    assert not ok and why == "검색이 작년 같은 달의 0.8배(줄어듦)"


def test_gate_price_only_and_not_sold() -> None:
    assert pv.judge(GATE, vs_all_ratio=None, search_ratio=None, recent=None, recent_days=3,
                    price_only=True) == (False, pv.PRICE_ONLY)  # fmt: skip
    assert pv.judge(GATE, vs_all_ratio=2.0, search_ratio=2.0, recent=9, recent_days=3,
                    not_sold=True) == (False, pv.NOT_SOLD)  # fmt: skip


def test_gate_below_threshold_and_small_sample_say_why() -> None:
    ok, why = pv.judge(GATE, vs_all_ratio=1.2, search_ratio=1.1, recent=9, recent_days=3)
    assert not ok
    assert why == "오름 근거가 기준 미만 (숏츠 전체 대비 1.2배 < 1.5배, 검색 1.1배 < 1.3배)"
    ok, why = pv.judge(GATE, vs_all_ratio=None, search_ratio=None, recent=1, recent_days=3,
                       short_sample="표본 부족 (3개, 기준 5개)")  # fmt: skip
    assert not ok and why == "트렌드 근거 없음 (숏츠 표본 부족 (3개, 기준 5개))"


def test_gate_values_come_from_config_and_env(tmp_path) -> None:  # noqa: ANN001
    cfg = tmp_path / "trends.toml"
    cfg.write_text("[brief]\ngate_format_vs_all_ratio = 2.0\n", encoding="utf-8")
    env = {"DATA_TREND_CONFIG": str(cfg), "DATA_BRIEF_GATE_RECENT_MIN": "7",
           "DATA_BRIEF_GATE_NAVER_RATIO": "nope"}  # fmt: skip
    gate = brief.gate_of(brief.load_config(env))
    assert gate == pv.Gate(vs_all_ratio=2.0, naver_ratio=1.3, recent_min=7, promo_max=3)
    env2 = {"DATA_BRIEF_GATE_FORMAT_VS_ALL_RATIO": "1.8"}
    assert brief.gate_of(brief.load_config(env2)).vs_all_ratio == 1.8
    assert gate.max_ratio == 10.0  # 브리프 highlight_max_ratio와 같은 값
    assert brief.gate_of(brief.load_config({})) == pv.Gate()  # 기본 1.5·1.3·5·3


# ------------------------------------------------------------------ 형식 근거·promo


def test_format_signals_are_ratios_of_the_shorts_values() -> None:
    groups = (_group("리액션", 160.0, 6), _group("브이로그", None, 2, "unknown"))
    sig = pv.format_signals(_shorts(*groups))
    assert sig["리액션"].vs_all_ratio == 1.6 and sig["리액션"].recent == 6
    assert sig["리액션"].at == AT
    assert sig["브이로그"].vs_all_ratio is None and sig["브이로그"].short_sample
    assert pv.format_signals(None) == {}


def test_build_promos_keeps_passing_formats_and_holds_back_the_rest() -> None:
    signals = pv.format_signals(_shorts(
        _group("리액션", 160.0, 6), _group("챌린지", 210.0, 8), _group("브이로그", 300.0, 2),
        _group("정보·팁", 90.0, 9), _group("기타", 500.0, 20), _group("밈 패러디", 170.0, 5),
        _group("상황극·스케치", 155.0, 7),
    ))  # fmt: skip
    trends = [_trend("리그오브레전드", fmt="챌린지", naver=1.8)]
    promos, held = pv.build_promos(signals, trends, GATE, insight_id=lambda t: f"id:{t}")
    assert [p.topic for p in promos] == ["챌린지", "밈 패러디", "리액션"]  # 배수 큰 순 최대 3개
    top = promos[0]
    assert top.kind == "promo" and top.promo_format == "챌린지" and not top.reference_only
    assert top.promo_views_per_hour_ratio == 2.1 and top.promo_recent_count == 8
    assert top.evidence.naver_ratio == 1.8  # 같은 형식 트렌드 주제의 검색 상승 (주제 이름은 안 씀)
    assert "리그오브레전드" not in (top.company_reason or "") + top.topic
    assert top.insight_id == "id:promo:챌린지" and top.shorts_format_at == AT
    reasons = {h.topic: h.reason for h in held}
    assert reasons["홍보 영상 · 브이로그"] == "최근 3일 같은 형식 숏츠 2개 (기준 5개, 반짝)"
    assert reasons["홍보 영상 · 정보·팁"].startswith("오름 근거가 기준 미만")
    assert reasons["홍보 영상 · 상황극·스케치"] == "기준은 넘었지만 홍보 영상은 최대 3개"
    assert "홍보 영상 · 기타" not in reasons  # 빌릴 형식이 아님


def test_apply_gate_turns_weak_trend_insights_into_reference() -> None:
    signals = pv.format_signals(_shorts(_group("리액션", 160.0, 6), _group("챌린지", 160.0, 2)))
    good = _trend("고양이 그림책", fmt="리액션")
    spike = _trend("니체 철학", fmt="챌린지")
    falling = _trend("겨울 다이어리", season=0.8)
    stationery = _trend("문구 하울", naver=2.0, fmt="리액션")
    price = _trend("책추천")
    held = pv.apply_gate([good, spike, falling, stationery, price], signals, GATE,
                         not_sold=lambda t: t == "문구 하울",
                         price_only={"ins_책추천": True})  # fmt: skip
    assert not good.reference_only and good.gate_reason
    assert all(i.reference_only for i in (spike, falling, stationery, price))
    assert [(h.topic, h.reason) for h in held] == [
        ("니체 철학", "최근 3일 같은 형식 숏츠 2개 (기준 5개, 반짝)"),
        ("겨울 다이어리", "검색이 작년 같은 달의 0.8배(줄어듦)"),
        ("문구 하울", pv.NOT_SOLD),
        ("책추천", pv.PRICE_ONLY),
    ]


# ------------------------------------------------------------------ 아이디어 한 줄


def test_fixed_idea_uses_the_right_particle() -> None:
    assert pv.fixed_idea("리액션") == "리액션으로 캣북스의 책 속 반전을 읽는 순간의 표정 보여 주기"
    assert pv.fixed_idea("브이로그") == "브이로그로 캣북스의 하루 독서 시간 보여 주기"
    assert pv.fixed_idea("밈 패러디") == "밈 패러디로 캣북스의 책 읽는 고양이 장면 보여 주기"
    assert pv.fixed_idea("상황극·스케치").startswith("상황극·스케치로 ")


def test_idea_rules() -> None:
    assert pv.idea_problem("반전 표정으로 캣북스 신간을 읽는 장면", []) is None
    assert pv.idea_problem("독서 챌린지 3일 도전", []) == "숫자를 씀 (숫자 지어내기 금지)"
    assert pv.idea_problem("반전 표정 리액션 영상", []) == "캣북스·책·독서 소재가 없음"
    assert pv.idea_problem("리그오브레전드 밈으로 책 읽기", ["리그오브레전드"]) == (
        "다른 이름('리그오브레전드')을 씀"
    )
    assert pv.idea_problem("책" * 61, []) == "60자를 넘음 (61자)"


class _NoLLM:
    async def structured(self, *a, **k):  # noqa: ANN002, ANN003, ANN202
        raise LLMNotConfiguredError("LLM 키 없음")


class _BadLLM:
    async def structured(self, prompt, schema, **k):  # noqa: ANN001, ANN003, ANN202
        return schema(items=[{"format": "리액션", "idea": "아이돌 리액션 따라 하기"}])


def _promo(name: str) -> TrendInsight:
    return TrendInsight(insight_id=f"id:{name}:{id(object())}", rank=1, topic=name, line="",
                        kind="promo", promo_format=name, promo_views_per_hour_ratio=1.6,
                        promo_recent_count=6, promo_recent_days=3)  # fmt: skip


def test_write_ideas_falls_back_to_the_fixed_frame(monkeypatch, tmp_path) -> None:  # noqa: ANN001
    from data_agent import line_store

    monkeypatch.setattr(line_store, "LINES", line_store.LineStore(str(tmp_path / "lines.db")))
    queries: list[str] = []
    a = _promo("리액션")
    asyncio.run(pv.write_ideas([a], _NoLLM(), None, queries))  # type: ignore[arg-type]
    assert a.promo_idea_fallback and a.promo_idea == pv.fixed_idea("리액션")
    assert a.line == a.promo_idea
    assert a.line_status == "fixed" and any("고정 틀" in x for x in a.limits)
    b = _promo("리액션")
    asyncio.run(pv.write_ideas([b], _BadLLM(), None, queries))  # type: ignore[arg-type]
    assert b.promo_idea_fallback  # 책·독서 소재가 없는 답은 쓰지 않는다
    assert any("캣북스·책·독서 소재가 없음" in x for x in b.limits)


# ------------------------------------------------------------------ 숏츠 통계 recent_count


def test_group_stats_counts_recent_shorts_per_format() -> None:
    items = []
    for n in range(6):
        s = _Short(f"v{n}", "t", None, 100 + n, 10.0, (), "popular", format="리액션")
        s.extra["recent"] = n < 4
        items.append(s)
    nopub = _Short("y", "t", None, 7, 1.0, (), "search", format="리액션")  # 게시 시각 모름
    old = _Short("x", "t", None, 5, 1.0, (), "popular", format="챌린지")  # 게시 시각 모름
    groups = {g.name: g for g in group_stats([*items, nopub, old], "format",
                                             ["리액션", "챌린지"], ShortsConfig())}  # fmt: skip
    assert groups["리액션"].recent_count == 4 and groups["리액션"].recent_unknown == 1
    assert groups["챌린지"].recent_count == 0 and groups["챌린지"].recent_unknown == 1
    assert groups["챌린지"].status == "unknown"


def test_publish_times_come_from_snippet_published_at(tmp_path) -> None:  # noqa: ANN001
    """검색·다시 조회 응답의 published_at을 남기고 그대로 읽는다 (처음 수집 시각이 아님)."""
    from types import SimpleNamespace

    from data_agent.skills.shorts_stats import ShortsStore

    store = ShortsStore(tmp_path / "shorts.db")
    pub = datetime(2026, 11, 1, 3, 0, tzinfo=UTC)
    store.record_views(AT, [SimpleNamespace(video_id="a", view_count=10, published_at=pub),
                            SimpleNamespace(video_id="b", view_count=5)])  # fmt: skip
    assert store.published(["a", "b"]) == {"a": pub}
    store.close()


def test_gate_does_not_count_huge_ratios_from_tiny_baselines() -> None:
    """평소 값이 거의 0이라 부풀려진 배수(예: 검색 537배)는 근거로 치지 않는다."""
    ok, why = pv.judge(GATE, vs_all_ratio=0.84, search_ratio=537.61, recent=9, recent_days=3)
    assert not ok and why == "오름 근거가 기준 미만 (숏츠 전체 대비 0.84배 < 1.5배)"
    ok, why = pv.judge(GATE, vs_all_ratio=None, search_ratio=537.61, recent=9, recent_days=3)
    assert not ok and why == "트렌드 근거 없음 (검색 537.61배는 비교 값이 너무 작아 근거로 안 씀)"
    signals = pv.format_signals(_shorts(_group("정보·팁", 84.0, 9)))
    promos, held = pv.build_promos(signals, [_trend("급상승 뉴스", fmt="정보·팁", naver=537.61)],
                                   GATE, insight_id=lambda t: t)  # fmt: skip
    assert promos == [] and held[0].reason.startswith("오름 근거가 기준 미만")
