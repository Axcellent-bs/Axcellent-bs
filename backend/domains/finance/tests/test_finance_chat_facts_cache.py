"""재무 대화 사실 묶음 기억하기: 대화 한 번마다 화면 계산 6개를 다시 하지 않는다."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from finance_agent.chat import facts


@pytest.fixture
def world(monkeypatch: pytest.MonkeyPatch) -> dict[str, Any]:
    w: dict[str, Any] = {"books": 1, "records": "r1", "built": 0}

    async def build() -> dict[str, Any]:
        w["built"] += 1
        await asyncio.sleep(0)
        return {"n": w["built"], "books": w["books"], "records": w["records"]}

    monkeypatch.setattr(facts, "enabled", lambda: True)
    monkeypatch.setattr(facts, "_collect_now", build)
    monkeypatch.setattr(facts, "_books_id", lambda: w["books"])
    monkeypatch.setattr(facts, "_records_key", lambda: w["records"])
    facts.CACHE.clear()
    yield w
    facts.CACHE.clear()


def test_same_books_and_records_reuse_facts(world: dict[str, Any]) -> None:
    async def run() -> None:
        first = await facts.collect()
        again = await facts.collect()
        assert first is again and world["built"] == 1

    asyncio.run(run())


def test_changed_records_wait_for_new_facts(world: dict[str, Any]) -> None:
    """결산 확정·통장 파일 같은 사람 기록이 바뀌면 낡은 사실로 답하지 않는다."""

    async def run() -> None:
        await facts.collect()
        world["records"] = "r2"
        out = await facts.collect()
        assert out["records"] == "r2" and world["built"] == 2

    asyncio.run(run())


def test_new_books_answer_now_and_rebuild_behind(world: dict[str, Any]) -> None:
    async def run() -> None:
        await facts.collect()
        world["books"] = 2
        out = await facts.collect()
        assert out["books"] == 1  # 바로 답한다
        await facts.CACHE.task
        await asyncio.sleep(0)
        assert (await facts.collect())["books"] == 2 and world["built"] == 2

    asyncio.run(run())


def test_too_old_facts_are_rebuilt(world: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    async def run() -> None:
        await facts.collect()
        world["books"] = 2
        facts.CACHE.made -= facts.MAX_STALE_SECONDS + 1
        assert (await facts.collect())["books"] == 2

    asyncio.run(run())


def test_concurrent_requests_build_once(world: dict[str, Any]) -> None:
    async def run() -> None:
        outs = await asyncio.gather(*(facts.collect() for _ in range(5)))
        assert world["built"] == 1 and all(o is outs[0] for o in outs)

    asyncio.run(run())


def test_warm_builds_before_first_question(world: dict[str, Any]) -> None:
    async def run() -> None:
        facts.warm()
        await facts.CACHE.task
        await asyncio.sleep(0)
        assert world["built"] == 1
        facts.warm()  # 이미 새것이면 다시 만들지 않는다
        await facts.collect()
        assert world["built"] == 1

    asyncio.run(run())


def test_new_books_rebuild_only_after_recent_chat(world: dict[str, Any]) -> None:
    async def run() -> None:
        facts.on_new_books()
        assert world["built"] == 0  # 아무도 대화하지 않았으면 계산하지 않는다
        await facts.collect()
        facts.on_new_books()
        await facts.CACHE.task
        assert world["built"] == 2

    asyncio.run(run())


def test_mock_mode_always_builds(world: dict[str, Any], monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(facts, "enabled", lambda: False)

    async def run() -> None:
        await facts.collect()
        await facts.collect()
        assert world["built"] == 2

    asyncio.run(run())
