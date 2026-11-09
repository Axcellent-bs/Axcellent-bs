"""재무 AI 직접 대화 A2A skill (dashboard-api → finance). 화면은 `/api/dashboard/finance-chat`.

`finance_chat_send`·`_close`(A, 대화 기록만 쓴다) · `finance_chat_list`·`_get`(read).
말한 사람(`who`)은 dashboard-api가 사람 토큰에서만 채운다. 대화는 업무 상태(장부·결산·정산)를
바꾸지 않아 `execute()`를 부르지 않는다(기획·마케팅·고객지원 직접 대화와 같다).
거절은 FAILED + outcome(`turn.REFUSALS`의 키) + `{message}`.
"""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from ax_contracts.schemas import AgentRequest, AgentResult, Evidence, TaskStatus
from ax_contracts.schemas.finance import (
    FinanceChatListInput,
    FinanceChatRef,
    FinanceChatSendInput,
)
from finance_agent.chat import facts
from finance_agent.chat.turn import ChatRefused, get_chat_service

facts.install()  # 새 장부가 모이면 최근 대화한 경우 사실 묶음을 미리 다시 만든다


def _invalid(request: AgentRequest, exc: ValidationError) -> AgentResult:
    errors = [f"{'.'.join(map(str, e['loc']))}: {e['type']}" for e in exc.errors()]
    return AgentResult(
        meta=request.meta,
        status=TaskStatus.FAILED,
        outcome="invalid_input",
        output={"message": "입력이 올바르지 않다", "errors": errors},
        evidence=Evidence(data_summary="재무 AI 대화 입력 오류", ref_ids=[]),
    )


def _done(request: AgentRequest, skill: str, output: dict[str, Any]) -> AgentResult:
    return AgentResult(
        meta=request.meta,
        status=TaskStatus.COMPLETED,
        outcome="ok",
        output=output,
        evidence=Evidence(data_summary=f"재무 AI 대화 {skill}", ref_ids=[]),
    )


def _refused(request: AgentRequest, exc: ChatRefused) -> AgentResult:
    return AgentResult(
        meta=request.meta,
        status=TaskStatus.FAILED,
        outcome=exc.outcome,
        output={"message": exc.message},
        evidence=Evidence(data_summary=f"재무 AI 대화 거절: {exc.outcome}", ref_ids=[]),
    )


async def finance_chat_send(request: AgentRequest) -> AgentResult:
    try:
        inp = FinanceChatSendInput.model_validate(request.input)
    except ValidationError as exc:
        return _invalid(request, exc)
    try:
        thread = get_chat_service().send(inp.who, inp.thread_id, inp.message)
    except ChatRefused as exc:
        return _refused(request, exc)
    return _done(request, "finance_chat_send", {"thread": thread})


async def finance_chat_list(request: AgentRequest) -> AgentResult:
    try:
        inp = FinanceChatListInput.model_validate(request.input)
    except ValidationError as exc:
        return _invalid(request, exc)
    facts.warm()  # 대화방을 열었다: 첫 질문 전에 사실 묶음을 미리 만든다
    return _done(
        request, "finance_chat_list", {"threads": get_chat_service().list(inp.who, inp.limit)}
    )


async def finance_chat_get(request: AgentRequest) -> AgentResult:
    try:
        inp = FinanceChatRef.model_validate(request.input)
    except ValidationError as exc:
        return _invalid(request, exc)
    try:
        thread = get_chat_service().get(inp.who, inp.thread_id)
    except ChatRefused as exc:
        return _refused(request, exc)
    facts.warm()
    return _done(request, "finance_chat_get", {"thread": thread})


async def finance_chat_close(request: AgentRequest) -> AgentResult:
    try:
        inp = FinanceChatRef.model_validate(request.input)
    except ValidationError as exc:
        return _invalid(request, exc)
    try:
        thread = get_chat_service().close(inp.who, inp.thread_id)
    except ChatRefused as exc:
        return _refused(request, exc)
    return _done(request, "finance_chat_close", {"thread": thread})


CHAT_HANDLERS = {
    "finance_chat_send": finance_chat_send,
    "finance_chat_list": finance_chat_list,
    "finance_chat_get": finance_chat_get,
    "finance_chat_close": finance_chat_close,
}
