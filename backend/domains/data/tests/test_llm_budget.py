"""data LLM 하루 상한: 닿으면 부르지 않고 그 칸은 대체(고정 문장·unknown) + "LLM 하루 상한"."""

from __future__ import annotations

import asyncio
from datetime import date
from pathlib import Path

import pytest

from ax_agent_template.llm import LLMNotConfiguredError, MockLLMClient
from data_agent import handlers, llm_budget
from data_agent.explain import LLM_DAILY_CAP, explain
from data_agent.llm_budget import Budget, LLMDailyCapReached, no_llm
from data_agent.skills.data_sources import llm_usage_today

DAY = date(2026, 11, 5)


def test_caps_come_from_settings_and_the_count_survives_restart(tmp_path: Path) -> None:
    path = tmp_path / "budget.db"
    first = Budget(path)
    first.add(1, 1200, day=DAY)
    first.add(1, 800, day=DAY)
    again = Budget(path).usage({"DATA_LLM_DAILY_CALLS": "5"}, day=DAY)  # 다시 켜도 남는다
    assert (again.calls, again.tokens, again.call_cap) == (2, 2000, 5)
    assert again.token_cap == llm_budget.DEFAULT_TOKENS and not again.reached
    assert Budget(path).usage({"DATA_LLM_DAILY_CALLS": "x"}, day=DAY).call_cap == 200


def test_reaching_either_cap_stops_the_call(tmp_path: Path) -> None:
    budget = Budget(tmp_path / "b.db")
    budget.add(2, 10, day=budget.today())
    with pytest.raises(LLMDailyCapReached) as calls:
        budget.check({"DATA_LLM_DAILY_CALLS": "2"})
    assert "LLM 하루 상한" in str(calls.value) and "호출 2/2회" in str(calls.value)
    with pytest.raises(LLMDailyCapReached):
        budget.check({"DATA_LLM_DAILY_TOKENS": "10"})
    budget.check({"DATA_LLM_DAILY_CALLS": "3", "DATA_LLM_DAILY_TOKENS": "100"})  # 아직 아님
    # 설정 없음과 같은 대체 길로 가되 이유만 다르다
    assert isinstance(calls.value, LLMNotConfiguredError)
    assert no_llm(calls.value).startswith("LLM 하루 상한에 닿아")
    assert no_llm(LLMNotConfiguredError("x")) == "LLM 설정이 없어"


def test_data_llm_does_not_call_past_the_cap_and_counts_each_request(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    budget = Budget(tmp_path / "b.db")
    monkeypatch.setattr(llm_budget, "BUDGET", budget)
    client = handlers.TimedLLMClient("data")
    client.record_usage("ctx", "m", 30, 20, True)  # 요청 한 번 = 호출 1 + 토큰 50
    now = budget.usage({})
    assert (now.calls, now.tokens) == (1, 50)
    monkeypatch.setenv("DATA_LLM_DAILY_CALLS", "1")
    with pytest.raises(LLMDailyCapReached):
        asyncio.run(client.structured("p", dict))  # 부르기 전에 막힌다 (HTTP 없음)
    usage = llm_usage_today({"DATA_LLM_DAILY_CALLS": "1"})
    assert usage.reached and usage.calls == 1 and "LLM 하루 상한" in (usage.note or "")


class CapLLM(MockLLMClient):
    async def structured(self, *a, **k):  # noqa: ANN002, ANN003, ANN201
        raise LLMDailyCapReached("LLM 하루 상한에 닿아 (오늘 호출 200/200회)")


def test_explanation_falls_back_with_the_daily_cap_reason() -> None:
    said = asyncio.run(explain("trend", {"급상승 수": 1}, CapLLM()))
    assert said.fallback and said.reason == LLM_DAILY_CAP
    assert "LLM 하루 상한" in (said.note() or "")
