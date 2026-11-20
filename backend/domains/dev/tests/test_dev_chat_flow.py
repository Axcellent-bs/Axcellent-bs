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
from dev_agent.dev_chat import ChatTurn, TurnPlan
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


# ---------------------------------------------------------------- 리뷰 반영 (11/20)

ASK = ChatTurn(reply="언제까지 필요한가요?", action="ask")


def test_handed_chat_keeps_waiting_after_more_talk(service: PlanningService, adapter) -> None:
    """넘긴 뒤 묻기만 하면 handed 그대로. 계획 답으로 talking이 돼도 요청을 기다리는 대화라
    30분에 끝나지 않고, 확정되면 작업과 잇는다."""
    later = TurnPlan(title="다른 계획", plan="문구를 또 바꾼다", done=["보인다"])
    dev_chat.set_llm(ChatLLM([START, ASK, ChatTurn(reply="이렇게요", action="ask", plan=later)]))
    with TestClient(app) as c:
        c.post(URL, json={"message": GO_MSG})
        th = c.post(URL, json={"thread_id": "chat-1", "message": "언제 돼?"}).json()["data"]
        assert th["status"] == "handed"
        th = c.post(URL, json={"thread_id": "chat-1", "message": "문구 또 바꿀래"}).json()["data"]
        assert th["status"] == "talking" and th["request_id"] == "REQ-91"
    base = service.clock()
    service.clock = lambda: base + timedelta(minutes=dev_chat.CLOSE_AFTER_MINUTES + 1)
    assert asyncio.run(dev_chat.close_idle(service)) == []
    task_id = _start_dev(service, "REQ-91")
    got = service.chat_of("chat-1")
    assert got.status == "started" and got.task_id == task_id


def test_chat_upkeep_steps_run_even_if_one_fails(
    service: PlanningService, adapter, monkeypatch: pytest.MonkeyPatch
) -> None:
    ran: list[str] = []

    async def boom(svc) -> list[str]:
        raise RuntimeError("close_idle 실패")

    def spy(name):
        async def step(svc, *a) -> list[str]:
            ran.append(name)
            return []

        return step

    monkeypatch.setattr(dev_chat, "notice_long_waits", boom)
    monkeypatch.setattr(dev_chat, "close_idle", boom)
    monkeypatch.setattr(dev_chat, "release_stuck", spy("release_stuck"))
    monkeypatch.setattr(dev_chat, "link_confirmed", spy("link_confirmed"))
    failed = asyncio.run(service.chat_upkeep())
    assert failed == ["notice_long_waits", "close_idle"]
    assert ran == ["release_stuck", "link_confirmed"]


class TwoTickets:
    """첫 요청은 새 REQ-91, 다음 요청은 REQ-91에 묶인 REQ-92."""

    def __init__(self):
        self.got: list[AgentRequest] = []

    async def __call__(self, req: AgentRequest) -> AgentResult:
        self.got.append(req)
        if len(self.got) == 1:
            out = {"request_id": "REQ-91", "duplicate_of": None}
            return AgentResult(
                meta=req.meta, status=TaskStatus.COMPLETED, outcome="created", output=out
            )
        out = {"request_id": f"REQ-9{len(self.got)}", "duplicate_of": "REQ-91"}
        return AgentResult(
            meta=req.meta, status=TaskStatus.COMPLETED, outcome="linked_duplicate", output=out
        )


@pytest.mark.parametrize("other", [True, False])
def test_duplicate_of_someone_elses_request_is_view_only(
    service: PlanningService, adapter, monkeypatch: pytest.MonkeyPatch, other: bool
) -> None:
    """남의 요청에 묶여 이은 작업은 진행만 보여 준다 (PR 수정 지시를 보내지 않는다).
    같은 계정이 낸 요청에 묶였으면 지금처럼 수정 지시가 간다."""
    dev_chat.set_ticket_sender(TwoTickets())
    dev_chat.set_llm(ChatLLM([START, START]))
    with TestClient(app) as c:
        c.post(URL, json={"message": GO_MSG, "account": "u1"})
        b = c.post(URL, json={"message": GO_MSG, "account": "u2" if other else "u1"}).json()
    b_id = b["data"]["id"]
    task_id = _start_dev(service, "REQ-91")
    got = service.chat_of(b_id)
    assert got.status == "started" and got.task_id == task_id
    assert dev_chat.view_only(service, got) is other
    assert ("진행만 볼 수 있어요" in got.messages[-1].text) is other

    revised: list[str] = []

    async def revise(svc, llm, th, text):
        revised.append(th.id)
        return th

    monkeypatch.setattr(dev_chat, "_open_pr_waiting", lambda svc, task_id: True)
    monkeypatch.setattr(dev_chat, "_revise", revise)
    dev_chat.set_llm(ChatLLM([]))
    asyncio.run(dev_chat.send(service, b_id, "버튼 색도 바꿔 줘", account=got.account))
    if other:
        assert revised == [] and service.chat_of(b_id).messages[-1].text == dev_chat.VIEW_ONLY
    else:
        assert revised == [b_id]


