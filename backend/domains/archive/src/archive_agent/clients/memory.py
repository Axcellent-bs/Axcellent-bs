"""회사 기억(memory) 연결: 카드 저장·검색·상태 변경.

실제 연동(3단계)은 같은 `MemoryClient` 인터페이스로 memory 서비스를 부른다. 저장 같은 상태 변경은
호출하는 쪽이 `execute("memory.card_write")`가 `executed`를 돌려준 뒤에만 부른다.

카드의 상태는 계약 `Card.status`(`active`·`superseded`·`retired`)에 후보 여부
(`Card.candidate`)를 더해 다룬다. 후보는 `candidate`가 true인 카드다
(`extra["candidate"]`도 같이 둔다).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol

from archive_agent.config import Clock
from archive_agent.store import Store
from archive_agent.util import dumps, loads, similarity
from ax_agent_template import company_scope
from ax_contracts.schemas import Card


def owned(tenant_id: str | None) -> bool:
    """이 카드를 지금 요청 회사에 보여도 되는가.

    저장소는 회사마다 파일이 따로라(#1773) 다른 회사 파일에는 그 회사 카드만 있다(예전 카드는
    회사 칸이 기본값 `shop-demo`로 남았을 수 있어 칸으로 거르지 않는다). 캣북스 파일에는 분리 전
    다른 회사 요청이 쓴 카드가 남아 있을 수 있어서, 캣북스 요청이면 칸이 캣북스인 것만 보인다.
    요청 밖(스스로 도는 루틴)은 예전처럼 다 본다.
    """
    company = company_scope.active()
    if company != company_scope.CATBOOKS:
        return True
    return company_scope.scope_id(tenant_id) == company_scope.CATBOOKS


def card_state(card: Card) -> str:
    """검색·표시에 쓰는 상태: `candidate`·`active`·`superseded`·`retired`.

    후보는 계약의 `Card.candidate`가 true인 카드다(`status`는 `active`). 옛 방식
    (`extra["candidate"]`)으로 저장된 카드도 후보로 읽는다. 대체·폐기된 카드는 그 상태가 우선이다.
    """
    if card.status == "active" and (card.candidate or card.extra.get("candidate")):
        return "candidate"
    return card.status


def card_text(card: Card) -> str:
    """유사도 비교에 쓰는 카드 본문(제목·결정·이유·본문·태그)."""
    extra = card.extra
    parts = [card.title, str(extra.get("decision", "")), str(extra.get("reason", "")), card.body]
    parts.append(" ".join(card.tags))
    return " ".join(p for p in parts if p)


@dataclass(frozen=True)
class CardHit:
    card: Card
    score: float


class MemoryClient(Protocol):
    async def write_card(self, card: Card) -> str: ...

    async def get_card(self, card_id: str) -> Card | None: ...

    async def search_cards(
        self,
        query: str,
        *,
        area: str | None = None,
        kinds: list[str] | None = None,
        states: list[str] | None = None,
        limit: int = 5,
    ) -> list[CardHit]: ...

    async def list_cards(
        self, *, area: str | None = None, states: list[str] | None = None
    ) -> list[Card]: ...

    async def set_status(
        self, card_id: str, status: str, superseded_by: str | None = None
    ) -> None: ...

    async def set_candidate(self, card_id: str, candidate: bool) -> None: ...


class MockMemoryClient:
    """SQLite(`mock_cards`)에 카드를 쌓는 목 저장소. 검색은 글자 2-gram 유사도다."""

    def __init__(self, store: Store, clock: Clock) -> None:
        self._store = store
        self._clock = clock

    async def write_card(self, card: Card) -> str:
        self._store.execute(
            "INSERT OR REPLACE INTO mock_cards(card_id, tenant_id, data, created_at)"
            " VALUES (?, ?, ?, ?)",
            (
                card.card_id,
                card.tenant_id,
                dumps(card.model_dump(mode="json")),
                card.created_at.isoformat(),
            ),
        )
        return card.card_id

    async def get_card(self, card_id: str) -> Card | None:
        row = self._store.one("SELECT data FROM mock_cards WHERE card_id = ?", (card_id,))
        card = Card.model_validate(loads(row["data"])) if row else None
        return card if card is not None and owned(card.tenant_id) else None

    async def list_cards(
        self, *, area: str | None = None, states: list[str] | None = None
    ) -> list[Card]:
        rows = self._store.all("SELECT data FROM mock_cards ORDER BY created_at, card_id")
        cards = [Card.model_validate(loads(r["data"])) for r in rows]
        # 파일은 회사마다 따로지만(#1773), 그 전에 같은 파일에 쌓인 다른 회사 카드는 행으로도 거른다
        return [
            c for c in cards
            if owned(c.tenant_id) and _match(c, area, None, states)
        ]  # fmt: skip

    async def search_cards(
        self,
        query: str,
        *,
        area: str | None = None,
        kinds: list[str] | None = None,
        states: list[str] | None = None,
        limit: int = 5,
    ) -> list[CardHit]:
        hits = [
            CardHit(card, round(similarity(query, card_text(card)), 4))
            for card in await self.list_cards(area=area, states=states)
            if _match(card, area, kinds, states)
        ]
        hits.sort(key=lambda h: (-h.score, h.card.card_id))
        return hits[:limit]

    async def set_status(self, card_id: str, status: str, superseded_by: str | None = None) -> None:
        card = await self.get_card(card_id)
        if card is None:
            raise KeyError(card_id)
        updated = card.model_copy(update={"status": status, "superseded_by": superseded_by})
        await self.write_card(Card.model_validate(updated.model_dump()))

    async def set_candidate(self, card_id: str, candidate: bool) -> None:
        card = await self.get_card(card_id)
        if card is None:
            raise KeyError(card_id)
        extra = {**card.extra, "candidate": candidate}
        tags = [t for t in card.tags if t != "candidate"] + (["candidate"] if candidate else [])
        update = {"extra": extra, "tags": tags, "candidate": candidate}
        await self.write_card(Card.model_validate(card.model_copy(update=update).model_dump()))


def _match(card: Card, area: str | None, kinds: list[str] | None, states: list[str] | None) -> bool:
    if area is not None and card.extra.get("area") != area:
        return False
    if kinds is not None and card.kind not in kinds:
        return False
    return not (states is not None and card_state(card) not in states)
