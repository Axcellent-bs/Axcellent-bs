"""StateService 적용 틀: 끝까지 성공하면 바꿔 끼우고, 관문이 거부하면 원래 상태를 둔다."""

from __future__ import annotations

import asyncio

import pytest

from ax_contracts.schemas import ExecuteDecision
from ax_workflow.core import ActionResult, GateRefused, StateService
from ax_workflow.models import AutoPolicy, State


def _gate(status: str):
    async def gate(action, params, meta, evidence):
        return ExecuteDecision(execution_id="exe_1", grade="A", status=status)

    return gate


class Svc(StateService):
    soft_actions = frozenset({"side_note"})

    def __init__(self, status: str):
        super().__init__(gate=_gate(status))
        self.after: list[bool] = []
        self._init_state()

    def _seed(self) -> State:
        return State(auto_policy=AutoPolicy(level=1, paths=""))

    def _after(self, s, mark, changed) -> None:
        self.after.append(changed)


async def _add(svc: Svc, action: str = "add") -> ActionResult:
    async def run(s: State) -> ActionResult:
        await svc._execute(action, "ctx_1", "T-1", "작업 추가")
        s.activity.append(svc._act(svc.clock(), "ctx_1", "dev", "dev", "x", "API", "추가"))
        return ActionResult(outcome="ok")

    return await svc._apply(run)


def test_apply_commits_and_notifies() -> None:
    svc = Svc("executed")

    async def go() -> None:
        q = svc.subscribe()
        out = await _add(svc)
        assert out.changed and out.outcome == "ok"
        assert await asyncio.wait_for(q.get(), 1) == 1

    asyncio.run(go())
    assert len(svc.state.activity) == 1 and svc.version == 1
    assert [g.action for g in svc.gate_calls] == ["add"] and svc.after == [True]


def test_refused_gate_keeps_state() -> None:
    svc = Svc("rejected")
    with pytest.raises(GateRefused):
        asyncio.run(_add(svc))
    assert svc.state.activity == [] and svc.version == 0


def test_soft_action_hold_does_not_undo() -> None:
    svc = Svc("verifying")
    out = asyncio.run(_add(svc, "side_note"))
    assert out.changed and len(svc.state.activity) == 1
    assert [g.status for g in svc.soft_holds] == ["verifying"]


def test_find_duplicate_only_links_open_requests() -> None:
    """끝난 요청(완료·기각)에는 같은 제목이어도 묶지 않는다 (다시 급증한 같은 분류, #300)."""
    from ax_workflow.models import Request
    from ax_workflow.rules import find_duplicate

    def req(rid: str, state: str, dup: str | None = None) -> Request:
        return Request.model_validate(
            {
                "id": rid,
                "context_id": f"ctx_req_{rid}",
                "from": "support",
                "via": "create_ticket",
                "title": "배송 문의 급증 — 안내·화면 점검 검토",
                "evidence": "최근 60분 배송 문의 7건",
                "state": state,
                "duplicate_of": dup,
            }
        )

    title = "배송 문의 급증 — 안내·화면 점검 검토"
    assert find_duplicate([req("REQ-1", "작업 중")], title) == "REQ-1"
    assert find_duplicate([req("REQ-1", "완료")], title) is None
    assert find_duplicate([req("REQ-1", "기각")], title) is None
    # 중복 연결된 요청은 원본의 상태를 본다
    assert find_duplicate([req("REQ-1", "완료"), req("REQ-2", "중복 연결", "REQ-1")], title) is None
    assert (
        find_duplicate([req("REQ-1", "기획 중"), req("REQ-2", "중복 연결", "REQ-1")], title)
        == "REQ-1"
    )
    # 끝난 옛 요청 뒤에 새로 받은 열린 요청이 있으면 그쪽에 묶는다
    assert find_duplicate([req("REQ-1", "완료"), req("REQ-3", "접수")], title) == "REQ-3"
    assert find_duplicate([req("REQ-1", "작업 중")], "다른 제목") is None
