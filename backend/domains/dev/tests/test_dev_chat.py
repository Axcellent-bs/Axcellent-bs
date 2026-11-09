"""개발 AI와 대화: 되묻기 → 계획 → "진행해" → 기획에 요청으로 넘기기.

대화는 개발 작업을 직접 띄우지 않는다. 기획에서 사람이 기획서를 확정해야 개발로 간다 (11/09).
실제 LLM·GitHub·기획 서비스는 부르지 않는다. 형상관리는 파일을 넣은 목 부품, LLM은 가짜
클라이언트, 기획 create_ticket은 받은 요청을 남기는 가짜다.
"""

from __future__ import annotations

import asyncio
import json
import time
from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from ax_agent_template.llm import LLMResponseError, LLMSettings
from ax_contracts.schemas import AgentRequest, AgentResult, TaskStatus
from dev_agent import coder, dev_chat, repo_routing, vcs
from dev_agent.coder import CodePlan, CodeReview, FilePick, FileWrite
from dev_agent.dev_chat import ChatTurn, RevisionCheck, TurnPlan
from dev_agent.main import app
from dev_agent.vcs import MockVcsAdapter
from planning_agent.domain.service import PlanningService

pytestmark = pytest.mark.role("dev")  # 분리 3단계: 개발 서비스 역할로 돈다

BANNER = "shop-web/src/components/Banner.tsx"
FILES = {
    BANNER: "export function Banner() {\n  return <div>배너</div>\n}\n",
    "shop-web/package-lock.json": "{}\n",
    "node_modules/react/index.js": "module.exports = {}\n",
}
URL = "/api/planning/dev-chat"
PLAN = TurnPlan(
    title="메인 배너 문구를 신간 안내로",
    plan="Banner.tsx의 배너 문구를 '신간 배너'로 바꾼다.",
    done=["배너에 신간 문구가 보인다"],
    paths=[BANNER, "../etc/passwd"],
)


class ChatLLM:
    """대화용 가짜. 정해 둔 답을 차례로 준다. 받은 데이터를 남긴다.

    `checks`는 PR 수정 지시가 간단한지 묻는 답(RevisionCheck)이다."""

    def __init__(self, turns: list[ChatTurn | Exception], checks: list[RevisionCheck] = ()):
        self.settings = LLMSettings(model="fake-chat", base_url="http://llm.invalid")
        self.turns = list(turns)
        self.checks = list(checks)
        self.calls: list[dict] = []
        self.check_calls: list[dict] = []

    async def structured(self, prompt, schema, untrusted=None, images=None, context_id=None):
        if schema is RevisionCheck:
            self.check_calls.append(untrusted or {})
            return self.checks.pop(0)
        assert schema is ChatTurn
        self.calls.append(untrusted or {})
        got = self.turns.pop(0)
        if isinstance(got, Exception):
            raise got
        return got


class CoderLLM:
    def __init__(self):
        self.settings = LLMSettings(model="fake-coder", base_url="http://llm.invalid")

    async def structured(self, prompt, schema, untrusted=None, images=None, context_id=None):
        if schema is CodePlan:
            return CodePlan(files=[FilePick(path=BANNER, reason="문구")], summary="배너 문구")
        if schema is FileWrite:
            return FileWrite(
                content="export function Banner() {\n  return <div>신간 배너</div>\n}\n"
            )
        if schema is CodeReview:
            return CodeReview(verdict="통과", notes=[])
        raise KeyError(schema.__name__)


ASK = ChatTurn(reply="어느 화면의 배너인가요? 완료 기준도 알려 주세요.", action="ask")
START = ChatTurn(reply="배너 문구를 이렇게 바꿀게요.", action="start", plan=PLAN)
GO = ChatTurn(reply="", action="ask")  # "진행해"에 LLM이 계획 없이 답해도 지난 계획을 쓴다
GO_MSG = "Banner.tsx 문구를 '신간 배너'로 바꾸고 PR 올려 줘"  # "올려 줘" = 진행해


