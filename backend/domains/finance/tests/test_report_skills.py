"""A2A 읽기 skill `closing_report` 테스트."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from ax_agent_template import events
from finance_agent import handlers
from finance_agent.main import app
from finance_agent.schemas import ClosingReportOutput
from finance_agent.sources import MockSources


@pytest.fixture(autouse=True)
def _mock_mode(monkeypatch: pytest.MonkeyPatch, fixed_now) -> None:
    monkeypatch.setenv("AGENT_TEMPLATE_MOCK", "1")
    events.reset_mock_bus()
    handlers.reset_state(MockSources())


def _ask(skill: str, data: dict, token: str = "mock:orchestrator") -> dict:
    body = {
        "meta": {
            "context_id": "ctx_req_report",
            "token": token,
            "path": [],
            "deadline": (datetime.now(UTC) + timedelta(seconds=30)).isoformat(),
        },
        "skill": skill,
        "input": data,
    }
    response = TestClient(app).post("/a2a", json=body)
    assert response.status_code == 200
    return response.json()


def test_closing_report_matches_dashboard_numbers() -> None:
    result = _ask("closing_report", {"period": "day", "date": "2026-10-22"}, token="mock:data")
    assert (result["status"], result["outcome"]) == ("completed", "reported")
    out = ClosingReportOutput.model_validate(result["output"])
    api = TestClient(app).get("/api/finance/closing", params={"date": "2026-10-22"}).json()["data"]
    assert [ln.model_dump() for ln in out.lines] == api["lines"]
    assert (out.window_from, out.window_to) == (api["from"], api["to"])
    assert out.commentary == api["commentary"] and out.commentary_source == "template"
    assert result["evidence"]["data_summary"] == out.commentary


def test_closing_report_defaults_and_confirmation() -> None:
    handlers.state().records.closings[("month", "2026-10-01")] = {
        "by": "태윤",
        "at": "2026-10-23T00:00:00Z",
    }
    out = _ask("closing_report", {"period": "month"})["output"]
    assert (out["date"], out["status"], out["confirmed_by"]) == ("2026-10-01", "confirmed", "태윤")
    bad = _ask("closing_report", {"period": "year"})
    assert (bad["status"], bad["outcome"]) == ("failed", "invalid_input")
