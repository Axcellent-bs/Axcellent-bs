"""총괄의 사람 승인 범위, 시작 전 옛 계획 새로 만들기, 분류의 일 이름 받기 (운영에서 막힌 일)."""

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from ax_contracts.schemas import AgentResult, RequestMeta, TaskStatus
from ax_orchestrator import request_tasks
from ax_orchestrator.registry import SkillEntry
from ax_orchestrator.request_classifier import Classification, decide
from ax_orchestrator.request_runner import RequestRunner
from ax_orchestrator.work_requests import WorkRequests


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


def make(plan, task: str = "promo_video", text: str = "홍보 영상 만들어 주세요"):
    reqs = WorkRequests()
    row = reqs.create("member", text)
    reqs.set_plan(
        row["request_id"], task=task, confidence=0.9, reason="r", alternatives=[], plan=plan
    )
    return reqs, row["request_id"]


# ---- 사람 승인 범위: 고객 문장에서 입력이 나오는 상태 변경만 ----------------------------


@pytest.mark.parametrize(("task", "gated"), [("claim_register", True), ("feature_request", True)])
def test_customer_driven_state_changes_keep_the_human_gate(task: str, gated: bool) -> None:
    assert request_tasks.BY_NAME[task].needs_human_approval is gated
    assert request_tasks.plan_of(request_tasks.BY_NAME[task])[0]["human_gate"] is True


def test_promo_video_steps_that_take_department_results_are_not_gated() -> None:
    promo = request_tasks.BY_NAME["promo_video"]
    gated = {s.step_id for s in promo.steps if s.human_gate}
    assert gated == set()  # 기획 단계(A)·영상 제작(B)은 부서의 안전 관문이 등급대로 처리한다
    assert [s.step_id for s in promo.steps if s.kind == "human"] == ["confirm"]


@pytest.mark.asyncio
async def test_an_a_grade_step_without_a_human_gate_is_sent_without_asking() -> None:
    plan = [
        {
            "step_id": "a",
            "dept": "planning",
            "skill": "s_a",
            "label": "A",
            "after": [],
            "grade": "A",
        }
    ]
    reqs, rid = make(plan, task="demo")
    fake = Fake({"s_a": entry("planning", "s_a", "A")}, {"s_a": result()})
    await RequestRunner(reqs, lambda: fake, scope_check=lambda *_: True).run(rid)
    assert len(fake.calls) == 1  # 총괄이 더 막지 않는다
    assert reqs.get(rid)["steps"]["a"]["status"] == "completed"


@pytest.mark.asyncio
async def test_a_c_grade_step_still_waits_for_a_person() -> None:
    plan = [
        {
            "step_id": "a",
            "dept": "marketing",
            "skill": "s_a",
            "label": "A",
            "after": [],
            "grade": "C",
        }
    ]
    reqs, rid = make(plan, task="demo")
    fake = Fake({"s_a": entry("marketing", "s_a", "C")}, {"s_a": result()})
    await RequestRunner(reqs, lambda: fake, scope_check=lambda *_: True).run(rid)
    assert fake.calls == []
    assert reqs.get(rid)["steps"]["a"]["reason_code"] == "needs_approval"


# ---- 시작 전 의뢰의 옛 계획을 지금 일 목록으로 ---------------------------------------------------


def old_promo_plan() -> list[dict]:
    """#1474 배포 전에 저장된 계획(트렌드 → 기획, 인사이트 단계·긴 작업 입력 없음)."""
    return [
        {"step_id": "trend", "dept": "data", "skill": "trend_report_create", "label": "트렌드",
         "after": [], "grade": "read", "kind": "agent", "ref_key": "report_id", "takes_ref": None},
        {"step_id": "plan", "dept": "planning", "skill": "receive_insight", "label": "제작안",
         "after": ["trend"], "grade": "A", "kind": "agent", "ref_key": None, "takes_ref": None},
    ]  # fmt: skip


@pytest.mark.asyncio
async def test_a_request_that_has_not_started_is_rebuilt_from_the_current_task_list() -> None:
    reqs, rid = make(old_promo_plan())
    fake = Fake(
        {"trend_report_create": entry("data", "trend_report_create")},
        {"trend_report_create": result(TaskStatus.WORKING, task_id="tsk_1")},
    )
    await RequestRunner(reqs, lambda: fake).run(rid)
    row = reqs.get(rid)
    now_ids = [s.step_id for s in request_tasks.BY_NAME["promo_video"].steps]
    assert [s["step_id"] for s in row["plan"]] == now_ids  # 인사이트 단계가 생겼다
    sent = fake.calls[0].input
    assert sent["caller"] == "orchestrator"  # 긴 작업으로 불러 보고서를 기다린다
    assert row["steps"]["trend"]["status"] == "waiting_async"
    assert any("계획을 지금 기준으로 다시 만들었다" in log["message"] for log in row["log"])


@pytest.mark.asyncio
async def test_a_request_that_already_started_keeps_its_plan() -> None:
    reqs, rid = make(old_promo_plan())
    reqs.update_step(rid, "trend", status="completed", result_ref="rep_9")
    fake = Fake({"receive_insight": entry("planning", "receive_insight", "A")}, {})
    await RequestRunner(reqs, lambda: fake).run(rid)
    assert [s["step_id"] for s in reqs.get(rid)["plan"]] == [
        "trend",
        "plan",
    ]  # 중간에 바꾸지 않는다


def test_rerun_with_the_same_task_rebuilds_the_plan_from_scratch() -> None:
    reqs, rid = make(old_promo_plan())
    fake = Fake({}, {})
    runner = RequestRunner(reqs, lambda: None)
    runner.start = lambda _rid: None  # 실행은 이 시험의 관심이 아니다
    assert runner.rerun(rid, "promo_video", "준하") == "ok"
    row = reqs.get(rid)
    assert [s["step_id"] for s in row["plan"]][1] == "insight"
    assert fake.calls == []


# ---- 분류: 일 이름(label)로 와도 번호로 받는다 --------------------------------------------------


def raw(task: str | None, alternatives: list[str] | None = None) -> Classification:
    return Classification(
        kind="request", task=task, confidence=0.9, reason="r", alternatives=alternatives or []
    )


@pytest.mark.parametrize(
    "value", ["promo_video", "홍보 영상 만들기", "홍보영상만들기", " 홍보 영상  만들기 "]
)
def test_a_label_or_a_name_both_pick_the_task(value: str) -> None:
    decision = decide(raw(value))
    assert decision.outcome == "task" and decision.task.name == "promo_video"


def test_alternatives_may_also_be_labels_and_unknown_values_are_still_unknown() -> None:
    decision = decide(raw("promo_video", ["트렌드·경쟁사 분석 보고서", "없는 일", "promo_video"]))
    assert decision.alternatives == ["trend_report"]  # 고른 일과 같은 것·목록에 없는 것은 뺀다
    assert decide(raw("환불 실행")).outcome == "unknown"
    assert decide(raw(None)).outcome == "unknown"
