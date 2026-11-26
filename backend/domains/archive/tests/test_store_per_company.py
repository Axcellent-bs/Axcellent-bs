"""회사 분리(11/25, 로드맵 6단계): 아카이브 저장소는 요청 회사마다 파일을 따로 연다.

누비투어로 들어오면 캣북스 결정 카드·후보 카드(`mock_cards`)가 보이지 않는다.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from pathlib import Path

from archive_agent.clients.memory import MockMemoryClient
from archive_agent.store import Store, company_path
from ax_agent_template import company_scope


def _rows(store: Store) -> list[str]:
    return [r["card_id"] for r in store.all("SELECT card_id FROM mock_cards ORDER BY card_id")]


def test_cards_are_per_company(tmp_path: Path) -> None:
    store = Store(str(tmp_path / "archive.db"))
    put = "INSERT INTO mock_cards(card_id, data, created_at) VALUES (?, '{}', 'x')"
    store.execute(put, ("cat-1",))
    with company_scope.using("nubi-tour"):
        assert _rows(store) == []
        store.execute(put, ("nubi-1",))
        assert _rows(store) == ["nubi-1"]
    assert _rows(store) == ["cat-1"]
    with company_scope.using("catbooks"):
        assert _rows(store) == ["cat-1"]
    assert (tmp_path / "archive.nubi-tour.db").exists()
    store.close()


def test_mock_memory_list_is_per_company() -> None:
    store = Store()
    memory = MockMemoryClient(store, lambda: datetime(2026, 11, 25, tzinfo=UTC))
    with company_scope.using("nubi-tour"):
        assert asyncio.run(memory.list_cards()) == []
    assert company_path(":memory:", "nubi-tour") == ":memory:"
    assert Path(company_path("/d/archive.db", "nubi-tour")).name == "archive.nubi-tour.db"


def _card(card_id: str, tenant: str):  # type: ignore[no-untyped-def]
    from ax_contracts.schemas import Card

    return Card.model_validate({
        "card_id": card_id, "tenant_id": tenant, "kind": "decision", "title": card_id,
        "body": "배송 지연 환불 결정", "context_id": f"ctx-{card_id}", "status": "active",
        "author": "archive", "confidence": "high", "created_at": "2026-11-25T00:00:00+00:00",
    })  # fmt: skip


def test_catbooks_file_hides_cards_other_companies_wrote_before_split() -> None:
    """분리(#1773) 전에 캣북스 파일에 쌓인 누비투어 카드는 캣북스 요청에도 보이지 않는다."""
    store = Store()
    memory = MockMemoryClient(store, lambda: datetime(2026, 11, 26, tzinfo=UTC))
    asyncio.run(memory.write_card(_card("cat-1", "shop-demo")))
    asyncio.run(memory.write_card(_card("old-nubi", "c-cf2f0d")))
    with company_scope.using("catbooks"):
        assert [c.card_id for c in asyncio.run(memory.list_cards())] == ["cat-1"]
        hits = asyncio.run(memory.search_cards("배송 지연 환불"))
        assert [h.card.card_id for h in hits] == ["cat-1"]
        assert asyncio.run(memory.get_card("old-nubi")) is None
    with company_scope.using("c-cf2f0d"):  # 누비투어 검색에 캣북스 카드 없음
        assert asyncio.run(memory.search_cards("배송 지연 환불")) == []
        assert asyncio.run(memory.get_card("cat-1")) is None
        asyncio.run(memory.write_card(_card("nubi-1", "shop-demo")))  # 회사 칸이 기본값이어도
        assert [c.card_id for c in asyncio.run(memory.list_cards())] == ["nubi-1"]


def test_audit_is_per_company() -> None:
    from archive_agent.audit import AuditLog

    store = Store()
    audit = AuditLog(store, lambda: datetime(2026, 11, 26, tzinfo=UTC))
    audit.record("ctx-cat", "a")
    audit.record("ctx-old-nubi", "b", tenant_id="c-cf2f0d")  # 분리 전 캣북스 파일에 남은 것
    with company_scope.using("catbooks"):
        assert [r["context_id"] for r in audit.list()] == ["ctx-cat"]
        assert [r["context_id"] for r in audit.contexts()] == ["ctx-cat"]
    with company_scope.using("c-cf2f0d"):
        assert audit.list() == []
        audit.record("ctx-nubi", "c")
        rows = audit.list()
        assert [(r["context_id"], r["tenant_id"]) for r in rows] == [("ctx-nubi", "c-cf2f0d")]


def test_http_memory_passes_company_and_filters() -> None:
    import httpx

    from archive_agent.clients.real import HttpMemoryClient

    seen: list[dict[str, str]] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(dict(request.url.params))
        cards = [_card("cat-1", "catbooks").model_dump(mode="json"),
                 _card("nubi-1", "c-cf2f0d").model_dump(mode="json")]  # fmt: skip
        return httpx.Response(200, json={"data": cards})

    client = HttpMemoryClient("http://m", httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    with company_scope.using("c-cf2f0d"):
        assert [c.card_id for c in asyncio.run(client.list_cards())] == ["nubi-1"]
    assert seen[-1]["tenant_id"] == "c-cf2f0d"
