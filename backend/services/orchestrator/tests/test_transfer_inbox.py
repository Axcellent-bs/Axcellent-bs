"""`transfer_queued`(이관 큐로 넘김) 단계가 사람의 받은 요청에 올라가는지."""

from types import SimpleNamespace

import pytest

from ax_contracts.schemas import UNASSIGNED_UNIT, HandoffCreate, HandoffTask
from ax_orchestrator import inbox
from ax_orchestrator.mock.harness import new_incident
from ax_orchestrator.models import now
from ax_orchestrator.plans.scenario_a import scenario_a


class FakeInbox:
    """dashboard-api 받은 요청 대역. 같은 `idempotency_key`면 처음 만든 일을 돌려준다."""

    def __init__(self, reachable: bool = True) -> None:
        self.reachable = reachable
        self.bodies: list[HandoffCreate] = []
        self.tasks: dict[str, HandoffTask] = {}

    async def __call__(self, body: HandoffCreate) -> HandoffTask | None:
        if not self.reachable:
            return None
        self.bodies.append(body)
        key = body.idempotency_key or ""
        if key not in self.tasks:
            fields = body.model_dump(exclude={"idempotency_key"})
            self.tasks[key] = HandoffTask(
                task_id=f"hof_{len(self.tasks) + 1:012d}", created_at=now(), **fields
            )
        return self.tasks[key]


async def run(harness, fake: FakeInbox | None):
    engine = await harness.start()
    engine.inbox = fake
    inc = new_incident()
    await engine.run(inc, scenario_a())
    return engine, inc


@pytest.fixture(autouse=True)
def no_org(monkeypatch):
    """조직도에 맡은 부서가 없는 상태(담당 미정)가 기본이다."""
    monkeypatch.setattr(inbox.routing, "assignment_for", lambda service, skill: None)


@pytest.mark.asyncio
async def test_undetermined_cause_shows_up_in_the_inbox(harness) -> None:
    harness.fakes.script("infra", "diagnose_incident", "undetermined")
    fake = FakeInbox()
    _, inc = await run(harness, fake)
    rollback = inc.steps["rollback"]
    assert rollback.status == "transfer_queued"
    assert len(fake.bodies) == 1
    body = fake.bodies[0]
    assert body.context_id == inc.context_id  # 장애 방 링크는 이 번호로 잇는다
    assert body.from_service == "orchestrator"
    assert body.skill == "rollback_deployment"
    assert body.assignee_unit == UNASSIGNED_UNIT  # 담당이 정해지지 않았으면 "담당 미정"
    assert "원인을 모르므로 되돌리기를 보류" in body.reason
    assert body.input["to_check"].startswith("원인을 모르므로")
    assert "evidence" in body.input  # 근거(진단 결과 요약) 칸이 있다
    task = next(iter(fake.tasks.values()))
    assert rollback.handoff_task_id == task.task_id
    assert rollback.output["assignee_unit"] == UNASSIGNED_UNIT
    assert any("받은 요청에 올림" in e["message"] for e in inc.events)


@pytest.mark.asyncio
async def test_assigned_unit_comes_from_the_org_chart(harness, monkeypatch) -> None:
    monkeypatch.setattr(
        inbox.routing,
        "assignment_for",
        lambda service, skill: SimpleNamespace(unit="인프라팀", assignee="kim"),
    )
    harness.fakes.script("infra", "diagnose_incident", "undetermined")
    fake = FakeInbox()
    await run(harness, fake)
    assert fake.bodies[0].assignee_unit == "인프라팀"
    assert fake.bodies[0].assignee == "kim"


@pytest.mark.asyncio
async def test_unrecovered_check_is_also_queued(harness) -> None:
    harness.fakes.script("infra", "verify_recovery", "not_recovered")
    fake = FakeInbox()
    _, inc = await run(harness, fake)
    recovery = inc.steps["recovery"]
    assert recovery.status == "transfer_queued" and recovery.handoff_task_id
    assert any("복구 확인이 정상으로 나오지" in b.reason for b in fake.bodies)


@pytest.mark.asyncio
async def test_human_result_comes_back_to_the_incident(harness) -> None:
    harness.fakes.script("infra", "diagnose_incident", "undetermined")
    fake = FakeInbox()
    engine, inc = await run(harness, fake)
    rollback = inc.steps["rollback"]
    task_id = rollback.handoff_task_id
    assert task_id
    assert engine.complete_handoff(inc, task_id, "done", "배포 문제로 확인해 되돌림")
    assert rollback.status == "transfer_queued"  # 단계 상태는 그대로, 총괄 계획도 이어 가지 않는다
    assert "사람이 받은 요청에서 처리함" in rollback.note
    assert rollback.output["human_result"] == "배포 문제로 확인해 되돌림"
    assert any("사람이 받은 요청에서 처리함" in e["message"] for e in inc.events)
    # 모르는 일 번호는 반영하지 않는다
    assert not engine.complete_handoff(inc, "hof_unknown", "done", "x")


@pytest.mark.asyncio
async def test_same_step_is_not_queued_twice(harness) -> None:
    harness.fakes.script("infra", "diagnose_incident", "undetermined")
    fake = FakeInbox()
    engine, inc = await run(harness, fake)
    step = next(s for s in scenario_a().steps if s.id == "rollback")
    await engine._queue_transfer(inc, step)  # 다시 불러도 새로 올리지 않는다
    await engine._queue_transfer(inc, step)
    assert len(fake.bodies) == 1 and len(fake.tasks) == 1
    # 같은 이벤트·단계는 같은 중복 방지 키를 쓴다
    run_ = inc.steps["rollback"]
    assert fake.bodies[0].idempotency_key == inbox.idempotency_key(inc, run_)


@pytest.mark.asyncio
async def test_inbox_unreachable_keeps_the_step_and_notes_it(harness) -> None:
    harness.fakes.script("infra", "diagnose_incident", "undetermined")
    _, inc = await run(harness, FakeInbox(reachable=False))
    rollback = inc.steps["rollback"]
    assert rollback.status == "transfer_queued" and rollback.handoff_task_id is None
    assert any("받은 요청에 올리지 못함" in e["message"] for e in inc.events)


@pytest.mark.asyncio
async def test_without_an_inbox_nothing_is_posted(harness) -> None:
    harness.fakes.script("infra", "diagnose_incident", "undetermined")
    _, inc = await run(harness, None)  # 기본(목 모드): 올리지 않는다
    assert inc.steps["rollback"].status == "transfer_queued"
    assert inc.steps["rollback"].handoff_task_id is None
