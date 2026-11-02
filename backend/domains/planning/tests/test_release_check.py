"""배포 전 기획서 대조 (`request_release_check`) → 배포 지시 (`receive_release_order`).

코드 규칙 대조만 본다. 개발에 보내는 지시는 가짜 보내기로 받아 본다 (실제 A2A 없음).
"""

from __future__ import annotations

import asyncio

import pytest

from ax_contracts.schemas import ExecuteDecision
from ax_contracts.schemas.planning import RequestReleaseCheckInput, RequestReleaseCheckOutput
from planning_agent.domain.service import ActionError, PlanningService


def run(coro):
    return asyncio.run(coro)


async def _gate(action, params, meta, evidence) -> ExecuteDecision:
    return ExecuteDecision(execution_id="exe_ok", grade="A", status="executed")


class Sent:
    def __init__(self, how: str = "보냄") -> None:
        self.how = how
        self.calls: list[tuple[str, str, dict, str]] = []

    async def __call__(self, skill, ctx, tenant, payload, key) -> str:
        self.calls.append((skill, ctx, payload.model_dump(mode="json"), key))
        return self.how


def _svc(sent: Sent | None = None) -> tuple[PlanningService, Sent]:
    sent = sent or Sent()
    return PlanningService(gate=_gate, dev_sender=sent, relater=None), sent


def _confirmed(svc: PlanningService):
    """시드 SPEC-7을 사람이 확정해 작업을 만든다 (요구사항 문단마다 작업 하나)."""
    run(svc.dispatch({"type": "confirmSpec", "spec": "SPEC-7"}))
    sp = next(x for x in svc.state.specs if x.id == "SPEC-7")
    sp.question = None  # 시드 기획서의 남은 질문은 이 시험 범위 밖 (open_question은 따로 본다)
    tasks = [t for t in svc.state.tasks if t.spec == "SPEC-7"]
    return sp, tasks


def _ask(svc, sp, prs, key="k1", env="dev"):
    inp = RequestReleaseCheckInput.model_validate(
        {"spec_id": sp.id, "request_id": sp.request, "env": env, "prs": prs}
    )
    res = run(
        svc.request_release_check(
            context_id="ctx_a2a_dev", requester="dev", inp=inp, idempotency_key=key
        )
    )
    return RequestReleaseCheckOutput.model_validate(res.output)


def _prs(tasks, review="match", merged=True):
    return [
        {"number": 40 + i, "task_id": t.id, "merged": merged, "review": review}
        for i, t in enumerate(tasks)
    ]


@pytest.mark.role("planning")
def test_ready_sends_release_order() -> None:
    svc, sent = _svc()
    sp, tasks = _confirmed(svc)
    out = _ask(svc, sp, _prs(tasks), env="prod")
    assert out.verdict == "ready" and out.order_sent and out.hold_reasons == []
    assert all(i.status == "covered" and i.content == "match" for i in out.items)
    orders = [c for c in sent.calls if c[0] == "receive_release_order"]
    assert len(orders) == 1
    skill, ctx, payload, key = orders[0]
    assert payload["decided_by"] == "agent" and payload["env"] == "prod"
    assert payload["pr_numbers"] == [40 + i for i in range(len(tasks))]
    assert key == f"{ctx}:release_order:{out.check_id}"


@pytest.mark.role("planning")
def test_hold_reasons_and_no_order() -> None:
    svc, sent = _svc()
    sp, tasks = _confirmed(svc)
    prs = _prs(tasks)
    prs[0]["merged"] = False
    prs.append({"number": 99, "task_id": None, "merged": True, "review": "match"})
    out = _ask(svc, sp, prs)
    assert out.verdict == "hold" and not out.order_sent
    assert {"task_not_merged", "pr_outside_spec"} <= set(out.hold_reasons)
    assert not [c for c in sent.calls if c[0] == "receive_release_order"]


@pytest.mark.role("planning")
def test_open_question_holds() -> None:
    svc, _ = _svc()
    sp, tasks = _confirmed(svc)
    sp.question = "재고가 없어진 상품도 남겨 둘까요?"
    out = _ask(svc, sp, _prs(tasks))
    assert out.verdict == "hold" and out.hold_reasons == ["open_question"]


@pytest.mark.role("planning")
def test_unconfirmed_and_unknown_spec_hold() -> None:
    svc, _ = _svc()
    sp = next(x for x in svc.state.specs if x.status != "확정")
    out = _ask(svc, sp, [{"number": 1, "task_id": "T-1", "merged": True}])
    assert out.verdict == "hold" and "spec_not_confirmed" in out.hold_reasons
    inp = RequestReleaseCheckInput.model_validate(
        {"spec_id": "SPEC-999", "request_id": "REQ-1", "prs": [{"number": 1, "merged": True}]}
    )
    res = run(
        svc.request_release_check(context_id="c", requester="dev", inp=inp, idempotency_key="k9")
    )
    assert res.output["verdict"] == "hold" and res.output["hold_reasons"] == ["spec_unknown"]


@pytest.mark.role("planning")
def test_review_mismatch_needs_human_then_person_orders() -> None:
    svc, sent = _svc()
    sp, tasks = _confirmed(svc)
    prs = _prs(tasks)
    prs[0]["review"] = "mismatch"
    prs[0]["review_note"] = "완료 기준 C1의 문구가 빠짐"
    out = _ask(svc, sp, prs)
    assert out.verdict == "needs_human" and not out.order_sent
    assert out.items[0].content == "mismatch" and "C1" in out.items[0].content_notes[0]
    assert not [c for c in sent.calls if c[0] == "receive_release_order"]

    async def person() -> None:
        await svc.dispatch({"type": "sendReleaseOrder", "check": out.check_id, "note": "확인함"})
        await asyncio.gather(*svc._sending)

    run(person())
    orders = [c for c in sent.calls if c[0] == "receive_release_order"]
    assert len(orders) == 1 and orders[0][2]["decided_by"] == "human"
    assert svc.release_checks[out.check_id]["order_sent"]


@pytest.mark.role("planning")
def test_review_none_needs_human_and_hold_cannot_be_ordered() -> None:
    svc, _ = _svc()
    sp, tasks = _confirmed(svc)
    out = _ask(svc, sp, _prs(tasks, review="none"))
    assert out.verdict == "needs_human"
    held = _ask(svc, sp, _prs(tasks, merged=False), key="k2")
    with pytest.raises(ActionError):
        run(svc.dispatch({"type": "sendReleaseOrder", "check": held.check_id}))


@pytest.mark.role("planning")
def test_same_key_returns_same_check_and_failed_send_is_reported() -> None:
    svc, sent = _svc(Sent("보내지 못함 (failed FORBIDDEN_SKILL)"))
    sp, tasks = _confirmed(svc)
    first = _ask(svc, sp, _prs(tasks))
    again = _ask(svc, sp, _prs(tasks))
    assert first.check_id == again.check_id and len(svc.release_checks) == 1
    assert first.verdict == "ready" and not first.order_sent
    assert any(
        a.point == "receive_release_order" and "보내지 못함" in a.text for a in svc.state.activity
    )
