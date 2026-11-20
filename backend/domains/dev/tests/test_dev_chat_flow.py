# ruff: noqa: F811 - test_dev_chat의 adapter fixture를 가져와 인자로 받는다
"""개발 AI 대화 흐름 점검(11/20): 기획 확정 뒤 작업 잇기, 중복 묶기, 키 오류 안내, 못 적은 답,
끝내기·숨기기, 계획 기다리는 대화, 갇힌 answering, 레포 연결 안내.

실제 LLM·GitHub·기획 서비스는 부르지 않는다 (가짜 LLM·목 형상관리·가짜 create_ticket).
"""

from __future__ import annotations

import asyncio
import sys
from datetime import timedelta
from pathlib import Path

import httpx
import pytest
from fastapi.testclient import TestClient

from ax_agent_template.llm import LLMResponseError
from ax_contracts.schemas import AgentRequest, AgentResult, TaskStatus
from ax_workflow.models import Request, Spec, Task
from dev_agent import dev_chat, vcs
from dev_agent.main import app
from planning_agent.domain.service import PlanningService

sys.path.insert(0, str(Path(__file__).parent))
from test_dev_chat import (  # noqa: E402
    GO_MSG,
    PLAN,
    START,
    ChatLLM,
    adapter,  # noqa: F401 - fixture
)

pytestmark = pytest.mark.role("dev")

URL = "/api/planning/dev-chat"


def _start_dev(service: PlanningService, request_id: str, n: int = 1) -> str:
    """기획이 기획서를 확정해 개발로 넘긴 것처럼 start_dev를 부른다. 받은 작업 id를 준다."""
    req = Request.model_validate(
        {
            "id": request_id,
            "context_id": f"ctx_{request_id}",
            "from": "planning",
            "via": "create_ticket",
            "title": PLAN.title,
            "evidence": PLAN.plan,
            "state": "작업 중",
        }
    )
    sp = Spec(
        id=f"SPEC-9{n}",
        title=PLAN.title,
        request=request_id,
        status="확정",
        problem="p",
        goal="g",
        paras=[],
    )
    task = Task(id=f"T-9{n}", spec=sp.id, para="1", title=PLAN.title, owner="dev", state="진행 중")
    asyncio.run(
        service.start_dev(
            context_id=req.context_id,
            requester="planning",
            request=req.model_dump(mode="json", by_alias=True),
            spec=sp.model_dump(mode="json", by_alias=True),
            tasks=[task.model_dump(mode="json", by_alias=True)],
            idempotency_key=f"{req.context_id}:start_dev:{sp.id}",
        )
    )
    return task.id


# ---------------------------------------------------------------- 1·2. 기획 확정 뒤 작업 잇기


def test_confirmed_spec_links_handed_chat_without_user_message(
    service: PlanningService, adapter
) -> None:
    """기획서가 확정돼 start_dev가 오면 사람이 다시 말하지 않아도 대화가 그 작업과 이어진다.

    started 메시지(작업 카드)를 남겨 화면이 작업 카드와 'AI 작업 중'을 보여 준다."""
    dev_chat.set_llm(ChatLLM([START]))
    with TestClient(app) as c:
        th = c.post(URL, json={"message": GO_MSG}).json()["data"]
    assert th["status"] == "handed" and th["request_id"] == "REQ-91"

    task_id = _start_dev(service, "REQ-91")
    got = service.chat_of("chat-1")
    assert got is not None and got.status == "started" and got.task_id == task_id
    last = got.messages[-1]
    assert last.kind == "started" and last.task_id == task_id and task_id in last.text
    # 같은 확정이 다시 와도 started 메시지를 또 남기지 않는다
    asyncio.run(dev_chat.link_confirmed(service))
    assert sum(1 for m in service.chat_of("chat-1").messages if m.kind == "started") == 1


def test_other_request_does_not_link(service: PlanningService, adapter) -> None:
    dev_chat.set_llm(ChatLLM([START]))
    with TestClient(app) as c:
        c.post(URL, json={"message": GO_MSG})
    _start_dev(service, "REQ-7")
    got = service.chat_of("chat-1")
    assert got.status == "handed" and got.task_id is None


