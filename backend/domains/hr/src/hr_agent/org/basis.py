"""`find_org_change_basis`: 발령 사유와 비슷한 과거 업무 결정 카드를 찾는다 (RAG, 조회 전용).

- 후보 풀은 확정된(후보가 아닌) 업무 결정 카드(`kind=decision`)뿐이다.
  장애 대응 카드·사례·공지와 대체되거나 폐기된 카드는 뺀다.
- 사유를 임베딩해 코사인 유사도로 찾는다(기준 `dense_min_cosine`).
  임베딩을 쓸 수 없으면 Dice로 대체한다.
- 기준을 넘는 카드가 없으면 후보를 비워 둔다(근거 카드 없음). 가까운 것을 억지로 내놓지 않는다.
- 과거에 기각된 안(`extra.outcome = "rejected"`)은 `rejected`로 표시해 사람이 알고 판단하게 한다.
- 찾기만 한다. 근거로 연결하는 것은 사람이 골라 `record_org_change`에
  `basis_card_id`로 보낼 때다.
"""

from __future__ import annotations

from pydantic import ValidationError

from ax_contracts.schemas import (
    AgentRequest,
    AgentResult,
    FindOrgChangeBasisInput,
    FindOrgChangeBasisOutput,
    OrgChangeBasisCandidate,
)
from hr_agent.case_memory.service import _dense, _summary
from hr_agent.clients.memory import card_state
from hr_agent.results import completed, invalid_input, make_evidence
from hr_agent.services import Services

KINDS = ["decision"]


async def find_org_change_basis(svc: Services, request: AgentRequest) -> AgentResult:
    try:
        params = FindOrgChangeBasisInput.model_validate(request.input)
    except ValidationError as exc:
        return invalid_input(request, exc)

    cards = [
        c
        for c in await svc.memory.list_cards(area=None, states=["active"])
        if c.kind in KINDS and card_state(c) == "active"
    ]
    found = await _dense(svc, params.reason, cards, params.limit)
    if found is not None:
        method = "dense"
        pairs = found
    else:
        method = "dice"
        allowed = {c.card_id for c in cards}
        hits = await svc.memory.search_cards(
            params.reason, area=None, kinds=KINDS, states=["active"], limit=500
        )
        pairs = [
            (h.card, h.score)
            for h in hits
            if h.card.card_id in allowed and h.score >= svc.settings.similarity_threshold
        ]
    pairs = pairs[: params.limit]

    candidates = [
        OrgChangeBasisCandidate(
            card_id=c.card_id,
            title=c.title,
            summary=_summary(c),
            similarity=min(max(s, 0.0), 1.0),
            rejected=c.extra.get("outcome") == "rejected",
        )
        for c, s in pairs
    ]
    output = FindOrgChangeBasisOutput(
        candidates=candidates, method=method, collected_at=svc.clock()
    )
    summary = (
        f"근거 후보 결정 카드 {len(candidates)}장 ({method})"
        if candidates
        else "근거 후보 없음: 기준을 넘는 결정 카드가 없다"
    )
    # 사유 원문은 근거 기록에 남기지 않는다(카드 번호만)
    return completed(
        request,
        output,
        outcome="found" if candidates else "none",
        evidence=make_evidence(svc.clock, summary, [c.card_id for c in candidates]),
    )