class Tickets:
    """기획 create_ticket 가짜. 받은 요청을 남기고 새 REQ를 돌려준다."""

    def __init__(self):
        self.got: list[AgentRequest] = []
        self.fail: AgentResult | None = None

    async def __call__(self, req: AgentRequest) -> AgentResult:
        self.got.append(req)
        if self.fail is not None:
            return self.fail
        n = len(self.got)
        return AgentResult(
            meta=req.meta,
            status=TaskStatus.COMPLETED,
            outcome="created",
            output={
                "request_id": f"REQ-{90 + n}",
                "context_id": f"ctx_req_{9000 + n}",
                "outcome": "created",
                "duplicate_of": None,
            },
        )


@pytest.fixture
def adapter(monkeypatch: pytest.MonkeyPatch):
    for name in ("PLANNING_VCS_KIND", "PLANNING_VCS_REPO", "PLANNING_VCS_TOKEN"):
        monkeypatch.delenv(name, raising=False)
    vcs._screen = None
    a = MockVcsAdapter()
    a.seed_files(FILES)
    vcs.set_vcs(a)
    repo_routing.set_llm(None)
    coder.set_llm(CoderLLM())
    dev_chat.set_background(False)  # 보내기가 답까지 기다린다 (뒤에서 답하기는 따로 시험)
    a.tickets = Tickets()
    dev_chat.set_ticket_sender(a.tickets)
    yield a
    dev_chat.set_ticket_sender(None)
    dev_chat.set_background(True)
    vcs._screen = None
    vcs.set_vcs(None)
    coder.reset_llm()
    dev_chat.reset_llm()
    repo_routing.reset_llm()


def _task(svc: PlanningService, task_id: str = "T-D1"):
    return next(t for t in svc.state.tasks if t.id == task_id)


def _post(c: TestClient, **body):
    return c.post(URL, json=body)


def _confirm(c: TestClient, service: PlanningService, thread_id: str) -> str:
    """기획에서 사람이 기획서를 확정해 개발 작업이 생긴 것처럼 만든다.

    개발 화면 작업 만들기로 작업을 띄우고, 그 기획서를 대화가 넘긴 요청에 잇는다 (start_dev 대신).
    """
    rid = service.chat_of(thread_id).request_id
    assert rid
    body = {"title": PLAN.title, "plan": PLAN.plan, "done": "\n".join(PLAN.done), "paths": [BANNER]}
    r = c.post("/api/planning/dev-tasks", json=body)
    assert r.status_code == 200, r.text
    task_id = r.json()["data"]["task_id"]
    spec_id = _task(service, task_id).spec
    next(sp for sp in service.state.specs if sp.id == spec_id).request = rid
    return task_id


def test_plan_waits_for_go_ahead_then_goes_to_planning(service: PlanningService, adapter) -> None:
    """되묻기 → 계획 카드 → "진행해"면 기획에 요청으로 넘긴다. 개발 작업은 띄우지 않는다."""
    fake = ChatLLM([ASK, START, GO])
    dev_chat.set_llm(fake)
    with TestClient(app) as c:
        r = _post(c, message="배너 좀 바꿔 줘")
        assert r.status_code == 200
        th = r.json()["data"]
        assert th["id"] == "chat-1" and th["status"] == "talking" and th["task_id"] is None
        assert [(m["role"], m["kind"]) for m in th["messages"]] == [
            ("user", "text"),
            ("assistant", "question"),
        ]
        assert th["title"] == "배너 좀 바꿔 줘"
        # LLM에 대화·연결·실제 파일 경로(걸러낸 것)를 준다
        data = fake.calls[0]
        assert data["conversation"] == [{"role": "user", "text": "배너 좀 바꿔 줘"}]
        assert [x["id"] for x in data["connections"]] == ["default"]
        assert data["files"] == {"default": [BANNER]}

        # LLM이 action start로 답해도 사람 말 없이는 넘기지 않고 계획만 보여 준다
        th = _post(c, thread_id="chat-1", message="메인 배너 문구를 '신간 배너'로").json()["data"]
        last = th["messages"][-1]
        assert th["status"] == "talking" and last["kind"] == "plan"
        assert dev_chat.READY_HINT in last["text"] and last["plan"]["paths"] == [BANNER]
        assert adapter.tickets.got == []

        th = _post(c, thread_id="chat-1", message="진행해").json()["data"]
    assert th["status"] == "handed" and th["request_id"] == "REQ-91" and th["task_id"] is None
    last = th["messages"][-1]
    assert last["kind"] == "handoff" and "REQ-91" in last["text"]
    assert last["plan"]["title"] == PLAN.title and th["title"] == PLAN.title
    [req] = adapter.tickets.got
    assert req.skill == "create_ticket" and req.meta.idempotency_key
    assert req.input["title"] == PLAN.title
    assert req.input["source_ref"] == "dev_chat:chat-1"
    assert BANNER in req.input["evidence"] and "../etc" not in req.input["evidence"]
    assert "배너에 신간 문구가 보인다" in req.input["evidence"]
    # 개발 작업은 생기지 않는다 (기획 확정 뒤 start_dev로 온다)
    assert not any(t.coding for t in service.state.tasks)
    actions = [g.action for g in service.gate_calls]
    assert "dev_chat" in actions and "dev_task_create" not in actions