def test_duplicate_handoff_links_with_original_request(service: PlanningService, adapter) -> None:
    """기획이 같은 요청에 묶으면(linked_duplicate) 원래 요청 id를 대화에 남기고, 원래 요청의
    기획서가 확정되면 그 작업과 잇는다."""

    async def dup(req: AgentRequest) -> AgentResult:
        adapter.tickets.got.append(req)
        return AgentResult(
            meta=req.meta,
            status=TaskStatus.COMPLETED,
            outcome="linked_duplicate",
            output={"request_id": "REQ-91", "duplicate_of": "REQ-5"},
        )

    dev_chat.set_ticket_sender(dup)
    dev_chat.set_llm(ChatLLM([START]))
    with TestClient(app) as c:
        th = c.post(URL, json={"message": GO_MSG}).json()["data"]
    assert th["status"] == "handed" and th["duplicate_of"] == "REQ-5"

    task_id = _start_dev(service, "REQ-5")
    got = service.chat_of("chat-1")
    assert got.status == "started" and got.task_id == task_id
    assert got.messages[-1].kind == "started"


# ---------------------------------------------------------------- 4. 키 없음·틀림


@pytest.mark.parametrize(
    "exc",
    [
        LLMResponseError("LLM API가 401를 돌려줬다", status_code=401),
        httpx.HTTPStatusError(
            "403",
            request=httpx.Request("POST", "http://llm.invalid"),
            response=httpx.Response(403),
        ),
    ],
)
def test_llm_auth_error_points_to_company_key(service: PlanningService, adapter, exc) -> None:
    dev_chat.set_llm(ChatLLM([exc]))
    with TestClient(app) as c:
        th = c.post(URL, json={"message": "배너 바꿔 줘"}).json()["data"]
    last = th["messages"][-1]
    assert last["kind"] == "error" and dev_chat.KEY_HINT in last["text"]
    assert th["status"] == "talking"


def test_other_llm_error_keeps_plain_message(service: PlanningService, adapter) -> None:
    dev_chat.set_llm(ChatLLM([LLMResponseError("LLM API가 500를 돌려줬다", status_code=500)]))
    with TestClient(app) as c:
        th = c.post(URL, json={"message": "배너 바꿔 줘"}).json()["data"]
    assert dev_chat.KEY_HINT not in th["messages"][-1]["text"]


# ---------------------------------------------------------------- 6. 답을 적지 못함


def test_unwritable_answer_leaves_last_error(
    service: PlanningService, adapter, monkeypatch: pytest.MonkeyPatch
) -> None:
    """답·오류 메시지를 관문이 막아 못 적으면, 관문을 안 타는 상태 갱신으로 '답을 적지 못함'을
    남긴다. 다음에 사람이 보내면 지운다."""
    dev_chat.set_llm(ChatLLM([START, START]))
    real = service.chat_add

    async def refuse(thread_id, role, text, **kw):
        if role == "assistant":
            raise RuntimeError("관문 거절")
        return await real(thread_id, role, text, **kw)

    monkeypatch.setattr(service, "chat_add", refuse)
    with TestClient(app) as c:
        th = c.post(URL, json={"message": "배너 바꿔 줘"}).json()["data"]
        got = service.chat_of(th["id"])
        assert got.status == "talking" and got.last_error
        assert [m.role for m in got.messages] == ["user"]
        monkeypatch.setattr(service, "chat_add", real)
        c.post(URL, json={"thread_id": th["id"], "message": "배너 문구를 신간으로"})
    assert service.chat_of(th["id"]).last_error is None


# ---------------------------------------------------------------- 8. 끝내기·숨기기·계획 기다리기


