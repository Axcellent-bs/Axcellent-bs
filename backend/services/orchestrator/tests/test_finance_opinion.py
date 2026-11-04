"""재무 → 총괄 `receive_finance_opinion`: 받은 의견을 한 번만 보관한다."""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from ax_agent_template.config import set_agent_name
from ax_contracts.schemas import AgentRequest, AgentResult, RequestMeta, TaskStatus
from ax_orchestrator.main import app, state
from ax_orchestrator.manager import IncidentManager
from ax_orchestrator.plans.scenario_a import scenario_a

OPINION = {
    "opinion_id": "op_1",
    "kind": "campaign_result",
    "campaign_id": "cmp_1",
    "profit_krw": 207776,
    "recommendation": "재구매를 넣어 다시 본다",
    "submitted_at": "2026-11-04T01:00:00Z",
}


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setenv("AGENT_TEMPLATE_MOCK", "1")
    manager = IncidentManager(None, scenario_a)  # type: ignore[arg-type]
    monkeypatch.setattr(state, "manager", manager)
    yield TestClient(app), manager
    set_agent_name("orchestrator")


def _send(http: TestClient, opinion: dict, requester: str = "finance") -> AgentResult:
    meta = RequestMeta(
        context_id="ctx_cmp_1",
        token=f"mock:{requester}",
        path=[requester],
        deadline=datetime.now(UTC) + timedelta(seconds=30),
    )
    body = AgentRequest(meta=meta, skill="receive_finance_opinion", input=opinion)
    return AgentResult.model_validate(http.post("/a2a", json=body.model_dump(mode="json")).json())


def test_opinion_is_kept(client) -> None:
    http, manager = client
    res = _send(http, OPINION)
    assert res.status == TaskStatus.COMPLETED and res.outcome == "received"
    assert res.output == {"opinion_id": "op_1", "duplicate": False}
    rows = manager.finance_opinions.list()
    assert len(rows) == 1 and rows[0]["context_id"] == "ctx_cmp_1"
    assert rows[0]["opinion"]["profit_krw"] == 207776


def test_resending_the_same_opinion_keeps_one(client) -> None:
    http, manager = client
    _send(http, OPINION)
    res = _send(http, OPINION)
    assert res.status == TaskStatus.COMPLETED and res.outcome == "duplicate"
    assert res.output["duplicate"] is True
    assert len(manager.finance_opinions.list()) == 1


def test_bad_opinion_is_rejected_and_not_kept(client) -> None:
    http, manager = client
    res = _send(http, {**OPINION, "kind": "gossip"})
    assert res.status == TaskStatus.FAILED and res.outcome == "invalid_input"
    assert manager.finance_opinions.list() == []


def test_not_ready_without_manager_so_finance_retries(client, monkeypatch) -> None:
    http, _ = client
    monkeypatch.setattr(state, "manager", None)
    res = _send(http, OPINION)
    assert res.status == TaskStatus.FAILED and res.outcome == "not_ready"


def test_only_finance_may_send(client) -> None:
    http, manager = client
    res = _send(http, OPINION, requester="support")
    assert res.status != TaskStatus.COMPLETED
    assert manager.finance_opinions.list() == []


def test_old_opinions_are_dropped_past_the_limit(client, monkeypatch) -> None:
    from ax_orchestrator import finance_opinions

    monkeypatch.setattr(finance_opinions, "MAX_OPINIONS", 2)
    http, manager = client
    for n in range(3):
        _send(http, {**OPINION, "opinion_id": f"op_{n}"})
    ids = [r["opinion"]["opinion_id"] for r in manager.finance_opinions.list()]
    assert ids == ["op_1", "op_2"]
