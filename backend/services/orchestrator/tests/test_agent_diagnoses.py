"""인사 → 총괄 `diagnose_agent_issue`: 에이전트 평가 진단 요청을 받아 저장한다(접수까지)."""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from ax_agent_template.config import set_agent_name
from ax_contracts.schemas import AgentRequest, AgentResult, RequestMeta, TaskStatus
from ax_orchestrator import agent_diagnoses
from ax_orchestrator.main import app, state
from ax_orchestrator.manager import IncidentManager
from ax_orchestrator.plans.scenario_a import scenario_a

NOW = "2026-11-16T03:00:00Z"


def _evidence(*names: str) -> list[dict]:
    return [
        {
            "context_id": f"ctx_inc_{name}",
            "title": f"이슈 {name}",
            "verdict": "check",
            "work_seconds": 41.0,
            "attempts": 2,
            "limit_seconds": 30.0,
            "reason": "다시 시도 1회",
            "at": NOW,
        }
        for name in names
    ]


def _request(agent: str = "infra", step: str = "rollback", names=("a", "b")) -> dict:
    return {
        "agent": agent,
        "step_id": step,
        "step_label": "문제 배포 되돌리기",
        "window": 3,
        "flagged": len(names),
        "criteria_version": 3,
        "evidence": _evidence(*names),
        "requested_at": NOW,
    }


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("AGENT_TEMPLATE_MOCK", "1")
    manager = IncidentManager(None, scenario_a)  # type: ignore[arg-type]
    monkeypatch.setattr(state, "manager", manager)
    yield TestClient(app), manager
    set_agent_name("orchestrator")


def _send(http: TestClient, payload: dict, requester: str = "hr") -> AgentResult:
    meta = RequestMeta(
        context_id="ctx_inc_a",
        token=f"mock:{requester}",
        path=[requester],
        deadline=datetime.now(UTC) + timedelta(seconds=30),
    )
    body = AgentRequest(meta=meta, skill="diagnose_agent_issue", input=payload)
    return AgentResult.model_validate(http.post("/a2a", json=body.model_dump(mode="json")).json())


def test_request_is_received_and_kept(client) -> None:
    http, manager = client
    res = _send(http, _request())
    assert res.status == TaskStatus.COMPLETED and res.outcome == "received"
    assert res.output["request_id"] == "diag-1" and res.output["duplicate"] is False
    row = manager.agent_diagnoses.list()[0]
    assert (row["agent"], row["step_id"], row["status"]) == ("infra", "rollback", "received")
    assert [e["context_id"] for e in row["evidence"]] == ["ctx_inc_a", "ctx_inc_b"]


def test_same_evidence_is_a_duplicate(client) -> None:
    http, manager = client
    _send(http, _request())
    res = _send(http, _request())
    assert res.outcome == "duplicate" and res.output["duplicate"] is True
    assert len(manager.agent_diagnoses.list()) == 1


def test_new_evidence_is_added_to_the_open_request(client) -> None:
    http, manager = client
    _send(http, _request(names=("a", "b")))
    res = _send(http, _request(names=("c", "b")))
    assert res.outcome == "updated" and res.output["request_id"] == "diag-1"
    row = manager.agent_diagnoses.list()[0]
    assert [e["context_id"] for e in row["evidence"]] == ["ctx_inc_c", "ctx_inc_a", "ctx_inc_b"]
    assert row["flagged"] == 3


def test_second_new_request_for_the_same_agent_on_a_day_is_limited(client) -> None:
    http, manager = client
    assert _send(http, _request(step="rollback")).outcome == "received"
    res = _send(http, _request(step="diagnose"))
    assert res.status == TaskStatus.COMPLETED and res.outcome == "limited"
    assert res.output["status"] == "limited" and res.output["request_id"] is None
    assert len(manager.agent_diagnoses.list()) == 1
    # 다른 에이전트는 따로 센다
    assert _send(http, _request(agent="data", step="impact")).outcome == "received"


