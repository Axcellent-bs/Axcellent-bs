"""이벤트 기억: 해결 내용을 카드에 채우고, 비슷한 과거 이벤트를 찾아 준다.

`record_resolution`과 `find_similar_cases` 두 skill이 있다.

- `record_resolution`: 이벤트(`context_id`)에서 만든 카드에 원인·조치·결과·소요 시간을 채운다.
  카드 본문에도 적어서(`[해결 기록]` 블록) 나중에 임베딩 검색이 해결 내용까지 보고 찾게 한다.
  값이 없으면 지어내지 않고 "확인할 수 없음"으로 적는다.
  상태를 바꾸므로 `memory.card_write` 관문을 거친다.
- `find_similar_cases`: 현재 이벤트 설명으로 비슷한 과거 이벤트·결정 카드를 찾는다(조회 전용).
  Dense로 찾고, 안 되면 Dice로 대체한다. 검증이 켜져 있으면 LLM이 관련 없는 후보를 거른다.
  카드가 가진 해결 내용을 그대로 돌려줄 뿐 해결 방법을 새로 만들지 않는다.
"""

from __future__ import annotations

import hashlib
import json

from pydantic import ValidationError

from ax_contracts.schemas import (
    AgentRequest,
    AgentResult,
    Card,
    CaseResolution,
    EventTimeline,
    FindSimilarCasesInput,
    FindSimilarCasesOutput,
    RecordEventTimesInput,
    RecordEventTimesOutput,
    RecordResolutionInput,
    RecordResolutionOutput,
    SimilarCase,
)
from hr_agent.cards.builder import UNKNOWN
from hr_agent.dept_eval import CriteriaError, judge_timings, load_criteria
from hr_agent.results import (
    completed,
    gate_blocked,
    invalid_input,
    make_evidence,
    with_key,
)
from hr_agent.retrieval.verify import apply_verification
from hr_agent.services import Services

KINDS = (
    "incident",
    "decision",
    "case",
)  # 이벤트·결정·사례 카드만 찾는다(공지·정책·사기 카드는 제외)
BLOCK = "\n[해결 기록]"  # 카드 본문에서 해결 내용을 시작하는 표시
OUTCOME_KO = {"resolved": "해결됨", "workaround": "임시 조치", "unresolved": "미해결"}


def resolution_block(resolution: CaseResolution) -> str:
    # 시간은 이벤트 타임라인(감지~정상 복귀)이 맡는다. 값이 있을 때만 적고, 없다고 쓰지 않는다
    minutes = (
        f"\n소요 시간: {resolution.minutes_to_resolve}분"
        if resolution.minutes_to_resolve is not None
        else ""
    )
    return (
        f"{BLOCK}\n결과: {OUTCOME_KO[resolution.outcome]}\n"
        f"원인: {resolution.root_cause or UNKNOWN}\n"
        f"조치: {resolution.action or UNKNOWN}"
        f"{minutes}"
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


TIMES = (
    "detected_at",
    "cause_candidate_at",
    "human_work_started_at",
    "human_work_done_at",
    "recovered_at",
)


def _usable(card: Card) -> bool:
    """다음 장애에 내밀 수 있는 해결 기록: 해결·임시 조치이고 원인이나 조치가 적혀 있다."""
    res = resolution_of(card)
    return bool(
        res is not None
        and res.outcome in ("resolved", "workaround")
        and (res.root_cause or res.action)
    )


def timeline_of(card: Card) -> EventTimeline | None:
    raw = card.extra.get("timeline")
    if not isinstance(raw, dict):
        return None
    try:
        return EventTimeline.model_validate(raw)
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
    current = resolution_of(card)
    if params.only_if_unresolved and current is not None and current.outcome != "unresolved":
        # 이미 해결 기록이 있다(사람이 적었거나 자동 경로가 남겼다): 덮지 않는다
        svc.audit.record(params.context_id, "record_resolution.kept", {"card_id": card.card_id})
        return completed(
            request,
            RecordResolutionOutput(outcome="duplicate", card_id=card.card_id),
            outcome="duplicate",
            evidence=make_evidence(
                svc.clock,
                f"이미 해결 기록이 있어 바꾸지 않음: 이벤트 {params.context_id}",
                [card.card_id],
            ),
        )
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
        draft_source=params.draft_source,
    )
    key = f"record_resolution:{card.card_id}:{_payload_hash(resolution)}"
    evidence = make_evidence(
        svc.clock, f"해결 내용 기록: 이벤트 {params.context_id}", [card.card_id]
    )
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


def _type_of(card: Card) -> str | None:
    value = card.extra.get("event_type")
    return str(value) if value else None


def _same_type(wanted: str | None, found: str | None) -> bool | None:
    """어느 쪽이든 유형을 모르면 비교하지 않는다(None)."""
    return None if not wanted or not found else wanted == found


