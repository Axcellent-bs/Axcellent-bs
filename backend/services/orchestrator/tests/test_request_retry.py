"""읽기 단계만 다시 보내고 상태를 바꾸거나 비용이 드는 단계는 한 번만 보낸다. 단계별 대기 시간."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from ax_contracts.schemas import AgentResult, RequestMeta, TaskStatus
from ax_orchestrator import request_tasks
from ax_orchestrator.registry import SkillEntry
from ax_orchestrator.request_runner import RequestRunner
from ax_orchestrator.work_requests import WorkRequests


def result(status: TaskStatus, outcome: str | None = None, **output) -> AgentResult:
    meta = RequestMeta(
        context_id="c", token="t", deadline=datetime.now(UTC) + timedelta(seconds=30)
    )
    return AgentResult(meta=meta, status=status, outcome=outcome, output=output)


class Fake:
    def __init__(self, grade: str, answers: list[AgentResult]) -> None:
        entry = SkillEntry("marketing", "s_a", grade, None, "normal", True)
        self.registry = SimpleNamespace(find=lambda s: entry if s == "s_a" else None)
        self.answers = answers
        self.calls: list = []

    async def caller(self, dept, req):
        self.calls.append(req)
        return self.answers.pop(0) if len(self.answers) > 1 else self.answers[0]


def make(grade: str, **extra):
    spec = {
        "step_id": "a",
        "dept": "marketing",
        "skill": "s_a",
        "label": "A",
        "after": [],
        "grade": grade,
        **extra,
    }
    reqs = WorkRequests()
    row = reqs.create("member", "의뢰")
    reqs.set_plan(
        row["request_id"], task="demo", confidence=0.9, reason="r", alternatives=[], plan=[spec]
    )
    return reqs, row["request_id"]


def runner(reqs, fake) -> RequestRunner:
    return RequestRunner(reqs, lambda: fake, retry_delay=0, scope_check=lambda *_: True)


@pytest.mark.asyncio
@pytest.mark.parametrize("grade", ["A", "B", "C"])
async def test_a_failed_state_changing_step_is_sent_once_not_again(grade: str) -> None:
    reqs, rid = make(grade)
    fake = Fake(grade, [result(TaskStatus.FAILED, "in_progress"), result(TaskStatus.COMPLETED)])
    run = runner(reqs, fake)
    # 승인이 필요한 등급은 승인된 단계로 바꿔 부서 호출까지 가게 한다
    reqs.update_step(rid, "a", reason_code="approved")
    await run.run(rid)
    assert len(fake.calls) == 1  # 두 번째(성공) 답이 있어도 다시 보내지 않는다
    row = reqs.get(rid)
    assert row["steps"]["a"]["status"] == "failed" and "in_progress" in row["steps"]["a"]["note"]
    assert row["status"] == "needs_human"


@pytest.mark.asyncio
async def test_a_canceled_state_changing_step_is_not_sent_again_either() -> None:
    reqs, rid = make("B")
    reqs.update_step(rid, "a", reason_code="approved")
    fake = Fake("B", [result(TaskStatus.CANCELED, "TIMEOUT"), result(TaskStatus.COMPLETED)])
    await runner(reqs, fake).run(rid)
    assert len(fake.calls) == 1


@pytest.mark.asyncio
async def test_a_read_step_is_still_retried_after_a_failure() -> None:
    reqs, rid = make("read")
    fake = Fake("read", [result(TaskStatus.FAILED, "boom"), result(TaskStatus.COMPLETED, "ok")])
    await runner(reqs, fake).run(rid)
    assert len(fake.calls) == 2 and reqs.get(rid)["status"] == "done"


@pytest.mark.asyncio
async def test_the_wait_time_is_set_per_step_and_defaults_to_thirty_seconds() -> None:
    reqs, rid = make("read", timeout_s=300.0)
    fake = Fake("read", [result(TaskStatus.COMPLETED, "ok")])
    await runner(reqs, fake).run(rid)
    waited = (fake.calls[0].meta.deadline - datetime.now(UTC)).total_seconds()
    assert 280 < waited <= 300
    reqs2, rid2 = make("read")
    fake2 = Fake("read", [result(TaskStatus.COMPLETED, "ok")])
    await runner(reqs2, fake2).run(rid2)
    assert 20 < (fake2.calls[0].meta.deadline - datetime.now(UTC)).total_seconds() <= 30


def test_the_video_step_waits_long_and_is_a_paid_b_grade_step() -> None:
    make_step = next(s for s in request_tasks.BY_NAME["promo_video"].steps if s.step_id == "make")
    assert make_step.skill == "generate_media" and make_step.grade == "B"
    assert make_step.timeout_s == request_tasks.MEDIA_TIMEOUT_S >= 300
    plan = request_tasks.plan_of(request_tasks.BY_NAME["promo_video"])
    assert next(p for p in plan if p["step_id"] == "make")["timeout_s"] == 300.0
    # 11/25 기획 제작안도 길게 기다린다(SVG 콘티까지 쓰면 30초를 넘는다). 그 밖은 기본값
    assert next(p for p in plan if p["step_id"] == "plan")["timeout_s"] == 240.0
    assert request_tasks.PLAN_TIMEOUT_S >= 180
    assert all(p["timeout_s"] is None for p in plan if p["step_id"] not in ("make", "plan"))
