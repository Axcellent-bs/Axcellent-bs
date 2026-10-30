"""기존 결정과 연결: 같은 영역의 새 결정이 옛 결정을 바꾸면 옛 카드를 `superseded`로 돌린다.

바꾼다고 보는 조건(규칙):
- 새 카드와 옛 카드가 모두 영역(`area`)이 있고 같다.
- 둘 다 결정 카드이고 "채택(adopted)"한 결정이며, 옛 카드는 후보가 아닌 유효(active) 카드다.
- 결정 문장이 같은 주제이지만(아래 유사도 하한 이상) 문장은 다르다.
같은 주제 판단은 임베딩 코사인(Dense)으로 하고, 임베딩을 쓸 수 없으면 글자 겹침(Dice)으로 대체한다.
기각한 안(`rejected`)은 다른 결정을 대체하지 않는다. 이벤트 기록 카드(`incident`)는 대상이 아니다.
"""

from __future__ import annotations

from ax_contracts.schemas import Card
from hr_agent.clients.memory import MemoryClient
from hr_agent.retrieval.cards import DenseCardSearch
from hr_agent.util import similarity

SAME_TOPIC = 0.5  # Dice로 같은 주제로 보는 결정 문장 유사도 하한(임베딩이 없을 때)
DENSE_SAME_TOPIC = 0.7786  # Dense 코사인 하한. 15번 평가(link-rules.md)의 개발용 쌍에서 정했다
AUTO_MERGE_COSINE = (
    0.8351  # 이 이상이고 LLM이 같은 결정이라 해야 자동 병합(개발용 최대 비중복 코사인 + 0.005)
)


def _decision_text(card: Card) -> str:
    return str(card.extra.get("decision") or card.title)


def eligible_old(card: Card, old: Card) -> bool:
    return (
        old.card_id != card.card_id
        and old.kind == "decision"
        and old.extra.get("outcome") == "adopted"
        and not old.extra.get("candidate")
    )


async def topic_scores(
    new_text: str, olders: list[Card], dense: DenseCardSearch | None
) -> tuple[dict[str, float], float]:
    """옛 카드마다 "같은 주제" 점수와 그 하한. Dense가 안 되면 Dice 점수와 하한을 쓴다."""
    if dense is not None and olders:
        try:
            hits = await dense.search(new_text, olders, k=len(olders), min_cosine=-1.0)
            return {c.card_id: score for c, score in hits}, DENSE_SAME_TOPIC
        except Exception:  # noqa: BLE001 - 임베딩 실패가 연결 판단을 막지 않게 Dice로 대체한다
            pass
    return {o.card_id: similarity(_decision_text(o), new_text) for o in olders}, SAME_TOPIC


async def find_superseded(
    card: Card, memory: MemoryClient, dense: DenseCardSearch | None = None
) -> list[str]:
    """새 카드가 대체하는 옛 카드 ID 목록."""
    area = card.extra.get("area")
    if (
        not area
        or card.kind != "decision"
        or card.extra.get("outcome") != "adopted"
        or card.extra.get("candidate")
    ):
        return []
    olders = [
        o for o in await memory.list_cards(area=area, states=["active"]) if eligible_old(card, o)
    ]
    new_text = _decision_text(card)
    scores, floor = await topic_scores(new_text, olders, dense)
    return sorted(
        o.card_id
        for o in olders
        if _decision_text(o) != new_text and scores.get(o.card_id, 0.0) >= floor
    )
