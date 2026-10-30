"""중복 결정 병합 (15번).

같은 결정을 다시 쓴 카드는 확신이 매우 높을 때만 AI가 자동 병합하고(`apply_merge`), 애매하면
새 카드의 `extra["dup_candidates"]`에 올려 사람이 승인한다(`decide_duplicate_group`).
병합은 옛 카드를 지우지 않고 대체됨(`superseded`)으로 표시하고 `extra["merge"]`에 이유를 남긴다.
그래서 `restore_merged_card`로 되돌릴 수 있다. 이벤트 기록 카드(`incident`)는 대상이 아니다.
카드 상태를 바꾸므로 모두 관문(`memory.card_link`, 카드 쓰기는 `memory.card_write`)을 거친다.
"""

from __future__ import annotations

from typing import Any

from pydantic import ValidationError

from ax_contracts.schemas import (
    AgentRequest,
    AgentResult,
    Card,
    DecideDuplicateGroupInput,
    DecideDuplicateGroupOutput,
    DuplicateGroup,
    DuplicateMember,
    Evidence,
    ListDuplicateGroupsInput,
    ListDuplicateGroupsOutput,
    MergedCard,
    RestoreMergedCardInput,
    RestoreMergedCardOutput,
)
from hr_agent.cards.link import QueueItem, _decision_text
from hr_agent.clients.memory import card_state
from hr_agent.results import (
    completed,
    gate_blocked,
    invalid_input,
    make_evidence,
    with_key,
)
from hr_agent.services import Services
from hr_agent.util import stable_id

PENDING = "dup_candidates"  # 새 카드에 붙는 사람 승인 대기 목록
DISMISSED = "dup_dismissed"  # 사람이 "병합하지 않음"으로 정한 옛 카드 id
MERGE = "merge"  # 병합된 옛 카드의 이유
TITLE = "dup_title"  # LLM이 제안한 대표 제목(검증 통과분)


def group_id_of(card_id: str) -> str:
    return stable_id("dup_", card_id)


async def _link(
    svc: Services, request: AgentRequest, evidence: Evidence, op: str, old_id: str, new_id: str
):
    return await svc.gate.execute(
        "memory.card_link",
        {
            "card_id": old_id,
            "summary": f"{old_id} {op}",
            "op": op,
            "target": f"{old_id}>{new_id}",
        },
        with_key(request.meta, f"memory.card_link:{op}:{old_id}>{new_id}"),
        evidence,
    )


async def apply_merge(
    svc: Services,
    request: AgentRequest,
    evidence: Evidence,
    rep: Card,
    old_id: str,
    how: str,
    cosine: float | None,
) -> tuple[bool, Any]:
    """옛 카드를 대표 카드로 병합한다(대체됨 표시 + 이유 기록). (성공 여부, 관문 결정)."""
    decision = await _link(svc, request, evidence, "merge", old_id, rep.card_id)
    if decision.status != "executed":
        return False, decision
    await svc.memory.set_status(old_id, "superseded", rep.card_id)
    old = await svc.memory.get_card(old_id)
    if old is not None:
        info = {"into": rep.card_id, "how": how, "cosine": cosine, "at": svc.clock().isoformat()}
        await svc.memory.write_card(old.model_copy(update={"extra": {**old.extra, MERGE: info}}))
    svc.audit.record(
        request.meta.context_id, f"card_merge.{how}", {"old": old_id, "into": rep.card_id}
    )
    return True, decision


async def queue_candidates(svc: Services, card: Card, items: list[QueueItem]) -> Card:
    """사람이 승인할 중복 후보를 새 카드에 올리고, 대표 제목을 제안해 둔다(검증 통과분만)."""
    if not items:
        return card
    pending = [{"old": i.old_id, "cosine": round(i.cosine, 4), "verdict": i.verdict} for i in items]
    extra = {**card.extra, PENDING: pending}
    suggester = svc.extras.get("title_suggester")
    if suggester is not None:
        members = [(card.card_id, _decision_text(card))]
        for item in items:
            old = await svc.memory.get_card(item.old_id)
            if old is not None:
                members.append((old.card_id, _decision_text(old)))
        title = await suggester.suggest(members)
        if title:
            extra[TITLE] = title
    updated = card.model_copy(update={"extra": extra})
    await svc.memory.write_card(updated)
    return updated


def _member(card: Card, cosine: float | None = None, verdict: str | None = None) -> DuplicateMember:
    return DuplicateMember(
        card_id=card.card_id,
        title=card.title,
        decision=_decision_text(card),
        cosine=cosine,
        verdict=verdict,  # type: ignore[arg-type]
    )


