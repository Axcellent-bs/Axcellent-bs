"""accuracy_report: 평가 결과 파일을 읽어 준다. 없으면 확인할 수 없음."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path

from fastapi.testclient import TestClient

from data_agent.main import build_app
from data_agent.skills.accuracy_report import DEFAULT_EVAL_DIR, load_report

LATEST = {
    "generated_at": "2026-10-29T01:00:00Z",
    "case_count": 21,
    "metrics": {"anomaly.detection_rate": 0.6667, "fraud.precision": None},
    "wrong": [{"id": "normal_10h", "area": "이상 탐지", "what": "오탐"}],
}


def _write(folder: Path, name: str, data: dict) -> None:
    path = folder / name
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")


def test_latest_result_wins_over_baseline(tmp_path: Path) -> None:
    _write(tmp_path, "baseline.json", {"generated_at": "2026-10-01T00:00:00Z", "metrics": {}})
    _write(tmp_path, "results/latest.json", LATEST)
    report = load_report(tmp_path)
    assert report.source == "latest"
    assert report.case_count == 21
    assert report.metrics == {"anomaly.detection_rate": 0.6667, "fraud.precision": None}
    assert [m.case_id for m in report.misses] == ["normal_10h"]
    assert "틀린 케이스 1개" in report.summary


def test_baseline_is_used_without_a_recent_run(tmp_path: Path) -> None:
    _write(tmp_path, "baseline.json", {"generated_at": "2026-10-01T00:00:00Z", "metrics": {"x": 1}})
    report = load_report(tmp_path)
    assert (report.source, report.metrics, report.misses) == ("baseline", {"x": 1.0}, [])


def test_nothing_means_cannot_tell(tmp_path: Path) -> None:
    report = load_report(tmp_path)
    assert report.source == "none"
    assert report.metrics == {}
    assert report.summary.startswith("확인할 수 없음")


def test_committed_baseline_is_found_by_default() -> None:
    assert (DEFAULT_EVAL_DIR / "baseline.json").is_file()


def test_skill_answers_over_a2a(tmp_path: Path, monkeypatch) -> None:  # noqa: ANN001
    monkeypatch.setenv("AGENT_TEMPLATE_MOCK", "1")
    monkeypatch.setenv("DATA_EVAL_DIR", str(tmp_path))
    _write(tmp_path, "results/latest.json", LATEST)
    body = {
        "meta": {
            "context_id": "ctx_dash",
            "token": "mock:dashboard-api",
            "path": [],
            "deadline": (datetime.now(UTC) + timedelta(seconds=30)).isoformat(),
        },
        "skill": "accuracy_report",
        "input": {},
    }
    result = TestClient(build_app()).post("/a2a", json=body).json()
    assert result["status"] == "completed", result
    assert result["output"]["source"] == "latest"
