"""정확도 평가 회귀 관문: 평가를 돌려 기준(`eval/baseline.json`)보다 떨어지면 실패한다.

평가는 `eval/run.py`이고, LLM은 평가용 클라이언트라 CI에서도 같은 값이 나온다.
코드를 고쳐 정확도가 올라가면 `uv run python domains/data/eval/run.py --write-baseline`으로
기준을 새로 저장한다(기준을 낮추는 커밋은 리뷰에서 이유를 본다).
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
from pathlib import Path

import pytest

EVAL = Path(__file__).resolve().parents[1] / "eval"


def _load(name: str):  # noqa: ANN202
    sys.path.insert(0, str(EVAL))
    spec = importlib.util.spec_from_file_location(f"data_eval_{name}", EVAL / f"{name}.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def run():  # noqa: ANN201
    return _load("run")


@pytest.fixture(scope="module")
def report(run):  # noqa: ANN001, ANN201
    return asyncio.run(run.evaluate())


def test_golden_cases_cover_the_required_situations(run) -> None:  # noqa: ANN001
    scenarios, sellers = run.load_cases()
    kinds = {c["kind"] for c in scenarios}
    assert len(scenarios) >= 20
    assert kinds >= {"payment_fault", "order_drop", "normal", "sale", "empty"}
    assert {c["cause"]["type"] for c in scenarios} >= {"deploy", "notice", "campaign", "none"}
    assert {s["label"] for s in sellers} == {"fraud", "normal"}


def test_answers_are_computed_from_the_scenario_data(run) -> None:  # noqa: ANN001
    """정답지의 정답이 시나리오 데이터에서 다시 센 값과 같다 (손으로 적은 정답이 아님)."""
    make_cases = _load("make_cases")
    scenarios, _ = run.load_cases()
    for case in scenarios:
        spec = {k: v for k, v in case.items() if k != "expected"}
        assert make_cases.expected(spec) == case["expected"], case["id"]


def test_accuracy_does_not_drop_below_baseline(run, report) -> None:  # noqa: ANN001
    baseline = json.loads((EVAL / "baseline.json").read_text(encoding="utf-8"))["metrics"]
    worse = run.compare(report["metrics"], baseline)
    assert not worse, "정확도가 기준보다 떨어졌다:\n" + "\n".join(worse)
    assert set(baseline) == set(run.METRICS)  # 지표를 늘리면 기준도 다시 저장한다


def test_guards_hold_regardless_of_the_baseline(report) -> None:  # noqa: ANN001
    """기준과 상관없이 지켜야 하는 것: 지어낸 숫자·근거 없는 후보가 남지 않는다."""
    assert report["metrics"]["llm.fabricated_number_rate"] == 0.0
    assert report["metrics"]["root_cause.ungrounded_survived_rate"] == 0.0
    assert not any(c["answer_drift"] for c in report["cases"])


def test_compare_flags_drops_and_lost_values(run) -> None:  # noqa: ANN001
    base = {
        "anomaly.detection_rate": 0.8,
        "impact.mean_abs_amount_error": 10.0,
        "fraud.precision": 0.5,
    }
    now = {
        "anomaly.detection_rate": 0.7,
        "impact.mean_abs_amount_error": 12.0,
        "fraud.precision": None,
    }
    worse = run.compare(now, base)
    assert len(worse) == 3
    assert run.compare(base, base) == []
