"""단계별 기준 시간: 단계가 보통 걸리는 시간을 기록에 남기고, 호출 마감을 기준 시간에서 계산한다.

멘토 피드백: 모든 단계를 30초 하나로 재면 쉬운 일을 많이 처리한 쪽에 유리하고 오래 걸리는 일에
불리하다. 총괄은 부서를 한 번 부를 때 30초에서 끊고 다시 시도했기 때문에, 일이 큰 단계(영향 분석)는
일을 못해서가 아니라 마감이 짧아서 재시도로 기록됐다. 기준 시간이 없는 단계는 지금처럼 30초다.
"""

from __future__ import annotations

import pytest

from ax_orchestrator.engine import DEADLINE_FACTOR, MAX_DEADLINE_SECONDS
from ax_orchestrator.mock.harness import new_incident
from ax_orchestrator.models import Step, StepRun
from ax_orchestrator.plans.scenario_a import PROMISED_SECONDS, scenario_a
from ax_orchestrator.store import step_from_dict, step_to_dict
from ax_orchestrator.timeline import step_timings


def _step(**kw) -> Step:
    return Step("s", "some_skill", "시험 단계", **kw)


# ---- 호출 마감 -------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_step_without_a_promise_keeps_the_default_deadline(harness) -> None:
    engine = await harness.start()
    assert engine.deadline_seconds(_step()) == engine.step_timeout == 30.0


@pytest.mark.asyncio
async def test_a_long_step_waits_for_a_multiple_of_its_promise(harness) -> None:
    engine = await harness.start()
    assert engine.deadline_seconds(_step(expected_seconds=60.0)) == 60.0 * DEADLINE_FACTOR


@pytest.mark.asyncio
async def test_a_short_promise_never_shortens_the_default_deadline(harness) -> None:
    """기준 시간이 10초라고 20초에 끊으면 기존보다 나빠진다. 기본 마감 밑으로는 내리지 않는다."""
    engine = await harness.start()
    assert engine.deadline_seconds(_step(expected_seconds=10.0)) == engine.step_timeout


@pytest.mark.asyncio
async def test_the_deadline_has_a_ceiling_so_a_stuck_agent_cannot_hold_the_incident(
    harness,
) -> None:
    engine = await harness.start()
    assert engine.deadline_seconds(_step(expected_seconds=3600.0)) == MAX_DEADLINE_SECONDS


@pytest.mark.asyncio
async def test_an_explicit_step_timeout_wins_over_the_promise(harness) -> None:
    engine = await harness.start()
    assert engine.deadline_seconds(_step(timeout=180.0, expected_seconds=60.0)) == 180.0


# ---- 기록 ------------------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_promise_is_recorded_on_each_step_run(harness) -> None:
    engine = await harness.start()
    inc = new_incident()
    await engine.run(inc, scenario_a())
    assert inc.steps["impact"].promised_seconds == PROMISED_SECONDS["impact"]
    assert inc.steps["diagnose"].promised_seconds == PROMISED_SECONDS["diagnose"]
    # 기준 시간을 적지 않은 단계(복구 검증, 원인 확인 요청)는 비어 있다
    assert inc.steps["recovery"].promised_seconds is None
    assert inc.steps["cause_check"].promised_seconds is None


@pytest.mark.asyncio
async def test_the_request_deadline_follows_the_promise(harness) -> None:
    """영향 분석은 30초가 아니라 기준 시간의 2배(120초)를 기다린다. 나머지는 그대로 30초다."""
    engine = await harness.start()
    inc = new_incident()
    await engine.run(inc, scenario_a())
    sent = {r.skill: (d, r) for d, r in harness.fakes.calls}
    _, impact = sent["impact_analysis"]
    waited = (impact.meta.deadline - inc.steps["impact"].started_at).total_seconds()
    assert 119 <= waited <= 121
    _, diagnose = sent["diagnose_incident"]
    waited = (diagnose.meta.deadline - inc.steps["diagnose"].started_at).total_seconds()
    assert 29 <= waited <= 31


@pytest.mark.asyncio
async def test_the_timings_sent_to_hr_carry_the_promise(harness) -> None:
    engine = await harness.start()
    inc = new_incident()
    await engine.run(inc, scenario_a())
    rows = {row["step_id"]: row for row in step_timings(inc)}
    assert rows["impact"]["promised_seconds"] == PROMISED_SECONDS["impact"]
    assert rows["recovery"]["promised_seconds"] is None


def test_the_promise_survives_saving_and_loading() -> None:
    run = StepRun("impact", "impact_analysis", "영향 분석", promised_seconds=60.0)
    assert step_from_dict(step_to_dict(run)).promised_seconds == 60.0
    old = step_to_dict(run)
    del old["promised_seconds"]  # 이 기능이 들어오기 전에 저장된 기록
    assert step_from_dict(old).promised_seconds is None


def test_the_view_shows_the_promise() -> None:
    assert StepRun("a", "b", "c", promised_seconds=10.0).view()["promised_seconds"] == 10.0


# ---- 기준 시간 표 -----------------------------------------------------------------------------


def test_every_promise_belongs_to_a_real_step() -> None:
    """단계 번호를 바꾸거나 지웠는데 기준 시간 표만 남으면 조용히 어긋난다."""
    plan = scenario_a()
    ids = {s.id for s in plan.steps} | {s.id for s in plan.late_steps}
    assert set(PROMISED_SECONDS) <= ids


def test_the_plan_gets_its_promises_and_the_rest_stay_empty() -> None:
    plan = scenario_a()
    by_id = {s.id: s for s in plan.steps + plan.late_steps}
    assert by_id["impact"].expected_seconds == PROMISED_SECONDS["impact"]
    assert by_id["recovery"].expected_seconds is None
    assert by_id["cause_check"].expected_seconds is None
    assert by_id["end_notice"].expected_seconds is None  # 평가에서 빠지는 사후 정리


def test_every_promise_is_a_positive_number() -> None:
    assert all(v > 0 for v in PROMISED_SECONDS.values())
