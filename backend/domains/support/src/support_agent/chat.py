"""고객지원 AI와 사람의 직접 대화 (예진님 요청 2026-11-05, 개발 AI `/dev-chat` 모양).

사람이 고객지원 AI에게 문의·클레임·후기 현황을 묻고 설명을 듣는 대화다.
- 대화로는 조회와 설명만 한다. 상태를 바꾸는 일은 하지 않는다 (그런 일은 기존처럼 `execute()`와
  안전 관문을 거치는 skill로만 한다). 대화 때문에 실제 일이 생기면 `task_ids`에 id를 남긴다
  (이번 범위에서는 대화로 일이 생기지 않아 비어 있다).
- 대화마다 말한 사람(`who`, 사람 토큰 이름)을 저장한다. 지금은 모든 요청이 같은 사람("공용")이다.
- 대화가 끝나면(사람이 닫거나 30분 동안 말이 없으면) 한두 줄 요약(`summary`)을 저장한다.

열람 권한 (`access_of`, 인사 AI 직접 대화와 같은 규칙):
- 본인: 자기 대화 원문 전부.
- 다른 사람 대화: `messages: []` + `summary` + `restricted: true`만. 다른 사람 대화에는 쓸 수 없다.
- 담당(고객지원 담당자)·관리자: 다른 사람 대화도 일이 생긴 대화(`task_ids`)는 원문까지 본다.

서버 메모리가 기본이고 `SUPPORT_PG_DSN`이 있으면 `persistence`가 DB에도 쓴다.
"""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from pydantic import Field

from ax_contracts.schemas.common import TENANT_ID_DEFAULT, AxModel
from support_agent.store import now_iso

ThreadStatus = Literal["talking", "answering", "closed"]
Role = Literal["member", "staff", "admin"]  # 보는 사람의 자격 (화면 API가 토큰으로 정한다)
Access = Literal["full", "summary"]

MAX_MESSAGE_CHARS = 4000
MAX_HISTORY = 20  # LLM에 보낼 최근 메시지 수
MAX_REPLY_CHARS = 2000
MAX_SUMMARY_CHARS = 120
MAX_TITLE_CHARS = 30
MAX_LISTED = 100
DEFAULT_IDLE_MINUTES = 30
IDLE_ENV = "SUPPORT_CHAT_IDLE_MINUTES"
NO_SUMMARY_TITLE = "(요약 없음)"
ANSWER_TIMEOUT_SECONDS = 120.0  # AI 답 하나를 기다리는 한도 (넘으면 실패 한 줄을 남긴다)
STUCK_ANSWER_MINUTES = 5  # 이 시간이 지나도 답이 없는 answering은 멈춘 것으로 본다 (재시작·취소)


class ChatMessage(AxModel):
    id: str  # m1, m2 … (대화 안에서)
    role: Literal["user", "assistant"]
    text: str
    at: str
    kind: Literal["text", "error"] = "text"
    task_id: str | None = None  # 이 말 때문에 생긴 일 (이번 범위에서는 늘 비어 있다)


class ChatThread(AxModel):
    tenant_id: str = TENANT_ID_DEFAULT
    context_id: str  # 대화를 연 요청의 사건 번호 (화면 호출은 `ctx_ui_…`)
    id: str  # chat-1 …
    title: str
    status: ThreadStatus = "talking"
    messages: list[ChatMessage] = Field(default_factory=list)
    who: str  # 대화를 연 사람 (사람 토큰 이름, 본문 값이 아니다)
    summary: str | None = None  # 끝난 대화의 한두 줄 요약
    task_ids: list[str] = Field(default_factory=list)  # 대화 때문에 생긴 일 id
    created_at: str
    updated_at: str
    closed_at: str | None = None
    seq: int


def idle_minutes() -> int:
    raw = os.environ.get(IDLE_ENV, "").strip()
    return int(raw) if raw.isdigit() and int(raw) > 0 else DEFAULT_IDLE_MINUTES


def title_of(text: str) -> str:
    """첫 말의 앞부분을 제목으로 쓴다 (코드가 만든다)."""
    one_line = " ".join(text.split())
    return one_line if len(one_line) <= MAX_TITLE_CHARS else one_line[: MAX_TITLE_CHARS - 1] + "…"


def fallback_summary(thread: ChatThread) -> str:
    """LLM이 요약하지 못했을 때의 한 줄 (코드가 만든다). 고객 원문은 싣지 않는다."""
    asked = sum(1 for m in thread.messages if m.role == "user")
    return f"{thread.who} 님과 고객지원 현황 {asked}건을 이야기했어요."[:MAX_SUMMARY_CHARS]