async def list_duplicate_groups(svc: Services, request: AgentRequest) -> AgentResult:
    try:
        params = ListDuplicateGroupsInput.model_validate(request.input)
    except ValidationError as exc:
        return invalid_input(request, exc)
    groups: list[DuplicateGroup] = []
    active = await svc.memory.list_cards(area=params.area, states=["active"])
    for card in sorted(active, key=lambda c: (c.created_at, c.card_id), reverse=True):
        pending: list[dict[str, Any]] = card.extra.get(PENDING) or []
        if card.kind != "decision" or not pending:
            continue
        olders = []
        for item in pending:
            old = await svc.memory.get_card(str(item.get("old")))
            if old is not None and card_state(old) == "active":
                olders.append(_member(old, item.get("cosine"), item.get("verdict")))
        if not olders:
            continue
        groups.append(
            DuplicateGroup(
                group_id=group_id_of(card.card_id),
                area=str(card.extra.get("area") or ""),
                representative=_member(card),
                olders=olders,
                suggested_title=card.extra.get(TITLE),
                created_at=card.created_at,
            )
        )
    merged: list[MergedCard] = []
    for old in await svc.memory.list_cards(area=params.area, states=["superseded"]):
        info = old.extra.get(MERGE)
        if isinstance(info, dict) and info.get("into"):
            merged.append(
                MergedCard(
                    card_id=old.card_id,
                    title=old.title,
                    merged_into=str(info["into"]),
                    how=info.get("how", "auto"),
                    cosine=info.get("cosine"),
                    at=info.get("at") or old.created_at,
                )
            )
    merged.sort(key=lambda m: m.at, reverse=True)
    output = ListDuplicateGroupsOutput(
        groups=groups[: params.limit], merged=merged[: params.limit], collected_at=svc.clock()
    )
    return completed(
        request,
        output,
        outcome="listed",
        evidence=make_evidence(
            svc.clock,
            f"사람 승인을 기다리는 중복 묶음 {len(groups)}건, 병합된 카드 {len(merged)}장",
            [g.representative.card_id for g in groups],
        ),
    )


async def decide_duplicate_group(svc: Services, request: AgentRequest) -> AgentResult:
    try:
        params = DecideDuplicateGroupInput.model_validate(request.input)
    except ValidationError as exc:
        return invalid_input(request, exc)
    if not request.meta.idempotency_key:
        return invalid_input(request, "상태를 바꾸는 요청에는 idempotency_key가 필요하다")
    rep = await svc.memory.get_card(params.representative_id)
    evidence = make_evidence(svc.clock, f"중복 묶음 {params.decision}", [params.representative_id])
    if rep is None or group_id_of(rep.card_id) != params.group_id:
        return completed(
            request,
            DecideDuplicateGroupOutput(outcome="not_found"),
            outcome="not_found",
            evidence=evidence,
        )
    pending: list[dict[str, Any]] = rep.extra.get(PENDING) or []
    if not pending:  # 이미 처리된 묶음이다
        return completed(
            request,
            DecideDuplicateGroupOutput(outcome="duplicate"),
            outcome="duplicate",
            evidence=evidence,
        )
    merged_ids: list[str] = []
    if params.decision == "merge":
        for item in pending:
            old_id = str(item.get("old"))
            old = await svc.memory.get_card(old_id)
            if old is None or card_state(old) != "active":
                continue
            ok, gate = await apply_merge(
                svc, request, evidence, rep, old_id, "human", item.get("cosine")
            )
            if not ok:
                return gate_blocked(request, gate)
            merged_ids.append(old_id)
    extra = {k: v for k, v in rep.extra.items() if k not in (PENDING, TITLE)}
    if params.decision == "dismiss":
        extra[DISMISSED] = [*rep.extra.get(DISMISSED, []), *(str(i.get("old")) for i in pending)]
    updates: dict[str, Any] = {"extra": extra}
    if params.decision == "merge" and params.title and params.title.strip():
        updates["title"] = f"[결정] {params.title.strip()}"
    write = await svc.gate.execute(
        "memory.card_write",
        {
            "card_id": rep.card_id,
            "summary": f"중복 묶음 {params.decision}",
            "confidence": rep.confidence,
            "target": rep.card_id,
        },
        with_key(request.meta, f"memory.card_write:{rep.card_id}:dup:{params.decision}"),
        evidence,
    )
    if write.status != "executed":
        return gate_blocked(request, write)
    await svc.memory.write_card(rep.model_copy(update=updates))
    svc.audit.record(
        request.meta.context_id,
        f"duplicate_group.{params.decision}",
        {"representative": rep.card_id, "merged": merged_ids},
    )
    outcome = "merged" if params.decision == "merge" else "dismissed"
    return completed(
        request,
        DecideDuplicateGroupOutput(outcome=outcome, merged_ids=merged_ids),
        outcome=outcome,
        evidence=evidence,
    )


async def restore_merged_card(svc: Services, request: AgentRequest) -> AgentResult:
    try:
        params = RestoreMergedCardInput.model_validate(request.input)
    except ValidationError as exc:
        return invalid_input(request, exc)
    if not request.meta.idempotency_key:
        return invalid_input(request, "상태를 바꾸는 요청에는 idempotency_key가 필요하다")
    old = await svc.memory.get_card(params.card_id)
    evidence = make_evidence(svc.clock, f"병합 되돌리기: {params.card_id}", [params.card_id])
    if old is None:
        return completed(
            request,
            RestoreMergedCardOutput(outcome="not_found"),
            outcome="not_found",
            evidence=evidence,
        )
    info = old.extra.get(MERGE)
    if not isinstance(info, dict) or card_state(old) != "superseded":
        return completed(
            request,
            RestoreMergedCardOutput(outcome="not_merged", card_id=old.card_id),
            outcome="not_merged",
            evidence=evidence,
        )
    decision = await _link(svc, request, evidence, "restore", old.card_id, str(info["into"]))
    if decision.status != "executed":
        return gate_blocked(request, decision)
    await svc.memory.set_status(old.card_id, "active", None)
    fresh = await svc.memory.get_card(old.card_id)
    if fresh is not None:
        extra = {k: v for k, v in fresh.extra.items() if k != MERGE}
        extra["merge_restored"] = info
        await svc.memory.write_card(fresh.model_copy(update={"extra": extra}))
    svc.audit.record(
        request.meta.context_id,
        "card_merge.restored",
        {"card": old.card_id, "was_into": info["into"]},
    )
    return completed(
        request,
        RestoreMergedCardOutput(outcome="restored", card_id=old.card_id),
        outcome="restored",
        evidence=evidence,
    )
