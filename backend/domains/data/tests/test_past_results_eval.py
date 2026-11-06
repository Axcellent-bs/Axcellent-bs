"""인사이트 "지난번 결과" 평가 정의(`eval/past_results/`)의 약속.

- 캠페인 없음 → [], 4개면 최근 3개, 조회 달성 + 매출 감소 → 빨강 + 반대 문구, 측정 중 → 예상치
  없음, 지난 결과가 인사이트 판정·순서를 바꾸지 않음, insight_id(예전 id 포함)로 연결
- 이 PR에서는 관찰만 (관문은 코드 PR에서)
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import pytest

pytestmark = pytest.mark.eval

HERE = Path(__file__).resolve().parents[1] / "eval" / "past_results"


def _load(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(f"past_results_{name}", HERE / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


make = _load("make_cases")
score = _load("score")


def test_answers_come_from_the_specs() -> None:
    (case,) = make.cases()
    want = case["expected"]
    assert want["links"]["ins_none"] == [] and len(want["links"]["ins_four"]) == 3
    assert want["bands"]["ins_contra"] == "red" and want["notes"]["ins_contra"] == make.CONTRA
    assert want["links"]["ins_new_id"] == ["c_old_id"]  # 예전 id로 연결 (주제 이름은 다름)
    assert make.cases() == [case]


def _perfect(case: dict) -> dict:
    want = case["expected"]
    after = json.loads(json.dumps(case["brief"]))
    for i in after["insights"]:
        ids = want["links"][i["insight_id"]]
        i["past_results"] = [{"campaign_id": c, "views_verdict": want["views"].get(i["insight_id"]),
                              "band": want["bands"][i["insight_id"]] if n == 0 else "gray",
                              "note_line": want["notes"].get(i["insight_id"])}
                             for n, c in enumerate(ids)]  # fmt: skip
    return {"before": case["brief"], "after": after}


def test_the_scorer() -> None:
    cases = make.cases()
    assert score.score(cases, {"past": _perfect(cases[0])}) == dict.fromkeys(score.METRICS, 1.0)
    assert score.score(cases, {}) == dict.fromkeys(score.METRICS, 0.0)
    moved = _perfect(cases[0])
    moved["after"]["insights"].reverse()  # 순서를 바꾸면 틀림
    assert score.score(cases, {"past": moved})["past_results.invariance_rate"] < 1.0


def _answer(case: dict) -> dict | None:
    """붙이는 함수에 같은 입력을 넣은 답. 함수·계약 칸이 없으면(dev) None = 0점."""
    from pydantic import ValidationError

    from ax_contracts.schemas.data import CampaignResultOutput, TrendInsightBriefOutput

    try:
        from data_agent.past_results import attach_past
    except ImportError:
        return None
    try:
        brief = TrendInsightBriefOutput.model_validate(case["brief"])
        results = [CampaignResultOutput.model_validate(r) for r in case["results"]]
    except ValidationError:
        return None
    before = brief.model_dump(mode="json")
    after = attach_past(brief, results).model_dump(mode="json")
    return {"before": before, "after": after}


def test_observe_past_results() -> None:
    """관찰만: 지표를 남긴다 (관문은 코드 PR에서 켠다)."""
    cases = make.cases()
    metrics = score.score(cases, {c["name"]: _answer(c) for c in cases})
    print("past_results eval:", json.dumps(metrics, ensure_ascii=False))
    assert set(metrics) == set(score.METRICS)
