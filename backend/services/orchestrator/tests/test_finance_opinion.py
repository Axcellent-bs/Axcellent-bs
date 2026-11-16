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


def test_opinions_survive_a_restart(tmp_path, monkeypatch) -> None:
    """저장소를 주면 받은 의견이 파일에 남아 다시 켜도 그대로 올라온다."""
    from ax_orchestrator.store import IncidentStore

    monkeypatch.setenv("AGENT_TEMPLATE_MOCK", "1")
    path = str(tmp_path / "orch.db")
    first = IncidentManager(None, scenario_a, store=IncidentStore(path))  # type: ignore[arg-type]
    monkeypatch.setattr(state, "manager", first)
    http = TestClient(app)
    _send(http, OPINION)
    _send(http, {**OPINION, "opinion_id": "op_2", "kind": "insight", "campaign_id": None})
    assert first.store is not None
    first.store.close()

    again = IncidentManager(None, scenario_a, store=IncidentStore(path))  # type: ignore[arg-type]
    again.restore()
    ids = [r["opinion"]["opinion_id"] for r in again.finance_opinions.list()]
    assert ids == ["op_1", "op_2"]
    assert again.finance_opinions.list()[0]["opinion"]["profit_krw"] == 207776
    # 다시 켠 뒤 같은 의견이 또 와도 한 번만
    monkeypatch.setattr(state, "manager", again)
    assert _send(http, OPINION).outcome == "duplicate"
    set_agent_name("orchestrator")


def test_dropped_past_the_limit_also_leave_the_file(tmp_path, monkeypatch) -> None:
    from ax_orchestrator import finance_opinions
    from ax_orchestrator.store import IncidentStore

    monkeypatch.setattr(finance_opinions, "MAX_OPINIONS", 2)
    store = IncidentStore(str(tmp_path / "orch.db"))
    kept = finance_opinions.FinanceOpinions(store)
    from ax_contracts.schemas.finance import FinanceOpinion

    for n in range(3):
        kept.add(FinanceOpinion.model_validate({**OPINION, "opinion_id": f"op_{n}"}), "ctx")
    assert [r["opinion"]["opinion_id"] for r in store.load_opinions()] == ["op_1", "op_2"]


def test_screen_lists_received_opinions_newest_first(client) -> None:
    http, _ = client
    _send(http, OPINION)
    _send(http, {**OPINION, "opinion_id": "op_2"})
    res = http.get("/api/finance-opinions")
    assert res.status_code == 200
    rows = res.json()["data"]
    assert [r["opinion"]["opinion_id"] for r in rows] == ["op_2", "op_1"]
    assert rows[0]["received_at"] and rows[0]["context_id"] == "ctx_cmp_1"


def test_screen_is_empty_without_manager(client, monkeypatch) -> None:
    http, _ = client
    monkeypatch.setattr(state, "manager", None)
    assert http.get("/api/finance-opinions").json()["data"] == []
