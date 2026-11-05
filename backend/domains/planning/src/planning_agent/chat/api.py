"""기획 AI 직접 대화 REST. 인사 AI `/api/hr/chat`(#662)·개발 AI `/dev-chat`과 같은 모양이다.
(`{data}` 봉투, 대화 = id·title·status·messages[]·who·summary·task_id·시각)

- `POST /api/planning/chat` `{thread_id?, message}` → 바뀐 대화 전체. AI 답 하나까지 기다린다.
- `GET /api/planning/chat` 목록, `GET /api/planning/chat/{id}` 하나.
- 모든 요청에 사람 토큰이 필요하다 (`ax_workflow.auth.check_human`, 기획 쓰기와 같은 토큰).
- 열람: 대화를 연 사람(who)은 원문 전부. 다른 사람 대화는 `messages: []`와 요약만 준다
  (`restricted: true`). 다른 사람 대화에 쓰면 403. 지금은 화면이 모든 요청을 같은 사람 토큰
  ("공용")으로 보내 실제로는 막히지 않고, 사람마다 토큰을 나누면 서버에서 그대로 걸린다.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from fastapi import APIRouter, Header, Request
from fastapi.responses import JSONResponse

from ax_workflow.auth import check_human
from planning_agent.chat import threads, turn


def _ok(data: Any) -> dict:
    return {"data": data, "meta": {}}


def _err(status: int, code: str, message: str) -> JSONResponse:
    return JSONResponse(status_code=status, content={"error": {"code": code, "message": message}})


def _visible(th: dict[str, Any], who: str) -> dict[str, Any]:
    if not th["who"] or th["who"] == who:
        return {**th, "restricted": False, "message_count": len(th["messages"])}
    return {**th, "messages": [], "restricted": True, "message_count": len(th["messages"])}


_llm_cache: dict[str, Any] = {}


def _default_llm() -> Any:
    if "llm" not in _llm_cache:
        _llm_cache["llm"] = turn.default_llm()
    return _llm_cache["llm"]


def create_chat_router(
    get_state: Callable[[], Any], get_llm: Callable[[], Any] = _default_llm
) -> APIRouter:
    """`get_state`는 기획 상태(`PlanningService.state`)를 돌려준다. 읽기만 한다."""
    router = APIRouter(prefix="/api/planning", tags=["planning-chat"])

    @router.get("/chat", response_model=None)
    def list_chats(authorization: str | None = Header(default=None)) -> dict:
        who = check_human(authorization)
        return _ok([_visible(t, who) for t in threads.get_store().list_all()])

    @router.get("/chat/{thread_id}", response_model=None)
    def get_chat(
        thread_id: str, authorization: str | None = Header(default=None)
    ) -> dict | JSONResponse:
        who = check_human(authorization)
        th = threads.get_store().get(thread_id)
        if th is None:
            return _err(404, "NOT_FOUND", f"대화 {thread_id}가 없다")
        return _ok(_visible(th, who))

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
        limit = turn.MAX_MESSAGE_CHARS
        if not isinstance(message, str) or not message.strip() or len(message) > limit:
            return _err(400, "INVALID_INPUT", f"message는 1~{limit}자 문자열이어야 한다")
        try:
            th = await turn.turn(
                threads.get_store(), get_state(), get_llm(), thread_id, message.strip(), who
            )
        except turn.ChatRefused as exc:
            return _err(exc.status, exc.code, exc.message)
        return _ok(_visible(th, who))

    return router
