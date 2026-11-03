"""사람이 확정 전 기획서를 고친다 (`edit_spec`)."""

from __future__ import annotations

import asyncio

import pytest

from ax_contracts.schemas import ExecuteDecision
from planning_agent.domain.service import ActionError, PlanningService


def run(coro):
    return asyncio.run(coro)


async def _gate(action, params, meta, evidence) -> ExecuteDecision:
    return ExecuteDecision(execution_id="exe_ok", grade="A", status="executed")


def _svc() -> PlanningService:
    return PlanningService(gate=_gate, relater=None)


def _draft(svc: PlanningService):
    return next(x for x in svc.state.specs if x.status != "확정")


@pytest.mark.role("planning")
def test_person_edits_draft_text_and_count() -> None:
    svc = _svc()
    sp = _draft(svc)
    pid = sp.paras[0].id
    before = sp.human_sentences
    res = run(
        svc.dispatch(
            {
                "type": "editSpec",
                "spec": sp.id,
                "problem": "고친 문제",
                "goal": "",
                "paras": {pid: "고친 문단"},
            }
        )
    )
    assert res.output["changed"] == ["문제", pid]
    after = next(x for x in svc.state.specs if x.id == sp.id)
    assert after.problem == "고친 문제" and after.paras[0].text == "고친 문단"
    assert after.goal == sp.goal  # 빈 값은 그대로
    assert after.human_sentences == before + 2
    assert any(a.point == "edit_spec" for a in svc.state.activity)


@pytest.mark.role("planning")
def test_same_text_changes_nothing() -> None:
    svc = _svc()
    sp = _draft(svc)
    version = svc.version
    run(
        svc.dispatch(
            {"type": "editSpec", "spec": sp.id, "problem": sp.problem, "goal": sp.goal, "paras": {}}
        )
    )
    assert svc.version == version


@pytest.mark.role("planning")
def test_confirmed_spec_cannot_be_edited() -> None:
    svc = _svc()
    sp = next(x for x in svc.state.specs if x.status == "확정")
    with pytest.raises(ActionError):
        run(
            svc.dispatch(
                {"type": "editSpec", "spec": sp.id, "problem": "x", "goal": "", "paras": {}}
            )
        )
