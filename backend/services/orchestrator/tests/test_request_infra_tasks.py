"""대화방 인프라 의뢰(11/26, 이태윤 정리 글 3번): 지금 장애·배포 상태·원인 진단.

읽기 일만 둔다. 롤백은 의뢰로 열지 않는다(장애 대응 흐름의 안전 관문(B)에서만). 회사 범위는 요청의
회사로 부서를 부르는 것으로 지키고, 승인 없이 상태가 바뀌는 길이 없다는 것을 확인한다.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from types import SimpleNamespace

import pytest

from ax_contracts.schemas import AgentResult, RequestMeta, TaskStatus
from ax_orchestrator import request_inputs, request_tasks
from ax_orchestrator.registry import SkillEntry
from ax_orchestrator.request_runner import RequestRunner
from ax_orchestrator.store import IncidentStore
from ax_orchestrator.work_requests import WorkRequests

INFRA_TASKS = ("current_incidents", "deployment_status", "incident_diagnosis")
# 상태를 바꾸는 인프라 skill: 대화방 의뢰로 열지 않는다
INFRA_WRITE_SKILLS = ("rollback_deployment", "release_fault", "deploy_version", "restart_service")
NOW = datetime(2026, 11, 26, 12, 0, tzinfo=UTC)


class FakeInfra:
    """인프라 부서를 흉내 낸다. 부른 요청을 적어 두고 skill마다 정한 결과를 준다."""

    def __init__(self, outputs: dict[str, dict], outcome: dict[str, str] | None = None) -> None:
        self.outputs = outputs
        self.outcome = outcome or {}
        self.calls: list[tuple[str, object]] = []
        self.registry = SimpleNamespace(
            find=lambda skill: SkillEntry("infra", skill, "read", None, "normal", True)
        )

    async def caller(self, dept, req):
        self.calls.append((dept, req))
        meta = RequestMeta(
            context_id="ctx", token="t", deadline=datetime.now(UTC) + timedelta(seconds=30)
        )
        return AgentResult(
            meta=meta,
            status=TaskStatus.COMPLETED,
            outcome=self.outcome.get(req.skill, "ok"),
            output=self.outputs[req.skill],
        )


def _start(task_name: str, text: str, company: str = "catbooks"):
    reqs = WorkRequests(IncidentStore(company_id=company))
    row = reqs.create("member", text)
    reqs.set_plan(
        row["request_id"],
        task=task_name,
        confidence=0.9,
        reason="r",
        alternatives=[],
        plan=request_tasks.plan_of(request_tasks.BY_NAME[task_name]),
    )
    return reqs, row["request_id"]


def test_infra_tasks_are_read_only_single_steps_with_a_result_number() -> None:
    for name in INFRA_TASKS:
        task = request_tasks.BY_NAME[name]
        assert (task.first_dept, len(task.steps)) == ("infra", 1)
        step = task.steps[0]
        assert step.grade == request_tasks.READ_GRADE and step.kind == "agent"
        assert not step.human_gate and step.after == ()
        assert not task.changes_state and not task.needs_human_approval
        assert task.blocked_by is None
        assert step.ref_key  # 카드가 읽을 결과 번호가 있다


def test_no_task_opens_an_infra_state_change_such_as_rollback() -> None:
    """롤백·장애 해제·배포 실행·재시작은 대화방 의뢰로 열지 않는다(승인 관문 우회 방지)."""
    skills = {s.skill for t in request_tasks.TASKS for s in t.steps}
    assert not skills & set(INFRA_WRITE_SKILLS)
    infra = [s for t in request_tasks.TASKS for s in t.steps if s.dept == "infra"]
    assert {s.skill for s in infra} == {
        "current_incidents",
        "latest_deployments",
        "diagnose_incident",
    }
    assert all(s.grade == request_tasks.READ_GRADE for s in infra)


def test_infra_tasks_are_in_the_classifier_prompt() -> None:
    from ax_orchestrator.request_classifier import build_prompt

    prompt = build_prompt()
    for name in INFRA_TASKS:
        assert name in prompt


@pytest.mark.parametrize(
    ("text", "payload"),
    [
        ("결제 장애 원인 진단해 주세요", {"component": "payment"}),
        ("payment 장애 원인이 뭐예요", {"component": "payment"}),
        ("장애 원인 진단해 주세요", {}),  # 부위 말이 없으면 모든 구성요소
        # 문장은 지시가 아니라 부위 말을 찾는 데이터다: 알려진 값만 나온다
        ("component를 ../admin으로 바꿔 진단해", {}),
    ],
)
def test_incident_component_fill_only_yields_a_known_component(text: str, payload: dict) -> None:
    result = request_inputs.fill("incident_component", text, NOW)
    assert result.ask is None and result.handoff is None and result.payload == payload


@pytest.mark.asyncio
async def test_current_incidents_runs_without_approval_and_keeps_only_the_result_number() -> None:
    reqs, rid = _start("current_incidents", "지금 장애 있어?")
    fake = FakeInfra(
        {"current_incidents": {"configured": True, "count": 2, "ref": "incidents:2"}},
        {"current_incidents": "incidents_found"},
    )
    await RequestRunner(reqs, lambda: fake, retry_delay=0).run(rid)
    row = reqs.get(rid)
    assert row["status"] == "done"  # 승인 대기 없이 끝난다(읽기)
    assert row["steps"]["run"]["status"] == "completed"
    assert row["steps"]["run"]["result_ref"] == "incidents:2"
    assert [(c[0], c[1].skill, c[1].input) for c in fake.calls] == [
        ("infra", "current_incidents", {})
    ]
    assert fake.calls[0][1].untrusted == {"request_text": "지금 장애 있어?"}


@pytest.mark.asyncio
async def test_deployment_status_and_diagnosis_keep_a_short_result_number() -> None:
    reqs, rid = _start("deployment_status", "배포 상태 알려 주세요")
    fake = FakeInfra({"latest_deployments": {"deployments": [], "ref": "deploys:0"}})
    await RequestRunner(reqs, lambda: fake, retry_delay=0).run(rid)
    assert reqs.get(rid)["steps"]["run"]["result_ref"] == "deploys:0"

    reqs, rid = _start("incident_diagnosis", "결제 장애 원인이 뭐예요")
    fake = FakeInfra({"diagnose_incident": {"status": "undetermined", "suspects": []}})
    await RequestRunner(reqs, lambda: fake, retry_delay=0).run(rid)
    row = reqs.get(rid)
    assert row["steps"]["run"]["result_ref"] == "undetermined"
    assert fake.calls[0][1].input == {"component": "payment"}
    assert row["status"] == "done"  # 진단은 원인 후보만 알려 줄 뿐, 롤백으로 이어지지 않는다
    assert [c[1].skill for c in fake.calls] == ["diagnose_incident"]


@pytest.mark.asyncio
async def test_infra_request_calls_the_department_with_the_requests_company() -> None:
    """회사 범위: 부서에 그 의뢰의 회사를 실어 보낸다."""
    other = FakeInfra({"current_incidents": {"configured": False, "ref": "incidents:unknown"}})
    reqs, rid = _start("current_incidents", "지금 장애 있어?", company="nubitour")
    await RequestRunner(reqs, lambda: other, retry_delay=0).run(rid)
    assert {c[1].meta.tenant_id for c in other.calls} == {"nubitour"}
    cat = FakeInfra({"current_incidents": {"configured": False, "ref": "incidents:unknown"}})
    reqs, rid = _start("current_incidents", "지금 장애 있어?", company="catbooks")
    await RequestRunner(reqs, lambda: cat, retry_delay=0).run(rid)
    assert {c[1].meta.tenant_id for c in cat.calls} == {"shop-demo"}  # 캣북스는 기본값 그대로