def test_planning_refusal_keeps_plan_for_retry(service: PlanningService, adapter) -> None:
    adapter.tickets.fail = AgentResult(
        meta=AgentRequest(
            meta={"context_id": "x", "token": "t", "deadline": "2099-01-01T00:00:00Z"},
            skill="create_ticket",
        ).meta,
        status=TaskStatus.FAILED,
        outcome="gate_rejected",
    )
    dev_chat.set_llm(ChatLLM([START]))
    with TestClient(app) as c:
        th = _post(c, message=GO_MSG).json()["data"]
    last = th["messages"][-1]
    assert th["status"] == "talking" and th["request_id"] is None
    assert last["kind"] == "error" and "gate_rejected" in last["text"] and last["plan"]


def _settle(service: PlanningService, task_id: str) -> str:
    deadline = time.monotonic() + 5
    while time.monotonic() < deadline:
        st = _task(service, task_id).coding.status
        if st in ("merged", "waiting_human", "failed", "canceled"):
            return st
        time.sleep(0.02)
    return _task(service, task_id).coding.status


def test_confirmed_request_links_task_and_reports_status(service: PlanningService, adapter) -> None:
    """기획서가 확정돼 작업이 생기면 다음 말에서 그 작업을 대화에 잇고 진행 상태를 알려 준다."""
    dev_chat.set_llm(ChatLLM([START]))
    with TestClient(app) as c:
        th = _post(c, message=GO_MSG).json()["data"]
        assert th["status"] == "handed"
        task_id = _confirm(c, service, "chat-1")
        _settle(service, task_id)
        _task(service, task_id).coding.status = "writing"  # 아직 도는 중이라고 치고
        th = _post(c, thread_id="chat-1", message="어떻게 되고 있어?").json()["data"]
    assert th["status"] == "started" and th["task_id"] == task_id
    last = th["messages"][-1]
    assert last["kind"] == "text" and task_id in last["text"] and "새 대화" not in last["text"]
    assert sum(1 for t in service.state.tasks if t.coding is not None) == 1


def test_follow_up_after_merge_goes_to_planning_again(service: PlanningService, adapter) -> None:
    """끝난 작업 뒤 이어 말하면 같은 대화에서 다음 계획을 세우고, 진행해면 기획에 또 넘긴다."""
    follow = TurnPlan(
        title="배너 문구 한 번 더", plan="배너 문구를 '오늘의 신간'으로", done=["보인다"]
    )
    fake = ChatLLM([START, ChatTurn(reply="이어서 바꿀게요.", action="start", plan=follow), GO])
    dev_chat.set_llm(fake)
    with TestClient(app) as c:
        _post(c, message=GO_MSG)
        task_id = _confirm(c, service, "chat-1")
        assert _settle(service, task_id) == "merged"
        th = _post(c, thread_id="chat-1", message="문구를 '오늘의 신간'으로 다시 바꿔").json()[
            "data"
        ]
        assert th["status"] == "talking" and th["messages"][-1]["kind"] == "plan"
        th = _post(c, thread_id="chat-1", message="진행해").json()["data"]
    assert th["id"] == "chat-1" and th["status"] == "handed" and th["request_id"] == "REQ-92"
    assert [r.input["title"] for r in adapter.tickets.got] == [PLAN.title, follow.title]
    last_task = fake.calls[1]["last_task"]
    assert last_task["id"] == task_id and last_task["status"] == "merged"
    assert last_task["files"] == [BANNER]


