"""긴 작업(비동기): 몇 분에서 몇 시간 걸리는 부서 작업을 총괄이 붙잡고 기다리지 않는다.

부서가 접수만 알리고(`working` + task_id), 일하는 동안 진행 알림(`task.progress`)을 보내며, 끝나면
완료 이벤트(`task.completed`)를 보낸다. 총괄은 접수 뒤 다른 단계를 계속 돌리다가 완료가 오면 그
단계를 잇는다. 진행 알림이 정해진 시간 넘게 끊기면 응답 끊김으로 단계를 끝낸다.

시험용 가짜 장기 부서(`FakeDepartments`의 `async`)로 확인한다. 실제 부서에는 아직 적용하지 않았다.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable

import pytest

from ax_contracts.schemas import AgentResult, EventEnvelope, EventType, TaskStatus
from ax_orchestrator.engine import MAX_ASYNC_SECONDS
from ax_orchestrator.manager import INTERRUPTED_NOTE, IncidentManager
from ax_orchestrator.mock.fake_departments import DEFAULT_OUTPUTS
from ax_orchestrator.mock.harness import Harness, new_incident
from ax_orchestrator.models import NO_RESPONSE, Incident, Plan, Step, StepRun, now
from ax_orchestrator.plans.scenario_a import scenario_a
from ax_orchestrator.store import step_from_dict, step_to_dict
from ax_orchestrator.timeline import step_timings


async def wait_until(check: Callable[[], bool], timeout: float = 3.0) -> None:
    loop = asyncio.get_running_loop()
    end = loop.time() + timeout
    while not check():
        assert loop.time() < end, "조건이 시간 안에 맞지 않았다"
        await asyncio.sleep(0.01)


def status_of(inc: Incident, step_id: str) -> str | None:
    run = inc.steps.get(step_id)  # 단계 기록은 계획이 돌기 시작해야 생긴다
    return run.status if run else None


def slow_plan(**kw) -> Plan:
    """긴 작업 단계 하나만 있는 계획. 기준 시간이 없어서 전체 대기는 엔진 기본값을 쓴다."""
    return Plan("slow", [Step("slow", "impact_analysis", "긴 작업 (data)", **kw)])


def deliver(engine, inc: Incident, env: EventEnvelope) -> str:
    kind = "progress" if env.type == EventType.TASK_PROGRESS else "completed"
    return engine.receive_async(inc, kind, env.source, env.data)


def degraded(event_id: str = "evt_deg_1") -> EventEnvelope:
    return EventEnvelope(
        event_id=event_id,
        type=EventType.SHOP_DEGRADED,
        occurred_at=now(),
        source="shop-api",
        idempotency_key=event_id,
        data={"component": "payment", "metric_code": "p95_latency_ms", "severity": "high"},
    )


async def start_slow(harness: Harness, plan: Plan | None = None):
    """긴 작업 단계가 접수되어 기다리는 상태까지 돌린다. (엔진, 이벤트, 실행 작업)을 돌려준다."""
    engine = await harness.start()
    harness.fakes.script("data", "impact_analysis", "async")
    inc = new_incident()
    task = asyncio.create_task(engine.run(inc, plan or slow_plan()))
    await wait_until(lambda: status_of(inc, "slow") == "waiting_async")
    return engine, inc, task


# ---- 접수와 이어 가기 ----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_step_waits_without_blocking_the_other_steps(harness) -> None:
    """접수한 뒤 총괄은 다른 단계를 계속 돌린다. 이 단계에 기대는 단계만 기다린다."""
    engine = await harness.start()
    harness.fakes.script("data", "impact_analysis", "async")
    inc = new_incident()
    task = asyncio.create_task(engine.run(inc, scenario_a()))
    await wait_until(lambda: status_of(inc, "impact") == "waiting_async")
    await wait_until(lambda: status_of(inc, "diagnose") == "completed")  # 독립 단계는 돈다
    run = inc.steps["impact"]
    assert run.async_task_id == "tsk_data_1"
    assert run.last_response_at is not None
    assert not task.done()  # 이벤트는 열린 채다
    assert inc.steps["kpi"].status == "pending"  # 영향 분석 결과가 필요한 단계는 기다린다
    harness.fakes.script("data", "impact_analysis")  # 남은 스크립트 없음
    ((dept, req, task_id),) = harness.fakes.async_tasks
    output = DEFAULT_OUTPUTS["impact_analysis"](req)
    done = harness.fakes.task_event("completed", task_id, output=output)
    assert deliver(engine, inc, done) == "applied"
    await asyncio.wait_for(task, 5)
    assert inc.steps["impact"].status == "completed"
    assert inc.steps["kpi"].status == "completed"
    assert inc.status == "resolved"


@pytest.mark.asyncio
async def test_progress_then_completion_makes_the_result_the_step_result(harness) -> None:
    engine, inc, task = await start_slow(harness)
    ((_, _, task_id),) = harness.fakes.async_tasks
    for percent in (20, 60):
        env = harness.fakes.task_event("progress", task_id, percent=percent, note="집계 중")
        assert deliver(engine, inc, env) == "applied"
    run = inc.steps["slow"]
    assert (run.heartbeats, run.progress_percent, run.progress_note) == (2, 60, "집계 중")
    done = harness.fakes.task_event("completed", task_id, output={"affected": 7})
    assert deliver(engine, inc, done) == "applied"
    await asyncio.wait_for(task, 5)
    assert run.status == "completed"
    assert run.output == {"affected": 7}
    assert inc.outputs["slow"] == {"affected": 7}
    assert run.max_silence_seconds is not None


@pytest.mark.asyncio
async def test_a_failure_the_department_reports_fails_the_step(harness) -> None:
    engine, inc, task = await start_slow(harness)
    ((_, _, task_id),) = harness.fakes.async_tasks
    env = harness.fakes.task_event("completed", task_id, status="failed", note="원본 없음")
    assert deliver(engine, inc, env) == "applied"
    await asyncio.wait_for(task, 5)
    run = inc.steps["slow"]
    assert (run.status, run.reason_code) == ("failed", "async_failed")
    assert "원본 없음" in run.note


@pytest.mark.asyncio
async def test_a_completion_that_comes_before_the_step_waits_is_not_lost(harness) -> None:
    """접수 응답과 완료 이벤트가 엇갈려 완료가 먼저 와도 잃지 않는다."""
    engine = await harness.start()
    harness.fakes.script("data", "impact_analysis", "async")
    inc = new_incident()
    early = {
        "task_id": "tsk_data_1",
        "skill": "impact_analysis",
        "status": "done",
        "output": {"n": 1},
    }
    assert engine.receive_async(inc, "completed", "data", early) == "kept"
    await asyncio.wait_for(engine.run(inc, slow_plan()), 5)
    run = inc.steps["slow"]
    assert (run.status, run.output) == ("completed", {"n": 1})


# ---- 응답 끊김 ----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_no_progress_for_too_long_ends_the_step_as_no_response(harness) -> None:
    engine, inc, task = await start_slow(harness)  # 시험용 한계는 0.3초
    await asyncio.wait_for(task, 5)
    run = inc.steps["slow"]
    assert (run.status, run.reason_code) == ("failed", NO_RESPONSE)
    assert run.max_silence_seconds is not None and run.max_silence_seconds >= 0.3
    assert "응답 끊김" in run.note


@pytest.mark.asyncio
async def test_progress_notices_keep_a_long_task_alive_past_the_silence_limit(harness) -> None:
    """총 시간이 끊김 한계(0.3초)보다 길어도 알림이 계속 오면 끊기지 않는다."""
    engine, inc, task = await start_slow(harness)
    ((_, _, task_id),) = harness.fakes.async_tasks
    for _ in range(6):  # 0.9초 동안 0.15초마다
        await asyncio.sleep(0.15)
        deliver(engine, inc, harness.fakes.task_event("progress", task_id))
    assert inc.steps["slow"].status == "waiting_async"
    deliver(engine, inc, harness.fakes.task_event("completed", task_id, output={"ok": 1}))
    await asyncio.wait_for(task, 5)
    run = inc.steps["slow"]
    assert run.status == "completed" and run.heartbeats == 6
    assert (run.max_silence_seconds or 0) < 0.3  # 응답 사이가 한 번도 한계를 넘지 않았다


@pytest.mark.asyncio
async def test_the_total_wait_has_a_limit_even_when_progress_keeps_coming() -> None:
    harness = Harness(async_heartbeat_timeout=5.0, async_total_timeout=0.4)
    engine, inc, task = await start_slow(harness)
    ((_, _, task_id),) = harness.fakes.async_tasks
    while not task.done():
        deliver(engine, inc, harness.fakes.task_event("progress", task_id))
        await asyncio.sleep(0.05)
    run = inc.steps["slow"]
    assert (run.status, run.reason_code) == ("failed", "async_timeout")


@pytest.mark.asyncio
async def test_a_late_completion_after_the_step_ended_is_ignored(harness) -> None:
    engine, inc, task = await start_slow(harness)
    await asyncio.wait_for(task, 5)  # 응답 끊김으로 끝남
    ((_, _, task_id),) = harness.fakes.async_tasks
    late = harness.fakes.task_event("completed", task_id, output={"n": 1})
    assert deliver(engine, inc, late) == "unmatched"
    assert inc.steps["slow"].status == "failed"  # 한 번 끝난 일은 바뀌지 않는다


@pytest.mark.asyncio
async def test_the_same_completion_twice_is_applied_once(harness) -> None:
    engine, inc, task = await start_slow(harness)
    ((_, _, task_id),) = harness.fakes.async_tasks
    first = harness.fakes.task_event("completed", task_id, output={"n": 1})
    second = harness.fakes.task_event("completed", task_id, output={"n": 2})
    assert deliver(engine, inc, first) == "applied"
    assert deliver(engine, inc, second) == "unmatched"
    await asyncio.wait_for(task, 5)
    assert inc.steps["slow"].output == {"n": 1}


# ---- 위조와 잘못된 알림 -------------------------------------------------------------------


@pytest.mark.asyncio
async def test_a_notice_from_a_department_that_was_not_asked_is_ignored(harness) -> None:
    engine, inc, task = await start_slow(harness)
    ((_, _, task_id),) = harness.fakes.async_tasks
    forged = harness.fakes.task_event("completed", task_id, source="marketing", output={"x": 1})
    assert deliver(engine, inc, forged) == "ignored"
    assert inc.steps["slow"].status == "waiting_async"
    wrong_skill = harness.fakes.task_event("completed", task_id, skill="draft_notice")
    assert deliver(engine, inc, wrong_skill) == "ignored"
    real = harness.fakes.task_event("completed", task_id, output={"x": 2})
    assert deliver(engine, inc, real) == "applied"
    await asyncio.wait_for(task, 5)
    assert inc.steps["slow"].output == {"x": 2}


@pytest.mark.asyncio
async def test_a_working_answer_without_a_task_id_fails_the_step(harness) -> None:
    engine = await harness.start()
    original = harness.fakes._respond

    def no_id(dept, req, behavior):
        if behavior == "async":
            return AgentResult(meta=req.meta, status=TaskStatus.WORKING, outcome="accepted_async")
        return original(dept, req, behavior)

    harness.fakes._respond = no_id
    harness.fakes.script("data", "impact_analysis", "async")
    inc = new_incident()
    await asyncio.wait_for(engine.run(inc, slow_plan()), 5)
    run = inc.steps["slow"]
    assert run.status == "failed" and "작업 번호" in run.note


# ---- 전체 대기 시간 -----------------------------------------------------------------------


@pytest.mark.asyncio
async def test_the_total_wait_follows_the_promise_or_the_department_estimate(harness) -> None:
    engine = await harness.start()
    assert engine._async_total(None) == engine.async_total_timeout
    assert engine._async_total(600.0) == 1200.0  # 기준 시간의 2배
    assert engine._async_total(10 * 3600.0) == 20 * 3600.0
    assert engine._async_total(30 * 3600.0) == MAX_ASYNC_SECONDS  # 24시간을 넘지 않는다


# ---- 이벤트 경로 (매니저) -----------------------------------------------------------------


@pytest.mark.asyncio
async def test_events_from_the_bus_drive_the_step_through_the_manager(harness) -> None:
    engine = await harness.start()
    manager = IncidentManager(engine, scenario_a)
    harness.fakes.script("data", "impact_analysis", "async")
    inc = await manager.handle(degraded())
    await wait_until(lambda: status_of(inc, "impact") == "waiting_async")
    ((_, req, task_id),) = harness.fakes.async_tasks
    progress = harness.fakes.task_event("progress", task_id, percent=50)
    assert (await manager.process(progress))[0] == "progressed"
    done = harness.fakes.task_event(
        "completed", task_id, output=DEFAULT_OUTPUTS["impact_analysis"](req)
    )
    assert (await manager.process(done))[0] == "resumed"
    await manager.wait(inc.context_id)
    assert inc.steps["impact"].status == "completed"
    assert inc.status == "resolved"


@pytest.mark.asyncio
async def test_the_manager_ignores_forged_or_malformed_notices(harness) -> None:
    engine = await harness.start()
    manager = IncidentManager(engine, scenario_a)
    harness.fakes.script("data", "impact_analysis", "async")
    inc = await manager.handle(degraded())
    await wait_until(lambda: status_of(inc, "impact") == "waiting_async")
    ((_, _, task_id),) = harness.fakes.async_tasks
    forged = harness.fakes.task_event("completed", task_id, source="marketing")
    assert (await manager.process(forged))[0] == "ignored"
    bad = harness.fakes.task_event("progress", task_id, percent=50)
    bad = bad.model_copy(update={"event_id": "evt_bad", "data": {**bad.data, "percent": 150}})
    assert (await manager.process(bad))[0] == "unmatched"
    stranger = harness.fakes.task_event("progress", task_id)
    stranger = stranger.model_copy(update={"event_id": "evt_other", "context_id": "ctx_none"})
    assert (await manager.process(stranger))[0] == "unmatched"
    assert inc.steps["impact"].status == "waiting_async"
    done = harness.fakes.task_event("completed", task_id, output={})
    await manager.process(done)
    await manager.wait(inc.context_id)


# ---- 재시작, 기록, 시간 정보 --------------------------------------------------------------


def test_a_restart_does_not_resume_a_long_task_it_marks_it_interrupted() -> None:
    inc = new_incident()
    inc.steps["slow"] = StepRun("slow", "impact_analysis", "긴 작업", status="waiting_async")
    IncidentManager._mark_interrupted(inc)
    run = inc.steps["slow"]
    assert (run.status, run.note, run.reason_code) == (
        "failed",
        INTERRUPTED_NOTE,
        "restart_interrupted",
    )


def test_the_long_task_fields_survive_saving_and_loading() -> None:
    run = StepRun(
        "slow",
        "impact_analysis",
        "긴 작업",
        async_task_id="tsk_1",
        last_response_at=now(),
        heartbeats=3,
        max_silence_seconds=12.5,
        progress_percent=40,
        progress_note="절반",
    )
    back = step_from_dict(step_to_dict(run))
    assert (back.async_task_id, back.heartbeats, back.max_silence_seconds) == ("tsk_1", 3, 12.5)
    assert (back.progress_percent, back.progress_note) == (40, "절반")
    assert back.last_response_at == run.last_response_at
    old = step_to_dict(run)
    for key in ("async_task_id", "heartbeats", "max_silence_seconds"):
        del old[key]  # 이 기능이 들어오기 전에 저장된 기록
    assert step_from_dict(old).async_task_id is None


@pytest.mark.asyncio
async def test_timings_report_the_longest_silence_even_while_the_task_is_running(harness) -> None:
    engine, inc, task = await start_slow(harness)
    await asyncio.sleep(0.1)
    row = next(r for r in step_timings(inc) if r["step_id"] == "slow")
    assert row["max_silence_seconds"] >= 0.1  # 진행 중에도 마지막 응답 이후 시간을 센다
    await asyncio.wait_for(task, 5)
    row = next(r for r in step_timings(inc) if r["step_id"] == "slow")
    assert row["max_silence_seconds"] >= 0.3 and row["status"] == "failed"


@pytest.mark.asyncio
async def test_the_summary_line_says_the_long_task_is_in_progress(harness) -> None:
    engine, inc, task = await start_slow(harness)
    line = engine._summary_line(inc.steps["slow"])
    assert "긴 작업 진행 중" in line
    await asyncio.wait_for(task, 5)
