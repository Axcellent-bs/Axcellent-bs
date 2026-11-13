"""인사 AI 직접 대화 REST. 개발 AI의 `/dev-chat`과 같은 모양이다.
(`{data}` 봉투, 대화 = id·title·status·messages[]·who·시각)

- `POST /api/hr/chat` `{thread_id?, message}` → 바뀐 대화 전체. AI 답 하나까지 기다린다.
- `GET /api/hr/chat` 목록, `GET /api/hr/chat/{id}` 하나.
- `POST /api/hr/chat/{id}/remove` 본인 대화를 숨긴다(지우지 않고 removed_at·removed_by 기록).
  남의 대화 403, 없거나 이미 숨겼으면 404.
- 모든 요청에 사람 토큰이 필요하다(인사 데이터를 다루는 대화라 읽기도 막는다).
- 열람: 대화를 연 사람(who)은 원문 전부.
  다른 사람 대화는 `messages: []`와 요약만 준다(`restricted: true`).
  지금은 화면이 모든 요청을 같은 사람 토큰("공용")으로 보내
  모두 같은 사람이 되므로 실제로는 막히지 않는다.
  사람마다 토큰을 나누면 서버에서 그대로 걸린다.
"""

from __future__ import annotations

import hmac
import os
from typing import Any

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse

from ax_agent_template.config import is_mock
from hr_agent.chat import threads, turn
from hr_agent.services import get_services


class _AuthError(Exception):
    def __init__(self, status: int, message: str) -> None:
        super().__init__(message)
        self.status, self.message = status, message


def human_name(request: Request) -> str:
    """사람 토큰이면 그 사람 이름. 없으면 401, 사람 토큰이 아니면 403.

    토큰이 설정 안 됐고 목 모드도 아니면 모두 거부한다.
    """
    scheme, _, token = (request.headers.get("authorization") or "").partition(" ")
    token = token.strip()
    if scheme.lower() != "bearer" or not token:
        raise _AuthError(401, "토큰이 필요하다 (Authorization: Bearer 사람 토큰)")
    for item in os.getenv("SAFETY_HUMAN_TOKENS", "").split(","):
        name, sep, known = item.strip().partition(":")
        if sep and name and known and hmac.compare_digest(known, token):
            return name
    if is_mock("hr") and token.startswith("human:") and len(token) > len("human:"):
        return token.removeprefix("human:")
    raise _AuthError(403, "사람 토큰이 아니다")


def _ok(data: Any) -> dict:
    return {"data": data, "meta": {}}


def _err(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": {"code": code, "message": message}})


def _auth_err(exc: _AuthError) -> JSONResponse:
    return _err(exc.status, "UNAUTHORIZED" if exc.status == 401 else "FORBIDDEN", exc.message)


def _visible(th: dict[str, Any], who: str) -> dict[str, Any]:
    if not th["who"] or th["who"] == who:
        return {**th, "restricted": False, "message_count": len(th["messages"])}
    return {**th, "messages": [], "restricted": True, "message_count": len(th["messages"])}


def create_chat_router() -> APIRouter:
    router = APIRouter(prefix="/api/hr", tags=["hr-chat"])

    @router.get("/chat", response_model=None)
    def list_chats(request: Request) -> dict | JSONResponse:
        try:
            who = human_name(request)
        except _AuthError as exc:
            return _auth_err(exc)
        return _ok([_visible(t, who) for t in threads.list_all(get_services())])

    @router.get("/chat/{thread_id}", response_model=None)
    def get_chat(thread_id: str, request: Request) -> dict | JSONResponse:
        try:
            who = human_name(request)
        except _AuthError as exc:
            return _auth_err(exc)
        th = threads.get(get_services(), thread_id)
        if th is None:
            return _err(404, "NOT_FOUND", f"대화 {thread_id}가 없다")
        return _ok(_visible(th, who))

    @router.post("/chat", response_model=None)
    async def post_chat(request: Request) -> dict | JSONResponse:
        try:
            who = human_name(request)
        except _AuthError as exc:
            return _auth_err(exc)
        try:
            body = await request.json()
        except ValueError:
            body = None
        if not isinstance(body, dict):
            return _err(400, "INVALID_INPUT", "본문은 JSON 객체여야 한다")
        thread_id, message = body.get("thread_id"), body.get("message")
        if thread_id is not None and (not isinstance(thread_id, str) or not thread_id):
            return _err(400, "INVALID_INPUT", "thread_id는 문자열이어야 한다")
        if (
            not isinstance(message, str)
            or not message.strip()
            or len(message) > turn.MAX_MESSAGE_CHARS
        ):
            return _err(
                400, "INVALID_INPUT", f"message는 1~{turn.MAX_MESSAGE_CHARS}자 문자열이어야 한다"
            )
        try:
            th = await turn.turn(get_services(), thread_id, message.strip(), who)
        except turn.ChatRefused as exc:
            return _err(exc.status, exc.code, exc.message)
        return _ok(_visible(th, who))

    @router.post("/chat/{thread_id}/remove", response_model=None)
    def remove_chat(thread_id: str, request: Request) -> dict | JSONResponse:
        """본인 대화를 숨긴다 (POST만). 남의 대화는 403, 없거나 이미 숨겼으면 404."""
        try:
            who = human_name(request)
        except _AuthError as exc:
            return _auth_err(exc)
        svc = get_services()
        th = threads.get(svc, thread_id)
        if th is None:
            return _err(404, "NOT_FOUND", f"대화 {thread_id}가 없다")
        if th["who"] and th["who"] != who:
            return _err(403, "FORBIDDEN", "본인 대화만 삭제할 수 있어요")
        if not threads.remove(svc, thread_id, who):
            return _err(404, "NOT_FOUND", f"대화 {thread_id}가 없다")
        return _ok({"thread_id": thread_id, "removed": True})

    return router