class Picky(CoderLLM):
    """리뷰를 두 번 불일치로 돌려 PR을 사람 확인 대기로 둔다. 수정 지시가 오면 그 문구로 쓴다."""

    FIXED = "export function Banner() {\n  return <div>오늘의 신간</div>\n}\n"

    def __init__(self):
        super().__init__()
        self.reviews = ["불일치", "불일치", "통과"]
        self.instructions: list[str | None] = []

    async def structured(self, prompt, schema, untrusted=None, images=None, context_id=None):
        data = untrusted or {}
        if schema is FileWrite:
            self.instructions.append(data.get("instruction"))
            if data.get("instruction"):
                return FileWrite(content=self.FIXED)
            return FileWrite(content=f"// {len(self.instructions)}\n" + data["current"])
        if schema is CodeReview:
            return CodeReview(verdict=self.reviews.pop(0), notes=["문구가 다르다"])
        return await super().structured(prompt, schema, untrusted, images, context_id)


def test_simple_fix_on_waiting_pr_commits_to_same_pr(service: PlanningService, adapter) -> None:
    """사람 확인을 기다리는 PR에 문구만 바꾸라고 하면 AI가 간단한 수정으로 보고 같은 PR에 커밋."""
    llm = Picky()
    coder.set_llm(llm)
    chat = ChatLLM([START], checks=[RevisionCheck(simple=True, reason="문구만")])
    dev_chat.set_llm(chat)
    with TestClient(app) as c:
        _post(c, message=GO_MSG)
        task_id = _confirm(c, service, "chat-1")
        assert _settle(service, task_id) == "waiting_human"
        pr = next(p for p in service.state.prs if p.task == task_id)
        th = _post(c, thread_id="chat-1", message="문구는 '오늘의 신간'으로 해").json()["data"]
        assert th["messages"][-1]["kind"] == "started" and pr.id in th["messages"][-1]["text"]
        assert _settle(service, task_id) == "waiting_human"
        pr = next(p for p in service.state.prs if p.task == task_id)  # 상태는 바꿔 끼워진다
        assert pr.reviews[-1] == "통과" and not pr.merged
    assert chat.check_calls[0]["instruction"] == "문구는 '오늘의 신간'으로 해"
    assert chat.check_calls[0]["pr_files"] == [BANNER]
    assert len([p for p in service.state.prs if p.task == task_id]) == 1  # 새 PR 없음
    assert ("update_change", pr.vcs_number) in adapter.writes
    assert adapter.contents(pr.vcs_number)[BANNER] == Picky.FIXED
    assert "문구는 '오늘의 신간'으로 해" in llm.instructions
    assert len(adapter.tickets.got) == 1  # 기획에 또 넘기지 않는다


def test_bigger_fix_on_waiting_pr_goes_to_planning(service: PlanningService, adapter) -> None:
    """간단한 문구·스타일 수정이 아니면 같은 PR에 커밋하지 않고 기획에 새 요청으로 넘긴다."""
    llm = Picky()
    coder.set_llm(llm)
    dev_chat.set_llm(ChatLLM([START], checks=[RevisionCheck(simple=False, reason="동작 변경")]))
    with TestClient(app) as c:
        _post(c, message=GO_MSG)
        task_id = _confirm(c, service, "chat-1")
        assert _settle(service, task_id) == "waiting_human"
        pr = next(p for p in service.state.prs if p.task == task_id)
        th = _post(c, thread_id="chat-1", message="배너를 누르면 신간 목록으로 가게 해").json()[
            "data"
        ]
    assert th["status"] == "handed" and th["messages"][-1]["kind"] == "handoff"
    assert th["request_id"] == "REQ-92"
    second = adapter.tickets.got[1]
    assert second.input["title"].startswith(f"{pr.id} 수정")
    assert BANNER in second.input["evidence"]
    assert ("update_change", pr.vcs_number) not in adapter.writes
    assert not any(llm.instructions)  # 수정 지시로 코드를 쓰지 않았다


