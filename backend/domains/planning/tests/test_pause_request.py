"""사람이 요청을 중단·재개한다 (`pause_request`·`resume_request`)."""

from __future__ import annotations

import asyncio

import pytest

from ax_contracts.schemas import ExecuteDecision
from ax_contracts.schemas.planning import RequestReleaseCheckInput
from planning_agent.domain.service import ActionError, PlanningService


def run(coro):
    return asyncio.run(coro)


async def _gate(action, params, meta, evidence) -> ExecuteDecision:
    return ExecuteDecision(execution_id="exe_ok", grade="A", status="executed")


class Sent:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict, str]] = []

    async def __call__(self, skill, ctx, tenant, payload, key) -> str:
        self.calls.append((skill, payload.model_dump(mode="json"), key))
        return "보냄"


def _svc() -> tuple[PlanningService, Sent]:
    sent = Sent()
    return PlanningService(gate=_gate, dev_sender=sent, relater=None), sent


def _req(svc: PlanningService, rid: str):
    return next(r for r in svc.state.requests if r.id == rid)


def _draft_and_request(svc: PlanningService):
    sp = next(x for x in svc.state.specs if x.status != "확정")
    return sp, _req(svc, sp.request)


@pytest.mark.role("planning")
def test_paused_request_cannot_be_confirmed_until_resumed() -> None:
    svc, _ = _svc()
    sp, req = _draft_and_request(svc)
    run(svc.dispatch({"type": "pauseRequest", "request": req.id, "reason": "범위 다시 볼 것"}))
    assert _req(svc, req.id).paused
    with pytest.raises(ActionError):
        run(svc.dispatch({"type": "confirmSpec", "spec": sp.id}))
    run(svc.dispatch({"type": "resumeRequest", "request": req.id}))
    assert not _req(svc, req.id).paused
    run(svc.dispatch({"type": "confirmSpec", "spec": sp.id}))
    assert next(x for x in svc.state.specs if x.id == sp.id).status == "확정"
    points = [a.point for a in svc.state.activity if a.context_id == req.context_id]
    assert "pause_request" in points and "resume_request" in points


@pytest.mark.role("planning")
def test_paused_confirmed_spec_is_not_handed_and_release_holds() -> None:
    svc, sent = _svc()
    sp, req = _draft_and_request(svc)
    run(svc.dispatch({"type": "pauseRequest", "request": req.id}))
    run(svc.dispatch({"type": "resumeRequest", "request": req.id}))
    run(svc.dispatch({"type": "confirmSpec", "spec": sp.id}))
    run(svc.dispatch({"type": "pauseRequest", "request": req.id}))

    async def flush() -> None:
        await asyncio.gather(*svc._sending)

    run(flush())
    pauses = [c for c in sent.calls if c[0] == "receive_request_pause"]
    spec = next(x for x in svc.state.specs if x.id == sp.id)
    assert spec.handed_to_dev  # 개발로 넘어간 요청이라 개발에 중단을 알린다
    assert pauses and pauses[-1][1]["paused"] is True
    assert pauses[-1][2].startswith(f"{req.context_id}:request_pause:{req.id}:")
    tasks = [t for t in svc.state.tasks if t.spec == sp.id]
    inp = RequestReleaseCheckInput.model_validate(
        {
            "spec_id": sp.id,
            "request_id": req.id,
            "prs": [
                {"number": 50 + i, "task_id": t.id, "merged": True, "review": "match"}
                for i, t in enumerate(tasks)
            ],
        }
    )
    res = run(
        svc.request_release_check(
            context_id="ctx_dev", requester="dev", inp=inp, idempotency_key="k-pause"
        )
    )
    assert res.output["verdict"] == "hold" and "paused_by_human" in res.output["hold_reasons"]
    assert not res.output["order_sent"]


@pytest.mark.role("planning")
def test_closed_request_is_not_paused() -> None:
    svc, _ = _svc()
    done = next((r for r in svc.state.requests if r.state in ("완료", "기각")), None)
    if done is None:
        pytest.skip("시드에 끝난 요청 없음")
    run(svc.dispatch({"type": "pauseRequest", "request": done.id}))
    assert not _req(svc, done.id).paused
