"""`check_plan`: 새 계획 초안을 회사 기억의 결정 카드와 비교한다(조회 전용).

판정은 모두 코드 규칙이다(LLM은 설명 문장만 다듬고, 실패하면 고정 문장을 쓴다).
- `REJECTED_BEFORE`(warn): 같은 영역의 *기각된* 유효 카드와 비슷하다. 경과 일수는 코드가 계산한다.
- `CONFLICT`(warn): 채택된 카드가 "검토했지만 채택하지 않은 대안"과 초안이 같다.
- `MATCH`(info): 채택된 카드와 같은 방향이다.
- 관련 카드가 없으면 판정하지 않고 `no_related_cards`로 "기록에 없음"을 알린다.
후보(확신도 낮은) 카드는 참고 목록에만 넣고 판정 근거로 쓰지 않는다.

관련 카드는 임베딩(Dense)으로 찾고, 설정이 없거나 호출이 실패하면 글자 2-gram(Dice)으로 대체한다.
경고마다 LLM 설명을 붙일 수 있다(`explanation`). 설명은 참고용이고 판정에는 쓰지 않으며,
숫자가 계산값과 다르거나 호출이 실패하면 비운다.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from pydantic import ValidationError

from ax_contracts.schemas import (
    AgentRequest,
    AgentResult,
    Card,
    CheckPlanComputed,
    CheckPlanInput,
    CheckPlanOutput,
    PlanFinding,
    SimilarCard,
)
from hr_agent.clients.memory import CardHit, card_state
from hr_agent.results import completed, invalid_input, make_evidence, refused
from hr_agent.retrieval.cards import dense_text
from hr_agent.roster import store as roster
from hr_agent.services import Services
from hr_agent.util import overlap, similarity

MATCH_SCORE = 0.4  # 이 이상이면 같은 방향의 결정으로 본다
ALT_SCORE = 0.3  # 초안이 "채택하지 않은 대안"과 이 이상 비슷하면 충돌


def _decided_at(card: Card) -> datetime:
    raw = card.extra.get("decided_at")
    if isinstance(raw, str):
        try:
            return datetime.fromisoformat(raw).astimezone(UTC)
        except ValueError:
            pass
    return card.created_at.astimezone(UTC)


def _score(card: Card, draft_text: str) -> float:
    """초안과 카드 핵심 문장(제목·결정·채택하지 않은 대안)의 유사도. 길이 차이에 강하다."""
    alternatives = " ".join(str(a) for a in card.extra.get("alternatives_rejected") or [])
    key = f"{card.title} {card.extra.get('decision', '')} {alternatives}"
    return round(max(similarity(draft_text, key), overlap(key, draft_text)), 4)


def _is_rejected(card: Card) -> bool:
    return card.extra.get("outcome") == "rejected"


def _conflicting_alternative(card: Card, draft_text: str) -> str | None:
    for alt in card.extra.get("alternatives_rejected") or []:
        alt = str(alt).strip()
        if alt and (alt in draft_text or similarity(alt, draft_text) >= ALT_SCORE):
            return alt
    return None


EXPLAIN_LIMIT = 3  # 경고 설명을 LLM에 맡기는 최대 개수(호출 수와 지연을 묶는다)
EXPLAIN_PURPOSE = (
    "이 판정이 새 계획과 어떤 관계인지를 근거 카드의 결정과 이유로 풀어 쓴 설명 문장. "
    "진행·중단 같은 권고나 판단, 판정 코드 이름은 쓰지 않는다"
)
# 설명이 판단을 대신하면 버린다(판정은 코드가 한다). 완벽한 검사는 아니고 흔한 표현만 거른다
JUDGING_PHRASES = (
    "진행해도",
    "추진해도",
    "해도 된다",
    "해도 됩니다",
    "하면 된다",
    "하면 됩니다",
    "그대로 진행",
    "따르면",
    "어렵다",
    "어렵습니다",
    "MATCH",
    "CONFLICT",
    "REJECTED_BEFORE",
)


async def _verify(
    svc: Services, draft_text: str, found: list[tuple[Card, float]]
) -> list[tuple[Card, float]]:
    """검증을 켰으면 1위에서 `dense_cut` 넘게 낮은 후보를 빼고, 남은 앞 3개를 LLM이 거른다.

    LLM 설정이 없거나 판정이 실패하면(None) 거르지 않은 결과를 그대로 쓴다.
    """
    if svc.verifier is None or not found:
        return found
    top = found[0][1]
    near = [(c, s) for c, s in found if s >= top - svc.settings.dense_cut - 1e-9]
    verdict = await svc.verifier.verify(draft_text, [(c.card_id, dense_text(c)) for c, _ in near])
    if verdict is None:
        return near
    return [(c, s) for c, s in near[:3] if verdict.get(c.card_id)]


async def _dense_hits(svc: Services, draft_text: str, area: str) -> list[CardHit] | None:
    """임베딩으로 관련 카드를 찾는다. 쓸 수 없거나 실패하면 None이고 호출한 쪽이 Dice로 대체한다."""
    if svc.dense is None:
        return None
    try:
        cards = await svc.memory.list_cards(area=area, states=["active", "candidate"])
        found = await svc.dense.search(
            draft_text, cards, k=5, min_cosine=svc.settings.dense_min_cosine
        )
        found = await _verify(svc, draft_text, found)
    except Exception:  # noqa: BLE001 - 임베딩 실패가 계획 점검을 막지 않게 한다
        return None
    return [CardHit(card, score) for card, score in found]


async def _explain(
    svc: Services,
    draft_title: str,
    draft_summary: str,
    findings: list[PlanFinding],
    hits: list[CardHit],
) -> list[PlanFinding]:
    """경고마다 LLM 설명 문장을 붙인다. 사실은 코드가 정하고 LLM은 문장만 쓴다."""
    if not svc.narrator.enabled or not findings:
        return findings
    cards = {h.card.card_id: h.card for h in hits}
    now = svc.clock()

    async def one(finding: PlanFinding) -> PlanFinding:
        card = cards.get(finding.card_ids[0]) if finding.card_ids else None
        if card is None:
            return finding
        facts: dict[str, object] = {
            "판정": finding.code,
            "계획 제목": draft_title,
            "근거 카드 제목": card.title,
            "근거 카드 결정": str(card.extra.get("decision") or ""),
            "근거 카드 이유": str(card.extra.get("reason") or ""),
            "고정 문장": finding.message,
        }
        if finding.code == "REJECTED_BEFORE":
            days = max(0, (now.astimezone(UTC).date() - _decided_at(card).date()).days)
            facts["기각 후 경과 일수"] = days
        sentence = await svc.narrator.sentence(
            EXPLAIN_PURPOSE,
            facts,
            finding.message,
            untrusted={"계획 요약": draft_summary},
        )
        if sentence.fallback or any(p in sentence.text for p in JUDGING_PHRASES):
            return finding
        return finding.model_copy(update={"explanation": sentence.text})

    head = list(findings[:EXPLAIN_LIMIT])
    explained = await asyncio.gather(*(one(f) for f in head))
    return [*explained, *findings[EXPLAIN_LIMIT:]]


async def check_plan(svc: Services, request: AgentRequest) -> AgentResult:
    try:
        params = CheckPlanInput.model_validate(request.input)
    except ValidationError as exc:
        return invalid_input(request, exc)
    draft = params.draft
    if draft.area not in roster.list_areas(svc.store):
        return refused(request, "UNKNOWN_AREA", f"알 수 없는 영역: {draft.area}")
    if not draft.title.strip():
        return invalid_input(request, "계획 제목이 비어 있다")

    now = svc.clock()
    draft_text = f"{draft.title} {draft.summary}"
    dense_hits = await _dense_hits(svc, draft_text, draft.area)
    if dense_hits is not None:
        hits: list[CardHit] = dense_hits
        match_score, method = svc.settings.dense_match_cosine, "dense"
    else:
        found = await svc.memory.search_cards(
            draft_text, area=draft.area, states=["active", "candidate"], limit=500
        )
        rescored = [CardHit(h.card, _score(h.card, draft_text)) for h in found]
        hits = sorted(
            (h for h in rescored if h.score >= svc.settings.similarity_threshold),
            key=lambda h: (-h.score, h.card.card_id),
        )[:5]
        match_score, method = MATCH_SCORE, "dice"
    similar = [
        SimilarCard(card_id=h.card.card_id, summary=h.card.title, status=card_state(h.card))
        for h in hits
    ]
    firm = [h for h in hits if card_state(h.card) == "active"]  # 후보는 판정 근거로 쓰지 않는다

    findings: list[PlanFinding] = []
    computed = CheckPlanComputed()
    for hit in firm:
        card = hit.card
        if _is_rejected(card):
            days = max(0, (now.astimezone(UTC).date() - _decided_at(card).date()).days)
            reason = card.extra.get("reason") or "기록에 없음"
            # 경고 문장은 고정 문장이다: 경과 일수 같은 숫자를 LLM 문장에 맡기지 않는다
            message = f"이미 기각된 안과 비슷하다: {card.title} ({days}일 전 기각, 이유: {reason})"
            findings.append(
                PlanFinding(
                    code="REJECTED_BEFORE",
                    level="warn",
                    message=message,
                    card_ids=[card.card_id],
                )
            )
            if computed.days_since_rejected is None or days < computed.days_since_rejected:
                computed = CheckPlanComputed(
                    days_since_rejected=days,
                    basis=f"기각 카드 {card.card_id}의 결정 시각 {_decided_at(card).isoformat()}",
                )
            continue
        alt = _conflicting_alternative(card, draft_text)
        if alt:
            findings.append(
                PlanFinding(
                    code="CONFLICT",
                    level="warn",
                    message=f"채택하지 않기로 한 대안과 같다: '{alt}' ({card.title})",
                    card_ids=[card.card_id],
                )
            )
        elif hit.score >= match_score:
            findings.append(
                PlanFinding(
                    code="MATCH",
                    level="info",
                    message=f"이미 확정된 결정과 같은 방향이다: {card.title}",
                    card_ids=[card.card_id],
                )
            )

    findings = await _explain(svc, draft.title, draft.summary, findings, hits)
    verdict = "warn" if any(f.level == "warn" for f in findings) else "ok"
    output = CheckPlanOutput(
        verdict=verdict,
        findings=findings,
        similar_cards=similar,
        computed=computed,
        no_related_cards=not hits,
    )
    refs = [c.card_id for c in similar]
    summary = (
        f"{draft.area} 영역 관련 카드 {len(hits)}장 비교, 판정 {verdict}"
        if hits
        else f"{draft.area} 영역에 관련 카드 없음: 기록에 없음 (조회 {now.isoformat()})"
    )
    svc.audit.record(
        request.meta.context_id,
        "check_plan",
        {
            "area": draft.area,
            "verdict": verdict,
            "findings": [f.code for f in findings],
            "method": method,
            "explained": sum(1 for f in findings if f.explanation),
        },
    )
    return completed(
        request,
        output,
        outcome=verdict,
        evidence=make_evidence(svc.clock, summary, refs),
    )
