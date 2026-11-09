"""단계별 시간: 승인 대기·사람 할 일함 대기를 일한 시간에서 따로 떼어 기록하고 인사로 보낸다."""

from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest

from ax_contracts.schemas import RecordEventTimesInput
from ax_orchestrator.mock.harness import new_incident
from ax_orchestrator.models import StepRun, now
from ax_orchestrator.plans.scenario_a import scenario_a
from ax_orchestrator.store import incident_from_dict, incident_to_dict
from ax_orchestrator.timeline import step_timings


def _calls(harness) -> list[dict]:
    return [r.input for _, r in harness.fakes.calls if r.skill == "record_event_times"]


def _by_step(timings: list[dict]) -> dict[str, dict]:
    return {t["step_id"]: t for t in timings}


async def _wait_hold(inc, step_id: str) -> None:
    for _ in range(200):
        hold = inc.holds.get(step_id)
        if hold is not None and hold.pending:
            return
        await asyncio.sleep(0.01)
    raise AssertionError("승인 대기가 올라오지 않음")


def test_waits_are_taken_out_of_the_work_time() -> None:
    """화면에서 본 사례: 단계 전체 50초 중 승인 대기가 46초면 일한 시간은 4초다."""
    inc = new_incident()
    start = now()
    inc.steps["rollback"] = StepRun(
        step_id="rollback",
        skill="rollback_deployment",
        label="되돌리기",
        dept="infra",
        status="completed",
        attempts=1,
        started_at=start,
        finished_at=start + timedelta(seconds=50),
        approval_wait_seconds=46.0,
    )
    row = _by_step(step_timings(inc))["rollback"]
    assert row["work_seconds"] == 4.0 and row["approval_wait_seconds"] == 46.0
    assert row["human_wait_seconds"] is None and row["attempts"] == 1 and row["dept"] == "infra"


def test_human_inbox_wait_is_also_taken_out_and_never_goes_below_zero() -> None:
    inc = new_incident()
    start = now()
    inc.steps["cause_check"] = StepRun(
        step_id="cause_check",
        skill="request_cause_check",
        label="원인 확인 요청",
        dept="planning",
        status="completed",
        started_at=start,
        finished_at=start + timedelta(seconds=10),
        human_wait_seconds=12.0,  # 시각 오차로 대기가 전체보다 길어 보여도 음수가 되지 않는다
    )
    assert _by_step(step_timings(inc))["cause_check"]["work_seconds"] == 0.0


def test_a_step_that_did_not_run_has_no_work_time_instead_of_a_made_up_one() -> None:
    inc = new_incident()
    inc.steps["recovered_notice"] = StepRun(
        step_id="recovered_notice",
        skill="publish_notice",
        label="정상화 공지",
        status="skipped",
        finished_at=now(),
    )
    row = _by_step(step_timings(inc))["recovered_notice"]
    assert row["work_seconds"] is None and row["status"] == "skipped" and row["dept"] is None


def test_wait_times_survive_saving_and_loading() -> None:
    inc = new_incident()
    inc.steps["rollback"] = StepRun(
        step_id="rollback",
        skill="rollback_deployment",
        label="되돌리기",
        approval_wait_seconds=3.5,
        human_wait_seconds=7.25,
    )
    again = incident_from_dict(incident_to_dict(inc)).steps["rollback"]
    assert again.approval_wait_seconds == 3.5 and again.human_wait_seconds == 7.25


@pytest.mark.asyncio
async def test_an_approval_hold_is_measured_from_the_hold_to_the_human_decision(harness) -> None:
    harness.fakes.script("infra", "rollback_deployment", "hold")
    harness.hold_timeout = 5
    engine = await harness.start()
    inc = new_incident()
    task = asyncio.create_task(engine.run(inc, scenario_a()))
    await _wait_hold(inc, "rollback")
    await asyncio.sleep(0.3)
    assert engine.decide_hold(inc, "rollback", True)
    await task
    run = inc.steps["rollback"]
    assert run.approval_wait_seconds is not None and run.approval_wait_seconds >= 0.25
    row = _by_step(step_timings(inc))["rollback"]
    total = (run.finished_at - run.started_at).total_seconds()
    assert row["approval_wait_seconds"] >= 0.25
    assert (
        row["work_seconds"] <= total - row["approval_wait_seconds"] + 0.01
    )  # 대기는 일한 시간이 아니다
    assert inc.steps["impact"].approval_wait_seconds is None  # 승인이 없던 단계는 비어 있다


@pytest.mark.asyncio
async def test_an_automatic_rejection_after_the_hold_limit_counts_the_whole_wait(harness) -> None:
    harness.fakes.script("infra", "rollback_deployment", "hold")  # 보류 마감 0.2초
    engine = await harness.start()
    inc = new_incident()
    await engine.run(inc, scenario_a())
    run = inc.steps["rollback"]
    assert run.status == "rejected_by_human"
    assert run.approval_wait_seconds is not None and run.approval_wait_seconds >= 0.15


@pytest.mark.asyncio
async def test_step_timings_are_sent_to_hr_once_when_the_incident_closes(harness) -> None:
    engine = await harness.start()
    inc = new_incident()
    await engine.run(inc, scenario_a())
    sent = _calls(harness)
    assert len(sent) == 1
    parsed = RecordEventTimesInput.model_validate(sent[0])  # 계약과 같은 모양이다
    assert parsed.step_timings is not None
    assert {t.step_id for t in parsed.step_timings} == set(inc.steps)
    await engine.sync_times(inc)  # 바뀐 게 없으면 다시 보내지 않는다
    assert len(_calls(harness)) == 1


@pytest.mark.asyncio
async def test_step_timings_are_sent_again_when_a_late_human_finish_changes_them(harness) -> None:
    engine = await harness.start()
    inc = new_incident()
    await engine.run(inc, scenario_a())
    assert len(_calls(harness)) == 1
    inc.steps["impact"].human_wait_seconds = 99.0  # 사람 처리가 뒤늦게 끝나 값이 달라졌다
    await engine.sync_times(inc)
    again = _calls(harness)
    assert len(again) == 2 and "step_timings" in again[1]
    assert again[1].get("detected_at") is None  # 이미 보낸 시각은 다시 보내지 않는다


@pytest.mark.asyncio
async def test_step_timings_are_not_sent_while_the_incident_is_still_open(harness) -> None:
    engine = await harness.start()
    inc = new_incident()
    await engine.sync_times(inc)  # 아직 열린 이벤트
    assert all("step_timings" not in c for c in _calls(harness))
