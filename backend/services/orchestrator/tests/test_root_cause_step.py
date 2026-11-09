"""결제 장애: 영향 분석 다음에 원인 후보를 받는다.

확인이 필요하면 원인 확인 업무를 개발 쪽에 넘긴다.
"""

from __future__ import annotations

import asyncio
import contextlib

import pytest

from ax_contracts.schemas import AgentRequest, AgentResult, InputReason, TaskStatus
from ax_orchestrator.mock.harness import Harness, new_incident
from ax_orchestrator.plans.scenario_a import scenario_a

UNSURE = {
    "hypotheses": [
        {
            "explanation": "이상 시작 직전 배포가 원인일 수 있음 — PR #412 병합·배포",
            "fact_ids": ["f2", "f1"],
            "confidence": "mid",
        }
    ],
    "needs_confirmation": True,
    "facts": [
        {"fact_id": "f1", "kind": "anomaly", "source": "input", "ref": "ctx", "summary": "이상"},
        {
            "fact_id": "f2",
            "kind": "deploy",
            "source": "planning.find_related_prs",
            "ref": "pr:412",
            "summary": "PR #412 병합·배포",
        },
    ],
    "summary": "원인 후보 1개 (근거 사실 1개) · 시간 순서 규칙으로 고름 · 사람 확인 필요",
}


class Override:
    """skill 하나의 답만 바꾸고 나머지는 가짜 부서에 그대로 보낸다."""

    def __init__(self, inner, answers: dict) -> None:  # noqa: ANN001
        self.inner, self.answers = inner, answers

    async def __call__(self, dept: str, req: AgentRequest) -> AgentResult:
        answer = self.answers.get(req.skill)
        if answer is None:
            return await self.inner(dept, req)
        self.inner.calls.append((dept, req))
        return answer(req)


def _completed(output: dict):
    return lambda req: AgentResult(meta=req.meta, status=TaskStatus.COMPLETED, output=output)


def _disabled(service: str):
    return lambda req: AgentResult(
        meta=req.meta,
        status=TaskStatus.REJECTED,
        outcome="service_disabled",
        output={"to_service": service},
    )


def _handed_to_human(req: AgentRequest) -> AgentResult:
    return AgentResult(
        meta=req.meta,
        status=TaskStatus.INPUT_REQUIRED,
        reason_code=InputReason.DEPT_INPUT,
        outcome="handed_to_human",
        output={"task_id": "hof_cause", "to_service": "planning", "assignee_unit": "개발"},
    )


def _requests(harness: Harness, skill: str) -> list[AgentRequest]:
    return [req for _, req in harness.fakes.calls if req.skill == skill]


async def _run(answers: dict):
    harness = Harness()
    engine = await harness.start()
    engine.caller = Override(harness.fakes, answers)
    inc = await engine.run(new_incident(), scenario_a())
    return harness, inc


@pytest.mark.asyncio
async def test_root_cause_is_asked_after_impact_with_the_same_window() -> None:
    harness, inc = await _run({})
    skills = [req.skill for _, req in harness.fakes.calls]
    assert skills.index("impact_analysis") < skills.index("root_cause_hypotheses")
    [impact] = _requests(harness, "impact_analysis")
    [asked] = _requests(harness, "root_cause_hypotheses")
    assert asked.input["window_from"] == impact.input["window_from"]
    assert asked.input["window_to"] == impact.input["window_to"]
    assert asked.input["metric"] == "payment_fail_rate"
    assert asked.input["context_id"] == inc.context_id
    assert inc.steps["root_cause"].status == "completed"
    # 기존 단계 순서는 그대로: 롤백·공지·보상은 원인 추정을 기다리지 않는다
    plan = {s.id: s for s in scenario_a().steps}
    for sid in ("rollback", "notice", "compensation", "recovery"):
        assert "root_cause" not in (*plan[sid].after, *plan[sid].also_after)


@pytest.mark.asyncio
async def test_unsure_cause_is_handed_to_planning_as_a_cause_check() -> None:
    harness, inc = await _run({"root_cause_hypotheses": _completed(UNSURE)})
    [ticket] = _requests(harness, "create_ticket")
    assert ticket.input["title"] == "payment 장애 원인 확인"
    assert UNSURE["summary"] in ticket.input["evidence"]
    assert "PR #412 병합·배포" in ticket.input["evidence"]
    assert ticket.input["evidence_ids"] == ["pr:412"]  # 이상 구간 자체(f1)는 근거 id에 넣지 않는다
    assert ticket.input["source_ref"] == inc.context_id
    assert inc.steps["cause_check"].status == "completed"
    assert inc.status == "resolved"


@pytest.mark.asyncio
async def test_no_cause_estimate_still_asks_people_to_check() -> None:
    harness, inc = await _run({"root_cause_hypotheses": _disabled("data")})
    root = inc.steps["root_cause"]
    assert (root.status, root.reason_code) == ("skipped", "service_disabled")
    [ticket] = _requests(harness, "create_ticket")
    assert "원인 추정을 받지 못함" in ticket.input["evidence"]
    assert not any(s.status == "failed" for s in inc.steps.values())


@pytest.mark.asyncio
async def test_cause_check_goes_to_the_human_inbox_when_planning_is_off() -> None:
    harness = Harness()
    engine = await harness.start()
    engine.caller = Override(
        harness.fakes,
        {"root_cause_hypotheses": _completed(UNSURE), "create_ticket": _handed_to_human},
    )
    inc = new_incident()
    task = asyncio.create_task(engine.run(inc, scenario_a()))
    for _ in range(500):
        await asyncio.sleep(0.01)
        if inc.steps.get("cause_check") and inc.steps["cause_check"].status == "waiting_human":
            break
    check = inc.steps["cause_check"]
    assert check.status == "waiting_human"
    assert check.handoff_task_id == "hof_cause"
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
