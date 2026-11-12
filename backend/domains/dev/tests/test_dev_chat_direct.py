"""개발 AI 직접 대화: 저장 후 answering → 뒤에서 답, 바쁨·끝남·남의 대화, 30분 자동 끝내기,
다시 켜질 때 답하던 대화 되돌리기, A2A skill(`dev_chat_*`)과 개발 화면 REST.

실제 LLM·GitHub는 부르지 않는다 (가짜 LLM·목 형상관리).
"""

from __future__ import annotations

import asyncio
import time
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from ax_agent_template.llm import LLMSettings
from ax_contracts.schemas import AgentRequest, RequestMeta, TaskStatus
from ax_workflow.core import ActionError
from dev_agent import coder, dev_chat, handlers, repo_routing, vcs
from dev_agent.dev_chat import ChatTurn
from dev_agent.main import app
from dev_agent.vcs import MockVcsAdapter
from planning_agent.domain.service import PlanningService

pytestmark = pytest.mark.role("dev")

URL = "/api/planning/dev-chat"
ASK = ChatTurn(reply="어느 화면의 배너인가요?", action="ask")


class ChatLLM:
    """정해 둔 답을 차례로 준다. 다 쓰면 마지막 답을 되풀이한다."""

    def __init__(self, turns: list[ChatTurn | Exception]):
        self.settings = LLMSettings(model="fake-chat", base_url="http://llm.invalid")
        self.turns = list(turns)

    async def structured(self, prompt, schema, untrusted=None, images=None, context_id=None):
        got = self.turns.pop(0) if len(self.turns) > 1 else self.turns[0]
        if isinstance(got, Exception):
            raise got
        return got


