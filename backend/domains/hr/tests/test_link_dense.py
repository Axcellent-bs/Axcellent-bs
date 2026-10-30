"""같은 주제 판단: Dense로 하고, 임베딩이 안 되면 Dice로 대체한다 (15번)."""

from __future__ import annotations

import pytest

from ax_contracts.schemas import Card
from hr_agent.cards.link import DENSE_SAME_TOPIC, SAME_TOPIC, find_superseded, topic_scores


def decision(card_id: str, text: str, area: str = "infra", **extra) -> Card:
    return Card(
        card_id=card_id,
        kind="decision",
        context_id=f"ctx_{card_id}",
        title=f"[결정] {text}",
        body=f"결정: {text}",
        tags=[],
        status="active",
        candidate=False,
        source_refs=[],
        author="t",
        confidence="high",
        created_at="2026-10-30T00:00:00Z",
        extra={"area": area, "decision": text, "outcome": "adopted", "candidate": False, **extra},
    )


class Memory:
    def __init__(self, cards: list[Card]) -> None:
        self.cards = cards

    async def list_cards(self, *, area=None, states=None) -> list[Card]:
        return [c for c in self.cards if area is None or c.extra.get("area") == area]


class Dense:
    """코사인을 정해 둔 가짜 Dense. 카드마다 점수를 돌려준다."""

    def __init__(self, scores: dict[str, float] | None = None, fail: bool = False) -> None:
        self.scores = scores or {}
        self.fail = fail

    async def search(self, query, cards, k=5, min_cosine=0.0):
        if self.fail:
            raise RuntimeError("embedding down")
        return [
            (c, self.scores.get(c.card_id, 0.0))
            for c in cards
            if self.scores.get(c.card_id, 0.0) >= min_cosine
        ]


NEW = decision("new", "배포 가능 시간을 평일 오전 10시부터 오후 6시까지로 늘린다")
OLD = decision("old", "배포는 평일 오전 10시부터 오후 4시 사이에만 진행한다")
OTHER = decision("other", "백업은 매일 새벽 3시에 받는다")


@pytest.mark.asyncio
async def test_a_reworded_decision_is_found_even_with_little_text_overlap() -> None:
    dense = Dense({"old": DENSE_SAME_TOPIC + 0.05, "other": 0.5})
    found = await find_superseded(NEW, Memory([OLD, OTHER]), dense)
    assert found == ["old"]


@pytest.mark.asyncio
async def test_a_different_decision_below_the_floor_is_left_alone() -> None:
    dense = Dense({"old": DENSE_SAME_TOPIC - 0.05, "other": 0.3})
    assert await find_superseded(NEW, Memory([OLD, OTHER]), dense) == []


@pytest.mark.asyncio
async def test_when_embeddings_fail_the_dice_rule_still_works() -> None:
    near = decision("near", "배포는 평일 오전 10시부터 오후 6시 사이에만 진행한다")
    found = await find_superseded(NEW, Memory([near, OTHER]), Dense(fail=True))
    assert found == ["near"]
    scores, floor = await topic_scores("배포는 평일 오전", [near], Dense(fail=True))
    assert floor == SAME_TOPIC and "near" in scores


@pytest.mark.asyncio
async def test_without_dense_it_is_the_old_dice_rule() -> None:
    near = decision("near", "배포는 평일 오전 10시부터 오후 6시 사이에만 진행한다")
    assert await find_superseded(NEW, Memory([near, OTHER]), None) == ["near"]


@pytest.mark.asyncio
async def test_event_cards_and_rejected_decisions_are_never_replaced() -> None:
    event = decision("event", NEW.extra["decision"])
    event = event.model_copy(update={"kind": "incident"})
    rejected = decision("rej", "배포 시간을 늘리지 않는다", outcome="rejected")
    dense = Dense({"event": 0.99, "rej": 0.99})
    assert await find_superseded(NEW, Memory([event, rejected]), dense) == []