def access_of(thread: ChatThread, viewer: str | None, role: Role) -> Access:
    """이 사람이 이 대화를 어디까지 보나.

    본인은 원문 전부. 본인이 아니면 요약만 보고, 담당·관리자는 일이 생긴 대화(`task_ids`)만 원문까지
    본다 (인사 AI 직접 대화와 같은 규칙, 예진님 2026-11-05)."""
    if viewer is not None and viewer == thread.who:
        return "full"
    if role in ("staff", "admin") and thread.task_ids:
        return "full"
    return "summary"


def view(thread: ChatThread, access: Access) -> dict[str, Any]:
    """화면에 줄 대화. `summary` 접근은 원문(제목·메시지)을 빼고 요약만 싣는다(`restricted`)."""
    data = thread.model_dump(mode="json", exclude={"tenant_id", "context_id", "seq"})
    data["restricted"] = access != "full"
    data["task_id"] = thread.task_ids[0] if thread.task_ids else None  # 개발 AI 모양 (첫 일)
    if access == "summary":
        data["messages"] = []
        data["title"] = title_of(thread.summary) if thread.summary else NO_SUMMARY_TITLE
    return data


def is_idle(thread: ChatThread, now: datetime | None = None) -> bool:
    """말하던 중(talking)인데 30분(기본) 넘게 아무 말이 없다. 답을 만드는 중(answering)은 아니다."""
    if thread.status != "talking":
        return False
    last = datetime.fromisoformat(thread.updated_at.replace("Z", "+00:00"))
    return (now or datetime.now(UTC)) - last >= timedelta(minutes=idle_minutes())


def is_stuck(thread: ChatThread, now: datetime | None = None) -> bool:
    """답을 만드는 중(answering)인데 5분 넘게 답이 없다.

    서버가 다시 켜졌거나 답을 만들던 일이 중간에 멈추면 DB에 `answering`만 남는다. 이대로 두면
    그 대화는 새 말을 영영 받지 못한다."""
    if thread.status != "answering":
        return False
    last = datetime.fromisoformat(thread.updated_at.replace("Z", "+00:00"))
    return (now or datetime.now(UTC)) - last >= timedelta(minutes=STUCK_ANSWER_MINUTES)


class ChatStore:
    def __init__(self) -> None:
        self._threads: dict[str, ChatThread] = {}

    def _put(self, thread: ChatThread) -> ChatThread:
        """저장 지점. 영속 저장소가 여기서 DB에도 쓴다."""
        self._threads[thread.id] = thread
        return thread

    def get(self, thread_id: str) -> ChatThread | None:
        return self._threads.get(thread_id)

    def all(self) -> list[ChatThread]:
        return sorted(self._threads.values(), key=lambda t: (t.updated_at, t.seq), reverse=True)

    def create(self, who: str, first_text: str, context_id: str) -> ChatThread:
        seq = max((t.seq for t in self._threads.values()), default=0) + 1
        now = now_iso()
        thread = ChatThread(
            context_id=context_id,
            id=f"chat-{seq}",
            title=title_of(first_text),
            who=who,
            created_at=now,
            updated_at=now,
            seq=seq,
        )
        return self._put(thread)

    def add_message(
        self,
        thread_id: str,
        role: Literal["user", "assistant"],
        text: str,
        *,
        kind: Literal["text", "error"] = "text",
        status: ThreadStatus | None = None,
    ) -> ChatThread:
        thread = self._threads[thread_id]
        message = ChatMessage(
            id=f"m{len(thread.messages) + 1}", role=role, text=text, at=now_iso(), kind=kind
        )
        return self._put(
            thread.model_copy(
                update={
                    "messages": [*thread.messages, message],
                    "status": status or thread.status,
                    "updated_at": message.at,
                }
            )
        )

    def set_status(self, thread_id: str, status: ThreadStatus) -> ChatThread:
        thread = self._threads[thread_id]
        return self._put(thread.model_copy(update={"status": status, "updated_at": now_iso()}))

    def close(self, thread_id: str, summary: str) -> ChatThread:
        thread = self._threads[thread_id]
        now = now_iso()
        return self._put(
            thread.model_copy(
                update={"status": "closed", "summary": summary, "closed_at": now, "updated_at": now}
            )
        )

    def set_summary(self, thread_id: str, summary: str) -> ChatThread:
        """닫힌 대화의 요약을 LLM이 만든 것으로 바꾼다 (닫은 시각은 그대로)."""
        thread = self._threads[thread_id]
        return self._put(thread.model_copy(update={"summary": summary}))

    def add_task(self, thread_id: str, task_id: str) -> ChatThread:
        thread = self._threads[thread_id]
        if task_id in thread.task_ids:
            return thread
        return self._put(thread.model_copy(update={"task_ids": [*thread.task_ids, task_id]}))
