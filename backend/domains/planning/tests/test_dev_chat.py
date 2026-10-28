"""개발 AI와 대화: 되묻기 → 계획 → 코딩 작업 자동 시작.

실제 LLM·GitHub는 부르지 않는다. 형상관리는 파일을 넣은 목 부품, LLM은 가짜 클라이언트다.
"""

from __future__ import annotations

import json
import time

import pytest
from fastapi.testclient import TestClient

from ax_agent_template.llm import LLMResponseError, LLMSettings
from planning_agent import coder, dev_chat, repo_routing, vcs
from planning_agent.coder import CodePlan, CodeReview, FilePick, FileWrite
from planning_agent.dev_chat import ChatTurn, TurnPlan
from planning_agent.domain.service import PlanningService
from planning_agent.main import app
from planning_agent.vcs import MockVcsAdapter

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
    """대화용 가짜. 정해 둔 답을 차례로 준다. 받은 데이터를 남긴다."""

    def __init__(self, turns: list[ChatTurn | Exception]):
        self.settings = LLMSettings(model="fake-chat", base_url="http://llm.invalid")
        self.turns = list(turns)
        self.calls: list[dict] = []

    async def structured(self, prompt, schema, untrusted=None, images=None, context_id=None):
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
START = ChatTurn(reply="배너 문구를 바꾸고 PR을 올릴게요.", action="start", plan=PLAN)


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
    yield a
    vcs._screen = None
    vcs.set_vcs(None)
    coder.reset_llm()
    dev_chat.reset_llm()
    repo_routing.reset_llm()


def _task(svc: PlanningService, task_id: str = "T-D1"):
    return next(t for t in svc.state.tasks if t.id == task_id)


def _post(c: TestClient, **body):
    return c.post(URL, json=body)


def test_vague_then_start_creates_task_and_runs(service: PlanningService, adapter) -> None:
    fake = ChatLLM([ASK, START])
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

        r = _post(c, thread_id="chat-1", message="메인 배너 문구를 '신간 배너'로")
        th = r.json()["data"]
        assert th["status"] == "started" and th["task_id"] == "T-D1"
        assert th["connection"] == "default" and th["title"] == PLAN.title
        last = th["messages"][-1]
        assert last["kind"] == "started" and last["task_id"] == "T-D1"
        assert last["plan"]["paths"] == [BANNER]  # `..` 경로는 뺀다
        # 코딩 작업이 뒤에서 돌아 머지까지 간다 (진행은 작업 coding, SSE 상태)
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline:
            if _task(service).coding.status in ("merged", "waiting_human", "failed"):
                break
            time.sleep(0.02)
        assert _task(service).coding.status == "merged"
        assert _task(service).coding.pr is not None
        state = c.get("/api/planning/state").json()["data"]
        assert state["chats"][0]["task_id"] == "T-D1"
    assert "dev_chat" in [g.action for g in service.gate_calls]
    assert "dev_task_create" in [g.action for g in service.gate_calls]


def test_started_thread_reports_status_without_new_task(service: PlanningService, adapter) -> None:
    dev_chat.set_llm(ChatLLM([START]))
    with TestClient(app) as c:
        _post(c, message="Banner.tsx 문구를 '신간 배너'로 바꾸고 PR 올려 줘")
        r = _post(c, thread_id="chat-1", message="어떻게 되고 있어?")
        th = r.json()["data"]
    assert th["status"] == "started"
    assert th["messages"][-1]["kind"] == "text" and "T-D1" in th["messages"][-1]["text"]
    assert sum(1 for t in service.state.tasks if t.coding is not None) == 1


def test_go_ahead_uses_last_plan(service: PlanningService, adapter) -> None:
    proposal = ChatTurn(reply="이렇게 할까요?", action="ask", plan=PLAN)
    dev_chat.set_llm(ChatLLM([proposal, ChatTurn(reply="네", action="ask")]))
    with TestClient(app) as c:
        th = _post(c, message="배너 문구 바꿔").json()["data"]
        assert th["messages"][-1]["kind"] == "plan" and th["status"] == "talking"
        th = _post(c, thread_id="chat-1", message="좋아요 진행해").json()["data"]
    assert th["status"] == "started" and th["task_id"] == "T-D1"


def test_two_connections_asks_which_repo(service: PlanningService, adapter) -> None:
    vcs.upsert_connection(id="a", kind="mock", repo="acme/shop-web")
    vcs.upsert_connection(id="b", kind="mock", repo="acme/shop-api")
    dev_chat.set_llm(ChatLLM([START]))
    with TestClient(app) as c:
        th = _post(c, message="배너 문구 바꿔서 PR 올려").json()["data"]
        last = th["messages"][-1]
        assert last["kind"] == "question" and "a(acme/shop-web)" in last["text"]
        assert last["plan"] is not None and th["status"] == "talking"
        assert not any(t.coding for t in service.state.tasks)
        # 화면에서 연결을 고르고 다시 보내면 시작한다 (LLM 없이 지난 계획 사용)
        dev_chat.set_llm(ChatLLM([ChatTurn(reply="", action="ask")]))
        th = _post(c, thread_id="chat-1", message="진행해", connection="a").json()["data"]
    assert th["status"] == "started" and th["connection"] == "a"


def test_repo_named_in_message_is_picked(service: PlanningService, adapter) -> None:
    vcs.upsert_connection(id="a", kind="mock", repo="acme/shop-web")
    vcs.upsert_connection(id="b", kind="mock", repo="acme/shop-api")
    dev_chat.set_llm(ChatLLM([START]))
    with TestClient(app) as c:
        th = _post(c, message="shop-web 레포 배너 문구 바꿔 줘").json()["data"]
    assert th["status"] == "started" and th["connection"] == "a"


def test_no_connection_blocks_with_message(service: PlanningService, adapter) -> None:
    vcs.set_vcs(None)
    dev_chat.set_llm(ChatLLM([START]))
    with TestClient(app) as c:
        r = _post(c, message="배너 문구 바꿔 줘")
    assert r.status_code == 200
    th = r.json()["data"]
    assert th["status"] == "blocked" and th["messages"][-1]["kind"] == "error"
    assert "연결" in th["messages"][-1]["text"]
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


def test_too_many_questions_then_start(service: PlanningService, adapter) -> None:
    again = ChatTurn(reply="하나 더 물어볼게요", action="ask", plan=PLAN)
    q = ChatTurn(reply="무엇을요?", action="ask")
    dev_chat.set_llm(ChatLLM([q, q, again]))
    c = TestClient(app)
    _post(c, message="바꿔 줘")
    _post(c, thread_id="chat-1", message="배너")
    th = _post(c, thread_id="chat-1", message="메인 배너 문구").json()["data"]
    assert th["status"] == "started"


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
