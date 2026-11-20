"""아카이브 AI 직접 대화 (chat/): 사람 토큰, 대화 저장, 열람 제한, 숨기기, 기록 사실."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from fastapi.testclient import TestClient

from archive_agent.chat import facts
from archive_agent.chat.turn import ChatOut
from archive_agent.main import app
from archive_agent.services import Services

A = {"Authorization": "Bearer human:gaon"}
B = {"Authorization": "Bearer human:narae"}


class FakeLLM:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def structured(
        self, prompt: str, schema: type, untrusted: dict | None = None, **_: Any
    ) -> ChatOut:
        self.calls.append({"prompt": prompt, "untrusted": untrusted})
        return ChatOut(reply="최근 기록이 없어요.", summary="기록을 물어봄")


@pytest.fixture
def client(svc: Services) -> TestClient:
    svc.llm = FakeLLM()
    return TestClient(app)


def post(c: TestClient, message: str, headers: dict = A, thread_id: str | None = None):
    body: dict[str, Any] = {"message": message}
    if thread_id:
        body["thread_id"] = thread_id
    return c.post("/api/archive/chat", json=body, headers=headers)


def test_needs_a_human_token(client: TestClient) -> None:
    assert client.post("/api/archive/chat", json={"message": "안녕"}).status_code == 401
    bad = {"Authorization": "Bearer mock:planning"}
    assert client.get("/api/archive/chat", headers=bad).status_code == 403


def test_reply_is_saved_and_facts_reach_llm_as_data(client: TestClient, svc: Services) -> None:
    res = post(client, "최근 장애 알려줘")
    assert res.status_code == 200
    th = res.json()["data"]
    assert [m["role"] for m in th["messages"]] == ["user", "assistant"]
    assert th["task_id"] is None and th["summary"] == "기록을 물어봄"
    call = svc.llm.calls[0]
    assert "confirmed_by_kind" in call["untrusted"]["facts"]
    again = post(client, "더 알려줘", thread_id=th["id"]).json()["data"]
    assert len(again["messages"]) == 4


def test_other_people_see_summary_only_and_cannot_write(client: TestClient) -> None:
    th = post(client, "기록 알려줘", A).json()["data"]
    seen = client.get(f"/api/archive/chat/{th['id']}", headers=B).json()["data"]
    assert seen["restricted"] is True and seen["messages"] == []
    assert post(client, "끼어들기", B, th["id"]).status_code == 403


def test_remove_hides_only_own_thread(client: TestClient) -> None:
    th = post(client, "지울 대화", A).json()["data"]
    assert client.post(f"/api/archive/chat/{th['id']}/remove", headers=B).status_code == 403
    assert client.post(f"/api/archive/chat/{th['id']}/remove", headers=A).status_code == 200
    assert client.get(f"/api/archive/chat/{th['id']}", headers=A).status_code == 404


def test_no_llm_is_refused(svc: Services) -> None:
    svc.llm = None
    res = post(TestClient(app), "안녕")
    assert res.status_code == 409 and res.json()["error"]["code"] == "LLM_NOT_CONFIGURED"


def test_facts_count_confirmed_and_candidates_apart(svc: Services) -> None:
    out = asyncio.run(facts.collect(svc))
    assert "confirmed_by_kind" in out and isinstance(out["waiting_candidates"], int)


# ---- 단톡방(room_id)에서 만든 대화는 누구나 원문 (11/20) ----

URL = "/api/archive/chat"


def test_room_thread_is_readable_by_others_but_writes_stay_owner_only(client: TestClient) -> None:
    th = client.post(URL, json={"message": "결정 기록?", "room_id": "ctx_inc_1"},
                     headers=A).json()["data"]  # fmt: skip
    assert th["room_id"] == "ctx_inc_1" and th["shared"] is True and th["owner"] == "gaon"
    other = client.get(f"{URL}/{th['id']}", headers=B).json()["data"]
    assert other["restricted"] is False and len(other["messages"]) == 2
    assert other["shared"] is True and other["owner"] == "gaon"
    assert post(client, "끼어들기", headers=B, thread_id=th["id"]).status_code == 403
    assert client.post(f"{URL}/{th['id']}/remove", headers=B).status_code == 403
    assert client.post(f"{URL}/{th['id']}/remove", headers=A).status_code == 200
    assert client.get(f"{URL}/{th['id']}", headers=B).status_code == 404  # 숨긴 대화는 공유 안 함


def test_one_to_one_thread_is_still_hidden_and_room_id_on_reply_is_ignored(
    client: TestClient,
) -> None:
    th = post(client, "1:1 질문").json()["data"]
    assert th["room_id"] is None and th["shared"] is False and th["owner"] is None
    again = client.post(URL, json={"message": "더", "thread_id": th["id"], "room_id": "all"},
                        headers=A).json()["data"]  # fmt: skip
    assert again["room_id"] is None
    other = client.get(f"{URL}/{th['id']}", headers=B).json()["data"]
    assert other["restricted"] is True and other["messages"] == [] and other["shared"] is False


def test_room_id_must_be_1_to_64_chars(client: TestClient) -> None:
    for bad in ("", "x" * 65, 3):
        assert client.post(URL, json={"message": "안녕", "room_id": bad},
                           headers=A).status_code == 400  # fmt: skip
