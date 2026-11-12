"""대화 지우기 (POST 전용 새 경로).

- `POST /api/dashboard/<부서>-chat/{thread_id}/remove` → 그 부서 `<부서>_chat_remove` (본인 대화만)
- `POST /api/dashboard/office-room/{room_id}/remove` → 내가 만든 단톡방 대화를 office_room.db와
  부서 대화 서버에서 지운다. 만든 사람 아님 403, 없는 방 404, 한 부서 실패해도 부서별 결과
"""

from __future__ import annotations

from types import ModuleType
from typing import Any

import pytest
from fastapi.testclient import TestClient

from ax_agent_template.testing import reset_state  # noqa: F401  (autouse 픽스처)
from ax_contracts.schemas import AgentRequest, AgentResult, TaskStatus
from ax_dashboard_api import (
    chat_remove,
    data_chat,
    dev_chat,
    finance_chat,
    infra_chat,
    office_room,
    orchestrator_chat,
    planning_chat,
)
from ax_dashboard_api.envelope import ApiError
from ax_dashboard_api.main import app


class Fake:
    def __init__(self) -> None:
        self.calls: list[tuple[str, AgentRequest]] = []
        self.outcome: str | None = None
        self.status = TaskStatus.COMPLETED

    async def __call__(self, target: str, request: AgentRequest) -> AgentResult:
        self.calls.append((target, request))
        out = {"thread_id": request.input["thread_id"], "removed": True}
        return AgentResult(meta=request.meta, status=self.status, outcome=self.outcome, output=out)


@pytest.fixture(autouse=True)
def _real_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("DASHBOARD_MOCK", raising=False)
    monkeypatch.delenv("AGENT_TEMPLATE_MOCK", raising=False)
    monkeypatch.setenv("DASHBOARD_API_TOKEN", "test-token")
    monkeypatch.delenv(office_room.DB_ENV, raising=False)
    office_room.reset_store()


# ---------- 부서 1:1 대화 지우기 ----------

A2A = [
    ("data", data_chat),
    ("dev", dev_chat),
    ("finance", finance_chat),
    ("infra", infra_chat),
    ("orchestrator", orchestrator_chat),
    ("planning", planning_chat),
]


@pytest.mark.parametrize(("agent", "module"), A2A)
def test_remove_goes_to_that_department_with_the_token_owner(
    monkeypatch: pytest.MonkeyPatch, agent: str, module: ModuleType
) -> None:
    fake = Fake()
    monkeypatch.setattr(module, "SEND", fake)
    res = TestClient(app).post(f"/api/dashboard/{agent}-chat/t1/remove", json={"who": "someone"})
    assert res.status_code == 200
    assert res.json()["data"] == {"thread_id": "t1", "removed": True}
    target, request = fake.calls[0]
    # 받는 서비스는 그 중계 모듈의 대상 (인프라 AI는 incident 서비스)
    assert (target, request.skill) == (module.TARGET, f"{agent}_chat_remove")
    assert request.input == {"who": "tester", "thread_id": "t1"}  # 몸통의 who는 쓰지 않는다


@pytest.mark.parametrize(("outcome", "status"), [("forbidden", 403), ("not_found", 404)])
def test_department_refusal_becomes_http_status(
    monkeypatch: pytest.MonkeyPatch, outcome: str, status: int
) -> None:
    fake = Fake()
    fake.status, fake.outcome = TaskStatus.FAILED, outcome
    monkeypatch.setattr(data_chat, "SEND", fake)
    assert TestClient(app).post("/api/dashboard/data-chat/t1/remove").status_code == status