async def record_event_times(svc: Services, request: AgentRequest) -> AgentResult:
    """이벤트 카드에 시스템이 잰 시각을 채운다. 이미 있는 시각은 덮어쓰지 않는다."""
    try:
        params = RecordEventTimesInput.model_validate(request.input)
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
        svc.audit.record(params.context_id, "record_event_times.no_card", {})
        return completed(
            request,
            RecordEventTimesOutput(outcome="no_card"),
            outcome="no_card",
            evidence=make_evidence(svc.clock, "시각을 기록할 카드가 없음: 확인할 수 없음", []),
        )
    card = max(cards, key=lambda c: c.created_at)
    current = timeline_of(card) or EventTimeline()
    fresh = {
        name: getattr(params, name)
        for name in (*TIMES, "prior_case_id")
        if getattr(current, name) is None and getattr(params, name) is not None
    }
    # 단계별 시간은 시각과 달리 장애가 끝난 뒤 값이 바뀔 수 있어, 달라졌으면 마지막 값으로 바꾼다
    new_timings = (
        [t.model_dump(mode="json") for t in params.step_timings]
        if params.step_timings is not None
        else None
    )
    timings_changed = new_timings is not None and new_timings != card.extra.get("step_timings")
    evidence = make_evidence(svc.clock, f"이벤트 시각 기록: {params.context_id}", [card.card_id])
    if not fresh and not timings_changed:
        return completed(
            request,
            RecordEventTimesOutput(outcome="duplicate", card_id=card.card_id, timeline=current),
            outcome="duplicate",
            evidence=evidence,
        )
    merged = current.model_copy(update=fresh)
    digest_source = merged.model_dump_json() + (
        json.dumps(new_timings, sort_keys=True) if timings_changed else ""
    )
    digest = hashlib.sha256(digest_source.encode("utf-8")).hexdigest()[:12]
    decision = await svc.gate.execute(
        "memory.card_write",
        {
            "card_id": card.card_id,
            "summary": f"{card.title} 시각",
            "confidence": card.confidence,
            "target": card.card_id,
        },
        with_key(request.meta, f"memory.card_write:{card.card_id}:times:{digest}"),
        evidence,
    )
    if decision.status != "executed":
        svc.audit.record(
            params.context_id, "record_event_times.blocked", {"status": decision.status}
        )
        return gate_blocked(request, decision)
    extra = dict(card.extra)
    if fresh:
        extra["timeline"] = merged.model_dump(mode="json")
    if timings_changed:
        extra["step_timings"] = new_timings
        _store_judgments(svc, params.context_id, extra, new_timings or [])
    await svc.memory.write_card(card.model_copy(update={"extra": extra}))
    svc.audit.record(
        params.context_id,
        "record_event_times",
        {
            "card_id": card.card_id,
            "added": sorted(fresh),
            "step_timings": len(new_timings or []) if timings_changed else 0,
        },
    )
    return completed(
        request,
        RecordEventTimesOutput(outcome="recorded", card_id=card.card_id, timeline=merged),
        outcome="recorded",
        evidence=evidence,
    )


def _store_judgments(svc: Services, context_id: str, extra: dict, timings: list[dict]) -> None:
    """단계 시간이 바뀔 때마다 부서 평가 판정을 다시 계산해 카드에 기준값 버전과 같이 둔다.

    기준값 파일을 읽지 못해도 시각 기록은 막지 않는다(판정만 건너뛰고 감사 기록에 남긴다).
    """
    try:
        extra["step_judgments"] = judge_timings(timings, load_criteria())
    except CriteriaError as exc:
        extra.pop("step_judgments", None)
        svc.audit.record(context_id, "dept_eval.criteria_unreadable", {"error": str(exc)[:200]})
        return
    svc.audit.record(
        context_id,
        "dept_eval.judged",
        {
            "criteria_version": extra["step_judgments"]["criteria_version"],
            "steps": len(extra["step_judgments"]["items"]),
        },
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
    # 자를 때 같은 유형·해결 기록(원인 또는 조치)이 있는 카드를 앞에 둔다. 제목이 같은 미해결
    # 카드가 많으면 유사도가 거의 같아 해결된 카드가 잘려 나가 해결 내용을 내밀지 못했다.
    # 같은 그룹 안의 순서는 유사도 그대로다(정렬이 안정적)
    pairs = sorted(
        pairs,
        key=lambda pair: (
            _same_type(params.event_type, _type_of(pair[0])) is False,
            not _usable(pair[0]),
        ),
    )[: params.limit]

    cases = [
        SimilarCase(
            card_id=c.card_id,
            title=c.title,
            summary=_summary(c),
            similarity=min(max(s, 0.0), 1.0),
            resolved=resolution_of(c) is not None,
            resolution=resolution_of(c),
            timeline=timeline_of(c),
            event_type=_type_of(c),
            same_type=_same_type(params.event_type, _type_of(c)),
            created_at=c.created_at,
        )
        for c, s in pairs
    ]
    # 다른 유형은 빼지 않고 뒤로 보낸다(표시만 붙인다). 같은 그룹 안의 순서는 유사도 그대로다
    cases.sort(key=lambda c: c.same_type is False)
    output = FindSimilarCasesOutput(
        cases=cases,
        max_similarity=max((c.similarity for c in cases), default=0.0),
        method=method,
        collected_at=svc.clock(),
    )
    summary = (
        f"비슷한 과거 이벤트 {len(cases)}건 ({method})"
        if cases
        else "비슷한 과거 이벤트 없음: 기록에 없음"
    )
    return completed(
        request,
        output,
        outcome="found" if cases else "none",
        evidence=make_evidence(svc.clock, summary, [c.card_id for c in cases], [params.query]),
    )
