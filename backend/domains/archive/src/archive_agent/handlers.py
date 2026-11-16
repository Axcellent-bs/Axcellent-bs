"""아카이브 skill 핸들러. 기능마다 모듈이 있고 여기서는 부품 묶음(`Services`)과 연결만 한다.

새 skill을 만들 때는 모듈에 `async def run(svc, request)`를 만들고 여기 `HANDLERS`에 등록한다.
상태를 바꾸는 일은 모두 `svc.gate.execute()`를 거치고, 외부 입력(`request.untrusted`)은 지시로
해석하지 않는다.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable

from archive_agent import decision_basis, planning_questions
from archive_agent.cards import decision as card_decision
from archive_agent.cards import merge
from archive_agent.cards import retire as card_retire
from archive_agent.cards import service as cards
from archive_agent.case_memory import service as case_memory
from archive_agent.clients.errors import UpstreamError, bind_meta
from archive_agent.fraud_similar import service as fraud_similar
from archive_agent.planning_check import service as planning_check
from archive_agent.results import failed
from archive_agent.services import get_services
from archive_agent.views import service as views
from ax_contracts.schemas import AgentRequest, AgentResult


async def record_case(request: AgentRequest) -> AgentResult:
    return await cards.record_case(get_services(), request)


async def check_plan(request: AgentRequest) -> AgentResult:
    return await planning_check.check_plan(get_services(), request)


Handler = Callable[[AgentRequest], Awaitable[AgentResult]]


def _guarded(fn: Handler) -> Handler:
    """요청 메타를 연동 클라이언트에 알리고, 다른 서비스가 실패하면 지어내지 않고 실패로 답한다."""

    async def run(request: AgentRequest) -> AgentResult:
        bind_meta(request.meta)
        try:
            return await fn(request)
        except UpstreamError as exc:
            return failed(
                request, "UPSTREAM_UNAVAILABLE", f"{exc.service} 연결 문제로 처리하지 못했다"
            )

    return run


def _with_services(fn):  # type: ignore[no-untyped-def]
    async def run(request: AgentRequest) -> AgentResult:
        return await fn(get_services(), request)

    return run


_HANDLERS = {
    "list_candidate_cards": _with_services(views.list_candidate_cards),
    "list_cards": _with_services(views.list_cards),
    "get_audit_timeline": _with_services(views.get_audit_timeline),
    "decide_candidate_card": _with_services(card_decision.decide_candidate_card),
    "retire_request_cards": _with_services(card_retire.retire_request_cards),
    "check_plan": check_plan,
    "answer_planning_question": _with_services(planning_questions.answer_planning_question),
    "find_similar_fraud_cards": _with_services(fraud_similar.find_similar_fraud_cards),
    "find_similar_cases": _with_services(case_memory.find_similar_cases),
    "record_resolution": _with_services(case_memory.record_resolution),
    "record_event_times": _with_services(case_memory.record_event_times),
    "list_duplicate_groups": _with_services(merge.list_duplicate_groups),
    "decide_duplicate_group": _with_services(merge.decide_duplicate_group),
    "restore_merged_card": _with_services(merge.restore_merged_card),
    "record_case": record_case,
    "find_similar_decisions": _with_services(decision_basis.find_similar_decisions),
}


HANDLERS = {name: _guarded(fn) for name, fn in _HANDLERS.items()}
