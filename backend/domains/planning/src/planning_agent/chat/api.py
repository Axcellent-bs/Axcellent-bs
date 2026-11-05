"""기획 AI 직접 대화 REST (기획 서버에 직접). 화면은 대시보드 `/api/dashboard/planning-chat`
(→ A2A `planning_chat_*`, `chat/a2a.py`)을 쓰고, 이 경로는 같은 서비스를 그대로 연다.
(`{data}` 봉투, 대화 = id·title·status·messages[]·who·summary·task_id·restricted·message_count·시각)

- `POST /api/planning/chat` `{thread_id?, message}` → 말을 저장한 대화(`status: answering`).
  답은 뒤에서 붙는다 → `GET /api/planning/chat/{id}`로 다시 읽는다.
- `GET /api/planning/chat` 목록, `GET /api/planning/chat/{id}` 하나,
  `POST /api/planning/chat/{id}/close` 끝내기.
- 모든 요청에 사람 토큰이 필요하다 (`ax_workflow.auth.check_human`, 기획 쓰기와 같은 토큰).
"""

from __future__ import annotations

from collections.abc import Callable

from fastapi import APIRouter, Header, Request
from fastapi.responses import JSONResponse

from ax_workflow.auth import check_human
from planning_agent.chat.turn import MAX_MESSAGE_CHARS, ChatRefused, ChatService


def _ok(data: object) -> dict:
    return {"data": data, "meta": {}}


def _err(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": {"code": code, "message": message}})


def _refused(exc: ChatRefused) -> JSONResponse:
    return _err(exc.status, exc.code, exc.message)


def create_chat_router(get_chat: Callable[[], ChatService]) -> APIRouter:
    router = APIRouter(prefix="/api/planning", tags=["planning-chat"])

    @router.get("/chat", response_model=None)
    def list_chats(authorization: str | None = Header(default=None)) -> dict:
        who = check_human(authorization)
        return _ok(get_chat().list(who))

    @router.get("/chat/{thread_id}", response_model=None)
    def get_chat_thread(
        thread_id: str, authorization: str | None = Header(default=None)
    ) -> dict | JSONResponse:
        who = check_human(authorization)
        try:
            return _ok(get_chat().get(who, thread_id))
        except ChatRefused as exc:
            return _refused(exc)

    @router.post("/chat", response_model=None)
    async def post_chat(
        request: Request, authorization: str | None = Header(default=None)
    ) -> dict | JSONResponse:
        who = check_human(authorization)
        try:
            body = await request.json()
        except ValueError:
            body = None
        if not isinstance(body, dict):
            return _err(400, "INVALID_INPUT", "본문은 JSON 객체여야 한다")
        thread_id, message = body.get("thread_id"), body.get("message")
        if thread_id is not None and (not isinstance(thread_id, str) or not thread_id):
            return _err(400, "INVALID_INPUT", "thread_id는 문자열이어야 한다")
        if not isinstance(message, str):
            return _err(400, "INVALID_INPUT", f"message는 1~{MAX_MESSAGE_CHARS}자 문자열이다")
        try:
            return _ok(get_chat().send(who, thread_id, message))
        except ChatRefused as exc:
            return _refused(exc)

    @router.post("/chat/{thread_id}/close", response_model=None)
    def close_chat(
        thread_id: str, authorization: str | None = Header(default=None)
    ) -> dict | JSONResponse:
        who = check_human(authorization)
        try:
            return _ok(get_chat().close(who, thread_id))
        except ChatRefused as exc:
            return _refused(exc)

    return router
