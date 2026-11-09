"""인프라 AI 직접 대화: 저장 후 answering → 뒤에서 답, 끝내기·30분 자동 끝내기, 열람 제한,
읽기 전용 인프라 사실(배포·쇼핑몰 상태·진단·총괄 사건), LLM 없을 때, A2A skill(`infra_chat_*`)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from ax_contracts.schemas import AgentRequest, RequestMeta, TaskStatus
from infra_agent.chat import a2a, facts, threads
from infra_agent.chat.turn import ChatOut, ChatRefused, ChatService, set_chat_service
from infra_agent.main import app, load_card


@pytest.fixture(autouse=True)
def _reset_service():
    yield
    set_chat_service(None)


class FakeLLM:
    def __init__(self, error: Exception | None = None) -> None:
        self.seen: list[dict] = []
        self.prompts: list[str] = []
        self.error = error

    async def structured(self, system, schema, *, untrusted):
        self.prompts.append(system)
        self.seen.append(untrusted)
        if self.error:
            raise self.error
        return ChatOut(reply="최근 배포는 payment-api v2.4.1이에요.", summary="최근 배포 질문")


class Clock:
    def __init__(self) -> None:
        self.now = datetime(2026, 11, 9, 6, 0, tzinfo=UTC)

    def __call__(self) -> datetime:
        return self.now


def _service(llm, clock: Clock | None = None) -> ChatService:
    store = threads.ChatStore(clock=clock or Clock())
    svc = ChatService(lambda: store, lambda: llm)
    set_chat_service(svc)
    return svc


def _req(skill: str, data: dict) -> AgentRequest:
    deadline = datetime.now(UTC) + timedelta(seconds=30)
    meta = RequestMeta(context_id="ctx-t", token="t", deadline=deadline, idempotency_key="k-1")
    return AgentRequest(meta=meta, skill=skill, input=data)


def test_send_returns_answering_then_answer_arrives_with_infra_facts() -> None:
    async def run() -> None:
        llm = FakeLLM()
        svc = _service(llm)
        th = svc.send("yejin", None, "최근 배포 알려 줘")
        assert th["id"] == "infrachat-1" and th["status"] == "answering"
        await svc.wait_idle()
        done = svc.get("yejin", th["id"])
        assert done["status"] == "talking" and done["summary"] == "최근 배포 질문"
        got = llm.seen[0]["facts"]
        # 숫자·상태는 코드가 모은 사실에만 있다 (배포 기록, 쇼핑몰 결제 서버 상태)
        assert got["recent_deployments"] and "version" in got["recent_deployments"][0]
        assert got["shop_payment_server"]["failing"] is True
        assert "recent_incidents" in got  # 목 모드는 총괄을 부르지 않고 빈 목록
        # 대화로는 롤백하지 않는다고 프롬프트에 박혀 있다
        assert "롤백" in llm.prompts[0] and "조회와 설명만" in llm.prompts[0]

    asyncio.run(run())


def test_busy_closed_and_other_people() -> None:
    async def run() -> None:
        svc = _service(FakeLLM())
        tid = svc.send("yejin", None, "안녕")["id"]
        with pytest.raises(ChatRefused) as busy:
            svc.send("yejin", tid, "또")
        assert busy.value.outcome == "thread_busy"
        await svc.wait_idle()
        seen = svc.get("taeyoon", tid)
        assert seen["restricted"] and seen["messages"] == []
        with pytest.raises(ChatRefused) as other:
            svc.send("taeyoon", tid, "끼어들기")
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
        clock.now += timedelta(minutes=31)
        assert svc.get("yejin", tid)["status"] == "closed"

    asyncio.run(run())


def test_without_llm_is_refused() -> None:
    svc = _service(None)
    with pytest.raises(ChatRefused) as exc:
        svc.send("yejin", None, "안녕")
    assert exc.value.code == "LLM_NOT_CONFIGURED"


def test_facts_keep_going_when_one_part_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    async def boom() -> dict:
        raise RuntimeError("shop down")

    monkeypatch.setattr(facts, "_shop", boom)
    got = asyncio.run(facts.collect())
    assert "shop_payment_server" in got["unavailable"] and "shop_payment_server" not in got
    assert got["recent_deployments"]


def test_a2a_skills_use_the_same_service_and_are_on_the_card() -> None:
    async def run() -> None:
        svc = _service(FakeLLM())
        sent = await a2a.infra_chat_send(
            _req("infra_chat_send", {"who": "yejin", "message": "안녕"})
        )
        assert sent.status == TaskStatus.COMPLETED
        await svc.wait_idle()
        tid = sent.output["thread"]["id"]
        listed = await a2a.infra_chat_list(_req("infra_chat_list", {"who": "taeyoon"}))
        assert listed.output["threads"][0]["restricted"]
        missing = await a2a.infra_chat_get(
            _req("infra_chat_get", {"who": "yejin", "thread_id": "x"})
        )
        assert missing.status == TaskStatus.FAILED and missing.outcome == "not_found"
        closed = await a2a.infra_chat_close(
            _req("infra_chat_close", {"who": "yejin", "thread_id": tid})
        )
        assert closed.output["thread"]["status"] == "closed"
        bad = await a2a.infra_chat_send(_req("infra_chat_send", {"who": "yejin", "message": ""}))
        assert bad.status == TaskStatus.FAILED and bad.outcome == "invalid_input"

    asyncio.run(run())
    skills = {s.id: s for s in load_card().skills}
    names = ("infra_chat_send", "infra_chat_close", "infra_chat_list", "infra_chat_get")
    for name in names:
        assert list(skills[name].allowed_requesters) == ["dashboard-api"]
    assert (skills["infra_chat_send"].grade, skills["infra_chat_get"].grade) == ("A", "read")


def test_other_agents_cannot_open_the_chat() -> None:
    body = {
        "meta": {
            "context_id": "ctx-t",
            "token": "mock:marketing",
            "path": [],
            "deadline": (datetime.now(UTC) + timedelta(seconds=30)).isoformat(),
        },
        "skill": "infra_chat_list",
        "input": {"who": "yejin"},
    }
    res = TestClient(app).post("/a2a", json=body).json()
    assert res["status"] != "completed"
