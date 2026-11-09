"""기획 AI 직접 대화: 저장 후 answering → 뒤에서 답, 끝내기·30분 자동 끝내기, 열람 제한,
읽기 전용 사실, LLM 없을 때, A2A skill(`planning_chat_*`)과 기획 서버 REST 경로."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.testclient import TestClient

from ax_contracts.schemas import AgentRequest, RequestMeta, TaskStatus
from ax_workflow.auth import AuthError
from planning_agent.chat import a2a, facts, threads
from planning_agent.chat.api import create_chat_router
from planning_agent.chat.turn import ChatOut, ChatRefused, ChatService, set_chat_service


class FakeLLM:
    def __init__(self, error: Exception | None = None) -> None:
        self.seen: list[dict] = []
        self.error = error

    async def structured(self, system, schema, *, untrusted):
        self.seen.append(untrusted)
        if self.error:
            raise self.error
        return ChatOut(reply="받은 요청은 1건이에요.", summary="요청 현황 질문")


def _state():
    req = SimpleNamespace(id="REQ-1", title="결제 장애 원인", state="기획 중", from_="support",
                          spec="SPEC-1", duplicate_of=None)  # fmt: skip
    spec = SimpleNamespace(id="SPEC-1", title="결제 장애 원인", status="초안", request="REQ-1",
                           auto=False, question=None)  # fmt: skip
    prop = SimpleNamespace(id="PROP-1", title="무지 노트 숏츠", status="확인 대기", spec=None,
                           products=["무지 노트 3권 세트"], result=None)  # fmt: skip
    return SimpleNamespace(requests=[req], specs=[spec], proposals=[prop], decisions=[])


class Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 11, 5, 6, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now


def _service(llm, clock: Clock | None = None) -> ChatService:
    store = threads.ChatStore(clock=clock or Clock())
    svc = ChatService(lambda: store, _state, lambda: llm)
    set_chat_service(svc)
    return svc


def _req(skill: str, data: dict) -> AgentRequest:
    deadline = datetime.now(UTC) + timedelta(seconds=30)
    meta = RequestMeta(context_id="ctx-t", token="t", deadline=deadline, idempotency_key="k-1")
    return AgentRequest(meta=meta, skill=skill, input=data)


def test_send_returns_answering_then_answer_arrives() -> None:
    async def run() -> None:
        llm = FakeLLM()
        svc = _service(llm)
        th = svc.send("yejin", None, "요청 몇 건 있어?")
        assert th["id"] == "planchat-1" and th["status"] == "answering"
        assert [m["role"] for m in th["messages"]] == ["user"]
        await svc.wait_idle()
        done = svc.get("yejin", th["id"])
        assert done["status"] == "talking" and done["summary"] == "요청 현황 질문"
        assert [m["role"] for m in done["messages"]] == ["user", "assistant"]
        got = llm.seen[0]["facts"]
        assert got["requests"]["total"] == 1 and got["proposals"]["recent"][0]["id"] == "PROP-1"

    asyncio.run(run())


def test_busy_closed_and_other_people() -> None:
    async def run() -> None:
        svc = _service(FakeLLM())
        tid = svc.send("yejin", None, "안녕")["id"]
        with pytest.raises(ChatRefused) as busy:
            svc.send("yejin", tid, "또")
        assert busy.value.outcome == "thread_busy"
        await svc.wait_idle()
        seen = svc.get("jaehun", tid)
        assert seen["restricted"] and seen["messages"] == [] and seen["message_count"] == 2
        with pytest.raises(ChatRefused) as other:
            svc.send("jaehun", tid, "끼어들기")
        assert other.value.status == 403
        assert svc.close("yejin", tid)["status"] == "closed"
        with pytest.raises(ChatRefused) as closed:
            svc.send("yejin", tid, "다시")
        assert closed.value.outcome == "thread_closed"

    asyncio.run(run())


def test_llm_failure_says_why_and_idle_threads_close() -> None:
    async def run() -> None:
        clock = Clock()
        svc = _service(FakeLLM(RuntimeError("LLM API가 429를 돌려줬다")), clock)
        tid = svc.send("yejin", None, "안녕")["id"]
        await svc.wait_idle()
        last = svc.get("yejin", tid)["messages"][-1]
        assert last["kind"] == "error" and "429" in last["text"]
        assert svc.get("yejin", tid)["status"] == "talking"
        clock.now += timedelta(minutes=31)
        th = svc.get("yejin", tid)
        assert th["status"] == "closed" and th["summary"] == "안녕"  # 요약이 없으면 첫 질문

    asyncio.run(run())


def test_orphan_answering_is_released_on_start() -> None:
    store = threads.ChatStore()
    tid = store.create("t", "yejin")["id"]
    store.begin_turn(tid, "안녕")
    assert store.recover_orphans() == [tid]
    th = store.get(tid)
    assert th["status"] == "talking" and th["messages"][-1]["kind"] == "error"


def test_without_llm_is_refused() -> None:
    svc = _service(None)
    with pytest.raises(ChatRefused) as exc:
        svc.send("yejin", None, "안녕")
    assert exc.value.status == 409 and exc.value.code == "LLM_NOT_CONFIGURED"


def test_a2a_skills_use_the_same_service() -> None:
    async def run() -> None:
        svc = _service(FakeLLM())
        send = {"who": "yejin", "message": "안녕"}
        sent = await a2a.planning_chat_send(_req("planning_chat_send", send))
        assert sent.status == TaskStatus.COMPLETED
        assert sent.output["thread"]["status"] == "answering"
        await svc.wait_idle()
        tid = sent.output["thread"]["id"]
        listed = await a2a.planning_chat_list(_req("planning_chat_list", {"who": "jaehun"}))
        assert listed.output["threads"][0]["restricted"]
        ref = {"who": "yejin", "thread_id": "x"}
        missing = await a2a.planning_chat_get(_req("planning_chat_get", ref))
        assert missing.status == TaskStatus.FAILED and missing.outcome == "not_found"
        ref = {"who": "yejin", "thread_id": tid}
        closed = await a2a.planning_chat_close(_req("planning_chat_close", ref))
        assert closed.output["thread"]["status"] == "closed"
        empty = {"who": "yejin", "message": ""}
        bad = await a2a.planning_chat_send(_req("planning_chat_send", empty))
        assert bad.status != TaskStatus.COMPLETED

    asyncio.run(run())


def _client(monkeypatch, llm) -> TestClient:
    monkeypatch.setenv("SAFETY_HUMAN_TOKENS", "yejin:tok-a,jaehun:tok-b")
    svc = _service(llm)
    app = FastAPI()
    app.include_router(create_chat_router(lambda: svc))

    @app.exception_handler(AuthError)
    async def _auth(_: Request, exc: AuthError) -> JSONResponse:
        body = {"error": {"code": exc.code, "message": exc.detail}}
        return JSONResponse(status_code=exc.status_code, content=body)

    return TestClient(app)


A = {"Authorization": "Bearer tok-a"}
B = {"Authorization": "Bearer tok-b"}


@pytest.mark.role("planning")
def test_rest_needs_human_token_and_keeps_rules(monkeypatch) -> None:
    c = _client(monkeypatch, FakeLLM())
    assert c.get("/api/planning/chat").status_code == 401
    r = c.post("/api/planning/chat", json={"message": "요청 몇 건 있어?"}, headers=A)
    assert r.status_code == 200
    th = r.json()["data"]
    assert th["who"] == "yejin" and th["status"] in ("answering", "talking")
    w = c.post("/api/planning/chat", json={"thread_id": th["id"], "message": "x"}, headers=B)
    assert w.status_code == 403
    assert c.post(f"/api/planning/chat/{th['id']}/close", headers=B).status_code == 403
    assert c.get("/api/planning/chat/planchat-9", headers=A).status_code == 404
    assert c.post("/api/planning/chat", json={"message": " "}, headers=A).status_code == 400
    assert c.post("/api/planning/chat", json=["x"], headers=A).status_code == 400


@pytest.mark.role("planning")
def test_rest_without_llm_is_409(monkeypatch) -> None:
    c = _client(monkeypatch, None)
    r = c.post("/api/planning/chat", json={"message": "안녕"}, headers=A)
    assert r.status_code == 409 and r.json()["error"]["code"] == "LLM_NOT_CONFIGURED"


def test_facts_reads_only_and_counts() -> None:
    out = facts.collect(_state())
    assert out["specs"]["by_status"] == {"초안": 1}
    assert out["proposals"]["by_status"] == {"확인 대기": 1}
