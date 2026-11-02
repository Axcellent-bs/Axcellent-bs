"""trend_scan 채점기(`eval/trend/score.py`) 테스트. skill 없이 가짜 판정 함수로 돈다.

holdout은 사람이 열지 않는다. 여기서는 정답을 그대로 돌려주는 가짜 판정이 기계로만 읽는다.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

import httpx
import pytest

from ax_contracts.schemas import TrendScanInput, TrendScanOutput, TrendVerdict
from data_agent.connectors.naver_datalab import TrendResponse

TREND = Path(__file__).resolve().parents[1] / "eval" / "trend"


def _score() -> ModuleType:
    spec = importlib.util.spec_from_file_location("trend_score", TREND / "score.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


score = _score()


def _output(query: TrendScanInput, statuses: dict[str, str]) -> TrendScanOutput:
    verdicts = [
        TrendVerdict(group_name=g, status="unknown", unknown_reason="가짜 판정")
        if s == "unknown"
        else TrendVerdict(group_name=g, status=s, ratio=1.0)
        for g, s in statuses.items()
    ]
    return TrendScanOutput(start_date=query.start_date, end_date=query.end_date,
                           time_unit=query.time_unit, source="fake", results=verdicts)  # fmt: skip


def always(status: str):  # noqa: ANN201
    def judge(query: TrendScanInput, response: TrendResponse) -> TrendScanOutput:
        return _output(query, {s.group_name: status for s in response.series})

    return judge


def _key(query: TrendScanInput) -> tuple:
    return (query.start_date, query.end_date, tuple(g.group_name for g in query.keyword_groups))


def oracle(*folders: Path):  # noqa: ANN201
    """정답을 그대로 돌려주는 가짜 판정 (채점 계산 확인용)."""
    answers = {}
    for folder in folders:
        for case in score.load(folder):
            query = TrendScanInput.model_validate(case["input"])
            answers[_key(query)] = {g: e["status"] for g, e in case["expected"].items()}

    def judge(query: TrendScanInput, response: TrendResponse) -> TrendScanOutput:
        return _output(query, answers[_key(query)])

    return judge


def _run(judge) -> dict:  # noqa: ANN001
    return asyncio.run(score.run(judge))


def test_perfect_judge_scores_full_marks() -> None:
    report = _run(oracle(score.CASES, score.HOLDOUT))
    m = report["metrics"]
    assert m["trend.detection_rate"] == 1.0
    assert m["trend.false_positive_rate"] == 0.0
    assert m["trend.unknown_handled_rate"] == 1.0
    assert m["trend.group_isolation"] == 1.0
    assert m["holdout.trend.detection_rate"] == 1.0
    assert m["holdout.trend.false_positive_rate"] == 0.0
    assert report["wrong"] == []
    assert report["holdout"]["wrong_count"] == 0


def test_always_rising_is_caught_by_false_positive_and_isolation() -> None:
    m = _run(always("rising"))["metrics"]
    assert m["trend.detection_rate"] == 1.0
    assert m["trend.false_positive_rate"] == 1.0
    assert m["trend.unknown_handled_rate"] == 0.0
    assert m["trend.group_isolation"] == 0.0
    assert m["trend.seasonal_false_positive_rate"] == 1.0
    assert m["trend.real_detection_rate"] == 1.0


def test_always_normal_detects_nothing() -> None:
    m = _run(always("normal"))["metrics"]
    assert m["trend.detection_rate"] == 0.0
    assert m["trend.false_positive_rate"] == 0.0
    assert m["holdout.trend.detection_rate"] == 0.0


def test_seasonal_and_real_cases_stay_out_of_main_metrics() -> None:
    report = _run(always("rising"))
    counts = report["counts"]
    cases = score.load(score.CASES)
    main = [c for c in cases if c["kind"] not in ("seasonal", "real_event")]
    rising = sum(e["status"] == "rising" for c in main for e in c["expected"].values())
    normal = sum(e["status"] == "normal" for c in main for e in c["expected"].values())
    assert counts["rising_groups"] == rising
    assert counts["normal_groups"] == normal
    assert counts["seasonal_groups"] == 3
    assert counts["real_groups"] == 3
    assert not any(w["case"].startswith(("seasonal_", "event_")) for w in report["wrong"])


def test_group_isolation_needs_only_the_injected_group() -> None:
    joint = [c for c in score.load(score.CASES) if c["kind"] == "joint"]
    answers = {c["id"]: {g: "rising" for g in c["expected"]} for c in joint}
    assert score.score(joint, answers)["metrics"]["trend.group_isolation"] == 0.0
    answers = {c["id"]: {g: e["status"] for g, e in c["expected"].items()} for c in joint}
    assert score.score(joint, answers)["metrics"]["trend.group_isolation"] == 1.0


def test_judge_errors_and_missing_groups_count_as_wrong() -> None:
    def broken(query: TrendScanInput, response: TrendResponse) -> TrendScanOutput:
        raise RuntimeError("판정 실패")

    def drops_groups(query: TrendScanInput, response: TrendResponse) -> TrendScanOutput:
        return _output(query, {})

    for judge in (broken, drops_groups):
        report = _run(judge)
        assert report["metrics"]["trend.detection_rate"] == 0.0
        assert report["metrics"]["trend.unknown_handled_rate"] == 0.0
        assert all(w["got"] == "missing" for w in report["wrong"])


def test_async_judge_is_supported() -> None:
    sync = always("normal")

    async def judge(query: TrendScanInput, response: TrendResponse) -> TrendScanOutput:
        return sync(query, response)

    assert _run(judge)["metrics"] == _run(sync)["metrics"]


def test_judge_gets_saved_values_in_connector_shape() -> None:
    case = next(c for c in score.load(score.CASES) if c["id"] == "unknown_no_data")
    query, response = score.to_request(case)
    assert isinstance(response, TrendResponse)
    assert response.series[0].points == ()  # 빈 자료는 빈 채로 (0으로 채우지 않음)


def test_scoring_does_not_call_the_network(monkeypatch: pytest.MonkeyPatch) -> None:
    def blocked(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        raise AssertionError("채점이 네트워크를 불렀다")

    monkeypatch.setattr(httpx.AsyncClient, "send", blocked)
    monkeypatch.setattr(httpx.Client, "send", blocked)
    _run(always("normal"))


def test_holdout_shows_only_the_wrong_count(capsys: pytest.CaptureFixture[str]) -> None:
    report = _run(always("rising"))
    text = score.render(report)
    assert "holdout_" not in text  # holdout 케이스 이름을 보여 주지 않는다
    assert f"틀린 그룹 {report['holdout']['wrong_count']}" in text
    assert "wrong" not in json.dumps(report["holdout"]).replace("wrong_count", "")
