"""판매자 정산 받기(`settlement_feed.py`): 고객지원 응답을 그대로 받고, 못 받으면 None이다."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from finance_agent import settlement_feed as feed


def test_weeks_must_all_arrive(support, kit) -> None:
    support.weeks["2026-W41"] = kit.statement("2026-W41", [{"seller_id": "s", "payout": 1}])
    got = asyncio.run(feed.weeks(["2026-W42", "2026-W41", "2026-W41"]))
    assert got is not None and set(got) == {"2026-W41", "2026-W42"}
    assert got["2026-W41"]["statements"][0]["payout"] == 1
    assert support.calls == [("seller_settlement_weeks", {"periods": ["2026-W41", "2026-W42"]})]

    async def partial(skill: str, payload: dict[str, Any]) -> Any:
        result = await support(skill, payload)
        result.output["weeks"].pop("2026-W42")
        return result

    feed.set_caller(partial)
    assert asyncio.run(feed.weeks(["2026-W41", "2026-W42"])) is None  # 일부만 쓰지 않는다


def test_many_weeks_are_asked_in_chunks(support) -> None:
    periods = [f"2026-W{k:02d}" for k in range(1, 41)]
    got = asyncio.run(feed.weeks(periods))
    assert got is not None and len(got) == 40
    sizes = [len(p["periods"]) for s, p in support.calls if s == "seller_settlement_weeks"]
    assert sizes == [30, 10]


def test_failures_are_none_and_not_retried_at_once(support) -> None:
    support.down = True
    assert asyncio.run(feed.batches()) is None
    assert asyncio.run(feed.batches()) is None
    assert len(support.calls) == 1  # 실패도 잠깐 기억한다

    async def boom(skill: str, payload: dict[str, Any]) -> Any:
        raise RuntimeError("down")

    feed.set_caller(boom)
    assert asyncio.run(feed.deadlines()) is None


def test_bad_period_is_reported_only_for_the_settlement_view(support) -> None:
    with pytest.raises(feed.InvalidPeriod):
        asyncio.run(feed.settlement("41"))


def test_does_not_call_support_before_it_takes_the_skill(monkeypatch) -> None:
    """고객지원이 정산 skill을 맡은 서비스를 등록하기 전에는 부르지 않는다 (할 일함에 안 넘김)."""
    feed.set_caller(None)
    called: list[str] = []

    async def fake_call(target: str, request: Any) -> Any:
        called.append(request.skill)
        raise AssertionError("부르면 안 된다")

    monkeypatch.setattr(feed, "call_agent", fake_call)
    monkeypatch.setattr(feed, "route_target", lambda target, skill: target)
    assert asyncio.run(feed.settlement(None)) is None
    assert called == []
