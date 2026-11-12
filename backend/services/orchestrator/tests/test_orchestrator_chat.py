"""총괄 AI 직접 대화: 저장 후 answering → 뒤에서 답, 이벤트 기록만 읽는 사실,
열람 제한, LLM 없을 때."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from ax_agent_template.config import set_agent_name
from ax_contracts.schemas import AgentRequest, RequestMeta, TaskStatus
from ax_orchestrator.chat import a2a, facts, threads
from ax_orchestrator.chat.turn import ChatOut, ChatRefused, ChatService, set_chat_service
from ax_orchestrator.manager import IncidentManager
from ax_orchestrator.models import Incident, StepRun
from ax_orchestrator.plans.scenario_a import scenario_a


class FakeLLM:
    def __init__(self, error: Exception | None = None) -> None:
        self.seen: list[dict] = []
        self.error = error

    async def structured(self, system, schema, *, untrusted):
        self.seen.append(untrusted)
        if self.error:
            raise self.error
        return ChatOut(reply="인사 단계가 가장 오래 걸렸어요.", summary="장애 진행 질문")


@pytest.fixture(autouse=True)
def _reset(monkeypatch):
    monkeypatch.setenv("AGENT_TEMPLATE_MOCK", "1")
    yield
    set_chat_service(None)
    set_agent_name("orchestrator")


def _manager() -> IncidentManager:
    m = IncidentManager(None, scenario_a)  # type: ignore[arg-type]
    t0 = datetime(2026, 11, 9, 2, 0, tzinfo=UTC)
    inc = Incident(
        context_id="ctx_1", kind="incident", title="결제 장애", component="shop-api", opened_at=t0
    )
    inc.steps["a"] = StepRun(
        "a",
        "x",
        "원인 분석",
        dept="data",
        status="completed",
        started_at=t0,
        finished_at=t0 + timedelta(seconds=20),
    )
    inc.steps["b"] = StepRun(
        "b",
        "y",
        "인사 기록",
        dept="hr",
        status="completed",
        started_at=t0,
        finished_at=t0 + timedelta(seconds=80),
    )
    m.incidents["ctx_1"] = inc
    return m


def _service(llm, manager=None) -> ChatService:
    store = threads.ChatStore()
    svc = ChatService(lambda: store, lambda: manager, lambda: llm)
    set_chat_service(svc)
    return svc


def test_answer_arrives_with_orchestrator_facts() -> None:
    async def run() -> None:
        llm = FakeLLM()
        svc = _service(llm, _manager())
        th = svc.send("jun", None, "어느 부서에서 오래 걸렸어?")
        assert th["id"] == "orchchat-1" and th["status"] == "answering"
        await svc.wait_idle()
        done = svc.get("jun", th["id"])
        assert done["status"] == "talking" and done["summary"] == "장애 진행 질문"
        got = llm.seen[0]["facts"]
        assert got["event_count"] == 1
        slowest = got["active_events"][0]["slowest_step"]
        assert (slowest["label"], slowest["seconds"]) == ("인사 기록", 80.0)

    asyncio.run(run())


def test_without_manager_says_unavailable_not_zero() -> None:
    assert "unavailable" in facts.collect(None) and "event_count" not in facts.collect(None)


def test_other_people_see_summary_only_and_cannot_write() -> None:
    async def run() -> None:
        svc = _service(FakeLLM(), _manager())
        th = svc.send("jun", None, "지금 승인 대기 있어?")
        await svc.wait_idle()
        seen = svc.get("other", th["id"])
        assert seen["restricted"] is True and seen["messages"] == []
        with pytest.raises(ChatRefused) as e:
            svc.send("other", th["id"], "끼어들기")
        assert e.value.outcome == "forbidden"

    asyncio.run(run())


def test_no_llm_refuses_and_llm_error_leaves_one_line() -> None:
    async def run() -> None:
        with pytest.raises(ChatRefused) as e:
            _service(None).send("jun", None, "안녕")
        assert e.value.outcome == "llm_not_configured"
        svc = _service(FakeLLM(error=RuntimeError("boom")), _manager())
        th = svc.send("jun", None, "안녕")
        await svc.wait_idle()
        last = svc.get("jun", th["id"])["messages"][-1]
        assert last["kind"] == "error" and "boom" not in last["text"]

    asyncio.run(run())


def test_a2a_skills_validate_and_close() -> None:
    async def run() -> None:
        _service(FakeLLM(), _manager())
        meta = RequestMeta(
            context_id="c",
            token="t",
            deadline=datetime.now(UTC) + timedelta(seconds=30),
            idempotency_key="k",
        )
        bad = await a2a.orchestrator_chat_send(
            AgentRequest(meta=meta, skill="orchestrator_chat_send", input={})
        )
        assert bad.status == TaskStatus.FAILED and bad.outcome == "invalid_input"
        ok = await a2a.orchestrator_chat_send(
            AgentRequest(
                meta=meta, skill="orchestrator_chat_send", input={"who": "jun", "message": "안녕"}
            )
        )
        tid = ok.output["thread"]["id"]
        closed = await a2a.orchestrator_chat_close(
            AgentRequest(
                meta=meta, skill="orchestrator_chat_close", input={"who": "jun", "thread_id": tid}
            )
        )
        assert closed.output["thread"]["status"] == "closed"

    asyncio.run(run())


def test_a2a_remove_own_thread_then_get_is_not_found() -> None:
    async def run() -> None:
        svc = _service(FakeLLM(), _manager())
        meta = RequestMeta(
            context_id="c",
            token="t",
            deadline=datetime.now(UTC) + timedelta(seconds=30),
            idempotency_key="k",
        )

        def req(skill: str, who: str, tid: str) -> AgentRequest:
            return AgentRequest(meta=meta, skill=skill, input={"who": who, "thread_id": tid})

        sent = await a2a.orchestrator_chat_send(
            AgentRequest(
                meta=meta, skill="orchestrator_chat_send", input={"who": "jun", "message": "안녕"}
            )
        )
        tid = sent.output["thread"]["id"]

        # 남의 대화는 forbidden, 그대로 남는다
        other = await a2a.orchestrator_chat_remove(req("orchestrator_chat_remove", "mina", tid))
        assert other.status == TaskStatus.FAILED and other.outcome == "forbidden"
        assert svc.store.get(tid) is not None

        # 없는 대화는 not_found
        gone = await a2a.orchestrator_chat_remove(req("orchestrator_chat_remove", "jun", "nope"))
        assert gone.status == TaskStatus.FAILED and gone.outcome == "not_found"

        # 본인 대화는 답하는 중이어도 지워지고, 끝난 답이 대화를 되살리지 않는다
        removed = await a2a.orchestrator_chat_remove(req("orchestrator_chat_remove", "jun", tid))
        assert removed.status == TaskStatus.COMPLETED
        assert removed.output == {"thread_id": tid, "removed": True}
        await svc.wait_idle()
        assert svc.store.get(tid) is None
        after = await a2a.orchestrator_chat_get(req("orchestrator_chat_get", "jun", tid))
        assert after.status == TaskStatus.FAILED and after.outcome == "not_found"

        # 지운 번호는 다시 쓰지 않는다
        again = await a2a.orchestrator_chat_send(
            AgentRequest(
                meta=meta, skill="orchestrator_chat_send", input={"who": "jun", "message": "다시"}
            )
        )
        assert again.output["thread"]["id"] != tid
        await svc.wait_idle()

    asyncio.run(run())