def test_rest_close_and_remove(service: PlanningService, adapter) -> None:
    dev_chat.set_llm(ChatLLM([START, START]))
    with TestClient(app) as c:
        a = c.post(URL, json={"message": "배너 바꿔 줘", "account": "u1"}).json()["data"]["id"]
        b = c.post(URL, json={"message": "문구 바꿔 줘", "account": "u1"}).json()["data"]["id"]
        r = c.post(f"{URL}/{a}/close?account=u1")
        assert r.status_code == 200 and r.json()["data"]["status"] == "closed"
        assert c.post(f"{URL}/{b}/remove?account=u2").status_code == 404  # 다른 계정
        r = c.post(f"{URL}/{b}/remove?account=u1")
        assert r.status_code == 200 and r.json()["data"]["thread_id"] == b
        assert service.chat_of(b) is None
        assert any(x.id == b and x.removed_at is not None for x in service.state.chats)
        ids = [x["id"] for x in c.get(f"{URL}?account=u1").json()["data"]]
        assert b not in ids and a in ids
        assert c.post(f"{URL}/{b}/remove?account=u1").status_code == 404


def test_thread_waiting_on_plan_is_kept_longer(service: PlanningService, adapter) -> None:
    """계획 카드를 띄운 채 기다리는 대화는 30분에 끝내지 않는다 (PLAN_CLOSE_AFTER_HOURS)."""
    dev_chat.set_llm(ChatLLM([START]))
    dev_chat.set_background(False)

    async def run() -> None:
        th = await dev_chat.send(service, None, "배너 문구 바꿔 줘", who="yejin")
        assert th.status == "talking" and th.messages[-1].kind == "plan"
        base = service.clock()
        service.clock = lambda: base + timedelta(minutes=dev_chat.CLOSE_AFTER_MINUTES + 1)
        assert await dev_chat.close_idle(service) == []
        service.clock = lambda: base + timedelta(hours=dev_chat.PLAN_CLOSE_AFTER_HOURS, minutes=1)
        assert await dev_chat.close_idle(service) == [th.id]

    asyncio.run(run())


# ---------------------------------------------------------------- 9. 갇힌 answering


def test_stuck_answering_is_released_by_the_loop(service: PlanningService, adapter) -> None:
    async def run() -> None:
        old = await service.chat_add(None, "user", "배너", status="answering", who="yejin")
        base = service.clock()
        service.clock = lambda: base + timedelta(seconds=dev_chat.ANSWER_STUCK_SECONDS + 1)
        new = await service.chat_add(None, "user", "문구", status="answering", who="yejin")
        assert await dev_chat.release_stuck(service) == [old.id]
        got = service.chat_of(old.id)
        assert got.status == "talking" and got.messages[-1].kind == "error"
        assert service.chat_of(new.id).status == "answering"

    asyncio.run(run())


def test_stuck_answering_goes_back_to_linked_status(service: PlanningService, adapter) -> None:
    """기획에 넘긴 대화에서 갇히면 handed로 되돌린다 (요청 기다리기를 잃지 않게)."""

    async def run() -> None:
        th = await service.chat_add(None, "user", "배너", status="answering", who="yejin")
        await dev_chat._link(service, th.id, request_id="REQ-91")
        base = service.clock()
        service.clock = lambda: base + timedelta(seconds=dev_chat.ANSWER_STUCK_SECONDS + 1)
        assert await dev_chat.release_stuck(service) == [th.id]
        assert service.chat_of(th.id).status == "handed"

    asyncio.run(run())


# ---------------------------------------------------------------- 10. 레포 연결 없음


def test_handoff_without_repo_says_connect_one(service: PlanningService, adapter) -> None:
    """레포 연결이 없어도 기획에는 넘기되(개발 단계에서 고른다), 연결이 필요하다고 알린다."""
    vcs.set_vcs(None)
    dev_chat.set_llm(ChatLLM([START]))
    with TestClient(app) as c:
        th = c.post(URL, json={"message": GO_MSG}).json()["data"]
    assert th["status"] == "handed"
    assert dev_chat.REPO_HINT in th["messages"][-1]["text"]


def test_handoff_with_repo_has_no_repo_hint(service: PlanningService, adapter) -> None:
    dev_chat.set_llm(ChatLLM([START]))
    with TestClient(app) as c:
        th = c.post(URL, json={"message": GO_MSG}).json()["data"]
    assert dev_chat.REPO_HINT not in th["messages"][-1]["text"]
