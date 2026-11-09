"""고객지원 AI 직접 대화 (chat.py, chat_handlers.py): 예진님 요청 2026-11-05."""

import asyncio
import json
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

from ax_agent_template.llm import LLMNotConfiguredError, LLMResponseError, MockLLMClient
from support_agent import chat, chat_handlers, handlers
from support_agent.chat import ChatStore
from support_agent.chat_handlers import ChatReply, ChatSummary
from support_agent.main import app, load_card
from support_agent.persistence import PersistentChatStore
from support_agent.store import InquiryStore

CTX = "ctx_ui_0001"


class Spawned(list):
    """`_spawn`으로 뒤에 맡긴 일. TestClient는 요청마다 루프를 닫아서 테스트가 직접 돌린다."""

    def run(self) -> None:
        while self:
            asyncio.run(self.pop(0))


@pytest.fixture(autouse=True)
def env(monkeypatch: pytest.MonkeyPatch) -> Iterator[Spawned]:
    monkeypatch.setenv("AGENT_TEMPLATE_MOCK", "1")
    monkeypatch.setattr(handlers, "STORE", InquiryStore())
    monkeypatch.setattr(chat_handlers, "CHATS", ChatStore())
    spawned = Spawned()
    monkeypatch.setattr(chat_handlers, "_spawn", spawned.append)
    yield spawned
    for pending in spawned:  # 돌리지 않은 일은 닫아 둔다 (never awaited 경고가 나지 않게)
        pending.close()


@pytest.fixture
def llm(monkeypatch: pytest.MonkeyPatch) -> MockLLMClient:
    client = MockLLMClient(
        responses={
            ChatReply: ChatReply(reply="답변 대기는 3건이에요."),
            ChatSummary: ChatSummary(summary="문의 현황을 묻고 답을 들었어요."),
        }
    )
    monkeypatch.setattr(chat_handlers, "get_llm", lambda: client)
    return client


def call(
    skill: str,
    input: dict[str, Any],
    *,
    message: str | None = None,
    requester: str = "dashboard-api",
) -> dict[str, Any]:
    body: dict[str, Any] = {
        "meta": {
            "context_id": CTX,
            "token": f"mock:{requester}",
            "path": [],
            "deadline": (datetime.now(UTC) + timedelta(seconds=30)).isoformat(),
            "idempotency_key": f"{skill}:{uuid.uuid4().hex}",  # 시각은 Windows에서 같은 값이 겹친다
        },
        "skill": skill,
        "input": input,
    }
    if message is not None:
        body["untrusted"] = {"message": message}
    return TestClient(app).post("/a2a", json=body).json()


def send(who: str, message: str, thread_id: str | None = None, **context: Any) -> dict[str, Any]:
    return call(
        "support_chat_send",
        {"who": who, "thread_id": thread_id, "context": context},
        message=message,
    )


def thread_of(reply: dict[str, Any]) -> dict[str, Any]:
    assert reply["status"] == "completed" and not reply.get("outcome"), reply
    return reply["output"]


# ---------------------------------------------------------------- 보내기와 답
def test_send_saves_message_with_who_and_answers_later(env: Spawned, llm: MockLLMClient) -> None:
    out = thread_of(send("공용", "답변 대기 문의가 몇 건이에요?", waiting=3))
    assert out["id"] == "chat-1" and out["who"] == "공용"
    assert out["status"] == "answering"
    assert [m["role"] for m in out["messages"]] == ["user"]
    assert out["title"] == "답변 대기 문의가 몇 건이에요?"
    env.run()
    done = thread_of(call("support_chat_get", {"thread_id": "chat-1", "viewer": "공용"}))
    assert done["status"] == "talking"
    assert [m["role"] for m in done["messages"]] == ["user", "assistant"]
    assert done["messages"][1]["text"] == "답변 대기는 3건이에요."
    assert done["task_ids"] == []  # 대화로는 일이 생기지 않는다
    assert set(done) >= {"id", "title", "status", "messages", "who", "created_at", "updated_at"}


def test_llm_gets_masked_text_and_context_as_data_block(env: Spawned, llm: MockLLMClient) -> None:
    send("공용", "010-1234-5678 고객 문의는요?", waiting=3, by_category={"refund": 2})
    env.run()
    prompt = llm.calls[-1]
    assert "[전화번호]" in prompt and "010-1234-5678" not in prompt
    assert '"waiting": 3' in prompt or '"waiting":3' in prompt


