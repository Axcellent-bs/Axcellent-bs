"""대시보드 결정 기록 화면이 보여 줄 현황 조회 skill 3개(모두 읽기 전용, 관문을 거치지 않는다).

- `list_candidate_cards` · `list_cards` · `get_audit_timeline`
값은 저장된 그대로 돌려주고 새로 계산하거나 지어내지 않는다(진행률·완성도는 저장·계산된 값).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ValidationError

from archive_agent.results import completed, invalid_input, make_evidence
from archive_agent.services import Services
from ax_contracts.schemas import (
    AgentRequest,
    AgentResult,
    AuditEntry,
    GetAuditTimelineInput,
    GetAuditTimelineOutput,
    ListCandidateCardsInput,
    ListCandidateCardsOutput,
    ListCardsInput,
    ListCardsOutput,
)


def _parse_input[T: BaseModel](model: type[T], request: AgentRequest) -> T | AgentResult:
    try:
        return model.model_validate(request.input)
    except ValidationError as exc:
        return invalid_input(request, exc)


def _done(
    svc: Services, request: AgentRequest, output: BaseModel, summary: str, refs: list[str]
) -> AgentResult:
    return completed(
        request,
        output,
        outcome="listed",
        evidence=make_evidence(svc.clock, summary, refs),
    )


async def list_candidate_cards(svc: Services, request: AgentRequest) -> AgentResult:
    params = _parse_input(ListCandidateCardsInput, request)
    if isinstance(params, AgentResult):
        return params
    cards = await svc.memory.list_cards(area=params.area, states=["candidate"])
    output = ListCandidateCardsOutput(cards=cards, collected_at=svc.clock())
    return _done(
        svc,
        request,
        output,
        f"사람 확인을 기다리는 후보 카드 {len(cards)}장",
        [c.card_id for c in cards],
    )


async def list_cards(svc: Services, request: AgentRequest) -> AgentResult:
    """후보가 아닌 확정 카드를 최근 순으로 돌려준다. 사건 번호를 주면 그 사건에서 만든 카드만."""
    params = _parse_input(ListCardsInput, request)
    if isinstance(params, AgentResult):
        return params
    found = await svc.memory.list_cards(area=params.area, states=["active"])
    cards = [
        c
        for c in found
        if c.kind != "request"  # 기획·개발 요청 카드는 결정 카드가 아니다
        and (params.context_id is None or c.context_id == params.context_id)
    ]
    cards.sort(key=lambda c: (c.created_at, c.card_id), reverse=True)
    cards = cards[: params.limit]
    output = ListCardsOutput(cards=cards, collected_at=svc.clock())
    return _done(
        svc, request, output, f"확정된 결정 카드 {len(cards)}장", [c.card_id for c in cards]
    )


async def get_audit_timeline(svc: Services, request: AgentRequest) -> AgentResult:
    params = _parse_input(GetAuditTimelineInput, request)
    if isinstance(params, AgentResult):
        return params
    entries: list[dict[str, Any]] = svc.audit.timeline(params.context_id)
    output = GetAuditTimelineOutput(
        context_id=params.context_id,
        entries=[
            AuditEntry(
                seq=e["seq"],
                at=datetime.fromisoformat(e["at"]),
                kind=e["kind"],
                payload=e["payload"],
            )
            for e in entries
        ],
        collected_at=svc.clock(),
    )
    summary = (
        f"감사 기록 {len(entries)}건"
        if entries
        else f"{params.context_id}에 대한 감사 기록 없음: 확인할 수 없음"
    )
    return _done(svc, request, output, summary, [params.context_id])