@pytest.fixture
def adapter(monkeypatch: pytest.MonkeyPatch):
    for name in ("PLANNING_VCS_KIND", "PLANNING_VCS_REPO", "PLANNING_VCS_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    vcs._screen = None
    a = MockVcsAdapter()
    a.seed_files({"shop-web/src/Banner.tsx": "export const Banner = () => null\n"})
    vcs.set_vcs(a)
    repo_routing.set_llm(None)
    dev_chat.set_background(True)
    yield a
    vcs._screen = None
    vcs.set_vcs(None)
    coder.reset_llm()
    dev_chat.reset_llm()
    repo_routing.reset_llm()


def _req(skill: str, data: dict) -> AgentRequest:
    deadline = datetime.now(UTC) + timedelta(seconds=30)
    meta = RequestMeta(
        context_id="ctx-devchat-t", token="t", deadline=deadline, idempotency_key=f"k-{skill}"
    )
    return AgentRequest(meta=meta, skill=skill, input=data)


def test_send_returns_answering_then_answer_arrives(service: PlanningService, adapter) -> None:
    dev_chat.set_llm(ChatLLM([ASK]))

    async def run() -> None:
        th = await dev_chat.send(service, None, "배너 좀 바꿔 줘", who="yejin")
        assert th.status == "answering" and [m.role for m in th.messages] == ["user"]
        await dev_chat.wait_idle()
        done = service.chat_of(th.id)
        assert done is not None and done.status == "talking"
        assert [m.kind for m in done.messages] == ["text", "question"]

    asyncio.run(run())


def test_busy_closed_and_other_people(service: PlanningService, adapter) -> None:
    dev_chat.set_llm(ChatLLM([ASK]))

    async def run() -> None:
        tid = (await dev_chat.send(service, None, "배너", who="yejin")).id
        with pytest.raises(ActionError) as busy:
            await dev_chat.send(service, tid, "또", who="yejin")
        assert busy.value.code == "THREAD_BUSY"
        await dev_chat.wait_idle()
        with pytest.raises(ActionError) as other:
            await dev_chat.send(service, tid, "끼어들기", who="jaehun")
        assert other.value.code == "FORBIDDEN"
        with pytest.raises(ActionError) as other_close:
            await dev_chat.close(service, tid, "jaehun")
        assert other_close.value.code == "FORBIDDEN"
        assert (await dev_chat.close(service, tid, "yejin")).status == "closed"
        with pytest.raises(ActionError) as closed:
            await dev_chat.send(service, tid, "다시", who="yejin")
        assert closed.value.code == "THREAD_CLOSED"

    asyncio.run(run())


def test_llm_failure_is_written_and_status_comes_back(service: PlanningService, adapter) -> None:
    dev_chat.set_llm(ChatLLM([RuntimeError("LLM API가 429를 돌려줬다")]))

    async def run() -> None:
        tid = (await dev_chat.send(service, None, "배너", who="yejin")).id
        await dev_chat.wait_idle()
        th = service.chat_of(tid)
        assert th is not None and th.status == "talking"
        assert th.messages[-1].kind == "error" and "RuntimeError" in th.messages[-1].text

    asyncio.run(run())


def test_idle_threads_close_after_30_minutes(service: PlanningService, adapter) -> None:
    dev_chat.set_llm(ChatLLM([ASK]))
    dev_chat.set_background(False)

    async def run() -> None:
        talking = (await dev_chat.send(service, None, "배너", who="yejin")).id
        waiting = (await dev_chat.send(service, None, "문구", who="yejin")).id
        await service.chat_set(waiting, status="waiting", waiting_for=["REQ-1"])
        assert await dev_chat.close_idle(service) == []  # 아직 30분 안 됨
        base = service.clock()
        service.clock = lambda: base + timedelta(minutes=dev_chat.CLOSE_AFTER_MINUTES + 1)
        assert await dev_chat.close_idle(service) == [talking]
        assert service.chat_of(waiting).status == "waiting"  # 앞 요청을 기다리는 대화는 그대로

    asyncio.run(run())


def test_orphan_answering_is_released_on_start(service: PlanningService, adapter) -> None:
    async def run() -> None:
        th = await service.chat_add(None, "user", "배너", status="answering", who="yejin")
        assert await dev_chat.recover_orphans(service) == [th.id]
        got = service.chat_of(th.id)
        assert got is not None and got.status == "talking"
        assert got.messages[-1].kind == "error"

    asyncio.run(run())


def test_a2a_skills_use_the_same_chats(service: PlanningService, adapter) -> None:
    dev_chat.set_llm(ChatLLM([ASK]))

    async def run() -> None:
        sent = await handlers.dev_chat_send(
            _req("dev_chat_send", {"who": "yejin", "message": "배너 바꿔 줘"})
        )
        assert sent.status == TaskStatus.COMPLETED
        thread = sent.output["thread"]
        assert thread["status"] == "answering" and thread["restricted"] is False
        await dev_chat.wait_idle()
        tid = thread["id"]
        listed = await handlers.dev_chat_list(_req("dev_chat_list", {"who": "jaehun"}))
        seen = next(t for t in listed.output["threads"] if t["id"] == tid)
        assert seen["restricted"] and seen["messages"] == [] and seen["message_count"] == 2
        mine = await handlers.dev_chat_get(_req("dev_chat_get", {"who": "yejin", "thread_id": tid}))
        assert len(mine.output["thread"]["messages"]) == 2
        missing = await handlers.dev_chat_get(
            _req("dev_chat_get", {"who": "yejin", "thread_id": "chat-99"})
        )
        assert missing.status == TaskStatus.FAILED and missing.outcome == "not_found"
        busy_other = await handlers.dev_chat_send(
            _req("dev_chat_send", {"who": "jaehun", "thread_id": tid, "message": "x"})
        )
        assert busy_other.status == TaskStatus.FAILED and busy_other.outcome == "forbidden"
        closed = await handlers.dev_chat_close(
            _req("dev_chat_close", {"who": "yejin", "thread_id": tid})
        )
        assert closed.output["thread"]["status"] == "closed"
        bad = await handlers.dev_chat_send(_req("dev_chat_send", {"who": "yejin", "message": ""}))
        assert bad.status != TaskStatus.COMPLETED
        bad_mode = await handlers.dev_chat_send(
            _req("dev_chat_send", {"who": "yejin", "message": "x", "mode": "boss"})
        )
        assert bad_mode.status != TaskStatus.COMPLETED

    asyncio.run(run())


def test_without_llm_is_refused(service: PlanningService, adapter) -> None:
    dev_chat.set_llm(None)

    async def run() -> None:
        out = await handlers.dev_chat_send(_req("dev_chat_send", {"who": "y", "message": "안녕"}))
        assert out.status == TaskStatus.FAILED and out.outcome == "llm_not_configured"

    asyncio.run(run())


def test_rest_send_answers_later_and_close(service: PlanningService, adapter) -> None:
    dev_chat.set_llm(ChatLLM([ASK]))
    with TestClient(app) as c:
        r = c.post(URL, json={"message": "배너 좀 바꿔 줘"})
        assert r.status_code == 200
        th = r.json()["data"]
        assert th["status"] == "answering"
        assert c.post(URL, json={"thread_id": th["id"], "message": "또"}).status_code in (200, 409)
        end = time.monotonic() + 5
        got = th
        while got["status"] == "answering" and time.monotonic() < end:
            time.sleep(0.05)
            got = c.get(f"{URL}/{th['id']}").json()["data"]
        assert got["status"] == "talking"
        closed = c.post(f"{URL}/{th['id']}/close")
        assert closed.status_code == 200 and closed.json()["data"]["status"] == "closed"
        again = c.post(URL, json={"thread_id": th["id"], "message": "다시"})
        assert again.status_code == 409 and again.json()["error"]["code"] == "THREAD_CLOSED"
        assert c.post(f"{URL}/chat-99/close").status_code == 404


def test_dashboard_and_screen_chats_do_not_mix(service: PlanningService, adapter) -> None:
    """대시보드 AI 대화방(A2A) 대화와 개발 화면 대화 패널(REST) 대화는 서로 보이지 않는다."""
    dev_chat.set_llm(ChatLLM([ASK]))
    with TestClient(app) as c:
        screen = c.post(URL, json={"message": "화면에서 연 대화"}).json()["data"]
        assert screen["origin"] == "screen"

        async def from_dashboard() -> tuple[str, list[str]]:
            sent = await handlers.dev_chat_send(
                _req("dev_chat_send", {"who": "yejin", "message": "대화방에서 연 대화"})
            )
            await dev_chat.wait_idle()
            listed = await handlers.dev_chat_list(_req("dev_chat_list", {"who": "yejin"}))
            other = await handlers.dev_chat_get(
                _req("dev_chat_get", {"who": "yejin", "thread_id": screen["id"]})
            )
            assert other.status == TaskStatus.FAILED and other.outcome == "not_found"
            return sent.output["thread"]["id"], [t["id"] for t in listed.output["threads"]]

        dash, dash_ids = asyncio.run(from_dashboard())
        assert dash_ids == [dash]
        assert service.chat_of(dash).origin == "dashboard"
        assert [t["id"] for t in c.get(URL).json()["data"]] == [screen["id"]]
        assert c.get(f"{URL}/{dash}").status_code == 404
        assert c.post(URL, json={"thread_id": dash, "message": "끼어들기"}).status_code == 404
        assert c.post(f"{URL}/{dash}/close").status_code == 404


def test_old_chats_without_origin_stay_in_the_dashboard(service: PlanningService, adapter) -> None:
    """나누기 전 대화(origin 없음)는 어디서 열었는지 몰라 개발 화면에 보이지 않는다."""
    dev_chat.set_llm(ChatLLM([ASK]))

    async def old() -> str:
        th = await dev_chat.send(service, None, "예전 대화", who="공용")
        await dev_chat.wait_idle()
        listed = await handlers.dev_chat_list(_req("dev_chat_list", {"who": "공용"}))
        assert [t["id"] for t in listed.output["threads"]] == [th.id]
        return th.id

    tid = asyncio.run(old())
    assert service.chat_of(tid).origin is None
    with TestClient(app) as c:
        assert c.get(URL).json()["data"] == []
        assert c.get(f"{URL}/{tid}").status_code == 404
