"""기획 AI 직접 대화 (`/api/planning/chat`): 사람 토큰, 열람 제한, 읽기 전용 사실, LLM 없을 때."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from ax_workflow.auth import AuthError
from planning_agent.chat import facts, threads
from planning_agent.chat.api import create_chat_router
from planning_agent.chat.turn import ChatOut


class FakeLLM:
    def __init__(self) -> None:
        self.seen: list[dict] = []

    async def structured(self, system, schema, *, untrusted):
        self.seen.append(untrusted)
        return ChatOut(reply="받은 요청은 1건이에요.", summary="요청 현황 질문")


def _state():
    req = SimpleNamespace(id="REQ-1", title="결제 장애 원인", state="기획 중", from_="support",
                          spec="SPEC-1", duplicate_of=None)  # fmt: skip
    spec = SimpleNamespace(id="SPEC-1", title="결제 장애 원인", status="초안", request="REQ-1",
                           auto=False, question=None)  # fmt: skip
    prop = SimpleNamespace(id="PROP-1", title="무지 노트 숏츠", status="확인 대기", spec=None,
                           products=["무지 노트 3권 세트"], result=None)  # fmt: skip
    return SimpleNamespace(requests=[req], specs=[spec], proposals=[prop], decisions=[])


def _client(monkeypatch, llm) -> TestClient:
    monkeypatch.setenv("SAFETY_HUMAN_TOKENS", "yejin:tok-a,jaehun:tok-b")
    threads.set_store(threads.ChatStore())
    app = FastAPI()
    app.include_router(create_chat_router(_state, lambda: llm))

    @app.exception_handler(AuthError)
    async def _auth(_: Request, exc: AuthError) -> JSONResponse:
        body = {"error": {"code": exc.code, "message": exc.detail}}
        return JSONResponse(status_code=exc.status_code, content=body)

    return TestClient(app)


A = {"Authorization": "Bearer tok-a"}
B = {"Authorization": "Bearer tok-b"}


@pytest.mark.role("planning")
def test_chat_needs_human_token_and_answers_from_facts(monkeypatch) -> None:
    llm = FakeLLM()
    c = _client(monkeypatch, llm)
    assert c.get("/api/planning/chat").status_code == 401
    assert c.get("/api/planning/chat", headers={"Authorization": "Bearer nope"}).status_code == 401
    r = c.post("/api/planning/chat", json={"message": "요청 몇 건 있어?"}, headers=A)
    assert r.status_code == 200
    th = r.json()["data"]
    assert th["id"] == "planchat-1" and th["who"] == "yejin" and th["status"] == "talking"
    assert [m["role"] for m in th["messages"]] == ["user", "assistant"]
    assert th["summary"] == "요청 현황 질문" and th["task_id"] is None and not th["restricted"]
    got = llm.seen[0]["facts"]
    assert got["requests"]["total"] == 1 and got["proposals"]["recent"][0]["id"] == "PROP-1"
    again = c.post("/api/planning/chat", json={"thread_id": th["id"], "message": "더"}, headers=A)
    assert len(again.json()["data"]["messages"]) == 4


@pytest.mark.role("planning")
def test_other_people_see_summary_only_and_cannot_write(monkeypatch) -> None:
    c = _client(monkeypatch, FakeLLM())
    tid = c.post("/api/planning/chat", json={"message": "안녕"}, headers=A).json()["data"]["id"]
    seen = c.get(f"/api/planning/chat/{tid}", headers=B).json()["data"]
    assert seen["restricted"] and seen["messages"] == [] and seen["summary"]
    assert seen["message_count"] == 2
    listed = c.get("/api/planning/chat", headers=B).json()["data"]
    assert listed[0]["restricted"] and listed[0]["messages"] == []
    w = c.post("/api/planning/chat", json={"thread_id": tid, "message": "끼어들기"}, headers=B)
    assert w.status_code == 403
    assert c.get("/api/planning/chat/planchat-9", headers=A).status_code == 404


@pytest.mark.role("planning")
def test_chat_without_llm_is_refused_and_bad_input_is_400(monkeypatch) -> None:
    c = _client(monkeypatch, None)
    r = c.post("/api/planning/chat", json={"message": "안녕"}, headers=A)
    assert r.status_code == 409 and r.json()["error"]["code"] == "LLM_NOT_CONFIGURED"
    assert c.post("/api/planning/chat", json={"message": " "}, headers=A).status_code == 400
    assert c.post("/api/planning/chat", json=["x"], headers=A).status_code == 400


def test_facts_reads_only_and_counts() -> None:
    out = facts.collect(_state())
    assert out["specs"]["by_status"] == {"초안": 1}
    assert out["proposals"]["by_status"] == {"확인 대기": 1}
