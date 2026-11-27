"""HR memory 카드 읽기를 회사로 거른다 (11/27, 회사 없음 = 캣북스만).

memory 서비스가 회사를 몰라 여러 회사 카드를 섞어 줘도, 요청 회사(회사 없으면 캣북스) 카드만
대화 사실·지시문에 들어간다. 요청 회사는 `tenant_id` 쿼리로 넘긴다(아카이브와 같은 방식).
"""

from datetime import UTC, datetime
from typing import Any

import httpx
import pytest

from ax_agent_template import company_scope
from ax_contracts.schemas import Card
from hr_agent.clients.real import HttpMemoryClient

NUBI = "nubi-tour"


def _card(card_id: str, tenant: str) -> dict[str, Any]:
    card = Card(
        card_id=card_id,
        kind="decision",
        title=f"[결정] {card_id}",
        body="결정: 테스트",
        context_id="ctx_x_0001",
        author="planning",
        confidence="high",
        created_at=datetime(2026, 11, 27, tzinfo=UTC),
        extra={"area": "payment", "decision": "테스트"},
    ).model_dump(mode="json")
    card["tenant_id"] = tenant
    return card


class MixedMemory:
    """회사를 모르고 모든 회사 카드를 섞어 주는 memory 서비스."""

    def __init__(self) -> None:
        self.cards = {c["card_id"]: c for c in (_card("cat-1", "shop-demo"), _card("nubi-1", NUBI))}
        self.params: list[dict[str, str]] = []

    def handler(self, request: httpx.Request) -> httpx.Response:
        self.params.append(dict(request.url.params))
        path = request.url.path
        if path == "/api/memory/cards/search":
            hits = [{"card": c, "score": 0.9} for c in self.cards.values()]
            return httpx.Response(200, json={"data": hits})
        if path == "/api/memory/cards":
            return httpx.Response(200, json={"data": list(self.cards.values())})
        card = self.cards.get(path.rsplit("/", 1)[1])
        return httpx.Response(404) if card is None else httpx.Response(200, json={"data": card})


@pytest.fixture
def mixed() -> tuple[MixedMemory, HttpMemoryClient]:
    fake = MixedMemory()
    client = httpx.AsyncClient(transport=httpx.MockTransport(fake.handler))
    return fake, HttpMemoryClient("http://memory.test", http_client=client)


@pytest.mark.asyncio
async def test_no_company_sees_only_catbooks_cards(mixed) -> None:
    fake, memory = mixed
    assert company_scope.active() is None
    assert [c.card_id for c in await memory.list_cards()] == ["cat-1"]
    assert [h.card.card_id for h in await memory.search_cards("x")] == ["cat-1"]
    assert await memory.get_card("nubi-1") is None
    assert await memory.get_card("cat-1") is not None
    assert all("tenant_id" not in p for p in fake.params)  # 회사가 안 걸렸으면 넘기지 않는다


@pytest.mark.asyncio
async def test_company_request_sees_only_its_cards_and_sends_the_company(mixed) -> None:
    fake, memory = mixed
    with company_scope.using(NUBI):
        assert [c.card_id for c in await memory.list_cards()] == ["nubi-1"]
        assert [h.card.card_id for h in await memory.search_cards("x")] == ["nubi-1"]
        assert await memory.get_card("cat-1") is None
    assert fake.params and all(p.get("tenant_id") == NUBI for p in fake.params)