def test_closed_request_needs_new_evidence_before_asking_again(client) -> None:
    http, manager = client
    _send(http, _request(names=("a", "b")))
    manager.agent_diagnoses.list()[0]["status"] = "diagnosed"
    # 같은 이슈로는 다시 받지 않는다
    assert _send(http, _request(names=("a", "b"))).outcome == "duplicate"
    # 하나만 새로 쌓여도 아직 부족하다
    assert _send(http, _request(names=("c", "a"))).outcome == "duplicate"


def test_closed_request_is_reopened_with_enough_new_evidence_the_next_day(client) -> None:
    from ax_contracts.schemas.diagnosis import DiagnoseAgentIssueInput

    _http, manager = client
    first = DiagnoseAgentIssueInput.model_validate(_request(names=("a", "b")))
    day1 = datetime(2026, 11, 16, 3, tzinfo=UTC)
    manager.agent_diagnoses.receive(first, now=day1)
    manager.agent_diagnoses.list()[0]["status"] = "diagnosed"
    again = DiagnoseAgentIssueInput.model_validate(_request(names=("c", "d")))
    out = manager.agent_diagnoses.receive(again, now=day1 + timedelta(days=1))
    assert out.status == "received" and out.request_id == "diag-2"
    assert [e["context_id"] for e in manager.agent_diagnoses.list()[1]["evidence"]] == [
        "ctx_inc_c",
        "ctx_inc_d",
    ]


def test_invalid_input_fails(client) -> None:
    http, _manager = client
    res = _send(http, {"agent": "infra"})
    assert res.status == TaskStatus.FAILED and res.outcome == "invalid_input"


def test_list_endpoint_returns_newest_first(client) -> None:
    http, _manager = client
    _send(http, _request(agent="infra"))
    _send(http, _request(agent="data", step="impact"))
    body = http.get("/api/agent-diagnoses").json()
    assert [r["agent"] for r in body["data"]] == ["data", "infra"]


def test_requests_survive_a_restart(tmp_path, monkeypatch) -> None:
    from ax_orchestrator.store import IncidentStore

    monkeypatch.setenv("AGENT_TEMPLATE_MOCK", "1")
    path = str(tmp_path / "orch.db")
    first = IncidentManager(None, scenario_a, store=IncidentStore(path))  # type: ignore[arg-type]
    monkeypatch.setattr(state, "manager", first)
    http = TestClient(app)
    _send(http, _request())
    assert first.store is not None
    first.store.close()

    again = IncidentManager(None, scenario_a, store=IncidentStore(path))  # type: ignore[arg-type]
    again.restore()
    assert [r["request_id"] for r in again.agent_diagnoses.list()] == ["diag-1"]
    monkeypatch.setattr(state, "manager", again)
    assert _send(http, _request()).outcome == "duplicate"  # 다시 켠 뒤에도 한 번만
    set_agent_name("orchestrator")


def test_dropped_past_the_limit_also_leave_the_file(tmp_path, monkeypatch) -> None:
    from ax_contracts.schemas.diagnosis import DiagnoseAgentIssueInput
    from ax_orchestrator.store import IncidentStore

    monkeypatch.setattr(agent_diagnoses, "MAX_REQUESTS", 2)
    store = IncidentStore(str(tmp_path / "orch.db"))
    kept = agent_diagnoses.AgentDiagnoses(store, daily_limit=10)
    for agent in ("infra", "data", "dev"):
        kept.receive(DiagnoseAgentIssueInput.model_validate(_request(agent=agent)))
    assert [r["agent"] for r in kept.list()] == ["data", "dev"]
    assert [r["agent"] for r in store.load_diagnoses()] == ["data", "dev"]


def test_only_hr_may_send_in_the_card() -> None:
    from ax_orchestrator.a2a import load_card

    skill = next(s for s in load_card().skills if s.id == "diagnose_agent_issue")
    assert skill.allowed_requesters == ["hr"] and skill.grade == "A"
