"""예진 님 리뷰 작은 것 세 개 (11/26 T). 가짜 LLM만 쓴다.

- 제작안 전체 시간 상한 하나 (다시 쓰기가 있어도 최악 대기는 상한 한 번)
- 외형 낱말 glasses·cap·mask: 와인잔·병뚜껑·마스크팩에서는 걸리지 않는다
- "~하고 있다"(보조 용언)는 동작 하나
"""

from __future__ import annotations

import asyncio
import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest

from planning_agent import plan_checks as pc
from planning_agent import video_plan as vp

pytestmark = pytest.mark.role("planning")


def _load(name: str, file: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, Path(__file__).with_name(file))
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


V = _load("video_plan_helpers_review_small", "test_video_plan.py")
C = _load("company_character_helpers_review_small", "test_plan_company_character.py")
F = _load("feasibility_helpers_review_small", "test_plan_feasibility.py")


# ---------------------------------------------------------------- 전체 시간 상한


class _Clock:
    """video_plan 안의 time만 바꾼다 (asyncio 시계는 그대로). 제작안 LLM 답마다 at초씩 간다."""

    def __init__(self) -> None:
        self.now = 0.0

    def monotonic(self) -> float:
        return self.now


class _SlowLLM(C.SeqLLM):  # type: ignore[name-defined, misc]
    clock: Any = None
    step: float = 0.0

    async def structured(self, prompt, schema, untrusted=None, images=None, context_id=None):
        if schema is vp._PlanOut and self.clock is not None:  # noqa: SLF001
            self.clock.now += self.step
        return await super().structured(prompt, schema, untrusted, images, context_id)


def _write_slow(step: float, monkeypatch: pytest.MonkeyPatch, timeout: float = 150.0) -> Any:
    clock = _Clock()
    monkeypatch.setattr(vp, "time", clock)
    waits: list[float] = []
    real = vp.asyncio.wait_for

    async def wait_for(aw: Any, limit: float) -> Any:
        waits.append(limit)
        return await real(aw, limit)

    monkeypatch.setattr(vp.asyncio, "wait_for", wait_for)
    llm = _SlowLLM(plans=[vp._PlanOut.model_validate(  # noqa: SLF001
        {"concept": "무대 위 춤", "script": "춤춘다", "scenes": F.TRIO})])  # fmt: skip
    llm.clock, llm.step = clock, step
    plan = asyncio.run(vp.write(llm, V._prop(), "ctx", timeout=timeout))  # noqa: SLF001
    return plan, V.plan_calls(llm), waits


def test_no_rewrite_when_the_total_time_is_almost_used(monkeypatch: pytest.MonkeyPatch) -> None:
    """1차 답에 ✕(인물 3명)가 있어도 남은 시간이 REWRITE_MIN_SECONDS보다 적으면 다시 안 부른다."""
    plan, sent, _ = _write_slow(150.0 - vp.REWRITE_MIN_SECONDS + 1, monkeypatch)
    assert len(sent) == 1
    assert plan.written_by == "llm"  # 1차 답을 쓰고 ✕는 검사 기록에 남는다
    assert len(plan.check_log) == 1 and not plan.check_log[0].passed


def test_rewrite_gets_only_the_time_left(monkeypatch: pytest.MonkeyPatch) -> None:
    _, sent, waits = _write_slow(30.0, monkeypatch)
    assert len(sent) == 2
    assert waits[:2] == [150.0, 120.0]  # 두 번째 호출은 남은 시간만 (최악 대기 = 상한 한 번)


def test_total_comes_from_env_but_never_above_the_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv(vp.TOTAL_ENV, "90")
    assert vp.total_seconds(150.0) == 90.0
    assert vp.total_seconds(vp.HUMAN_WAIT_SECONDS) == vp.HUMAN_WAIT_SECONDS  # 사람 기다림은 그대로
    monkeypatch.setenv(vp.TOTAL_ENV, "x")
    assert vp.total_seconds(150.0) == 150.0
    monkeypatch.delenv(vp.TOTAL_ENV)
    assert vp.total_seconds(150.0) == 150.0


# ---------------------------------------------------------------- 외형 낱말


def test_wine_glass_bottle_cap_and_mask_pack_are_not_looks() -> None:
    p = V._prop(company_character=C.VIRTUAL)  # noqa: SLF001
    things = ["She raises two wine glasses", "glasses of wine on the table",
              "drinking glasses on a tray", "She twists a bottle cap",
              "She puts on a mask pack", "a sheet mask on her face", "a mask sheet"]  # fmt: skip
    assert vp.look_problems(p, list(enumerate(things, start=1))) == []
    looks = [(1, "She waves, wearing glasses"), (2, "a red cap"), (3, "a black mask")]
    assert vp.look_problems(p, looks) == [(1, "glasses"), (2, "cap"), (3, "mask")]
    # 같은 글에 물건과 외형이 같이 있으면 외형으로 본다
    assert vp.look_problems(p, [(1, "wearing glasses, holding wine glasses")]) == [(1, "glasses")]


# ---------------------------------------------------------------- 동작 수


def test_auxiliary_hago_itda_is_one_action() -> None:
    assert pc.action_count("", "책을 정리하고 있다") == (1, "")
    assert pc.action_count("", "책을 정리하고 서 있다") == (1, "")
    assert pc.action_count("", "점프하고 회전한다") == (2, "점프하고 회전한다")
    assert pc.action_count("", "인사하고 서점으로 들어간다")[0] == 2  # 서점 ≠ 보조 용언 서
