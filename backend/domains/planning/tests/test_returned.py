"""개발이 "코드 변경이 아니다"로 돌려보낸 작업: 기록 → 사람이 마케팅으로·개발로 다시·닫기.
폐기한 작업(dev `discard_dev_task`)만 남은 제안은 지울 수 있다."""

from __future__ import annotations

import asyncio

import pytest

from ax_contracts.schemas import ExecuteDecision
from ax_contracts.schemas.planning import CampaignBriefOutput, ReceiveInsightInput
from planning_agent.domain.returned_actions import (
    DEV_RETURNED,
    REDEV_NOTE,
    RETURNED_DISMISSED,
    pending_return,
    return_ctx,
)
from planning_agent.domain.service import ActionError, PlanningService
from planning_agent.proposals import ProposalDraft


async def _gate(action, params, meta, evidence) -> ExecuteDecision:
    return ExecuteDecision(execution_id="exe_ok", grade="A", status="executed")


class Writer:
    async def draft(self, inp, ctx):
        return ProposalDraft("루틴 브이로그 숏츠", "독서 검색이 평균의 1.6배", None, "숏폼", "llm")


class Brief:
    def __init__(self) -> None:
        self.sent: list = []

    async def send(self, ctx, brief, key):
        self.sent.append((ctx, brief, key))
        return CampaignBriefOutput(outcome="accepted", campaign_id="CMP-1")


class Dev:
    def __init__(self) -> None:
        self.sent: list = []

    async def __call__(self, skill, ctx, tenant, payload, key):
        self.sent.append((skill, key, payload.model_dump(mode="json")))
        return "보냄"


def _svc() -> tuple[PlanningService, Brief, Dev]:
    brief, dev = Brief(), Dev()
    svc = PlanningService(gate=_gate, relater=None, marketing=brief, proposal_writer=Writer(),
                          dev_sender=dev)  # fmt: skip
    return svc, brief, dev


async def _flush(svc: PlanningService) -> None:
    while svc._sending:
        await asyncio.gather(*list(svc._sending))


def _handed(svc: PlanningService) -> tuple[str, str]:
    """제안 → 기획안 → 개발로 넘김. (제안 id, 기획서 id)."""
    inp = ReceiveInsightInput.model_validate({
        "insight_id": "ins-1", "title": "가을 밤 독서 숏츠", "summary": "독서 검색이 오른다",
        "skill": "trend_history", "products": ["밤의 도서관"],
        "facts": [{"label": "독서 검색", "value": "평균의 1.6배", "source": "trend_history"}],
    })  # fmt: skip
    pid = asyncio.run(svc.receive_insight(requester="data", inp=inp)).output["proposal_id"]
    spec = asyncio.run(svc.dispatch({"type": "makePlan", "proposal": pid})).output["spec_id"]
    asyncio.run(svc.dispatch({"type": "proposalToRequest", "proposal": pid}))
    return pid, spec


def _dev_result(svc: PlanningService, spec: str, coding: dict | None, n: int, **task) -> str:
    """개발이 결과를 보낸 것처럼: 이 기획서 작업을 coding·task 칸으로 바꿔 받는다."""
    sp = next(x for x in svc.state.specs if x.id == spec)
    req = next(r for r in svc.state.requests if r.spec == spec or r.id == sp.request)
    tasks = []
    for t in svc.state.tasks:
        if t.spec != spec:
            continue
        d = t.model_dump(mode="json", by_alias=True)
        d["coding"] = coding and {"updated_at": "2026-11-05T08:00:00Z", **coding}
        d.update(state="중단", **task)
        tasks.append(d)
    asyncio.run(svc.receive_dev_result(
        context_id=req.context_id, requester="dev", spec_id=spec, request_id=req.id,
        request_state="작업 중", tasks=tasks, prs=[], idempotency_key=f"k-{n}",
    ))  # fmt: skip
    return tasks[0]["id"]


RETURNED = {"status": "returned", "returned_to": "marketing", "returned_reason": "홍보 문구 일"}


@pytest.mark.role("planning")
def test_returned_task_is_logged_and_sent_to_marketing_without_reason() -> None:
    svc, brief, _ = _svc()
    pid, spec = _handed(svc)
    tid = _dev_result(svc, spec, RETURNED, 1)
    assert pending_return(svc.state, tid) is not None
    assert any(
        a.point == DEV_RETURNED and "마케팅 · 홍보 문구 일" in a.text for a in svc.state.activity
    )

    async def go() -> None:
        await svc.dispatch({"type": "returnedToMarketing", "task": tid})
        await _flush(svc)

    asyncio.run(go())
    assert [b.proposal_id for _, b, _ in brief.sent] == [pid]  # 제안에서 만든 기획안은 그 제안으로
    assert pending_return(svc.state, tid) is None
    with pytest.raises(ActionError):  # 처리한 건 다시 못 보낸다
        asyncio.run(svc.dispatch({"type": "returnedToMarketing", "task": tid}))


@pytest.mark.role("planning")
def test_returned_to_dev_needs_reason_and_resends_with_keep_dev() -> None:
    svc, _, dev = _svc()
    _, spec = _handed(svc)
    tid = _dev_result(svc, spec, RETURNED, 1)
    with pytest.raises(ActionError):  # 이유 없이는 안 된다
        asyncio.run(svc.dispatch({"type": "returnedToDev", "task": tid, "reason": " "}))
    before = len(dev.sent)

    async def go() -> None:
        await svc.dispatch(
            {"type": "returnedToDev", "task": tid, "reason": "버튼 문구 코드 수정임"}
        )
        await _flush(svc)

    asyncio.run(go())
    sp = next(x for x in svc.state.specs if x.id == spec)
    assert sp.keep_dev and sp.extra[REDEV_NOTE] == "버튼 문구 코드 수정임"
    skill, key, payload = dev.sent[before]
    assert skill == "start_dev" and ":keep:" in key and payload["spec"]["keep_dev"] is True
    assert pending_return(svc.state, tid) is None


@pytest.mark.role("planning")
def test_dismiss_hides_only_in_planning() -> None:
    svc, _, _ = _svc()
    _, spec = _handed(svc)
    tid = _dev_result(svc, spec, RETURNED, 1)
    asyncio.run(svc.dispatch({"type": "dismissReturned", "task": tid}))
    assert pending_return(svc.state, tid) is None
    t = next(x for x in svc.state.tasks if x.id == tid)
    assert t.coding is not None and t.coding.status == "returned"  # 개발 작업은 그대로
    assert any(
        a.context_id == return_ctx(tid) and a.point == RETURNED_DISMISSED
        for a in svc.state.activity
    )


@pytest.mark.role("planning")
def test_proposal_with_only_discarded_tasks_can_be_deleted() -> None:
    svc, _, _ = _svc()
    pid, spec = _handed(svc)
    with pytest.raises(ActionError):  # 작업이 살아 있으면 막는다
        asyncio.run(svc.dispatch({"type": "deleteProposal", "proposal": pid}))
    _dev_result(svc, spec, {"status": "canceled"}, 2, discarded=True, discarded_by="예진")
    asyncio.run(svc.dispatch({"type": "deleteProposal", "proposal": pid}))
    assert all(p.id != pid for p in svc.state.proposals)
    assert all(x.id != spec for x in svc.state.specs)
