"""의뢰 실행 중 총괄을 껐다 켜도 이어 간다 (20번): 저장소의 의뢰를 다시 올려 확인한다."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from ax_contracts.schemas import AgentResult, RequestMeta, TaskStatus
from ax_orchestrator import request_tasks
from ax_orchestrator.registry import SkillEntry
from ax_orchestrator.request_runner import RequestRunner
from ax_orchestrator.store import IncidentStore
from ax_orchestrator.work_requests import WorkRequests

TASK = "tsk_1"


def result(status: TaskStatus = TaskStatus.COMPLETED, **output) -> AgentResult:
    meta = RequestMeta(
        context_id="c", token="t", deadline=datetime.now(UTC) + timedelta(seconds=30)
    )
    return AgentResult(meta=meta, status=status, outcome="ok", output=output)


class Fake:
    def __init__(self, entries: dict[str, SkillEntry], answers: dict[str, AgentResult]) -> None:
        self.answers = answers
        self.calls: list = []
        self.registry = SimpleNamespace(find=entries.get)

    async def caller(self, dept, req):
        self.calls.append(req)
        return self.answers[req.skill]


def entry(dept: str, skill: str, grade: str = "read") -> SkillEntry:
    return SkillEntry(dept, skill, grade, None, "normal", True)


def boot(path: str) -> WorkRequests:
    """총괄을 (다시) 켠다: 같은 저장 파일에서 의뢰를 올린다."""
    reqs = WorkRequests(IncidentStore(path))
    reqs.restore()
    return reqs


def one_step_plan(grade: str, **extra) -> list[dict]:
    return [
        {
            "step_id": "a",
            "dept": "data",
            "skill": "s_a",
            "label": "A",
            "after": [],
            "grade": grade,
            **extra,
        }
    ]


def new_request(reqs: WorkRequests, plan: list[dict], task: str = "demo") -> str:
    row = reqs.create("member", "의뢰")
    reqs.set_plan(
        row["request_id"], task=task, confidence=0.9, reason="r", alternatives=[], plan=plan
    )
    return row["request_id"]


@pytest.mark.asyncio
async def test_a_long_task_keeps_waiting_across_a_restart_and_a_late_event_finishes_it(
    tmp_path,
) -> None:
    path = str(tmp_path / "orc.db")
    first = boot(path)
    row = first.create("member", "트렌드 보고서 만들어 주세요")
    rid = row["request_id"]
    plan = [request_tasks.plan_of(request_tasks.BY_NAME["trend_report"])[0]]
    first.set_plan(rid, task="trend_report", confidence=0.9, reason="r", alternatives=[], plan=plan)
    fake = Fake(
        {"trend_report_create": entry("data", "trend_report_create")},
        {"trend_report_create": result(TaskStatus.WORKING, task_id=TASK)},
    )
    await RequestRunner(first, lambda: fake).run(rid)
    assert first.get(rid)["steps"]["run"]["status"] == "waiting_async"

    second = boot(path)  # 총괄을 껐다 켰다
    runner = RequestRunner(second, lambda: fake)
    assert await runner.resume_open() == 0  # 다시 시킬 단계는 없다: 알림을 기다린다
    waiting = second.get(rid)["steps"]["run"]
    assert waiting["status"] == "waiting_async" and waiting["async_task_id"] == TASK
    assert len(fake.calls) == 1  # 부서에 다시 보내지 않았다

    done = {"task_id": TASK, "skill": "trend_report_create", "status": "done"}
    done["output"] = {"report_id": "rep_1"}
    assert runner.receive_async("completed", "data", done, f"ctx_{rid}") == "resumed"
    await runner.wait_idle()
    assert second.get(rid)["status"] == "done"
    assert boot(path).get(rid)["steps"]["run"]["result_ref"] == "rep_1"  # 끝난 것도 남는다


@pytest.mark.asyncio
async def test_a_read_step_cut_off_mid_call_is_sent_again_after_a_restart(tmp_path) -> None:
    path = str(tmp_path / "orc.db")
    first = boot(path)
    rid = new_request(first, one_step_plan("read"))
    first.update_step(rid, "a", status="running", grade="read")  # 부서를 부르던 중 꺼졌다

    second = boot(path)
    assert second.get(rid)["steps"]["a"]["status"] == "pending"  # 읽기라 안전하게 다시 시킨다
    fake = Fake({"s_a": entry("data", "s_a")}, {"s_a": result()})
    runner = RequestRunner(second, lambda: fake, scope_check=lambda *_: True)
    assert await runner.resume_open() == 1
    await runner.wait_idle()
    assert len(fake.calls) == 1 and second.get(rid)["status"] == "done"


@pytest.mark.asyncio
async def test_a_state_changing_step_cut_off_mid_call_is_never_sent_twice(tmp_path) -> None:
    path = str(tmp_path / "orc.db")
    first = boot(path)
    rid = new_request(first, one_step_plan("A"))
    first.update_step(rid, "a", status="running", grade="A")  # 보냈는지 알 수 없다

    second = boot(path)
    step = second.get(rid)["steps"]["a"]
    assert step["status"] == "waiting_human" and step["reason_code"] == "restart_unconfirmed"
    fake = Fake({"s_a": entry("data", "s_a", "A")}, {"s_a": result()})
    runner = RequestRunner(second, lambda: fake, scope_check=lambda *_: True)
    assert await runner.resume_open() == 0
    await runner.wait_idle()
    assert fake.calls == []  # 이미 처리됐을 수 있어 다시 보내지 않는다


def _restart_stuck(tmp_path, *, gated: bool = False) -> tuple[str, WorkRequests]:
    """상태를 바꾸는 단계를 부르던 중 꺼졌다가 켠 의뢰 (11/24 wr_a8f5aaa32f): 재시작으로 멈춤."""
    path = str(tmp_path / "orc.db")
    first = boot(path)
    extra = {"human_gate": True} if gated else {}
    rid = new_request(first, one_step_plan("A", **extra))
    first.update_step(rid, "a", status="running", grade="A", reason_code="approved")
    second = boot(path)
    step = second.get(rid)["steps"]["a"]
    assert step["status"] == "waiting_human" and step["reason_code"] == "restart_unconfirmed"
    return rid, second


@pytest.mark.asyncio
@pytest.mark.parametrize("gated", [False, True])
async def test_a_restart_stuck_step_is_sent_again_when_a_person_approves(
    tmp_path, gated: bool
) -> None:
    rid, reqs = _restart_stuck(tmp_path, gated=gated)
    fake = Fake({"s_a": entry("data", "s_a", "A")}, {"s_a": result()})
    runner = RequestRunner(reqs, lambda: fake, scope_check=lambda *_: True)
    assert runner.decide(rid, "a", True, "준하") == "ok"
    step = reqs.get(rid)["steps"]["a"]
    assert step["reason_code"] == "approved"  # 승인이 필요한 일도 다시 묻지 않는다
    assert "다시 보내기로 했다 (준하)" in step["note"]
    await runner.wait_idle()
    assert len(fake.calls) == 1  # 다시 보냈다 (사람 승인 대기로 되돌아가지 않음)
    assert fake.calls[0].meta.idempotency_key == f"{rid}:s_a:a"  # 처음 보낸 것과 같은 멱등 키
    assert reqs.get(rid)["steps"]["a"]["status"] == "completed"
    assert reqs.get(rid)["status"] == "done"


def test_a_restart_stuck_step_goes_back_to_pending_on_approval(tmp_path) -> None:
    rid, reqs = _restart_stuck(tmp_path)
    fake = Fake({"s_a": entry("data", "s_a", "A")}, {"s_a": result()})
    runner = RequestRunner(reqs, lambda: fake, scope_check=lambda *_: True)
    runner.start = lambda *_a, **_k: None  # 이어 시키기를 막고 되돌린 상태만 본다
    assert runner.decide(rid, "a", True) == "ok"
    assert reqs.get(rid)["steps"]["a"]["status"] == "pending"


@pytest.mark.asyncio
async def test_a_restart_stuck_step_stops_when_a_person_rejects(tmp_path) -> None:
    rid, reqs = _restart_stuck(tmp_path)
    fake = Fake({"s_a": entry("data", "s_a", "A")}, {"s_a": result()})
    runner = RequestRunner(reqs, lambda: fake, scope_check=lambda *_: True)
    assert runner.decide(rid, "a", False, "준하") == "ok"
    await runner.wait_idle()
    assert reqs.get(rid)["steps"]["a"]["status"] == "rejected_by_human"
    assert fake.calls == []


def test_other_waiting_human_reasons_are_still_not_decidable(tmp_path) -> None:
    reqs = boot(str(tmp_path / "orc.db"))
    rid = new_request(reqs, one_step_plan("A"))
    reqs.update_step(rid, "a", status="waiting_human", reason_code="something_else")
    runner = RequestRunner(reqs, lambda: Fake({}, {}), scope_check=lambda *_: True)
    assert runner.decide(rid, "a", True) == "not_waiting"
    assert reqs.get(rid)["steps"]["a"]["status"] == "waiting_human"


@pytest.mark.asyncio
async def test_an_approval_wait_survives_a_restart_and_the_approval_still_continues(
    tmp_path,
) -> None:
    path = str(tmp_path / "orc.db")
    first = boot(path)
    rid = new_request(first, one_step_plan("A", human_gate=True))
    fake = Fake({"s_a": entry("data", "s_a", "A")}, {"s_a": result()})
    await RequestRunner(first, lambda: fake, scope_check=lambda *_: True).run(rid)
    assert first.get(rid)["steps"]["a"]["reason_code"] == "needs_approval"

    second = boot(path)
    runner = RequestRunner(second, lambda: fake, scope_check=lambda *_: True)
    assert await runner.resume_open() == 0  # 사람이 승인하기 전에는 아무것도 보내지 않는다
    assert fake.calls == []
    assert runner.decide(rid, "a", True, "준하") == "ok"
    await runner.wait_idle()
    assert len(fake.calls) == 1 and second.get(rid)["status"] == "done"


@pytest.mark.asyncio
async def test_finished_and_closed_requests_are_left_alone_after_a_restart(tmp_path) -> None:
    path = str(tmp_path / "orc.db")
    first = boot(path)
    rid = new_request(first, one_step_plan("read"))
    fake = Fake({"s_a": entry("data", "s_a")}, {"s_a": result()})
    await RequestRunner(first, lambda: fake, scope_check=lambda *_: True).run(rid)
    assert first.get(rid)["status"] == "done"

    second = boot(path)
    runner = RequestRunner(second, lambda: fake, scope_check=lambda *_: True)
    assert await runner.resume_open() == 0
    assert len(fake.calls) == 1  # 끝난 의뢰는 다시 시키지 않는다
