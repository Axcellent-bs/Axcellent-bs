"""사람 할 일함: 에이전트가 넣고(에이전트 토큰), 사람이 끝내면(사람 토큰)
handoff.completed를 발행한다.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from ax_agent_template import config, events, routing
from ax_agent_template.client import call_agent
from ax_agent_template.testing import reset_state  # noqa: F401  (autouse 픽스처)
from ax_contracts.schemas import AgentRequest, EventType, RequestMeta
from ax_dashboard_api import handoffs
from ax_dashboard_api.main import app

BASE = "/api/dashboard/handoffs"
AGENT = {"authorization": "Bearer mock:inquiry"}


def _body(**extra) -> dict:
    return {
        "context_id": "ctx_req_h1",
        "from_service": "someone-else",
        "to_service": "finance",
        "skill": "closing_report",
        "input": {"seller_id": "sel_1"},
        "untrusted": {"memo": "고객이 적은 글"},
        "reason": "finance 서비스가 켜져 있지 않다",
        "assignee_unit": "재무/정산",
        **extra,
    }


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("AGENT_TEMPLATE_MOCK", "1")
    monkeypatch.delenv(handoffs.DB_ENV, raising=False)
    handoffs.reset_store()
    yield
    handoffs.reset_store()


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def _create(client: TestClient, **extra) -> dict:
    res = client.post(BASE, json=_body(**extra), headers=AGENT)
    assert res.status_code == 201, res.text
    return res.json()["data"]


def test_an_agent_puts_work_in_and_the_sender_comes_from_its_token(client) -> None:
    task = _create(client)
    assert task["task_id"].startswith("hof_")
    assert task["from_service"] == "inquiry"  # 본문의 값이 아니라 토큰의 이름
    assert (task["status"], task["result"], task["closed_at"]) == ("open", None, None)
    assert task["untrusted"] == {"memo": "고객이 적은 글"}


@pytest.mark.parametrize("token", ["test-human-token", "unknown"])
def test_only_agent_tokens_can_put_work_in(client, token) -> None:
    res = client.post(BASE, json=_body(), headers={"authorization": f"Bearer {token}"})
    assert res.status_code == 403
    assert res.json()["error"]["code"] == "AGENT_TOKEN_INVALID"


def test_putting_work_in_without_a_token_is_401(client) -> None:
    client.headers.pop("authorization")
    assert client.post(BASE, json=_body()).status_code == 401


def test_the_same_idempotency_key_returns_the_first_task(client) -> None:
    first = _create(client, idempotency_key="k1")
    again = _create(client, idempotency_key="k1")
    assert first["task_id"] == again["task_id"]
    assert len(client.get(BASE).json()["data"]) == 1


def test_the_inbox_filters_by_unit_and_status(client) -> None:
    a = _create(client)
    _create(client, assignee_unit="고객지원", to_service="claims", skill="decide_claim")
    client.post(f"{BASE}/{a['task_id']}/complete", json={"result": "보류 해제함"})

    finance = client.get(BASE, params={"unit": "재무/정산"}).json()
    assert [t["task_id"] for t in finance["data"]] == [a["task_id"]]
    still_open = client.get(BASE, params={"status": "open"}).json()["data"]
    assert [t["assignee_unit"] for t in still_open] == ["고객지원"]


def test_completing_needs_a_human_token(client) -> None:
    task = _create(client)
    client.headers.pop("authorization")
    res = client.post(f"{BASE}/{task['task_id']}/complete", json={"result": "끝"})
    assert res.status_code == 401
    res = client.post(f"{BASE}/{task['task_id']}/complete", json={"result": "끝"}, headers=AGENT)
    assert res.status_code == 403  # 에이전트 토큰으로는 끝낼 수 없다


def test_completing_publishes_handoff_completed(client) -> None:
    task = _create(client)
    res = client.post(f"{BASE}/{task['task_id']}/complete", json={"result": "보류 해제함"})
    assert res.status_code == 200
    done = res.json()["data"]
    assert (done["status"], done["result"]) == ("done", "보류 해제함")
    assert done["closed_at"] is not None

    [event] = [e for e in events.MOCK_PUBLISHED if e.type == EventType.HANDOFF_COMPLETED]
    assert event.context_id == "ctx_req_h1"
    assert event.data == {
        "task_id": task["task_id"],
        "status": "done",
        "from_service": "inquiry",
        "to_service": "finance",
        "skill": "closing_report",
    }
    assert event.untrusted == {"result": "보류 해제함"}  # 사람이 적은 글은 사실 필드가 아니다
    assert event.idempotency_key == f"handoff.completed:{task['task_id']}"


def test_a_human_can_reject_the_work(client) -> None:
    task = _create(client)
    res = client.post(
        f"{BASE}/{task['task_id']}/complete", json={"result": "담당 아님", "status": "rejected"}
    )
    assert res.json()["data"]["status"] == "rejected"
    assert events.MOCK_PUBLISHED[-1].data["status"] == "rejected"


def test_closed_or_unknown_tasks_cannot_be_completed(client) -> None:
    task = _create(client)
    url = f"{BASE}/{task['task_id']}/complete"
    assert client.post(url, json={"result": "끝"}).status_code == 200
    again = client.post(url, json={"result": "또"})
    assert (again.status_code, again.json()["error"]["code"]) == (409, "ALREADY_CLOSED")
    assert client.post(f"{BASE}/hof_none/complete", json={"result": "끝"}).status_code == 404
    assert client.post(url.replace(task["task_id"], "x"), json={"result": ""}).status_code == 422


def test_if_the_event_bus_is_down_the_task_stays_open(client, monkeypatch) -> None:
    task = _create(client)

    async def down(envelope):  # noqa: ANN001
        raise events.EventBusUnavailable("REDIS_URL 없음")

    monkeypatch.setattr(events, "publish", down)
    res = client.post(f"{BASE}/{task['task_id']}/complete", json={"result": "끝"})
    assert (res.status_code, res.json()["error"]["code"]) == (503, "EVENT_BUS_UNAVAILABLE")
    assert client.get(BASE, params={"status": "open"}).json()["meta"]["total"] == 1


def test_tasks_survive_in_the_sqlite_file(client, monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv(handoffs.DB_ENV, str(tmp_path / "handoffs.db"))
    task = _create(client)
    handoffs.reset_store()  # 다시 켠 것처럼
    assert [t["task_id"] for t in client.get(BASE).json()["data"]] == [task["task_id"]]


@pytest.mark.asyncio
async def test_end_to_end_a_disabled_service_reaches_a_human_and_comes_back(
    monkeypatch, tmp_path: Path
) -> None:
    org = tmp_path / "org.yaml"
    org.write_text("assignments:\n  finance: {unit: 재무/정산}\n", encoding="utf-8")
    monkeypatch.setenv(routing.ORG_ENV, str(org))
    monkeypatch.setenv(routing.ENABLED_ENV, "inquiry")
    config.set_agent_name("inquiry")
    req = AgentRequest(
        meta=RequestMeta(
            context_id="ctx_req_e2e",
            token="ignored",
            deadline=datetime.now(UTC) + timedelta(seconds=30),
        ),
        skill="closing_report",
        input={"seller_id": "sel_1"},
    )
    transport = httpx.ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://dashboard-api") as http:
        result = await call_agent("finance", req, http_client=http)
        assert (result.status, result.outcome) == ("input-required", "handed_to_human")
        task_id = result.output["task_id"]

        listed = (await http.get(BASE, params={"unit": "재무/정산"})).json()["data"]
        assert [t["task_id"] for t in listed] == [task_id]
        res = await http.post(
            f"{BASE}/{task_id}/complete",
            json={"result": "보류 해제함"},
            headers={"authorization": "Bearer test-human-token"},
        )
        assert res.status_code == 200

    [event] = [e for e in events.MOCK_PUBLISHED if e.type == EventType.HANDOFF_COMPLETED]
    assert (event.context_id, event.data["task_id"]) == ("ctx_req_e2e", task_id)


def test_concurrent_first_requests_open_one_store(monkeypatch: pytest.MonkeyPatch) -> None:
    """첫 요청 두 개가 동시에 와도 저장소는 하나다 (#231 3). 예전에는 메모리 저장소가 둘 생겨
    한쪽에 넣은 일이 다른 쪽 목록에 없었다."""
    import threading
    import time

    monkeypatch.setenv(handoffs.DB_ENV, "")
    handoffs.reset_store()
    made: list[object] = []
    original = handoffs.HandoffStore.__init__

    def slow_init(self, path: str) -> None:  # noqa: ANN001
        time.sleep(0.05)  # 만드는 사이에 다른 요청이 끼어들 틈을 넓힌다
        original(self, path)
        made.append(self)

    monkeypatch.setattr(handoffs.HandoffStore, "__init__", slow_init)
    got: list[object] = []
    start = threading.Barrier(8)

    def first_request() -> None:
        start.wait()
        got.append(handoffs.get_store())

    threads = [threading.Thread(target=first_request) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert len(made) == 1
    assert len({id(s) for s in got}) == 1
    handoffs.reset_store()
