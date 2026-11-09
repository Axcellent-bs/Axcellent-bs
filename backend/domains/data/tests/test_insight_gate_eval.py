"""인사이트 브리프 기획에 넘기는 기준·홍보 영상 평가 정의(`eval/insight_gate/`)의 약속.

- 근거로 치지 않음: 가격만, 검색 줄어듦(1.0 미만), 캣북스가 팔지 않는 상품군 → 넘기지 않고 이유
- 숏츠 형식: 시간당 조회 1.6배·최근 3일 6개 → 홍보 영상, 1.6배여도 3일 2개 → 반짝이라 넘기지 않음
- LLM 없이(고정 틀) 돌린다
- 관문 켬 (평가 정의 PR #913은 관찰만: dev 모두 0.0)
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

pytestmark = pytest.mark.eval

HERE = Path(__file__).resolve().parents[1] / "eval" / "insight_gate"


def _load(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(f"insight_gate_{name}", HERE / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


make = _load("make_cases")
score = _load("score")


def test_answers_come_from_the_rules() -> None:
    by = {c["name"]: c for c in make.cases()}
    assert by["price_only_product"]["expected"]["passed"] is False
    assert by["format_rising_6_new"]["expected"]["passed"] is True
    assert by["format_rising_but_2_new"]["expected"]["passed"] is False
    assert {c["kind"] for c in by.values()} == {"trend", "promo"}


def test_the_scorer() -> None:
    cases = make.cases()
    perfect = {c["name"]: dict(c["expected"]) for c in cases}
    assert score.score(cases, perfect) == dict.fromkeys(score.METRICS, 1.0)
    assert score.score(cases, {}) == dict.fromkeys(score.METRICS, 0.0)


class _NoLLM:
    """LLM 설정 없음 (고정 틀로 돌린다)."""

    async def structured(self, *a: Any, **k: Any) -> Any:
        from ax_agent_template.llm import LLMNotConfiguredError

        raise LLMNotConfiguredError("평가: LLM 없이 돌림")


def _run(case: dict, tmp_path: Path) -> dict | None:
    """기준 코드(`data_agent.skills.promo_video`)로 한 케이스. 코드가 없으면 None."""
    try:
        from ax_contracts.schemas.data import (
            InsightEvidence,
            ShortsGroup,
            ShortsStatsOutput,
            TrendInsight,
        )
        from data_agent import line_store
        from data_agent.skills import promo_video as pv
    except ImportError:
        return None
    gate = pv.Gate()
    if case["kind"] == "trend":
        x = case["input"]
        groups = [ShortsGroup(name=x["fmt"], count=12, median_views_per_hour=100.0,
                              recent_count=9)] if x["fmt"] else []  # fmt: skip
        shorts = ShortsStatsOutput(status="known", by_format=groups,
                                   median_views_per_hour=make.BASE_VPH, recent_days=3)  # fmt: skip
        ev = InsightEvidence(naver_ratio=x["naver"], season_ratio=x["season"])
        i = TrendInsight(insight_id=f"ins_{x['topic']}", rank=1, topic=x["topic"], line="x",
                         shorts_format=x["fmt"], evidence=ev)  # fmt: skip
        held = pv.apply_gate([i], pv.format_signals(shorts), gate,
                             not_sold=lambda t: x["not_sold"],
                             price_only={i.insight_id: x["price_only"]})  # fmt: skip
        return {"passed": not i.reference_only, "reason": held[0].reason if held else None}
    x = case["input"]
    shorts = ShortsStatsOutput(
        status="known", median_views_per_hour=make.BASE_VPH, recent_days=3,
        by_format=[ShortsGroup(name=x["format"], count=12, median_views_per_hour=x["vph"],
                               recent_count=x["recent"])],
    )  # fmt: skip
    promos, held = pv.build_promos(pv.format_signals(shorts), [], gate, insight_id=lambda t: t)
    if not promos:
        return {"passed": False, "reason": held[0].reason if held else None}
    p = promos[0]
    old = line_store.LINES
    line_store.LINES = line_store.LineStore(str(tmp_path / "lines.db"))
    try:
        asyncio.run(pv.write_ideas([p], _NoLLM(), None, []))  # type: ignore[arg-type]
    finally:
        line_store.LINES = old
    return {"passed": True, "views_ratio": p.promo_views_per_hour_ratio,
            "recent": p.promo_recent_count, "fallback": p.promo_idea_fallback,
            "idea": p.promo_idea}  # fmt: skip


def observe(tmp_path: Path) -> tuple[dict[str, float], dict]:
    cases = make.cases()
    got = {}
    for c in cases:
        folder = tmp_path / c["name"]
        folder.mkdir(parents=True, exist_ok=True)
        got[c["name"]] = _run(c, folder)
    return score.score(cases, got), got


GATES = dict.fromkeys(score.METRICS, 1.0)  # 모두 맞아야 함 (평가 정의 PR #913 dev: 0.0 / 0.0)


def test_insight_gate_passes_the_gates(tmp_path: Path) -> None:
    metrics, got = observe(tmp_path)
    print("insight_gate eval:", json.dumps(metrics, ensure_ascii=False))
    print("insight_gate cases:", json.dumps(got, ensure_ascii=False, default=str))
    assert set(metrics) == set(score.METRICS)
    for name, gate in GATES.items():
        assert metrics[name] >= gate, (name, metrics[name], got)
