"""사건 기억: 해결 내용을 카드에 채우고, 비슷한 과거 사건을 찾아 준다.

`record_resolution`과 `find_similar_cases` 두 skill이 있다.

- `record_resolution`: 사건(`context_id`)에서 만든 카드에 원인·조치·결과·소요 시간을 채운다.
  카드 본문에도 적어서(`[해결 기록]` 블록) 나중에 임베딩 검색이 해결 내용까지 보고 찾게 한다.
  값이 없으면 지어내지 않고 "확인할 수 없음"으로 적는다.
  상태를 바꾸므로 `memory.card_write` 관문을 거친다.
- `find_similar_cases`: 현재 사건을 설명하는 글로 비슷한 과거 사건·결정 카드를 찾는다(조회 전용).
  Dense로 찾고, 안 되면 Dice로 대체한다. 검증이 켜져 있으면 LLM이 관련 없는 후보를 거른다.
  카드가 가진 해결 내용을 그대로 돌려줄 뿐 해결 방법을 새로 만들지 않는다.
"""

from __future__ import annotations

import hashlib

from pydantic import ValidationError

from ax_contracts.schemas import (
    AgentRequest,
    AgentResult,
    Card,
    CaseResolution,
    FindSimilarCasesInput,
    FindSimilarCasesOutput,
    RecordResolutionInput,
    RecordResolutionOutput,
    SimilarCase,
)
from hr_agent.cards.builder import UNKNOWN
from hr_agent.results import (
    completed,
    gate_blocked,
    invalid_input,
    make_evidence,
    with_key,
)
from hr_agent.retrieval.verify import apply_verification
from hr_agent.services import Services

KINDS = ("incident", "decision", "case")  # 사건·결정·사례 카드만 찾는다(공지·정책·사기 카드는 제외)
BLOCK = "\n[해결 기록]"  # 카드 본문에서 해결 내용을 시작하는 표시
OUTCOME_KO = {"resolved": "해결됨", "workaround": "임시 조치", "unresolved": "미해결"}


def resolution_block(resolution: CaseResolution) -> str:
    minutes = (
        f"{resolution.minutes_to_resolve}분"
        if resolution.minutes_to_resolve is not None
        else UNKNOWN
    )
    return (
        f"{BLOCK}\n결과: {OUTCOME_KO[resolution.outcome]}\n"
        f"원인: {resolution.root_cause or UNKNOWN}\n"
        f"조치: {resolution.action or UNKNOWN}\n"
        f"소요 시간: {minutes}"
    )


def with_resolution(body: str, resolution: CaseResolution) -> str:
    """본문의 해결 기록 블록을 새 내용으로 바꾼다(처음이면 끝에 붙인다)."""
    base = body.split(BLOCK)[0]
    return f"{base}{resolution_block(resolution)}"


def resolution_of(card: Card) -> CaseResolution | None:
    raw = card.extra.get("resolution")
    if not isinstance(raw, dict):
        return None
    try:
        return CaseResolution.model_validate(raw)
    except ValidationError:
        return None


def _payload_hash(resolution: CaseResolution) -> str:
    data = resolution.model_dump_json(
        exclude={"resolved_at"}
    )  # 시각만 다른 같은 내용은 같은 기록이다
    return hashlib.sha256(data.encode("utf-8")).hexdigest()[:12]


