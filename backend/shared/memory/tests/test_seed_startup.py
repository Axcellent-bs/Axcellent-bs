"""서버 시작 때 정책 시드를 넣고 검색에 올린다."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest
from fastapi.testclient import TestClient

from ax_agent_template.llm import MockLLMClient
from ax_memory import handlers
from ax_memory.embed import LLMEmbedder
from ax_memory.search import InMemoryVectorIndex, search_cards
from ax_memory.seed import POLICY_SECTIONS
from ax_memory.seed.startup import ENV_SWITCH, seed_and_index
from ax_memory.store import InMemoryCardStore


class _State:
    def __init__(self, embedder: Any = None) -> None:
        self.store = InMemoryCardStore()
        self.index = InMemoryVectorIndex()
        self.embedder = embedder or LLMEmbedder(MockLLMClient())


class _BrokenEmbedder:
    async def embed(self, texts: list[str]) -> list[list[float]]:
        raise RuntimeError("embedding model not set")


def test_seeded_cards_are_searchable() -> None:
    state = _State()
    report = asyncio.run(seed_and_index(state))
    assert report is not None and report.created == len(POLICY_SECTIONS)
    hits = asyncio.run(
        search_cards(state.store, state.index, state.embedder, "환불 정책", kind=["policy"])
    )
    assert hits and all(h.card.kind == "policy" for h in hits)


def test_second_start_does_not_duplicate_cards() -> None:
    state = _State()
    asyncio.run(seed_and_index(state))
    report = asyncio.run(seed_and_index(state))
    assert report is not None and report.created == 0
    assert len(state.store.list(kind="policy")) == len(POLICY_SECTIONS)


def test_embedding_failure_keeps_cards_and_does_not_raise() -> None:
    state = _State(_BrokenEmbedder())
    report = asyncio.run(seed_and_index(state))
    assert report is not None and report.created == len(POLICY_SECTIONS)
    assert len(state.store.list(kind="policy")) == len(POLICY_SECTIONS)


def test_server_start_seeds_and_the_switch_turns_it_off(monkeypatch: pytest.MonkeyPatch) -> None:
    from ax_memory.main import app

    monkeypatch.setenv("MEMORY_MOCK", "1")
    handlers.reset_state(None)
    with TestClient(app):
        assert len(handlers.get_state().store.list(kind="policy")) == len(POLICY_SECTIONS)

    monkeypatch.setenv(ENV_SWITCH, "off")
    handlers.reset_state(None)
    with TestClient(app):
        assert handlers.get_state().store.list(kind="policy") == []
    handlers.reset_state(None)