def test_remove_is_post_only_and_needs_a_human_token(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(data_chat, "SEND", Fake())
    client = TestClient(app)
    assert client.get("/api/dashboard/data-chat/t1/remove").status_code in (404, 405)
    assert client.delete("/api/dashboard/data-chat/t1").status_code == 405
    client.headers.pop("authorization", None)
    assert client.post("/api/dashboard/data-chat/t1/remove").status_code == 401


# ---------- 단톡방 지우기 ----------

ROOM = "all"
POST = "/api/dashboard/office-room"


def _talk(client: TestClient, agent: str, thread: str, from_id: str, room: str = ROOM) -> None:
    body = {"agent": agent, "thread_id": thread, "from_id": from_id, "from_name": from_id}
    assert client.post(POST, json=body | {"room_id": room}).status_code == 201


@pytest.fixture
def removed(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str]]:
    """부서 대화 지우기를 가로챈다. finance는 실패(503), hr는 이미 없음(404)."""
    calls: list[tuple[str, str]] = []

    async def fake_remove(agent: str, thread_id: str, request: Any) -> dict[str, Any]:
        calls.append((agent, thread_id))
        if agent == "finance":
            raise ApiError(503, "UPSTREAM_FAILED", "재무 AI가 응답하지 않았다")
        if agent == "hr":
            raise ApiError(404, "NOT_FOUND", "없는 대화")
        return {"thread_id": thread_id, "removed": True}

    monkeypatch.setattr(chat_remove, "remove", fake_remove)
    return calls


def test_room_remove_deletes_only_my_threads(removed: list[tuple[str, str]]) -> None:
    client = TestClient(app)
    _talk(client, "dev", "c1", "acc_me")
    _talk(client, "data", "d1", "acc_me")
    _talk(client, "dev", "c2", "acc_other")
    _talk(client, "dev", "c9", "acc_me", room="ctx_inc_1")  # 다른 방은 그대로
    res = client.post(f"{POST}/{ROOM}/remove", json={"from_id": "acc_me"})
    assert res.status_code == 200
    data = res.json()["data"]
    assert (data["removed"], data["kept"]) == (2, 1)
    assert sorted(removed) == [("data", "d1"), ("dev", "c1")]
    assert all(d["ok"] for d in data["departments"])
    left = {(r["thread_id"], r["room_id"]) for r in client.get(POST).json()["data"]}
    assert left == {("c2", ROOM), ("c9", "ctx_inc_1")}  # 다른 PC도 목록에서 사라진 것을 본다


def test_room_remove_by_someone_who_made_nothing_is_403(removed: list[tuple[str, str]]) -> None:
    client = TestClient(app)
    _talk(client, "dev", "c2", "acc_other")
    res = client.post(f"{POST}/{ROOM}/remove", json={"from_id": "acc_me"})
    assert res.status_code == 403
    assert res.json()["error"]["message"] == "만든 사람만 삭제할 수 있어요"
    assert removed == []
    assert len(client.get(POST).json()["data"]) == 1


def test_room_remove_of_unknown_room_is_404(removed: list[tuple[str, str]]) -> None:
    res = TestClient(app).post(f"{POST}/ctx_inc_none/remove", json={"from_id": "acc_me"})
    assert res.status_code == 404
    assert removed == []


def test_one_department_failing_still_removes_the_rest(removed: list[tuple[str, str]]) -> None:
    client = TestClient(app)
    _talk(client, "finance", "f1", "acc_me")
    _talk(client, "dev", "c1", "acc_me")
    _talk(client, "hr", "h1", "acc_me")
    res = client.post(f"{POST}/{ROOM}/remove", json={"from_id": "acc_me"})
    assert res.status_code == 200
    by_agent = {d["agent"]: d for d in res.json()["data"]["departments"]}
    assert by_agent["finance"] == {
        "agent": "finance", "thread_id": "f1", "ok": False, "status": 503,
        "reason": "재무 AI가 응답하지 않았다",
    }  # fmt: skip
    assert by_agent["dev"]["ok"] is True
    assert by_agent["hr"] == {"agent": "hr", "thread_id": "h1", "ok": True, "note": "이미 없음"}
    assert client.get(POST).json()["data"] == []  # 목록에서는 모두 빠진다


def test_room_remove_needs_a_human_token_and_a_from_id(removed: list[tuple[str, str]]) -> None:
    client = TestClient(app)
    _talk(client, "dev", "c1", "acc_me")
    assert client.post(f"{POST}/{ROOM}/remove", json={}).status_code in (400, 422)
    client.headers.pop("authorization", None)
    assert client.post(f"{POST}/{ROOM}/remove", json={"from_id": "acc_me"}).status_code == 401
    assert client.get(f"{POST}/{ROOM}/remove").status_code in (404, 405)  # POST 전용
