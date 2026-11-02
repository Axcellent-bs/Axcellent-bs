"""개발(dev) 쪽 related 시험 (분리 5단계에서 domains/planning/tests/test_related.py에서 나눔)."""

from __future__ import annotations

import asyncio

import pytest

from ax_contracts.schemas import ExecuteDecision
from dev_agent.service import DevService


def run(coro):
    return asyncio.run(coro)


async def _gate(action, params, meta, evidence) -> ExecuteDecision:
    return ExecuteDecision(execution_id="exe_ok", grade="A", status="executed")


@pytest.mark.role("dev")
def test_open_prs_on_same_file_mark_each_request_as_conflict() -> None:
    """같은 파일을 고치는 열린 PR이 둘이면 (검색 없이) 두 요청에 서로 충돌 가능으로 표시한다."""
    svc = DevService(gate=_gate, relater=None)
    s = svc.state
    base = next(p for p in s.prs if p.task and p.changed_paths)
    # 다른 요청(REQ-12 → SPEC-7)의 작업을 하나 더 둔다
    other_task = next(t for t in s.tasks if t.id == base.task).model_copy(
        update={"id": "T-99", "spec": "SPEC-7", "para": "R1"}
    )
    s.tasks.append(other_task)
    first = base.model_copy(
        update={
            "id": "PR #90",
            "number": 90,
            "merged": False,
            "closed": False,
            "changed_paths": ["shop-web/src/pages/Cart.tsx"],
        }
    )
    s.prs.append(first)

    async def add_pr(st):
        st.prs.append(
            first.model_copy(
                update={
                    "id": "PR #91",
                    "number": 91,
                    "task": other_task.id,
                    "changed_paths": ["shop-web/src/pages/Cart.tsx", "docs/a.md"],
                }
            )
        )

    run(svc._apply(add_pr))
    st = svc.state
    from planning_agent.related import request_of_pr

    mine = request_of_pr(st, next(p for p in st.prs if p.id == "PR #91"))
    theirs = request_of_pr(st, next(p for p in st.prs if p.id == "PR #90"))
    assert mine and theirs and mine.id != theirs.id
    hit = next(x for x in mine.related if x.by == "path")
    assert (hit.id, hit.relation) == (theirs.id, "conflict") and "Cart.tsx" in hit.reason
    back = next(x for x in theirs.related if x.by == "path")
    assert back.id == mine.id and back.reason.startswith("PR #91가")
    assert any(a.point == "request.related" and "PR #91" in a.text for a in st.activity)