def test_go_ahead_uses_last_plan(service: PlanningService, adapter) -> None:
    proposal = ChatTurn(reply="이렇게 할까요?", action="ask", plan=PLAN)
    dev_chat.set_llm(ChatLLM([proposal, ChatTurn(reply="네", action="ask")]))
    with TestClient(app) as c:
        th = _post(c, message="배너 문구 바꿔").json()["data"]
        assert th["messages"][-1]["kind"] == "plan" and th["status"] == "talking"
        th = _post(c, thread_id="chat-1", message="좋아요 진행해").json()["data"]
    assert th["status"] == "handed" and th["request_id"] == "REQ-91"
    assert adapter.tickets.got[0].input["title"] == PLAN.title


def test_chosen_or_named_repo_goes_with_request(service: PlanningService, adapter) -> None:
    """고른 연결이나 메시지에 나온 레포는 기획에 넘기는 근거에 적는다. 모르면 적지 않는다."""
    vcs.upsert_connection(id="a", kind="mock", repo="acme/shop-web")
    vcs.upsert_connection(id="b", kind="mock", repo="acme/shop-api")
    dev_chat.set_llm(ChatLLM([START, START, START]))
    with TestClient(app) as c:
        _post(c, message="배너 문구 바꿔서 PR 올려")
        _post(c, message="배너 문구 바꿔서 PR 올려", connection="b")
        _post(c, message="shop-web 레포 배너 문구 바꿔 줘 진행해")
    unknown, chosen, named = (r.input["evidence"] for r in adapter.tickets.got)
    assert "레포 연결" not in unknown
    assert "레포 연결: b" in chosen and "레포 연결: a" in named


def test_no_connection_still_goes_to_planning(service: PlanningService, adapter) -> None:
    """연결이 없어도 막지 않는다. 레포 연결은 기획 확정 뒤 개발이 정한다."""
    vcs.set_vcs(None)
    dev_chat.set_llm(ChatLLM([START]))
    with TestClient(app) as c:
        r = _post(c, message="배너 문구 바꿔 줘 진행해")
    assert r.status_code == 200
    assert r.json()["data"]["status"] == "handed"
    assert not any(t.coding for t in service.state.tasks)


def test_llm_not_configured_and_validation(service: PlanningService, adapter) -> None:
    c = TestClient(app)
    dev_chat.set_llm(None)
    r = _post(c, message="hi")
    assert r.status_code == 409 and r.json()["error"]["code"] == "LLM_NOT_CONFIGURED"
    dev_chat.set_llm(ChatLLM([]))
    for body in (
        {},
        {"message": ""},
        {"message": "   "},
        {"message": "x" * 4001},
        {"message": 3},
        {"message": "hi", "thread_id": 5},
        {"message": "hi", "connection": 5},
        {"message": "hi", "connection": "nope"},
    ):
        r = c.post(URL, json=body)
        assert r.status_code == 400 and r.json()["error"]["code"] == "INVALID_INPUT", body
    r = _post(c, message="hi", thread_id="chat-404")
    assert r.status_code == 404 and r.json()["error"]["code"] == "NOT_FOUND"
    assert service.state.chats == []
    r = c.get(f"{URL}/chat-404")
    assert r.status_code == 404


def test_llm_error_becomes_error_message(service: PlanningService, adapter) -> None:
    dev_chat.set_llm(ChatLLM([LLMResponseError("LLM API가 500를 돌려줬다 key=sk-secret")]))
    c = TestClient(app)
    th = _post(c, message="배너 바꿔 줘").json()["data"]
    last = th["messages"][-1]
    assert last["kind"] == "error" and "LLMResponseError" in last["text"]
    assert "sk-secret" not in last["text"] and th["status"] == "talking"


