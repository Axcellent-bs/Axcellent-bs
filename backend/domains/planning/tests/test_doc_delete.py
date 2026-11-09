"""사람이 정리 문서를 지운다 (`delete_doc`). 지운 뒤 새 문서 번호가 겹치지 않는다."""

from __future__ import annotations

import asyncio

import pytest

from ax_contracts.schemas import ExecuteDecision
from planning_agent.domain.service import PlanningService


async def _gate(action, params, meta, evidence) -> ExecuteDecision:
    return ExecuteDecision(execution_id="exe_ok", grade="A", status="executed")


@pytest.mark.role("planning")
def test_delete_doc_and_new_id_does_not_collide() -> None:
    svc = PlanningService(gate=_gate, relater=None)
    a = asyncio.run(svc.dispatch({"type": "newDoc", "title": "a"})).output["doc_id"]
    b = asyncio.run(svc.dispatch({"type": "newDoc", "title": "b"})).output["doc_id"]
    asyncio.run(svc.dispatch({"type": "deleteDoc", "doc": a}))
    ids = [x.id for x in svc.state.docs]
    assert a not in ids and b in ids
    c = asyncio.run(svc.dispatch({"type": "newDoc", "title": "c"})).output["doc_id"]
    assert c not in (a, b) and len({x.id for x in svc.state.docs}) == len(svc.state.docs)
