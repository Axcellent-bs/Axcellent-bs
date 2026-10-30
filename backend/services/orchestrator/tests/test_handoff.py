"""꺼진 서비스 단계(handed_to_human): 사람 처리를 기다렸다가 handoff.completed로 이어 간다."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest

from ax_contracts.schemas import (
    AgentRequest,
    AgentResult,
    EventEnvelope,
    EventType,
    InputReason,
    TaskStatus,
)
from ax_orchestrator.manager import SUBSCRIBED, IncidentManager
from ax_orchestrator.mock.harness import Harness, new_incident
from ax_orchestrator.plans.scenario_a import scenario_a


async def wait_for_status(inc, step_id: str, status: str) -> None:
    for _ in range(500):
        await asyncio.sleep(0.01)
        if inc.steps.get(step_id) and inc.steps[step_id].status == status:
            return
    raise AssertionError(f"{step_id}가 {status}가 되지 않음")


async def settle(inc, *step_ids: str) -> None:
    """다른 갈래가 갈 수 있는 데까지 가게 둔다."""
    for _ in range(500):
        await asyncio.sleep(0.01)
        if all(inc.steps[s].status in {"completed", "skipped"} for s in step_ids):
            return
    raise AssertionError("다른 갈래가 진행되지 않음")


def handed_to_human(req: AgentRequest, output: dict | None = None) -> AgentResult:
    """업무 요청 라우터(call_agent)가 꺼진 서비스 요청에 돌려주는 결과와 같은 모양."""
    step_id = (req.meta.idempotency_key or "").rsplit(":", 1)[-1]
    return AgentResult(
        meta=req.meta,
        status=TaskStatus.INPUT_REQUIRED,
        reason_code=InputReason.DEPT_INPUT,
        outcome="handed_to_human",
        output=output
        if output is not None
        else {
            "task_id": f"hof_{step_id}",
            "to_service": "coupon",
            "assignee_unit": "마케팅",
            "assignee": None,
        },
    )


class DisabledService:
    """`skill`을 처음 부를 때만 꺼진 서비스처럼 사람 할 일함으로 넘긴다.

    나머지는 부서에 그대로 보낸다.
    """

    def __init__(self, inner, skill: str, output: dict | None = None) -> None:  # noqa: ANN001
        self.inner, self.skill, self.output, self.used = inner, skill, output, False

    async def __call__(self, dept: str, req: AgentRequest) -> AgentResult:
        if req.skill == self.skill and not self.used:
            self.used = True
            self.inner.calls.append((dept, req))
            return handed_to_human(req, self.output)
        return await self.inner(dept, req)


async def start_engine(harness: Harness, output: dict | None = None):
    engine = await harness.start()
    engine.caller = DisabledService(harness.fakes, "draft_notice", output)
    return engine


async def start_with_handoff(harness: Harness):
    engine = await start_engine(harness)
    inc = new_incident()
    task = asyncio.create_task(engine.run(inc, scenario_a()))
    await wait_for_status(inc, "notice", "waiting_human")
    return engine, inc, task


def completed_event(inc, task_id: str, status: str = "done", event_id: str = "evt_h1"):
    return EventEnvelope(
        event_id=event_id,
        type=EventType.HANDOFF_COMPLETED,
        occurred_at=datetime.now(UTC),
        context_id=inc.context_id,
        source="dashboard-api",
        idempotency_key=f"handoff.completed:{task_id}",
        data={
            "task_id": task_id,
            "status": status,
            "from_service": "orchestrator",
            "to_service": "coupon",
            "skill": "draft_notice",
        },
        untrusted={"result": "점검 공지 초안을 사람이 작성함"},
    )


@pytest.mark.asyncio
async def test_a_disabled_service_step_waits_for_a_human_and_the_incident_stays_open(
    harness,
) -> None:
    engine, inc, task = await start_with_handoff(harness)
    notice = inc.steps["notice"]
    assert notice.handoff_task_id == "hof_notice"
    assert notice.reason_code == "dept_input"
    assert notice.status != "held"  # C 등급 승인 대기와 다른 상태
    assert "notice" not in inc.holds
    assert inc.status == "open" and inc.closed_at is None
    assert inc.view()["pending_handoffs"] == [
        {
            "step_id": "notice",
            "skill": "draft_notice",
            "label": notice.label,
            "task_id": "hof_notice",
            "assignee_unit": "마케팅",
        }
    ]
    assert inc.view()["pending_holds"] == []

    # 이 단계에 기대지 않는 갈래는 계속 진행한다
    await settle(inc, "rollback", "compensation", "recovery")
    assert inc.steps["publish_notice"].status == "pending"  # 공지 초안에 기대는 단계는 기다린다
    assert not task.done()

    engine.complete_handoff(inc, "hof_notice", "done", None)
    await task


@pytest.mark.asyncio
async def test_handoff_completed_finishes_the_step_and_the_incident_resolves(harness) -> None:
    engine, inc, task = await start_with_handoff(harness)
    manager = IncidentManager(engine, scenario_a)
    manager.incidents[inc.context_id] = inc

    outcome, found = await manager.process(completed_event(inc, "hof_notice"))
    assert (outcome, found) == ("resumed", inc)
    await asyncio.wait_for(task, timeout=5)

    notice = inc.steps["notice"]
    assert notice.status == "completed"
    assert notice.output == {
        "handled_by": "human",
        "task_id": "hof_notice",
        "assignee_unit": "마케팅",
        "to_service": "coupon",
        "result": "점검 공지 초안을 사람이 작성함",
    }
    assert inc.steps["publish_notice"].status == "completed"  # 다음 단계가 이어졌다
    order = [r.skill for _, r in harness.fakes.calls]
    assert order.index("publish_notice") > order.index("draft_notice")
    assert inc.status == "resolved"
    assert inc.metrics["handoffs"] == 1
    assert inc.metrics["held"] == 0


@pytest.mark.asyncio
async def test_a_rejected_handoff_is_handled_like_a_rejected_hold(harness) -> None:
    engine, inc, task = await start_with_handoff(harness)
    manager = IncidentManager(engine, scenario_a)
    manager.incidents[inc.context_id] = inc

    outcome, _ = await manager.process(completed_event(inc, "hof_notice", status="rejected"))
    assert outcome == "resumed"
    await asyncio.wait_for(task, timeout=5)

    assert inc.steps["notice"].status == "rejected_by_human"
    assert inc.steps["notice"].note == "사람이 처리하지 않기로 함"
    assert inc.steps["publish_notice"].status == "skipped"  # 선행이 완료되지 않음
    assert inc.steps["record"].status == "completed"  # 이벤트 기록은 남긴다
    assert inc.status == "degraded" and inc.closed_at is not None


@pytest.mark.asyncio
async def test_the_same_completion_is_applied_only_once(harness) -> None:
    engine, inc, task = await start_with_handoff(harness)
    manager = IncidentManager(engine, scenario_a)
    manager.incidents[inc.context_id] = inc

    first = completed_event(inc, "hof_notice")
    assert (await manager.process(first))[0] == "resumed"
    assert (await manager.process(first))[0] == "duplicate"  # 같은 event_id
    again = completed_event(inc, "hof_notice", status="rejected", event_id="evt_h2")
    assert (await manager.process(again))[0] == "unmatched"  # 다른 event_id로 다시 와도
    await asyncio.wait_for(task, timeout=5)
    assert inc.steps["notice"].status == "completed"
    resumed = [e for e in inc.events if "사람 할 일함 처리 결과 수신" in e["message"]]
    assert len(resumed) == 1


@pytest.mark.asyncio
async def test_completions_for_unknown_tasks_or_incidents_are_unmatched(harness) -> None:
    engine, inc, task = await start_with_handoff(harness)
    manager = IncidentManager(engine, scenario_a)
    manager.incidents[inc.context_id] = inc

    other = completed_event(inc, "hof_nope", event_id="evt_x1")
    assert (await manager.process(other))[0] == "unmatched"
    lost = completed_event(inc, "hof_notice", event_id="evt_x2").model_copy(
        update={"context_id": "ctx_inc_other"}
    )
    assert (await manager.process(lost))[0] == "unmatched"
    assert inc.steps["notice"].status == "waiting_human"
    engine.complete_handoff(inc, "hof_notice", "done", None)
    await asyncio.wait_for(task, timeout=5)


@pytest.mark.asyncio
async def test_waiting_too_long_rejects_the_step() -> None:
    harness = Harness()
    engine = await start_engine(harness)
    engine.handoff_timeout = 0.05
    inc = await engine.run(new_incident(), scenario_a())
    assert inc.steps["notice"].status == "rejected_by_human"
    assert inc.steps["notice"].note == "사람 처리 시간 초과로 자동 거절"
    assert inc.status == "degraded"


@pytest.mark.asyncio
async def test_a_handoff_without_a_task_id_fails_instead_of_waiting_forever(harness) -> None:
    engine = await start_engine(harness, output={})
    inc = await asyncio.wait_for(engine.run(new_incident(), scenario_a()), timeout=5)
    assert inc.steps["notice"].status == "failed"
    assert "task_id" in inc.steps["notice"].note


def test_the_orchestrator_subscribes_to_handoff_completed() -> None:
    assert EventType.HANDOFF_COMPLETED in SUBSCRIBED
