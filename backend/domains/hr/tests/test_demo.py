"""데모 서버·시나리오 실행기: 모든 샘플 시나리오가 기대 결과와 같고 결정적인지 확인한다."""

import pytest
from fastapi.testclient import TestClient

from hr_agent.demo import runner
from hr_agent.demo.server import demo_app


@pytest.fixture
def client() -> TestClient:
    with TestClient(demo_app) as c:
        c.post("/api/reset")
        yield c


def test_every_scenario_has_title_steps_and_expectations() -> None:
    scenarios = runner.load_scenarios()
    assert len(scenarios) >= 4
    for name, scenario in scenarios.items():
        assert scenario["title"] and scenario["steps"], name
        for step in scenario["steps"]:
            if step.get("action") in ("advance", "decide_hold"):
                continue
            assert step["skill"] and step["requester"], name
            assert step.get("expect"), f"{name}: 기대 결과가 없다"


def test_demo_page_and_scenario_list(client: TestClient) -> None:
    assert "인사 에이전트 데모" in client.get("/").text
    names = [s["name"] for s in client.get("/api/scenarios").json()]
    assert names == sorted(names)
    assert "13_handover_full" in names


def test_run_all_matches_every_expectation_and_is_deterministic(client: TestClient) -> None:
    body = client.post("/api/run-all").json()
    assert body["passed"] == body["total"], [
        (r["name"], r["passed"], r["deterministic"], [s["problems"] for s in r["steps"]])
        for r in body["runs"]
        if not (r["passed"] and r["deterministic"])
    ]


def test_unknown_scenario_is_404(client: TestClient) -> None:
    assert client.post("/api/run", json={"name": "nope"}).status_code == 404


def test_clock_can_be_advanced(client: TestClient) -> None:
    before = client.get("/api/state").json()["clock"]
    after = client.post("/api/clock/advance", json={"minutes": 10}).json()["clock"]
    assert before < after


def test_timer_endpoint_starts_handovers_for_due_shifts(client: TestClient) -> None:
    body = client.post("/api/timer/run", json={"horizon_minutes": 60}).json()
    assert body["started"] and all(s["handover_id"] for s in body["started"])
    assert len(body["state"]["handovers"]) == len(body["started"])
    again = client.post("/api/timer/run", json={"horizon_minutes": 60}).json()
    assert len(again["state"]["handovers"]) == len(body["state"]["handovers"])