def test_too_many_questions_then_shows_plan(service: PlanningService, adapter) -> None:
    """되묻기를 다 썼는데 계획이 있으면 더 묻지 않고 계획을 보여 준다. 넘기기는 진행해 뒤."""
    again = ChatTurn(reply="하나 더 물어볼게요", action="ask", plan=PLAN)
    q = ChatTurn(reply="무엇을요?", action="ask")
    dev_chat.set_llm(ChatLLM([q, q, again]))
    c = TestClient(app)
    _post(c, message="바꿔 줘")
    _post(c, thread_id="chat-1", message="배너")
    th = _post(c, thread_id="chat-1", message="메인 배너 문구").json()["data"]
    last = th["messages"][-1]
    assert th["status"] == "talking" and last["kind"] == "plan"
    assert dev_chat.READY_HINT in last["text"] and adapter.tickets.got == []


def test_list_get_and_state_contains_chats(service: PlanningService, adapter) -> None:
    dev_chat.set_llm(ChatLLM([ASK, ASK]))
    c = TestClient(app)
    _post(c, message="첫 대화")
    _post(c, message="둘째 대화")
    rows = c.get(URL).json()["data"]
    assert [r["id"] for r in rows] == ["chat-2", "chat-1"]
    th = c.get(f"{URL}/chat-1").json()["data"]
    assert th["title"] == "첫 대화" and len(th["messages"]) == 2
    state = c.get("/api/planning/state").json()["data"]
    assert [x["id"] for x in state["chats"]] == ["chat-1", "chat-2"]
    r = c.get("/api/planning/events", params={"max_events": 1})
    frame = next(line for line in r.text.splitlines() if line.startswith("data: "))
    assert len(json.loads(frame[6:])["chats"]) == 2
    # 초기화하면 대화도 비운다
    c.post("/api/planning/reset")
    assert service.state.chats == []


async def _waiting_thread(service: PlanningService, target_id: str) -> str:
    """이 흐름 전에 앞 요청을 기다리게 둔 대화 (예전 상태)를 만든다."""
    th = await service.chat_add(None, "user", "메인 배너 문구를 바꿔 줘", title="배너")
    plan = dev_chat.clean_plan(PLAN)
    await service.chat_add(
        th.id, "assistant", "앞 요청이 끝나면 시작할게요.", kind="waiting", plan=plan,
        status="waiting", connection="default", waiting_for=[target_id],
    )  # fmt: skip
    return th.id


def test_old_waiting_thread_goes_to_planning_when_done(service: PlanningService, adapter) -> None:
    """예전에 앞 요청을 기다리던 대화는 앞 요청이 끝나면 작업 대신 기획에 넘긴다."""
    target = next(r for r in service.state.requests if r.state not in ("완료", "기각", "중복 연결"))
    tid = asyncio.run(_waiting_thread(service, target.id))
    asyncio.run(dev_chat.resume_waiting(service))
    assert service.chat_of(tid).status == "waiting"  # 아직 안 끝남
    next(r for r in service.state.requests if r.id == target.id).state = "완료"
    asyncio.run(dev_chat.resume_waiting(service))
    th = service.chat_of(tid)
    assert th.status == "handed" and th.waiting_for == [] and th.task_id is None
    assert target.id in th.messages[-1].text and adapter.tickets.got
    assert "레포 연결: default" in adapter.tickets.got[0].input["evidence"]
    assert not any(t.coding for t in service.state.tasks)


