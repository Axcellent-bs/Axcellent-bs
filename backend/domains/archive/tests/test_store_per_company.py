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
