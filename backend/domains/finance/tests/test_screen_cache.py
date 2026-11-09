"""화면 응답 기억하기(`screen_cache.py`).

같은 장부면 다시 계산하지 않고, 결정 뒤에는 새로 만든다.
"""

from __future__ import annotations

import asyncio
import time

import httpx
import pytest

from ax_agent_template import events
from finance_agent import books as bk
from finance_agent import handlers
from finance_agent import screen_cache as sc
from finance_agent.main import app
from finance_agent.sources import MockSources

HUMAN = {"Authorization": "Bearer human:taeyoon"}


@pytest.fixture(autouse=True)
def _live_like(monkeypatch: pytest.MonkeyPatch, fixed_now) -> list[bk.Books]:
    """실제 모드처럼 기억하기를 켜고, 장부는 부를 때마다 센다."""
    monkeypatch.setenv("AGENT_TEMPLATE_MOCK", "1")  # 가짜 안전 장치·이벤트
    monkeypatch.delenv("AX_SAFETY_URL", raising=False)
    events.reset_mock_bus()
    handlers.reset_state(MockSources())
    monkeypatch.setattr(sc, "enabled", lambda: True)
    sc.CACHE.clear()
    books = bk.seed_books()
    monkeypatch.setattr(bk, "_live_cache", (time.monotonic(), books))
    calls: list[bk.Books] = []

    async def loader() -> bk.Books:
        calls.append(books)
        return books

    handlers.state().books_loader = loader
    yield calls
    sc.CACHE.clear()


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://t")


def test_same_books_answer_from_memory(_live_like: list[bk.Books]) -> None:
    async def run() -> None:
        async with _client() as c:
            first = await c.get("/api/finance/variance")
            made = len(_live_like)
            again = await c.get("/api/finance/variance")
            assert again.headers["x-finance-cache"] == "hit"
            assert again.json() == first.json()
            assert len(_live_like) == made  # 다시 계산하지 않았다
            # 조회값이 다르면 다른 응답이다
            other = await c.get("/api/finance/variance", params={"date": "2026-10-20"})
            assert "x-finance-cache" not in other.headers

    asyncio.run(run())


def test_new_books_serve_the_old_answer_and_remake_behind(
    monkeypatch: pytest.MonkeyPatch, _live_like: list[bk.Books]
) -> None:
    async def run() -> None:
        async with _client() as c:
            await c.get("/api/finance/statements")
            monkeypatch.setattr(bk, "_live_cache", (time.monotonic(), bk.seed_books()))
            stale = await c.get("/api/finance/statements")
            assert stale.headers["x-finance-cache"] == "stale"
            assert sc.CACHE.worker is not None
            await sc.CACHE.worker  # 뒤에서 새 장부로 다시 만든다
            assert (await c.get("/api/finance/statements")).headers["x-finance-cache"] == "hit"
            # 너무 오래된 응답은 주지 않고 새로 만든다
            monkeypatch.setattr(sc, "MAX_STALE_SECONDS", 0.0)
            monkeypatch.setattr(bk, "_live_cache", (time.monotonic(), bk.seed_books()))
            fresh = await c.get("/api/finance/statements")
            assert "x-finance-cache" not in fresh.headers

    asyncio.run(run())


def test_a_decision_drops_remembered_answers() -> None:
    async def run() -> None:
        async with _client() as c:
            await c.get("/api/finance/closing", params={"period": "day", "date": "2026-10-22"})
            assert sc.CACHE.entries
            await c.post(
                "/api/finance/closing/confirm",
                json={"period": "day", "date": "2026-10-22"},
                headers=HUMAN,
            )
            assert not sc.CACHE.entries
            after = await c.get(
                "/api/finance/closing", params={"period": "day", "date": "2026-10-22"}
            )
            assert "x-finance-cache" not in after.headers
            assert after.json()["data"]["status"] == "confirmed"

    asyncio.run(run())


def test_new_books_remake_recently_seen_screens() -> None:
    async def run() -> None:
        async with _client() as c:
            await c.get("/api/finance/cashflow")
            sc.CACHE.on_new_books()
            assert sc.CACHE.worker is not None
            await sc.CACHE.worker
            assert (await c.get("/api/finance/cashflow")).headers["x-finance-cache"] == "hit"

    asyncio.run(run())


def test_only_screens_from_the_books_are_remembered() -> None:
    async def run() -> None:
        async with _client() as c:
            await c.get("/api/finance/self-test/last")
            await c.get("/api/finance/ledger")
            assert not sc.CACHE.entries

    asyncio.run(run())


def test_mock_mode_never_remembers(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(sc, "enabled", lambda: False)

    async def run() -> None:
        async with _client() as c:
            await c.get("/api/finance/variance")
            assert not sc.CACHE.entries

    asyncio.run(run())


def test_starting_the_server_prepares_the_first_screens(monkeypatch: pytest.MonkeyPatch) -> None:
    async def loaded() -> bk.Books:
        return await handlers.state().books_loader()

    monkeypatch.setattr(bk, "load_books", loaded)

    async def run() -> None:
        await sc.prime()
        assert sc.CACHE.worker is not None
        await sc.CACHE.worker
        assert set(sc.FIRST_SCREENS) <= set(sc.CACHE.entries)
        async with _client() as c:
            first = await c.get("/api/finance/overview")
            assert first.headers["x-finance-cache"] == "hit"

    asyncio.run(run())


def _a2a_body(skill: str = "finance_chat_list") -> dict:
    from datetime import UTC, datetime, timedelta

    return {
        "meta": {
            "context_id": "ctx_cache_a2a",
            "token": "mock:dashboard-api",
            "path": [],
            "deadline": (datetime.now(UTC) + timedelta(seconds=30)).isoformat(),
        },
        "skill": skill,
        "input": {"who": "taeyoon"},
    }


def test_a2a_that_changes_nothing_keeps_remembered_screens(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """대화방은 2초마다 A2A로 대화를 다시 읽는다. 그때마다 화면 기억을 버리면 화면이 늘 느리다."""
    monkeypatch.setattr(sc, "_records_fingerprint", lambda: "same")

    async def run() -> None:
        async with _client() as c:
            await c.get("/api/finance/variance")
            await c.post("/a2a", json=_a2a_body())
            assert sc.CACHE.entries
            again = await c.get("/api/finance/variance")
            assert again.headers["x-finance-cache"] == "hit"

    asyncio.run(run())


def test_a2a_that_changes_records_drops_remembered_screens(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    marks = iter(["before", "after"])
    monkeypatch.setattr(sc, "_records_fingerprint", lambda: next(marks))

    async def run() -> None:
        async with _client() as c:
            await c.get("/api/finance/variance")
            await c.post("/a2a", json=_a2a_body())
            assert not sc.CACHE.entries

    asyncio.run(run())


def test_records_fingerprint_follows_finance_records() -> None:
    from finance_agent import persist

    before = persist.fingerprint(handlers.state())
    assert persist.fingerprint(handlers.state()) == before
    handlers.state().records.recon_notes["pay_x"] = "확인함"
    assert persist.fingerprint(handlers.state()) != before
