"""기획 AI 직접 대화 A2A skill (dashboard-api → planning). 화면은 `/api/dashboard/planning-chat`.

`planning_chat_send`·`_close`(A, 대화 기록만 쓴다) · `planning_chat_list`·`_get`(read).
말한 사람(`who`)은 dashboard-api가 사람 토큰에서만 채운다. 대화는 업무 상태(요청·기획서·제안)를
바꾸지 않아 `execute()`를 부르지 않는다(마케팅·고객지원 직접 대화와 같다).
거절은 FAILED + outcome(`turn.REFUSALS`의 키) + `{message}`.
"""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from ax_contracts.schemas import AgentRequest, AgentResult, Evidence, TaskStatus
from ax_contracts.schemas.chat import ChatRemoveOutput
from ax_contracts.schemas.planning import (
    PlanningChatListInput,
    PlanningChatRef,
    PlanningChatSendInput,
)
from ax_workflow.a2a import _invalid
from planning_agent.chat.turn import ChatRefused, get_chat_service


def _done(request: AgentRequest, skill: str, output: dict[str, Any]) -> AgentResult:
    return AgentResult(
        meta=request.meta,
        status=TaskStatus.COMPLETED,
        outcome="ok",
        output=output,
        evidence=Evidence(data_summary=f"기획 AI 대화 {skill}", ref_ids=[]),
    )


def _refused(request: AgentRequest, exc: ChatRefused) -> AgentResult:
    return AgentResult(
        meta=request.meta,
        status=TaskStatus.FAILED,
        outcome=exc.outcome,
        output={"message": exc.message},
        evidence=Evidence(data_summary=f"기획 AI 대화 거절: {exc.outcome}", ref_ids=[]),
    )


async def planning_chat_send(request: AgentRequest) -> AgentResult:
    try:
        inp = PlanningChatSendInput.model_validate(request.input)
    except ValidationError as exc:
        return _invalid(request, exc)
    try:
        thread = get_chat_service().send(inp.who, inp.thread_id, inp.message)
    except ChatRefused as exc:
        return _refused(request, exc)
    return _done(request, "planning_chat_send", {"thread": thread})


async def planning_chat_list(request: AgentRequest) -> AgentResult:
    try:
        inp = PlanningChatListInput.model_validate(request.input)
    except ValidationError as exc:
        return _invalid(request, exc)
    return _done(
        request, "planning_chat_list", {"threads": get_chat_service().list(inp.who, inp.limit)}
    )


async def planning_chat_get(request: AgentRequest) -> AgentResult:
    try:
        inp = PlanningChatRef.model_validate(request.input)
    except ValidationError as exc:
        return _invalid(request, exc)
    try:
        thread = get_chat_service().get(inp.who, inp.thread_id)
    except ChatRefused as exc:
        return _refused(request, exc)
    return _done(request, "planning_chat_get", {"thread": thread})


async def planning_chat_close(request: AgentRequest) -> AgentResult:
    try:
        inp = PlanningChatRef.model_validate(request.input)
    except ValidationError as exc:
        return _invalid(request, exc)
    try:
        thread = get_chat_service().close(inp.who, inp.thread_id)
    except ChatRefused as exc:
        return _refused(request, exc)
    return _done(request, "planning_chat_close", {"thread": thread})


async def planning_chat_remove(request: AgentRequest) -> AgentResult:
    try:
        inp = PlanningChatRef.model_validate(request.input)
    except ValidationError as exc:
        return _invalid(request, exc)
    try:
        thread_id = get_chat_service().remove(inp.who, inp.thread_id)
    except ChatRefused as exc:
        return _refused(request, exc)
    out = ChatRemoveOutput(thread_id=thread_id, removed=True)
    return _done(request, "planning_chat_remove", out.model_dump(mode="json"))


CHAT_HANDLERS = {
    "planning_chat_send": planning_chat_send,
    "planning_chat_list": planning_chat_list,
    "planning_chat_get": planning_chat_get,
    "planning_chat_close": planning_chat_close,
    "planning_chat_remove": planning_chat_remove,
}