def test_follow_up_goes_into_the_same_thread(env: Spawned, llm: MockLLMClient) -> None:
    thread_of(send("공용", "첫 질문"))
    env.run()
    second = thread_of(send("공용", "두 번째 질문", thread_id="chat-1"))
    assert second["id"] == "chat-1" and len(second["messages"]) == 3
    env.run()
    assert len(chat_handlers.CHATS.get("chat-1").messages) == 4


def test_cannot_send_while_answering_or_after_closing(env: Spawned, llm: MockLLMClient) -> None:
    thread_of(send("공용", "질문"))
    busy = send("공용", "또 질문", thread_id="chat-1")
    assert busy["outcome"] == "invalid_state"
    env.run()
    thread_of(call("support_chat_close", {"thread_id": "chat-1", "viewer": "공용"}))
    closed = send("공용", "닫은 뒤 질문", thread_id="chat-1")
    assert closed["outcome"] == "invalid_state"


def test_writing_into_someone_elses_thread_is_forbidden(env: Spawned, llm: MockLLMClient) -> None:
    thread_of(send("예진", "질문"))
    env.run()
    other = send("병환", "끼어들기", thread_id="chat-1")
    assert other["outcome"] == "forbidden"
    assert send("병환", "없는 대화에", thread_id="chat-99")["outcome"] == "not_found"
    assert len(chat_handlers.CHATS.get("chat-1").messages) == 2  # 끼어든 말은 저장되지 않았다


def test_message_is_required_and_capped(env: Spawned) -> None:
    empty = call("support_chat_send", {"who": "공용"})
    assert empty["status"] == "failed"
    too_long = send("공용", "가" * (chat.MAX_MESSAGE_CHARS + 1))
    assert too_long["status"] == "failed"
    assert chat_handlers.CHATS.all() == []


