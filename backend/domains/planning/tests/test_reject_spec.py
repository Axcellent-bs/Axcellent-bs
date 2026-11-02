"""확인 대기 기획서 거절 (`reject_spec`): 사람이 확정하지 않으면 대기열에서 내리고 기각을 남긴다."""

from __future__ import annotations

import asyncio

import pytest

from planning_agent.domain.service import ActionError


def run(coro):
    return asyncio.run(coro)


def _spec(service, sid: str):
    return next(x for x in service.state.specs if x.id == sid)


def _held_spec(service):
    run(service.inbound("support_ticket"))
    sp = next(x for x in service.state.specs if x.status != "확정")
    if sp.auto_review is None:  # 시험용으로 대기열에 올린다
        from ax_workflow.autocheck import WAIT_CROSS_CHECK, Check

        service._hold(sp, WAIT_CROSS_CHECK, Check(params={}, rules=["spec_hr_unchecked"]))
    return sp


def test_reject_takes_spec_off_queue_and_records(service) -> None:
    sp = _held_spec(service)
    res = run(service.dispatch({"type": "rejectSpec", "spec": sp.id, "reason": "범위가 너무 넓음"}))
    sp = _spec(service, sp.id)
    assert sp.status == "초안" and sp.auto_review is None
    dec = service.state.decisions[-1]
    assert dec.spec == sp.id and dec.text == f"[기각한 안] {sp.title}"
    assert dec.reason == "범위가 너무 넓음" and res.output["decision_id"] == dec.id
    assert any(a.point == "reject_spec" for a in service.state.activity)


def test_reject_needs_reason(service) -> None:
    sp = _held_spec(service)
    with pytest.raises(ActionError):
        run(service.dispatch({"type": "rejectSpec", "spec": sp.id, "reason": ""}))
    assert _spec(service, sp.id).auto_review is not None


def test_reject_not_waiting_is_no_change(service) -> None:
    sp = _held_spec(service)
    _spec(service, sp.id).auto_review = None
    res = run(service.dispatch({"type": "rejectSpec", "spec": sp.id, "reason": "x"}))
    assert not res.changed


def test_reject_unknown_spec(service) -> None:
    with pytest.raises(ActionError):
        run(service.dispatch({"type": "rejectSpec", "spec": "SPEC-999", "reason": "x"}))