def test_long_wait_is_noticed_once(service: PlanningService, adapter) -> None:
    """앞 요청을 30분 넘게 기다리면 대화에 한 번 알리고 계속 기다린다."""
    target = next(r for r in service.state.requests if r.state not in ("완료", "기각", "중복 연결"))
    tid = asyncio.run(_waiting_thread(service, target.id))
    assert asyncio.run(dev_chat.notice_long_waits(service)) == 0  # 아직 30분 안 됨

    base = service.clock()
    service.clock = lambda: base + timedelta(minutes=dev_chat.WAIT_NOTICE_MINUTES + 1)
    assert asyncio.run(dev_chat.notice_long_waits(service)) == 1
    assert asyncio.run(dev_chat.notice_long_waits(service)) == 0  # 한 번만
    got = service.chat_of(tid)
    assert got.status == "waiting" and "지금 진행" in got.messages[-1].text


def test_chosen_mode_is_kept_and_checked(service: PlanningService, adapter) -> None:
    """'내 Claude'로 고른 것은 대화에 남는다 (작업은 기획 확정 뒤 개발이 띄운다)."""
    dev_chat.set_llm(ChatLLM([START]))
    with TestClient(app) as c:
        th = _post(c, message=GO_MSG, mode="subscription").json()["data"]
        assert th["mode"] == "subscription" and th["status"] == "handed"
        assert not any(t.coding for t in service.state.tasks)
        r = _post(c, message="배너 바꿔 줘", mode="robot")
        assert r.status_code == 400 and r.json()["error"]["code"] == "INVALID_INPUT"


# ---------------------------------------- 큰 동작 변경은 영향·되돌리기·지표를 먼저 묻는다
BIG = TurnPlan(
    title="결제 실패 시 장바구니 유지",
    plan="결제가 실패하면 장바구니를 비우지 않고 남긴다.",
    done=["결제 실패 뒤에도 장바구니가 남는다"],
    paths=[BANNER],
    big=True,
    impact="결제 실패한 고객의 장바구니",
)


def test_big_change_asks_impact_rollback_metric_once(service: PlanningService, adapter) -> None:
    filled = BIG.model_copy(update={"rollback": "설정 끄기", "metric": "결제 재시도율"})
    dev_chat.set_llm(
        ChatLLM(
            [
                ChatTurn(reply="바로 할게요", action="start", plan=BIG),
                ChatTurn(reply="시작할게요", action="start", plan=filled),
            ]
        )
    )
    with TestClient(app) as c:
        th = _post(c, message="결제 실패하면 장바구니 남겨 줘 진행해").json()["data"]
        ask = th["messages"][-1]
        assert th["status"] == "talking" and ask["kind"] == "question"
        assert ask["text"].startswith(dev_chat.IMPACT_ASK)
        assert "되돌리기" in ask["text"] and "확인 지표" in ask["text"]
        assert "영향 범위" not in ask["text"]  # 이미 아는 칸은 다시 묻지 않는다
        th = _post(c, thread_id="chat-1", message="설정 끄기로 되돌리고 재시도율 볼게요 진행해")
        th = th.json()["data"]
    assert th["status"] == "handed"
    plan = th["messages"][-1]["plan"]["plan"]
    assert "되돌리기: 설정 끄기" in plan and "확인 지표: 결제 재시도율" in plan


def test_big_change_asks_only_once_then_marks_unknown(service: PlanningService, adapter) -> None:
    dev_chat.set_llm(
        ChatLLM(
            [
                ChatTurn(reply="할게요", action="start", plan=BIG),
                ChatTurn(reply="시작할게요", action="start", plan=BIG),
            ]
        )
    )
    with TestClient(app) as c:
        _post(c, message="결제 실패하면 장바구니 남겨 줘")
        th = _post(c, thread_id="chat-1", message="모르겠어요 그냥 진행해").json()["data"]
    assert th["status"] == "handed"
    assert "되돌리기: 미정" in th["messages"][-1]["plan"]["plan"]


def test_small_change_does_not_ask_impact(service: PlanningService, adapter) -> None:
    dev_chat.set_llm(ChatLLM([START]))
    with TestClient(app) as c:
        th = _post(c, message="배너 문구 바꿔 줘").json()["data"]
    last = th["messages"][-1]
    assert th["status"] == "talking" and last["kind"] == "plan"
    assert not last["text"].startswith(dev_chat.IMPACT_ASK)
