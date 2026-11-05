"""LLM 호출이 실패한 결과(크레딧·연결·형식 오류)는 저장하지 않는다. 다음 요청이 다시 시도한다.

실제 LLM을 부르지 않는다.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from ax_agent_template.llm import LLMClient
from ax_contracts.schemas import AgentRequest, Evidence, RequestMeta
from data_agent import handlers, llm_failure
from data_agent.response_cache import ResponseCache


def _request() -> AgentRequest:
    meta = RequestMeta(context_id="ctx_t", token="x",
                       deadline=datetime.now(UTC) + timedelta(seconds=30))  # fmt: skip
    return AgentRequest(meta=meta, skill="trend_popular", input={})


def test_the_data_llm_wrapper_marks_a_failed_call(monkeypatch: pytest.MonkeyPatch) -> None:
    async def boom(self, *args: Any, **kwargs: Any) -> Any:  # noqa: ANN001
        raise RuntimeError("402 credit")

    monkeypatch.setattr(LLMClient, "structured", boom)
    monkeypatch.setattr(LLMClient, "embed", boom)

    async def go() -> llm_failure.Scope:
        with llm_failure.scope() as s:
            with pytest.raises(RuntimeError):
                await handlers.TimedLLMClient("data").structured("p", object)
        return s

    s = asyncio.run(go())
    assert s.failed and s.reason == "RuntimeError"
    assert not llm_failure.failed()  # 범위 밖은 깨끗하다


def test_no_settings_or_daily_cap_is_not_a_failure() -> None:
    """설정 없음·하루 상한은 다시 시도해도 같아서 대체 결과를 저장해도 된다."""
    from ax_agent_template.llm import LLMNotConfiguredError
    from data_agent.llm_budget import LLMDailyCapReached

    with llm_failure.scope() as s:
        llm_failure.mark(LLMNotConfiguredError("no key"))
        llm_failure.mark(LLMDailyCapReached("cap"))
    assert not s.failed


def test_inner_scope_failure_reaches_the_outer_scope() -> None:
    with llm_failure.scope() as outer:
        with llm_failure.scope() as inner:
            llm_failure.mark(TimeoutError())
        assert inner.failed
    assert outer.failed and outer.reason == "TimeoutError"


def test_screen_results_computed_with_a_failed_llm_are_not_stored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(handlers, "RESPONSES", ResponseCache(tmp_path / "responses.db"))
    calls = {"n": 0}

    def compute(fail: bool):  # noqa: ANN202
        async def run() -> tuple[str, dict[str, Any], Evidence]:
            calls["n"] += 1
            if fail:
                llm_failure.mark(RuntimeError("credit"))  # 계산 안에서 LLM이 실패함
            evidence = Evidence(queries=[], data_summary="", collected_at=datetime.now(UTC))
            return "known", {"n": calls["n"]}, evidence
        return run

    first = asyncio.run(handlers._stored(_request(), "trend_popular", {}, compute(True)))
    assert first.output == {"n": 1}
    second = asyncio.run(handlers._stored(_request(), "trend_popular", {}, compute(False)))
    assert second.output == {"n": 2}  # 저장하지 않았으니 다시 계산
    third = asyncio.run(handlers._stored(_request(), "trend_popular", {}, compute(False)))
    assert third.output == {"n": 2} and calls["n"] == 2  # 성공한 결과는 저장해 읽기만