async def record_resolution(svc: Services, request: AgentRequest) -> AgentResult:
    try:
        params = RecordResolutionInput.model_validate(request.input)
    except ValidationError as exc:
        return invalid_input(request, exc)
    if not request.meta.idempotency_key:
        return invalid_input(request, "상태를 바꾸는 요청에는 idempotency_key가 필요하다")

    cards = [
        c
        for c in await svc.memory.list_cards(states=["active"])
        if c.context_id == params.context_id and c.kind in KINDS
    ]
    if not cards:
        svc.audit.record(params.context_id, "record_resolution.no_card", {})
        return completed(
            request,
            RecordResolutionOutput(outcome="no_card"),
            outcome="no_card",
            evidence=make_evidence(svc.clock, "해결 내용을 기록할 카드가 없음: 확인할 수 없음", []),
        )
    card = max(cards, key=lambda c: c.created_at)
    resolved_at = params.resolved_at or svc.clock()
    minutes = params.minutes_to_resolve
    if params.opened_at is not None:
        if params.opened_at > resolved_at:
            return invalid_input(request, "opened_at: 해결 시각보다 늦을 수 없다")
        if minutes is None:  # 시스템이 잰다. 사람이 시간을 직접 적지 않는다
            minutes = int((resolved_at - params.opened_at).total_seconds() // 60)
    resolution = CaseResolution(
        outcome=params.outcome,
        root_cause=params.root_cause,
        action=params.action,
        minutes_to_resolve=minutes,
        resolved_at=resolved_at,
        opened_at=params.opened_at,
        prior_case_id=params.prior_case_id,
    )
    key = f"record_resolution:{card.card_id}:{_payload_hash(resolution)}"
    evidence = make_evidence(svc.clock, f"해결 내용 기록: 사건 {params.context_id}", [card.card_id])
    if svc.idem.get(key) is not None:
        svc.audit.record(
            params.context_id, "record_resolution.duplicate", {"card_id": card.card_id}
        )
        return completed(
            request,
            RecordResolutionOutput(outcome="duplicate", card_id=card.card_id),
            outcome="duplicate",
            evidence=evidence,
        )

    tags = (
        [*card.tags, "resolved"]
        if resolution.outcome == "resolved" and ("resolved" not in card.tags)
        else list(card.tags)
    )
    updated = card.model_copy(
        update={
            "body": with_resolution(card.body, resolution),
            "tags": tags,
            "extra": {**card.extra, "resolution": resolution.model_dump(mode="json")},
        }
    )
    decision = await svc.gate.execute(
        "memory.card_write",
        {
            "card_id": card.card_id,
            "summary": f"{card.title} 해결 내용",
            "confidence": card.confidence,
            "target": card.card_id,
        },
        with_key(request.meta, f"memory.card_write:{card.card_id}:{_payload_hash(resolution)}"),
        evidence,
    )
    if decision.status != "executed":
        svc.audit.record(
            params.context_id, "record_resolution.blocked", {"status": decision.status}
        )
        return gate_blocked(request, decision)
    await svc.memory.write_card(updated)
    svc.idem.put(key, {"card_id": card.card_id})
    svc.audit.record(
        params.context_id,
        "record_resolution",
        {"card_id": card.card_id, "outcome": resolution.outcome},
    )
    return completed(
        request,
        RecordResolutionOutput(outcome="recorded", card_id=card.card_id),
        outcome="recorded",
        evidence=evidence,
    )


def _summary(card: Card) -> str:
    decision = card.extra.get("decision")
    return str(decision) if decision else card.title


async def _dense(
    svc: Services, query: str, cards: list[Card], limit: int
) -> list[tuple[Card, float]] | None:
    if svc.dense is None:
        return None
    try:
        found = await svc.dense.search(
            query, cards, k=max(limit, 5), min_cosine=svc.settings.dense_min_cosine
        )
    except Exception:  # noqa: BLE001 - 임베딩 실패가 조회를 막지 않게 Dice로 대체한다
        return None
    return await apply_verification(svc.verifier, svc.settings.dense_cut, query, found)


async def find_similar_cases(svc: Services, request: AgentRequest) -> AgentResult:
    try:
        params = FindSimilarCasesInput.model_validate(request.input)
    except ValidationError as exc:
        return invalid_input(request, exc)

    cards = [
        c
        for c in await svc.memory.list_cards(area=params.area, states=["active"])
        if c.kind in KINDS and (not params.only_resolved or resolution_of(c) is not None)
    ]
    found = await _dense(svc, params.query, cards, params.limit)
    if found is not None:
        method = "dense"
        pairs = found
    else:
        method = "dice"
        allowed = {c.card_id for c in cards}
        hits = await svc.memory.search_cards(
            params.query, area=params.area, kinds=list(KINDS), states=["active"], limit=500
        )
        pairs = [
            (h.card, h.score)
            for h in hits
            if h.card.card_id in allowed and h.score >= svc.settings.similarity_threshold
        ]
    pairs = pairs[: params.limit]

    cases = [
        SimilarCase(
            card_id=c.card_id,
            title=c.title,
            summary=_summary(c),
            similarity=min(max(s, 0.0), 1.0),
            resolved=resolution_of(c) is not None,
            resolution=resolution_of(c),
            created_at=c.created_at,
        )
        for c, s in pairs
    ]
    output = FindSimilarCasesOutput(
        cases=cases,
        max_similarity=max((c.similarity for c in cases), default=0.0),
        method=method,
        collected_at=svc.clock(),
    )
    summary = (
        f"비슷한 과거 사건 {len(cases)}건 ({method})"
        if cases
        else "비슷한 과거 사건 없음: 기록에 없음"
    )
    return completed(
        request,
        output,
        outcome="found" if cases else "none",
        evidence=make_evidence(svc.clock, summary, [c.card_id for c in cases], [params.query]),
    )
