"""`retire_request_cards`: 부서가 기획에 보낸 요청을 거둬들일 때, 그 요청에서 나온 카드를 내린다.

기획이 요청을 지우면(`withdraw_ticket`) 그 요청과 기획서 초안은 사라지는데, 인사 AI 결정 기록에는
그 요청에서 나온 카드가 그대로 남아 있었다. 기획이 이 skill을 불러 카드를 `retired`로 내린다.

- 대상: `context_id`가 같은 카드, 근거 참조(`source_refs`)에 요청 번호나 `spec:<기획서 번호>`가 있는
  카드. 후보(`candidate`)와 확정(`active`) 카드를 모두 내린다. 업무 결정·요청 카드만 대상이고
  다른 종류(장애 사례 등)는 건드리지 않는다.
- 지우지 않는다. 상태만 `retired`로 바꿔 내력을 남기고, 판정 근거로는 쓰이지 않는다.
- 이미 내려간 카드(`retired`·`superseded`)는 건드리지 않는다. 같은 요청을 다시 불러도 같은 결과다.
- 카드마다 안전 관문(`memory.card_write`)을 거친다. 일부가 막히면 내린 것까지 보고하고 막힌
  카드는 `held`로 알린다(지어내지 않는다).
"""

from __future__ import annotations

from pydantic import ValidationError

from ax_contracts.schemas import (
    AgentRequest,
    AgentResult,
    Card,
    RetireRequestCardsInput,
    RetireRequestCardsOutput,
)
from hr_agent.clients.memory import card_state
from hr_agent.results import (
    completed,
    gate_blocked,
    invalid_input,
    make_evidence,
    with_key,
)
from hr_agent.services import Services

# 요청에서 나오는 카드 종류. 장애 사례(incident·case)·공지·사기 카드는 건드리지 않는다
KINDS = ("decision", "request")
# 내릴 수 있는 상태. 이미 내려간 카드(retired·superseded)는 건드리지 않는다
LIVE_STATES = ("candidate", "active")


def matches(card: Card, params: RetireRequestCardsInput) -> bool:
    """이 요청에서 나온 카드인가: 같은 사건이거나 근거 참조에 요청·기획서 번호가 있다."""
    if card.kind not in KINDS:
        return False
    if card.context_id == params.context_id:
        return True
    refs = set(card.source_refs)
    if params.request_id in refs or f"request:{params.request_id}" in refs:
        return True
    return params.spec_id is not None and f"spec:{params.spec_id}" in refs


async def retire_request_cards(svc: Services, request: AgentRequest) -> AgentResult:
    try:
        params = RetireRequestCardsInput.model_validate(request.input)
    except ValidationError as exc:
        return invalid_input(request, exc)
    if not request.meta.idempotency_key:
        return invalid_input(request, "상태를 바꾸는 요청에는 idempotency_key가 필요하다")

    found = [
        c
        for c in await svc.memory.list_cards(states=[*LIVE_STATES, "retired"])
        if matches(c, params)
    ]
    live = [c for c in found if card_state(c) in LIVE_STATES]
    already = [c.card_id for c in found if card_state(c) == "retired"]

    retired: list[str] = []
    held = None
    for card in live:
        evidence = make_evidence(
            svc.clock, f"요청 {params.request_id} 거둠: 카드 내림 ({card.title})", [card.card_id]
        )
        target = f"{card.card_id}#retire_request"
        decision = await svc.gate.execute(
            "memory.card_write",
            {"card_id": card.card_id, "summary": f"요청 거둠: {params.reason}", "target": target},
            with_key(request.meta, f"memory.card_write:{target}"),
            evidence,
        )
        if decision.status != "executed":
            held = decision
            continue
        await svc.memory.set_status(card.card_id, "retired")
        retired.append(card.card_id)
        svc.audit.record(
            request.meta.context_id,
            "card.retired_by_request",
            {
                "card_id": card.card_id,
                "request_id": params.request_id,
                "spec_id": params.spec_id,
                "reason": params.reason,
            },
        )

    if held is not None and not retired:
        return gate_blocked(request, held)  # 하나도 못 내렸으면 관문 결과를 그대로 알린다
    output = RetireRequestCardsOutput(retired_card_ids=retired, already_retired_card_ids=already)
    outcome = "retired" if retired else ("duplicate" if already else "none")
    if held is not None:
        outcome = "partial"  # 일부는 관문에서 보류됐다. 내린 카드만 목록에 있다
    return completed(
        request,
        output,
        outcome=outcome,
        evidence=make_evidence(
            svc.clock,
            f"요청 {params.request_id}에서 나온 카드 {len(retired)}장을 내렸다",
            retired,
        ),
        decision_reason=params.reason,
    )
