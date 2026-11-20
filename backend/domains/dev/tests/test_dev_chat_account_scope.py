# ruff: noqa: F811 - test_dev_chat의 adapter fixture를 가져와 인자로 받는다
"""개발 AI 대화의 계정 분리를 서버가 지킨다 (#1545 리뷰 후속).

1. SSE·`/state`에는 대화 원문을 싣지 않는다 (id·status·updated_at만). 원문은 계정을 확인하는
   REST(`/dev-chat`)로만 준다
2. 세션 모드(`AX_SESSION_SECRET`)면 세션 토큰이 있어야 하고 계정은 세션 계정이다. 적은
   `?account=`(본문 `account`)가 다르면 403. 세션 설정이 꺼진 데모 모드에서만 적은 계정을 쓴다
3. 남이 낸 요청에서 온 작업(보기만 하는 대화의 작업)은 작업 API로도 조작할 수 없다 (VIEW_ONLY)

실제 LLM·GitHub·기획 서비스는 부르지 않는다 (가짜 LLM·목 형상관리·가짜 create_ticket).
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ax_agent_template import human_auth
from dev_agent import dev_chat
from dev_agent.main import app
from planning_agent.domain.service import PlanningService

sys.path.insert(0, str(Path(__file__).parent))
from test_dev_chat import (  # noqa: E402
    ASK,
    GO_MSG,
    START,
    ChatLLM,
    adapter,  # noqa: F401 - fixture
)
from test_dev_chat_flow import TwoTickets, _start_dev  # noqa: E402

pytestmark = pytest.mark.role("dev")

URL = "/api/planning/dev-chat"
TASKS = "/api/planning/dev-tasks"
SECRET = "비밀 문구: 결제 키는 sk-live-1234"


@pytest.fixture
def sessions(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv(human_auth.SESSION_SECRET_ENV, "s" * 40)

    def token(account_id: str, name: str, role: str = "member") -> dict[str, str]:
        tok, _ = human_auth.issue_session(account_id=account_id, name=name, role=role)
        return {"Authorization": f"Bearer {tok}"}

    return token


# ---------------------------------------------------------------- 1. SSE·state에 원문 없음


def test_state_and_events_carry_no_chat_text(service: PlanningService, adapter) -> None:
    dev_chat.set_llm(ChatLLM([ASK]))
    with TestClient(app) as c:
        c.post(URL, json={"message": SECRET, "account": "u1"})
        state = c.get("/api/planning/state")
        sse = c.get("/api/planning/events", params={"max_events": 1})
        acted = c.post("/api/planning/actions", json={"type": "nope"})
    frame = next(line for line in sse.text.splitlines() if line.startswith("data: "))
    for chats in (state.json()["data"]["chats"], json.loads(frame[6:])["chats"]):
        assert [set(x) for x in chats] == [{"id", "status", "updated_at"}]
        assert chats[0]["id"] == "chat-1"
    for res in (state, sse, acted):
        assert SECRET not in res.text and "u1" not in res.text and "tester" not in res.text
    # 원문은 REST로 읽는다. 토큰 없이 적은 계정으로 읽히는 것은 세션 설정이 꺼진 데모 모드에서만
    # (세션 모드는 test_session_mode_needs_a_session_token)
    assert not human_auth.sessions_enabled()
    rows = TestClient(app).get(URL, params={"account": "u1"}).json()["data"]
    assert rows[0]["messages"][0]["text"] == SECRET


# ---------------------------------------------------------------- 2. 세션 계정과 맞아야


def test_session_account_scopes_chat_rest(service: PlanningService, adapter, sessions) -> None:
    dev_chat.set_llm(ChatLLM([ASK, ASK]))
    a, b = sessions("acc-a", "가"), sessions("acc-b", "나")
    with TestClient(app) as c:
        # 본문에 계정이 없으면 세션 계정으로 연다. 다른 계정을 적으면 403
        mine = c.post(URL, json={"message": "내 대화"}, headers=a)
        assert mine.status_code == 200 and mine.json()["data"]["account"] == "acc-a"
        tid = mine.json()["data"]["id"]
        forged = c.post(URL, json={"message": "남인 척", "account": "acc-b"}, headers=a)
        assert forged.status_code == 403
        # 읽기: 세션 계정의 대화만. 다른 계정을 적으면 403
        assert [t["id"] for t in c.get(URL, headers=a).json()["data"]] == [tid]
        assert c.get(URL, params={"account": "acc-a"}, headers=a).status_code == 200
        assert c.get(URL, params={"account": "acc-b"}, headers=a).status_code == 403
        assert c.get(f"{URL}/{tid}", params={"account": "acc-b"}, headers=a).status_code == 403
        # 다른 세션은 그 대화를 못 본다 (적은 계정을 바꿔도)
        assert c.get(URL, headers=b).json()["data"] == []
        assert c.get(f"{URL}/{tid}", headers=b).status_code == 404
        assert c.get(f"{URL}/{tid}", params={"account": "acc-a"}, headers=b).status_code == 403
        cut_in = {"thread_id": tid, "message": "끼어들기", "account": "acc-a"}
        assert c.post(URL, json=cut_in, headers=b).status_code == 403
        assert (
            c.post(f"{URL}/{tid}/close", params={"account": "acc-a"}, headers=b).status_code == 403
        )
        assert c.post(f"{URL}/{tid}/remove", headers=b).status_code == 404
        # 주인은 끝내기·숨기기 된다
        assert c.post(f"{URL}/{tid}/close", headers=a).status_code == 200
        assert c.post(f"{URL}/{tid}/remove", headers=a).status_code == 200


def test_bad_session_token_is_refused_on_reads(service: PlanningService, adapter, sessions) -> None:
    sessions("acc-a", "가")
    bad = {"Authorization": "Bearer axs1.e30.forged"}
    with TestClient(app) as c:
        assert c.get(URL, headers=bad).status_code == 403


# ---------------------------------------------------------------- 3. 남의 요청 작업은 조작 못 함


def _view_only_task(service: PlanningService) -> str:
    """u1이 낸 REQ-91에 u2의 대화가 묶이고, REQ-91 기획서가 확정돼 두 대화가 같은 작업을 잇는다."""
    dev_chat.set_ticket_sender(TwoTickets())
    dev_chat.set_llm(ChatLLM([START, START]))
    with TestClient(app) as c:
        c.post(URL, json={"message": GO_MSG, "account": "u1"})
        c.post(URL, json={"message": GO_MSG, "account": "u2"})
    task_id = _start_dev(service, "REQ-91")
    assert dev_chat.view_only(service, service.chat_of("chat-2"))
    return task_id


TASK_CALLS = [
    ("retry", {}),
    ("renew-hold", {}),
    ("cancel", {}),
    ("return", {"to": "planning", "reason": "기획 일"}),
    ("discard", {}),
    ("to-agent", {}),
    ("to-subscription", {}),
    ("skip-queue", {}),
    ("text-ok", {}),
    ("close-covered", {}),
    ("revise", {"note": "색도 바꿔"}),
]


@pytest.mark.parametrize(("path", "body"), TASK_CALLS)
def test_task_api_refuses_someone_elses_request(
    service: PlanningService, adapter, path: str, body: dict
) -> None:
    task_id = _view_only_task(service)
    with TestClient(app) as c:
        res = c.post(f"{TASKS}/{task_id}/{path}", params={"account": "u2"}, json=body)
        assert res.status_code == 403, res.text
        assert res.json()["error"]["code"] == "VIEW_ONLY"
        assert res.json()["error"]["message"] == dev_chat.TASK_OWNER_ONLY
        assert "대화를 연 사람이나 관리자만" in dev_chat.TASK_OWNER_ONLY
        # 계정을 모르는 호출도 주인이 아니다 (fail-closed)
        anon = c.post(f"{TASKS}/{task_id}/{path}", json=body)
        assert anon.status_code == 403 and anon.json()["error"]["code"] == "VIEW_ONLY"
        # 요청을 낸 계정은 막지 않는다 (작업 상태에 따라 다른 답일 수는 있다)
        own = c.post(f"{TASKS}/{task_id}/{path}", params={"account": "u1"}, json=body)
        assert own.status_code != 403 or own.json()["error"]["code"] != "VIEW_ONLY"


def test_to_chat_refuses_someone_elses_request(service: PlanningService, adapter) -> None:
    task_id = _view_only_task(service)
    with TestClient(app) as c:
        res = c.post(f"{TASKS}/{task_id}/to-chat", json={"account": "u2"})
        assert res.status_code == 403 and res.json()["error"]["code"] == "VIEW_ONLY"


def test_task_api_checks_the_session_account(service: PlanningService, adapter, sessions) -> None:
    """세션 토큰이면 계정은 토큰에서 본다. 다른 계정을 적으면 403."""
    dev_chat.set_ticket_sender(TwoTickets())
    dev_chat.set_llm(ChatLLM([START]))
    a, b = sessions("acc-a", "가"), sessions("acc-b", "나")
    with TestClient(app) as c:
        c.post(URL, json={"message": GO_MSG}, headers=a)
    task_id = _start_dev(service, "REQ-91")
    with TestClient(app) as c:
        other = c.post(f"{TASKS}/{task_id}/cancel", headers=b)
        assert other.status_code == 403 and other.json()["error"]["code"] == "VIEW_ONLY"
        forged = c.post(f"{TASKS}/{task_id}/cancel", params={"account": "acc-a"}, headers=b)
        assert forged.status_code == 403
        own = c.post(f"{TASKS}/{task_id}/cancel", headers=a)
        assert own.status_code != 403, own.text


def test_task_without_chat_owner_is_open(service: PlanningService, adapter) -> None:
    """대화가 낸 요청이 아닌 작업(기획에서 온 작업)은 지금처럼 누구나 조작한다."""
    task_id = _start_dev(service, "REQ-7")
    with TestClient(app) as c:
        res = c.post(f"{TASKS}/{task_id}/cancel", params={"account": "u2"})
        assert res.status_code != 403, res.text


def test_chat_rest_marks_view_only(service: PlanningService, adapter) -> None:
    """화면이 버튼을 숨기도록 대화에 view_only를 싣는다 (진짜 방어는 작업 API)."""
    _view_only_task(service)
    with TestClient(app) as c:
        theirs = c.get(URL, params={"account": "u2"}).json()["data"]
        mine = c.get(URL, params={"account": "u1"}).json()["data"]
        one = c.get(f"{URL}/chat-2", params={"account": "u2"}).json()["data"]
    assert [t["view_only"] for t in theirs] == [True] and one["view_only"] is True
    assert [t["view_only"] for t in mine] == [False]


# ---------------------------------------------------------------- 리뷰 반영: 세션 모드·주인·관리자

SHARED = {"Authorization": "Bearer human:tester"}  # 공용(목) 사람 토큰


def test_session_mode_needs_a_session_token(service: PlanningService, adapter, sessions) -> None:
    """세션 모드면 토큰 없음(401)·공용 토큰(403)으로는 계정을 적어도 대화를 못 읽고 못 쓴다."""
    dev_chat.set_llm(ChatLLM([ASK]))
    a = sessions("acc-a", "가")
    none = {"Authorization": ""}
    with TestClient(app) as c:
        tid = c.post(URL, json={"message": SECRET}, headers=a).json()["data"]["id"]
        for headers, status in ((none, 401), (SHARED, 403)):
            reads = (
                c.get(URL, params={"account": "acc-a"}, headers=headers),
                c.get(f"{URL}/{tid}", params={"account": "acc-a"}, headers=headers),
            )
            for res in reads:
                assert res.status_code == status and SECRET not in res.text
            write = {"thread_id": tid, "message": "끼어들기", "account": "acc-a"}
            assert c.post(URL, json=write, headers=headers).status_code == status
            for path in ("close", "remove"):
                res = c.post(f"{URL}/{tid}/{path}", params={"account": "acc-a"}, headers=headers)
                assert res.status_code == status
        assert c.get(URL, headers=a).json()["data"][0]["messages"][0]["text"] == SECRET


@pytest.mark.parametrize(("path", "body"), TASK_CALLS)
def test_session_mode_task_api_needs_a_session_token(
    service: PlanningService, adapter, sessions, path: str, body: dict
) -> None:
    dev_chat.set_ticket_sender(TwoTickets())
    dev_chat.set_llm(ChatLLM([START]))
    a = sessions("acc-a", "가")
    with TestClient(app) as c:
        c.post(URL, json={"message": GO_MSG}, headers=a)
    task_id = _start_dev(service, "REQ-91")
    with TestClient(app) as c:
        # 공용 토큰으로 주인 계정을 적어도 세션 모드에서는 안 된다
        res = c.post(
            f"{TASKS}/{task_id}/{path}", params={"account": "acc-a"}, json=body, headers=SHARED
        )
        assert res.status_code == 403, res.text
        res = c.post(f"{TASKS}/{task_id}/{path}", json=body, headers={"Authorization": ""})
        assert res.status_code == 401


def test_session_admin_may_operate_others_task(service: PlanningService, adapter, sessions) -> None:
    """회사 관리자 세션은 남의 대화 요청에서 온 작업도 중단·폐기할 수 있다."""
    dev_chat.set_ticket_sender(TwoTickets())
    dev_chat.set_llm(ChatLLM([START]))
    a = sessions("acc-a", "가")
    boss, member = sessions("acc-boss", "관리자", "admin"), sessions("acc-m", "일반")
    with TestClient(app) as c:
        c.post(URL, json={"message": GO_MSG}, headers=a)
    task_id = _start_dev(service, "REQ-91")
    with TestClient(app) as c:
        assert c.post(f"{TASKS}/{task_id}/cancel", headers=member).status_code == 403
        res = c.post(f"{TASKS}/{task_id}/cancel", headers=boss)
        assert res.status_code != 403, res.text  # 소유 확인 통과 (코딩 기록이 없어 404)
        res = c.post(f"{TASKS}/{task_id}/discard", headers=boss)
        assert res.status_code != 403, res.text


def test_same_owner_compares_account_for_screen_chats(service: PlanningService) -> None:
    """계정이 있는 개발 화면 대화는 계정만 본다 (사람 이름 무시). 계정 없는 예전 화면 대화와
    대시보드 대화는 사람으로 본다."""
    from datetime import UTC, datetime

    from ax_workflow.models import ChatThread

    t = datetime(2026, 11, 20, tzinfo=UTC)
    screen = ChatThread(
        id="chat-1", title="t", origin="screen", account="u1", who="가", created_at=t, updated_at=t
    )
    assert dev_chat.same_owner(screen, "u1", "다른 이름")
    assert dev_chat.same_owner(screen, "u1", None)
    assert not dev_chat.same_owner(screen, "u2", "가")
    old = screen.model_copy(update={"account": None})
    assert dev_chat.same_owner(old, "u9", "가") and not dev_chat.same_owner(old, None, "나")
    dash = screen.model_copy(update={"origin": "dashboard", "account": None})
    assert dev_chat.same_owner(dash, None, "가") and not dev_chat.same_owner(dash, None, "나")


def test_old_screen_chat_without_account_falls_back_to_who(
    service: PlanningService, adapter
) -> None:
    """계정 없이 연 예전 화면 대화의 요청 작업은 같은 사람(토큰 이름)만 조작한다."""
    dev_chat.set_ticket_sender(TwoTickets())
    dev_chat.set_llm(ChatLLM([START]))
    with TestClient(app) as c:
        c.post(URL, json={"message": GO_MSG})  # human:tester, 계정 없음
    task_id = _start_dev(service, "REQ-91")
    with TestClient(app) as c:
        other = c.post(f"{TASKS}/{task_id}/cancel", headers={"Authorization": "Bearer human:other"})
        assert other.status_code == 403 and other.json()["error"]["code"] == "VIEW_ONLY"
        assert c.post(f"{TASKS}/{task_id}/cancel", headers=SHARED).status_code != 403