def test_llm_failure_leaves_a_one_line_error_and_reopens(
    env: Spawned, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Broken(MockLLMClient):
        async def structured(self, *args: Any, **kwargs: Any) -> Any:
            raise RuntimeError("secret detail sk-12345")

    monkeypatch.setattr(chat_handlers, "get_llm", lambda: Broken())
    thread_of(send("공용", "질문"))
    env.run()
    t = chat_handlers.CHATS.get("chat-1")
    assert t.status == "talking"
    assert t.messages[-1].kind == "error" and t.messages[-1].text == chat_handlers.FAILED_TEXT
    assert "sk-12345" not in t.messages[-1].text  # 예외 내용은 싣지 않는다


def _limited(status_code: int | None) -> type[MockLLMClient]:
    """첫 호출은 `status_code` 오류를 내고, 두 번째부터 정상 답을 주는 모델."""

    class Limited(MockLLMClient):
        tries = 0

        async def structured(self, *args: Any, **kwargs: Any) -> Any:
            type(self).tries += 1
            if type(self).tries == 1:
                raise LLMResponseError("제한", status_code=status_code)
            return ChatReply(reply="답변 대기는 3건이에요.")

    return Limited


@pytest.mark.parametrize("status_code", [429, 503])
def test_a_brief_model_limit_is_retried_once_and_answers(
    env: Spawned, monkeypatch: pytest.MonkeyPatch, status_code: int
) -> None:
    limited = _limited(status_code)
    monkeypatch.setattr(chat_handlers, "get_llm", lambda: limited())
    monkeypatch.setattr(chat_handlers, "RETRY_DELAY_SECONDS", 0.0)
    thread_of(send("공용", "질문"))
    env.run()
    t = chat_handlers.CHATS.get("chat-1")
    assert limited.tries == 2
    assert t.status == "talking" and t.messages[-1].kind == "text"
    assert t.messages[-1].text == "답변 대기는 3건이에요."


@pytest.mark.parametrize("status_code", [None, 400, 500])
def test_other_model_errors_are_not_retried(
    env: Spawned, monkeypatch: pytest.MonkeyPatch, status_code: int | None
) -> None:
    limited = _limited(status_code)
    monkeypatch.setattr(chat_handlers, "get_llm", lambda: limited())
    monkeypatch.setattr(chat_handlers, "RETRY_DELAY_SECONDS", 0.0)
    thread_of(send("공용", "질문"))
    env.run()
    t = chat_handlers.CHATS.get("chat-1")
    assert limited.tries == 1
    assert t.messages[-1].kind == "error" and t.messages[-1].text == chat_handlers.FAILED_TEXT


def test_a_model_that_keeps_limiting_fails_after_one_retry(
    env: Spawned, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Always(MockLLMClient):
        tries = 0

        async def structured(self, *args: Any, **kwargs: Any) -> Any:
            type(self).tries += 1
            raise LLMResponseError("제한", status_code=429)

    monkeypatch.setattr(chat_handlers, "get_llm", lambda: Always())
    monkeypatch.setattr(chat_handlers, "RETRY_DELAY_SECONDS", 0.0)
    thread_of(send("공용", "질문"))
    env.run()
    t = chat_handlers.CHATS.get("chat-1")
    assert Always.tries == 2  # 한 번만 다시 묻는다
    # 다시 물어도 제한이면 1~2분 뒤를 알려 준다 (대화는 다시 열린다)
    assert t.status == "talking" and t.messages[-1].kind == "error"
    assert t.messages[-1].text == chat_handlers.LIMITED_TEXT
    assert "1~2분" in chat_handlers.LIMITED_TEXT


def test_no_llm_configured_says_so(env: Spawned, monkeypatch: pytest.MonkeyPatch) -> None:
    class NoKey(MockLLMClient):
        async def structured(self, *args: Any, **kwargs: Any) -> Any:
            raise LLMNotConfiguredError("no model")

    monkeypatch.setattr(chat_handlers, "get_llm", lambda: NoKey())
    thread_of(send("공용", "질문"))
    env.run()
    last = chat_handlers.CHATS.get("chat-1").messages[-1]
    assert last.kind == "error" and last.text == chat_handlers.NO_LLM_TEXT


# ---------------------------------------------------------------- 끝내기와 요약
def test_close_saves_a_summary_then_llm_refines_it(env: Spawned, llm: MockLLMClient) -> None:
    thread_of(send("공용", "질문"))
    env.run()
    closed = thread_of(call("support_chat_close", {"thread_id": "chat-1", "viewer": "공용"}))
    assert closed["status"] == "closed" and closed["closed_at"]
    assert closed["summary"] == "공용 님과 고객지원 현황 1건을 이야기했어요."  # 코드 요약
    env.run()
    assert chat_handlers.CHATS.get("chat-1").summary == "문의 현황을 묻고 답을 들었어요."


def test_summary_falls_back_when_llm_fails(env: Spawned, monkeypatch: pytest.MonkeyPatch) -> None:
    thread_of(send("공용", "질문"))
    env.run()

    class Broken(MockLLMClient):
        async def structured(self, *args: Any, **kwargs: Any) -> Any:
            raise RuntimeError("down")

    monkeypatch.setattr(chat_handlers, "get_llm", lambda: Broken())
    thread_of(call("support_chat_close", {"thread_id": "chat-1", "viewer": "공용"}))
    env.run()
    assert (
        chat_handlers.CHATS.get("chat-1").summary == "공용 님과 고객지원 현황 1건을 이야기했어요."
    )


def test_who_may_close(env: Spawned, llm: MockLLMClient) -> None:
    thread_of(send("예진", "질문"))
    env.run()
    nope = call("support_chat_close", {"thread_id": "chat-1", "viewer": "병환", "role": "member"})
    assert nope["outcome"] == "forbidden"
    assert (
        call("support_chat_close", {"thread_id": "chat-9", "viewer": "병환"})["outcome"]
        == "not_found"
    )
    admin = thread_of(
        call("support_chat_close", {"thread_id": "chat-1", "viewer": "병환", "role": "admin"})
    )
    assert admin["status"] == "closed"
    assert admin["restricted"] is True and admin["messages"] == []  # 관리자도 요약만 본다
    # 예전 화면 API가 보내던 `staff`는 일반 사람(member)으로 읽어 막힘 없이 거절된다
    legacy = call("support_chat_close", {"thread_id": "chat-1", "viewer": "병환", "role": "staff"})
    assert legacy["outcome"] == "forbidden"


def test_closing_twice_returns_the_same_thread(env: Spawned, llm: MockLLMClient) -> None:
    thread_of(send("공용", "질문"))
    env.run()
    first = thread_of(call("support_chat_close", {"thread_id": "chat-1", "viewer": "공용"}))
    again = thread_of(call("support_chat_close", {"thread_id": "chat-1", "viewer": "공용"}))
    assert again["closed_at"] == first["closed_at"]


def test_idle_threads_close_when_read(
    env: Spawned, llm: MockLLMClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    thread_of(send("공용", "질문"))
    env.run()
    store = chat_handlers.CHATS
    old = (datetime.now(UTC) - timedelta(minutes=31)).isoformat(timespec="seconds")
    store._put(store.get("chat-1").model_copy(update={"updated_at": old.replace("+00:00", "Z")}))
    listed = call("support_chat_list", {"viewer": "공용"})["output"]
    assert listed["items"][0]["status"] == "closed" and listed["items"][0]["summary"]


def _age(thread_id: str, **delta: int) -> None:
    store = chat_handlers.CHATS
    old = (datetime.now(UTC) - timedelta(**delta)).isoformat(timespec="seconds")
    store._put(store.get(thread_id).model_copy(update={"updated_at": old.replace("+00:00", "Z")}))


def test_a_fresh_answer_in_progress_is_left_alone(env: Spawned, llm: MockLLMClient) -> None:
    thread_of(send("공용", "질문"))  # 답을 만드는 중
    _age("chat-1", minutes=2)
    assert (
        call("support_chat_list", {"viewer": "공용"})["output"]["items"][0]["status"] == "answering"
    )


def test_a_stuck_answer_is_reopened_with_an_error_line(env: Spawned, llm: MockLLMClient) -> None:
    """서버가 다시 켜지면 DB에 answering만 남는다. 이대로면 그 대화는 새 말을 영영 못 받는다."""
    thread_of(send("공용", "질문"))
    for lost in env:  # 답을 만들던 일이 사라졌다 (재시작)
        lost.close()
    env.clear()
    _age("chat-1", minutes=chat.STUCK_ANSWER_MINUTES + 1)
    item = call("support_chat_list", {"viewer": "공용"})["output"]["items"][0]
    assert item["status"] == "talking"
    assert item["messages"][-1]["kind"] == "error"
    assert item["messages"][-1]["text"] == chat_handlers.FAILED_TEXT
    again = thread_of(send("공용", "다시 질문", thread_id="chat-1"))
    assert again["status"] == "answering"  # 다시 말할 수 있다


def test_a_cancelled_answer_does_not_leave_the_thread_answering(
    env: Spawned, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Slow(MockLLMClient):
        async def structured(self, *args: Any, **kwargs: Any) -> Any:
            await asyncio.sleep(60)

    monkeypatch.setattr(chat_handlers, "get_llm", lambda: Slow())
    thread_of(send("공용", "질문"))

    async def cancel_midway() -> None:
        task = asyncio.create_task(env.pop(0))
        await asyncio.sleep(0.01)
        task.cancel()  # 서버가 꺼지며 답을 만들던 일이 취소된다
        await asyncio.gather(task, return_exceptions=True)

    asyncio.run(cancel_midway())
    t = chat_handlers.CHATS.get("chat-1")
    assert t.status == "talking" and t.messages[-1].kind == "error"


def test_an_answer_that_takes_too_long_becomes_an_error_line(
    env: Spawned, monkeypatch: pytest.MonkeyPatch
) -> None:
    class Slow(MockLLMClient):
        async def structured(self, *args: Any, **kwargs: Any) -> Any:
            await asyncio.sleep(60)

    monkeypatch.setattr(chat_handlers, "get_llm", lambda: Slow())
    monkeypatch.setattr(chat, "ANSWER_TIMEOUT_SECONDS", 0.05)
    thread_of(send("공용", "질문"))
    env.run()
    t = chat_handlers.CHATS.get("chat-1")
    assert t.status == "talking" and t.messages[-1].text == chat_handlers.FAILED_TEXT


def test_a_late_answer_is_not_added_to_a_closed_thread(env: Spawned, llm: MockLLMClient) -> None:
    thread_of(send("공용", "질문"))
    thread_of(call("support_chat_close", {"thread_id": "chat-1", "viewer": "공용"}))
    env.run()  # 닫은 뒤에야 답이 만들어진다
    t = chat_handlers.CHATS.get("chat-1")
    assert t.status == "closed" and [m.role for m in t.messages] == ["user"]


# 안전 관문을 기다리는 사이 다른 요청이 끼어든 경우 (확인 → 관문 → 저장 사이)
def _meddle_before_write(monkeypatch: pytest.MonkeyPatch, action: Any) -> None:
    real = handlers._write

    async def write(*args: Any, **kwargs: Any) -> Any:
        action()
        return await real(*args, **kwargs)

    monkeypatch.setattr(handlers, "_write", write)


def test_a_close_during_the_gate_does_not_reopen_the_thread(
    env: Spawned, llm: MockLLMClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    thread_of(send("공용", "질문"))
    env.run()
    _meddle_before_write(
        monkeypatch, lambda: chat_handlers._close(chat_handlers.CHATS.get("chat-1"))
    )
    refused = send("공용", "닫히는 사이에 보낸 말", thread_id="chat-1")
    assert refused["outcome"] == "invalid_state"
    t = chat_handlers.CHATS.get("chat-1")
    assert t.status == "closed" and len(t.messages) == 2  # 말도 저장되지 않았다


def test_two_quick_sends_start_only_one_answer(
    env: Spawned, llm: MockLLMClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    thread_of(send("공용", "첫 질문"))
    env.run()
    _meddle_before_write(  # 다른 요청이 먼저 저장해 answering이 됐다
        monkeypatch,
        lambda: chat_handlers.CHATS.add_message("chat-1", "user", "먼저 온 말", status="answering"),
    )
    second = send("공용", "거의 동시에 온 말", thread_id="chat-1")
    assert second["outcome"] == "invalid_state"
    assert [m.text for m in chat_handlers.CHATS.get("chat-1").messages][-1] == "먼저 온 말"


def test_the_idempotency_store_never_holds_chat_text(env: Spawned, llm: MockLLMClient) -> None:
    secret = "고객지원 비밀 질문 원문"
    thread_of(send("공용", secret))
    env.run()
    thread_of(call("support_chat_close", {"thread_id": "chat-1", "viewer": "공용"}))
    stored = json.dumps(list(handlers.STORE.results.values()), ensure_ascii=False)
    assert secret not in stored and "답변 대기는 3건이에요" not in stored
    assert '"id": "chat-1"' in stored  # 대화 번호와 상태만 남는다


def test_idle_minutes_can_be_changed(monkeypatch: pytest.MonkeyPatch) -> None:
    assert chat.idle_minutes() == 30
    monkeypatch.setenv(chat.IDLE_ENV, "5")
    assert chat.idle_minutes() == 5
    monkeypatch.setenv(chat.IDLE_ENV, "abc")
    assert chat.idle_minutes() == 30


# ---------------------------------------------------------------- 열람 권한
def _two_threads(env: Spawned) -> None:
    thread_of(send("예진", "예진의 비밀 질문"))
    thread_of(send("병환", "병환의 질문"))
    env.run()


def test_owner_sees_originals_and_others_get_the_restricted_summary(
    env: Spawned, llm: MockLLMClient
) -> None:
    _two_threads(env)
    thread_of(call("support_chat_close", {"thread_id": "chat-1", "viewer": "예진"}))
    env.run()
    items = {i["id"]: i for i in call("support_chat_list", {"viewer": "예진"})["output"]["items"]}
    mine, theirs = items["chat-1"], items["chat-2"]
    assert mine["messages"] and mine["restricted"] is False
    assert theirs["messages"] == [] and theirs["restricted"] is True
    assert theirs["who"] == "병환" and "병환의 질문" not in str(theirs)  # 원문은 어디에도 없다


def test_everyone_sees_summaries_and_only_admin_sees_originals_when_a_task_exists(
    env: Spawned, llm: MockLLMClient
) -> None:
    _two_threads(env)
    thread_of(call("support_chat_close", {"thread_id": "chat-1", "viewer": "예진"}))
    env.run()
    for role in ("member", "admin"):  # 로그인한 사람 모두 요약을 본다 (예진님 2026-11-09)
        items = call("support_chat_list", {"viewer": "담당자", "role": role})["output"]["items"]
        assert len(items) == 2
        for item in items:
            assert item["restricted"] is True and item["messages"] == []
            assert "비밀" not in item["title"]  # 제목도 첫 말이 아니라 요약에서 만든다
        closed = next(i for i in items if i["id"] == "chat-1")
        assert closed["summary"] == "문의 현황을 묻고 답을 들었어요."
    # 일이 생긴 대화는 원문까지 본다
    chat_handlers.CHATS.add_task("chat-2", "task-9")
    got = thread_of(
        call("support_chat_get", {"thread_id": "chat-2", "viewer": "관리자", "role": "admin"})
    )
    assert got["restricted"] is False and got["task_ids"] == ["task-9"] and got["messages"]
    assert got["task_id"] == "task-9"
    # 일이 생겨도 관리자가 아닌 사람은 원문을 못 본다 (예전 `staff`로 와도 같다)
    for role in ("member", "staff"):
        plain = thread_of(
            call("support_chat_get", {"thread_id": "chat-2", "viewer": "낯선사람", "role": role})
        )
        assert plain["restricted"] is True and plain["messages"] == []


def test_getting_someone_elses_thread_returns_the_restricted_view(
    env: Spawned, llm: MockLLMClient
) -> None:
    _two_threads(env)
    got = thread_of(call("support_chat_get", {"thread_id": "chat-1", "viewer": "낯선사람"}))
    assert got["restricted"] is True and got["messages"] == [] and got["who"] == "예진"
    assert "비밀" not in got["title"]
    assert (
        call("support_chat_get", {"thread_id": "chat-99", "viewer": "예진"})["outcome"]
        == "not_found"
    )
    unknown = thread_of(
        call("support_chat_get", {"thread_id": "chat-1"})
    )  # 보는 사람을 몰라도 요약만
    assert unknown["restricted"] is True and unknown["messages"] == []


def test_shared_name_means_everyone_is_the_owner(env: Spawned, llm: MockLLMClient) -> None:
    """토큰을 사람별로 나누기 전에는 모두 "공용"이라 서버가 가를 수 없다 (예진님 확인)."""
    thread_of(send("공용", "질문 하나"))
    env.run()
    items = call("support_chat_list", {"viewer": "공용"})["output"]["items"]
    assert items[0]["restricted"] is False and items[0]["messages"]


# ---------------------------------------------------------------- 규칙 함수·카드·저장
def test_access_rules_are_pure() -> None:
    store = ChatStore()
    t = store.create("예진", "질문", CTX)
    assert chat.access_of(t, "예진", "member") == "full"
    assert chat.access_of(t, "병환", "member") == "summary"
    assert chat.access_of(t, None, "member") == "summary"
    assert chat.access_of(t, "병환", "admin") == "summary"
    with_task = store.add_task(t.id, "task-1")
    assert chat.access_of(with_task, "병환", "admin") == "full"
    assert chat.view(t, "summary")["title"] == chat.NO_SUMMARY_TITLE


def test_title_is_short_and_one_line() -> None:
    assert chat.title_of("첫 줄\n둘째 줄  입니다") == "첫 줄 둘째 줄 입니다"
    assert len(chat.title_of("가" * 100)) == chat.MAX_TITLE_CHARS


def test_chat_skills_are_on_the_inquiry_card_and_gated() -> None:
    from ax_agent_template.services import SERVICES

    card = {s.id: s for s in load_card().skills}
    inquiry = next(s for s in SERVICES if s.id == "inquiry")
    for skill, grade in (
        ("support_chat_send", "A"),
        ("support_chat_close", "A"),
        ("support_chat_list", "read"),
        ("support_chat_get", "read"),
    ):
        assert skill in inquiry.skills
        assert str(card[skill].grade) == grade or card[skill].grade == grade
        assert list(card[skill].allowed_requesters) == ["dashboard-api"]


def test_threads_survive_a_restart_with_a_database(memory_repo: type) -> None:
    repo = memory_repo()
    first = PersistentChatStore(repo)
    t = first.create("예진", "질문", CTX)
    first.add_message(t.id, "user", "질문", status="answering")
    first.add_message(t.id, "assistant", "답", status="talking")
    first.close(t.id, "요약")
    again = PersistentChatStore(repo)
    got = again.get(t.id)
    assert got is not None and got.who == "예진" and got.status == "closed"
    assert [m.text for m in got.messages] == ["질문", "답"] and got.summary == "요약"
    assert again.create("병환", "다음", CTX).id == "chat-2"
