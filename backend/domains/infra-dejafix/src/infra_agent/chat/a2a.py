"""인프라 AI 직접 대화 A2A skill (dashboard-api → incident). 화면은 `/api/dashboard/infra-chat`.

`infra_chat_send`·`_close`(A, 대화 기록만 쓴다) · `infra_chat_list`·`_get`(read).
말한 사람(`who`)은 dashboard-api가 사람 토큰에서만 채운다. 대화는 업무 상태(배포·롤백·장애)를
바꾸지 않아 `execute()`를 부르지 않는다(기획·마케팅·고객지원 직접 대화와 같다).
거절은 FAILED + outcome(`turn.REFUSALS`의 키) + `{message}`.
"""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from ax_contracts.schemas import AgentRequest, AgentResult, Evidence, TaskStatus
from ax_contracts.schemas.infra import (
    InfraChatListInput,
    InfraChatRef,
    InfraChatSendInput,
)
from infra_agent.chat.turn import ChatRefused, get_chat_service


def _invalid(request: AgentRequest, exc: ValidationError) -> AgentResult:
    errors = [f"{'.'.join(map(str, e['loc']))}: {e['type']}" for e in exc.errors()]
    return AgentResult(
        meta=request.meta,
        status=TaskStatus.FAILED,
        outcome="invalid_input",
        output={"message": "입력이 올바르지 않다", "errors": errors},
        evidence=Evidence(data_summary="인프라 AI 대화 입력 오류", ref_ids=[]),
    )


def _done(request: AgentRequest, skill: str, output: dict[str, Any]) -> AgentResult:
    return AgentResult(
        meta=request.meta,
        status=TaskStatus.COMPLETED,
        outcome="ok",
        output=output,
        evidence=Evidence(data_summary=f"인프라 AI 대화 {skill}", ref_ids=[]),
    )


def _refused(request: AgentRequest, exc: ChatRefused) -> AgentResult:
    return AgentResult(
        meta=request.meta,
        status=TaskStatus.FAILED,
        outcome=exc.outcome,
        output={"message": exc.message},
        evidence=Evidence(data_summary=f"인프라 AI 대화 거절: {exc.outcome}", ref_ids=[]),
    )


async def infra_chat_send(request: AgentRequest) -> AgentResult:
    try:
        inp = InfraChatSendInput.model_validate(request.input)
    except ValidationError as exc:
        return _invalid(request, exc)
    try:
        thread = get_chat_service().send(inp.who, inp.thread_id, inp.message)
    except ChatRefused as exc:
        return _refused(request, exc)
    return _done(request, "infra_chat_send", {"thread": thread})


async def infra_chat_list(request: AgentRequest) -> AgentResult:
    try:
        inp = InfraChatListInput.model_validate(request.input)
    except ValidationError as exc:
        return _invalid(request, exc)
    return _done(
        request, "infra_chat_list", {"threads": get_chat_service().list(inp.who, inp.limit)}
    )


async def infra_chat_get(request: AgentRequest) -> AgentResult:
    try:
        inp = InfraChatRef.model_validate(request.input)
    except ValidationError as exc:
        return _invalid(request, exc)
    try:
        thread = get_chat_service().get(inp.who, inp.thread_id)
    except ChatRefused as exc:
        return _refused(request, exc)
    return _done(request, "infra_chat_get", {"thread": thread})


async def infra_chat_close(request: AgentRequest) -> AgentResult:
    try:
        inp = InfraChatRef.model_validate(request.input)
    except ValidationError as exc:
        return _invalid(request, exc)
    try:
        thread = get_chat_service().close(inp.who, inp.thread_id)
    except ChatRefused as exc:
        return _refused(request, exc)
    return _done(request, "infra_chat_close", {"thread": thread})


CHAT_HANDLERS = {
    "infra_chat_send": infra_chat_send,
    "infra_chat_list": infra_chat_list,
    "infra_chat_get": infra_chat_get,
    "infra_chat_close": infra_chat_close,
}