def test_link_confirmed_at_once_writes_one_started(service: PlanningService, adapter) -> None:
    """확정 잇기가 겹쳐 와도(start_dev 직후 + 주기 루프) started는 한 번만 남는다."""
    dev_chat.set_llm(ChatLLM([START]))
    with TestClient(app) as c:
        c.post(URL, json={"message": GO_MSG})
    # start_dev 안의 잇기를 거치지 않게 다른 요청으로 작업을 받은 뒤 그 기획서를 이 요청에 붙인다
    task_id = _start_dev(service, "REQ-5")
    next(sp for sp in service.state.specs if sp.request == "REQ-5").request = "REQ-91"

    async def both() -> list[list[str]]:
        return await asyncio.gather(
            dev_chat.link_confirmed(service), dev_chat.link_confirmed(service)
        )

    got = asyncio.run(both())
    th = service.chat_of("chat-1")
    assert sorted(len(x) for x in got) == [0, 1]
    assert th.task_id == task_id and sum(1 for m in th.messages if m.kind == "started") == 1
    # 잠금 밖에서 고른 뒤 늦게 온 잇기(이미 이어짐)는 잠금 안에서 다시 보고 적지 않는다
    assert asyncio.run(dev_chat._started(service, "chat-1", task_id)) is False
    th = service.chat_of("chat-1")
    assert sum(1 for m in th.messages if m.kind == "started") == 1


def test_link_confirmed_skips_answering_thread(service: PlanningService, adapter) -> None:
    dev_chat.set_llm(ChatLLM([START]))
    with TestClient(app) as c:
        c.post(URL, json={"message": GO_MSG})
    asyncio.run(service.chat_set("chat-1", status="answering"))
    _start_dev(service, "REQ-91")
    th = service.chat_of("chat-1")
    assert th.status == "answering" and th.task_id is None  # 답하는 턴을 덮지 않는다
    # 고른 뒤 답하는 턴이 시작됐으면(잠금 안에서 answering) 그 턴을 덮지 않는다
    task_id = dev_chat.request_task(service, th)
    assert asyncio.run(dev_chat._started(service, "chat-1", task_id)) is False
    assert service.chat_of("chat-1").task_id is None
    asyncio.run(service.chat_set("chat-1", status="handed"))
    assert asyncio.run(dev_chat.link_confirmed(service)) == ["chat-1"]


class SlowLLM:
    """풀어 줄 때까지 답하지 않는 가짜 (갇힌 답)."""

    def __init__(self, turn: ChatTurn):
        from ax_agent_template.llm import LLMSettings

        self.settings = LLMSettings(model="fake-chat", base_url="http://llm.invalid")
        self.turn = turn
        self.go: asyncio.Event | None = None

    async def structured(self, prompt, schema, untrusted=None, images=None, context_id=None):
        assert self.go is not None
        await self.go.wait()
        return self.turn


def test_late_answer_after_release_is_dropped(service: PlanningService, adapter) -> None:
    """갇혀서 되돌린 뒤 늦게 온 답은 적지 않고, 상태도 바꾸지 않는다."""
    slow = SlowLLM(START)
    dev_chat.set_llm(slow)
    dev_chat.set_background(True)

    async def run() -> None:
        slow.go = asyncio.Event()
        th = await dev_chat.send(service, None, "배너 문구 바꿔 줘", who="yejin")
        await asyncio.sleep(0.05)
        base = service.clock()
        service.clock = lambda: base + timedelta(seconds=dev_chat.ANSWER_STUCK_SECONDS + 1)
        assert await dev_chat.release_stuck(service) == [th.id]
        await service.chat_set(th.id, status="handed")  # 되돌린 뒤 다른 갱신이 있었다고 치고
        slow.go.set()
        await dev_chat.wait_idle()
        got = service.chat_of(th.id)
        assert got.status == "handed"
        assert [m.kind for m in got.messages] == ["text", "error"]  # 늦은 계획은 버렸다

    asyncio.run(run())


def test_stuck_answering_in_linked_chat_goes_back_to_started(
    service: PlanningService, adapter
) -> None:
    dev_chat.set_llm(ChatLLM([START]))
    with TestClient(app) as c:
        c.post(URL, json={"message": GO_MSG})
    _start_dev(service, "REQ-91")

    async def run() -> None:
        await service.chat_add("chat-1", "user", "어떻게 돼?", status="answering")
        base = service.clock()
        service.clock = lambda: base + timedelta(seconds=dev_chat.ANSWER_STUCK_SECONDS + 1)
        assert await dev_chat.release_stuck(service) == ["chat-1"]
        assert service.chat_of("chat-1").status == "started"

    asyncio.run(run())


def test_request_task_picks_most_recent(service: PlanningService, adapter) -> None:
    dev_chat.set_llm(ChatLLM([START]))
    with TestClient(app) as c:
        c.post(URL, json={"message": GO_MSG})
    first = _start_dev(service, "REQ-91", n=1)
    second = _start_dev(service, "REQ-91", n=2)
    for t in service.state.tasks:
        if t.id in (first, second):
            t.coding = None
    assert dev_chat.request_task(service, service.chat_of("chat-1")) == second
