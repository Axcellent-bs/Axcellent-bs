"""재무 AI 직접 대화: 저장 후 answering → 뒤에서 답, 끝내기·30분 자동 끝내기, 열람 제한,
읽기 전용 재무 사실, LLM 없을 때, A2A skill(`finance_chat_*`)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from ax_contracts.schemas import AgentRequest, RequestMeta, TaskStatus
from finance_agent import handlers
from finance_agent import ledger as lg
from finance_agent.chat import a2a, facts, threads
from finance_agent.chat.turn import ChatOut, ChatRefused, ChatService, set_chat_service
from finance_agent.main import app, load_card
from finance_agent.sources import MockSources


@pytest.fixture(autouse=True)
def _mock(monkeypatch: pytest.MonkeyPatch, fixed_now) -> None:
    monkeypatch.setenv("AGENT_TEMPLATE_MOCK", "1")
    monkeypatch.setitem(lg.EVENTS, "connected", False)
    handlers.reset_state(MockSources())
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
        return ChatOut(reply="오늘 판매 수수료 매출을 확인했어요.", summary="오늘 손익 질문")


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


def test_send_returns_answering_then_answer_arrives_with_finance_facts() -> None:
    async def run() -> None:
        llm = FakeLLM()
        svc = _service(llm)
        th = svc.send("taeyoon", None, "오늘 손익 알려 줘")
        assert th["id"] == "finchat-1" and th["status"] == "answering"
        await svc.wait_idle()
        done = svc.get("taeyoon", th["id"])
        assert done["status"] == "talking" and done["summary"] == "오늘 손익 질문"
        assert [m["role"] for m in done["messages"]] == ["user", "assistant"]
        got = llm.seen[0]["facts"]
        # 숫자는 코드가 모은 재무 사실에만 있다
        assert "today_kpis" in got["overview"] and "items" in got["month_close_checklist"]
        assert got["campaigns"]["role"].startswith("재무는 캠페인 비용을 승인·반려하지 않고")
        # 대화는 조회·설명만, 비용 승인을 하지 않는다고 프롬프트에 박혀 있다
        assert "승인하거나 반려하지 않는다" in llm.prompts[0]

    asyncio.run(run())


def test_busy_closed_and_other_people() -> None:
    async def run() -> None:
        svc = _service(FakeLLM())
        tid = svc.send("taeyoon", None, "안녕")["id"]
        with pytest.raises(ChatRefused) as busy:
            svc.send("taeyoon", tid, "또")
        assert busy.value.outcome == "thread_busy"
        await svc.wait_idle()
        seen = svc.get("reviewer", tid)
        assert seen["restricted"] and seen["messages"] == [] and seen["message_count"] == 2
        with pytest.raises(ChatRefused) as other:
            svc.send("reviewer", tid, "끼어들기")
        assert other.value.status == 403
        assert svc.close("taeyoon", tid)["status"] == "closed"
        with pytest.raises(ChatRefused) as closed:
            svc.send("taeyoon", tid, "다시")
        assert closed.value.outcome == "thread_closed"

    asyncio.run(run())


def test_llm_failure_says_why_and_idle_threads_close() -> None:
    async def run() -> None:
        clock = Clock()
        svc = _service(FakeLLM(RuntimeError("LLM API가 429를 돌려줬다")), clock)
        tid = svc.send("taeyoon", None, "안녕")["id"]
        await svc.wait_idle()
        last = svc.get("taeyoon", tid)["messages"][-1]
        assert last["kind"] == "error" and "429" in last["text"]
        clock.now += timedelta(minutes=31)
        th = svc.get("taeyoon", tid)
        assert th["status"] == "closed" and th["summary"] == "안녕"

    asyncio.run(run())


def test_without_llm_is_refused() -> None:
    svc = _service(None)
    with pytest.raises(ChatRefused) as exc:
        svc.send("taeyoon", None, "안녕")
    assert exc.value.status == 409 and exc.value.code == "LLM_NOT_CONFIGURED"


def test_facts_keep_going_when_one_part_fails(monkeypatch: pytest.MonkeyPatch) -> None:
    async def boom() -> dict:
        raise RuntimeError("books down")

    monkeypatch.setattr(facts, "_bank", boom)
    got = asyncio.run(facts.collect())
    assert "bank" in got["unavailable"] and "bank" not in got
    assert "overview" in got and "records" in got


def test_facts_include_incident_finance_records() -> None:
    handlers.state().ledger.incident_reports["ctx_inc_x"] = {
        "context_id": "ctx_inc_x",
        "component": "payment",
        "damage_amount": 95000,
        "compensation_total": 4000,
        "refund_total": 0,
        "money_out_total": 4000,
        "recorded_at": "2026-11-09T06:00:00Z",
    }
    got = asyncio.run(facts.collect())
    assert got["incident_finance"][0]["money_out_total"] == 4000


def test_a2a_skills_use_the_same_service_and_are_on_the_card() -> None:
    async def run() -> None:
        svc = _service(FakeLLM())
        sent = await a2a.finance_chat_send(
            _req("finance_chat_send", {"who": "taeyoon", "message": "안녕"})
        )
        assert sent.status == TaskStatus.COMPLETED
        assert sent.output["thread"]["status"] == "answering"
        await svc.wait_idle()
        tid = sent.output["thread"]["id"]
        listed = await a2a.finance_chat_list(_req("finance_chat_list", {"who": "reviewer"}))
        assert listed.output["threads"][0]["restricted"]
        missing = await a2a.finance_chat_get(
            _req("finance_chat_get", {"who": "taeyoon", "thread_id": "x"})
        )
        assert missing.status == TaskStatus.FAILED and missing.outcome == "not_found"
        closed = await a2a.finance_chat_close(
            _req("finance_chat_close", {"who": "taeyoon", "thread_id": tid})
        )
        assert closed.output["thread"]["status"] == "closed"
        bad = await a2a.finance_chat_send(
            _req("finance_chat_send", {"who": "taeyoon", "message": ""})
        )
        assert bad.status == TaskStatus.FAILED and bad.outcome == "invalid_input"

    asyncio.run(run())
    skills = {s.id: s for s in load_card().skills}
    names = ("finance_chat_send", "finance_chat_close", "finance_chat_list", "finance_chat_get")
    for name in names:
        assert list(skills[name].allowed_requesters) == ["dashboard-api"]
    assert (skills["finance_chat_send"].grade, skills["finance_chat_get"].grade) == ("A", "read")


def test_other_agents_cannot_open_the_chat() -> None:
    body = {
        "meta": {
            "context_id": "ctx-t",
            "token": "mock:marketing",
            "path": [],
            "deadline": (datetime.now(UTC) + timedelta(seconds=30)).isoformat(),
        },
        "skill": "finance_chat_list",
        "input": {"who": "taeyoon"},
    }
    res = TestClient(app).post("/a2a", json=body).json()
    assert res["status"] != "completed"
