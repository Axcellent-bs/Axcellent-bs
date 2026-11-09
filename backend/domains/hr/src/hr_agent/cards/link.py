"""기존 결정과 연결: 같은 영역의 새 결정이 옛 결정과 같은 주제면 옛 카드를 `superseded`로 돌린다.

바꾼다고 보는 조건(규칙):
- 새 카드와 옛 카드가 모두 영역(`area`)이 있고 같다.
- 둘 다 결정 카드이고 "채택(adopted)"한 결정이며, 옛 카드는 후보가 아닌 유효(active) 카드다.
- 결정 문장이 같은 주제이지만(아래 유사도 하한 이상) 문장은 다르다.
같은 주제 판단은 임베딩 코사인(Dense)으로 하고, 임베딩을 쓸 수 없으면 글자 겹침(Dice)으로 대체한다.
기각한 안(`rejected`)은 다른 결정을 대체하지 않는다. 이벤트 기록 카드(`incident`)는 대상이 아니다.

Dense로 같은 주제라고 본 쌍은 LLM이 한 번 더 가른다(`plan_links`).
- 내용이 바뀐 결정(`changed`) → 지금까지처럼 옛 카드를 대체한다.
- 같은 결정을 다시 쓴 것(`same`)이고 코사인이 아주 높음(`AUTO_MERGE_COSINE`) → AI가 자동 병합한다.
- 같은 결정을 다시 쓴 것 같지만 확신이 모자라거나 판정이 없음 → 사람 승인 목록에 올린다.
- 별개의 결정(`separate`) → 건드리지 않는다.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ax_contracts.schemas import Card
from hr_agent.clients.memory import MemoryClient
from hr_agent.retrieval.cards import DenseCardSearch
from hr_agent.retrieval.duplicates import SameDecisionJudge
from hr_agent.util import similarity

SAME_TOPIC = 0.5  # Dice로 같은 주제로 보는 결정 문장 유사도 하한(임베딩이 없을 때)
# 결정 문장끼리 코사인. 15번 평가 v2(link-rules.md 5장)의 개발용 쌍에서 정했다
DENSE_SAME_TOPIC = 0.8184
# 이 이상이고 LLM이 같은 결정이라 해야 자동 병합(개발용 최대 비중복 코사인 + 0.005)
AUTO_MERGE_COSINE = 0.9101


def _decision_text(card: Card) -> str:
    return str(card.extra.get("decision") or card.title)


def eligible_old(card: Card, old: Card) -> bool:
    return (
        old.card_id != card.card_id
        and old.kind == "decision"
        and old.extra.get("outcome") == "adopted"
        and not old.extra.get("candidate")
    )


DECISION_VIEW = (
    "dec:"  # 결정 문장만 담은 비교용 카드의 id 접두사(카드 전체 글의 벡터와 섞이지 않게)
)


def decision_view(card: Card) -> Card:
    """같은 주제 비교용: 카드마다 다른 이유·교훈·근거 문단을 빼고 결정 문장만 남긴 카드."""
    text = _decision_text(card)
    return card.model_copy(
        update={
            "card_id": f"{DECISION_VIEW}{card.card_id}",
            "title": f"[결정] {text}",
            "body": f"결정: {text}",
        }
    )


async def dense_scores(
    new_text: str, olders: list[Card], dense: DenseCardSearch | None
) -> dict[str, float] | None:
    """옛 카드마다 코사인(결정 문장끼리 비교). 임베딩이 안 되거나 실패하면 None(Dice로 대체)."""
    if dense is None or not olders:
        return None
    try:
        hits = await dense.search(
            new_text, [decision_view(o) for o in olders], k=len(olders), min_cosine=-1.0
        )
    except Exception:  # noqa: BLE001 - 임베딩 실패가 연결 판단을 막지 않게 Dice로 대체한다
        return None
    return {c.card_id.removeprefix(DECISION_VIEW): score for c, score in hits}


async def topic_scores(
    new_text: str, olders: list[Card], dense: DenseCardSearch | None
) -> tuple[dict[str, float], float]:
    """옛 카드마다 "같은 주제" 점수와 그 하한. Dense가 안 되면 Dice 점수와 하한을 쓴다."""
    scores = await dense_scores(new_text, olders, dense)
    if scores is not None:
        return scores, DENSE_SAME_TOPIC
    return {o.card_id: similarity(_decision_text(o), new_text) for o in olders}, SAME_TOPIC


async def _olders(card: Card, memory: MemoryClient) -> list[Card]:
    area = card.extra.get("area")
    if (
        not area
        or card.kind != "decision"
        or card.extra.get("outcome") != "adopted"
        or card.extra.get("candidate")
    ):
        return []
    found = await memory.list_cards(area=area, states=["active"])
    return [o for o in found if eligible_old(card, o)]


async def find_superseded(
    card: Card, memory: MemoryClient, dense: DenseCardSearch | None = None
) -> list[str]:
    """새 카드와 같은 주제인 옛 카드 ID 목록(내용이 바뀌었는지 같은 결정인지는 가르지 않는다)."""
    olders = await _olders(card, memory)
    new_text = _decision_text(card)
    scores, floor = await topic_scores(new_text, olders, dense)
    return sorted(
        o.card_id
        for o in olders
        if _decision_text(o) != new_text and scores.get(o.card_id, 0.0) >= floor
    )


@dataclass
class QueueItem:
    old_id: str
    cosine: float
    verdict: str | None  # LLM 판정. 없으면 None


@dataclass
class LinkPlan:
    supersede: list[str] = field(default_factory=list)  # 내용이 바뀐 결정(또는 Dice 규칙)
    merge: list[tuple[str, float]] = field(default_factory=list)  # 자동 병합(옛 카드, 코사인)
    queue: list[QueueItem] = field(default_factory=list)  # 사람이 승인할 중복 후보


async def plan_links(
    card: Card,
    memory: MemoryClient,
    dense: DenseCardSearch | None = None,
    judge: SameDecisionJudge | None = None,
) -> LinkPlan:
    plan = LinkPlan()
    olders = await _olders(card, memory)
    if not olders:
        return plan
    new_text = _decision_text(card)
    scores = await dense_scores(new_text, olders, dense)
    if scores is None:  # 임베딩이 없으면 지금까지의 Dice 규칙 그대로
        plan.supersede = await find_superseded(card, memory, None)
        return plan
    for old in sorted(olders, key=lambda o: o.card_id):
        cosine = scores.get(old.card_id, 0.0)
        if cosine < DENSE_SAME_TOPIC or _decision_text(old) == new_text:
            continue
        verdict = await judge.judge(new_text, _decision_text(old)) if judge else None
        if verdict == "separate":
            continue
        if verdict == "changed":
            plan.supersede.append(old.card_id)
        elif verdict == "same" and cosine >= AUTO_MERGE_COSINE:
            plan.merge.append((old.card_id, cosine))
        else:  # 같은 결정 같지만 확신이 모자라거나 판정을 못 받음 → 사람이 본다
            plan.queue.append(QueueItem(old.card_id, cosine, verdict))
    return plan
