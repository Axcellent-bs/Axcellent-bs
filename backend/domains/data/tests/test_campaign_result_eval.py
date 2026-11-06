"""campaign_result 평가 정의(`eval/campaign_result/`)의 약속.

- 정답은 합성 campaign_effect 결과와 영상에서 나온다 (흔들림 안·위·아래, 표본 적음 + 마이너스,
  프로모션 없음, 마이너스 보존, 네 칸 합산 금지, 조회 달성인데 매출 감소, 데이터 공백)
- 채점기: 정답 그대로면 지표 넷 모두 1.0, 빈 답이면 0
- skill 판정 함수를 같은 입력으로 돌려 지표를 관찰한다 (이 PR에서는 관문 없음, 값만 남긴다)
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest

pytestmark = pytest.mark.eval

HERE = Path(__file__).resolve().parents[1] / "eval" / "campaign_result"


def _load(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(f"campaign_result_{name}", HERE / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


make = _load("make_cases")
score = _load("score")


def test_answers_come_from_the_specs() -> None:
    by = {c["name"]: c["expected"] for c in make.cases()}
    assert (by["in_range"]["verdict"], by["in_range"]["label"]) == ("no_effect", "효과 확인 안 됨")
    assert by["above"]["verdict"] == "more" and by["above"]["alerts"] == []
    assert by["below"]["verdict"] == "less" and by["below"]["alerts"] == ["loss"]
    low = by["low_sample_negative"]
    assert (low["verdict"], low["label"], low["low_sample"]) == (
        "no_effect", "효과 확인 안 됨(표본 적음)", True)  # fmt: skip
    assert low["cells"]["store"]["incremental_gross_krw"] == -60_000 and low["alerts"] == []
    assert by["no_promo"]["unmeasurable"] == ["vidnopromo1", "vidnopromo2"]
    assert by["no_promo"]["measurable_line"] == "영상 2개 중 매출 측정 가능 0개"
    assert by["negative_preserved"]["cells"]["products"]["incremental_gross_krw"] == -150_000
    assert by["no_sum"]["summed"] == 1_100_000
    assert by["views_up_sales_down"]["notes"] == ["조회는 목표의 2.50배였지만 매출은 줄었습니다"]
    assert by["data_gap"]["verdict"] is None and by["data_gap"]["alerts"] == ["data_gap"]
    for case in make.cases():
        assert make.build(case["name"]) == case


def _perfect(case: dict) -> dict:
    want = case["expected"]
    cells = [{"key": k, **v} for k, v in want["cells"].items()]
    return {
        "sales": {"verdict": {"verdict": want["verdict"], "label": want["label"],
                              "low_sample": want["low_sample"]},
                  "cells": cells, "measurable_line": want["measurable_line"]},
        "alerts": [{"kind": k} for k in want["alerts"]],
        "videos": [{"video_id": v, "sales_reason": score.UNMEASURABLE}
                   for v in want["unmeasurable"]],
    }  # fmt: skip


def test_the_scorer() -> None:
    cases = make.cases()
    perfect = score.score(cases, {c["name"]: _perfect(c) for c in cases})
    assert perfect == dict.fromkeys(score.METRICS, 1.0)
    assert score.score(cases, {}) == dict.fromkeys(score.METRICS, 0.0)
    summed = {c["name"]: _perfect(c) for c in cases}  # 네 칸을 더한 값이 있으면 틀림
    summed["no_sum"]["sales"]["total_krw"] = 1_100_000
    assert score.score(cases, summed)["campaign_result.negative_preserved_rate"] < 1.0
    clamped = {c["name"]: _perfect(c) for c in cases}  # 마이너스를 0으로 바꾸면 틀림
    clamped["low_sample_negative"]["sales"]["cells"][0]["incremental_gross_krw"] = 0
    assert score.score(cases, clamped)["campaign_result.negative_preserved_rate"] < 1.0


def _answer(case: dict) -> dict | None:
    """skill 판정 함수에 같은 입력을 넣은 답. 판정 함수·계약 칸이 없으면(dev) None = 0점."""
    from pydantic import ValidationError

    from ax_contracts.schemas.data import CampaignEffectOutput, VideoPerformanceOutput

    try:
        from data_agent.skills.campaign_result import judge
    except ImportError:
        return None
    try:
        effect = CampaignEffectOutput.model_validate(case["effect"])
        videos = [VideoPerformanceOutput.model_validate(v) for v in case["videos"]]
    except ValidationError:
        return None
    sales, alerts = judge(effect, videos, case["name"])
    return {"sales": sales.model_dump(mode="json"),
            "alerts": [a.model_dump(mode="json") for a in alerts],
            "videos": [v.model_dump(mode="json") for v in videos]}  # fmt: skip


def test_observe_the_skill_on_the_cases() -> None:
    """관찰만: 지표를 남긴다 (관문은 skill PR에서 켠다)."""
    cases = make.cases()
    metrics = score.score(cases, {c["name"]: _answer(c) for c in cases})
    print("campaign_result eval:", json.dumps(metrics, ensure_ascii=False))
    assert set(metrics) == set(score.METRICS)
