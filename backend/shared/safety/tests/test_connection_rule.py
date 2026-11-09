"""외부 연결에 쓰는 액션: 연결 id가 빠지면 실행하지 않고 사람 보류(C)로 보낸다."""

from __future__ import annotations

from typing import Any

import pytest

from ax_contracts.schemas import HoldReason
from ax_safety.gate import Gate
from ax_safety.rules import load_registry

EXTERNAL = [
    "generate_pr",
    "code_write",
    "review_pr",
    "merge_pr",
    "dev_task_cancel",
    "repo_comment",
    "repo_close_pr",
    "repo_reopen_pr",
    "repo_revert_pr",
    "repo_delete_branch",
]


def test_registry_lists_exactly_the_external_write_actions() -> None:
    marked = {a.action for a in load_registry().actions if a.connection_field}
    assert marked == set(EXTERNAL)


@pytest.mark.asyncio
@pytest.mark.parametrize("action", EXTERNAL)
@pytest.mark.parametrize("params", [{}, {"connection_id": ""}, {"connection_id": 7}])
async def test_missing_connection_is_held(gate: Gate, act: Any, action: str, params: dict) -> None:
    decision = await gate.execute(act(action, "dev", f"{action}-no-conn", **params))
    assert (decision.grade, decision.status) == ("C", "held")
    assert decision.hold_reason == HoldReason.SCHEMA_INVALID


@pytest.mark.asyncio
async def test_connection_passes_on_to_the_usual_rules(gate: Gate, act: Any) -> None:
    """연결 id는 통과 조건이 아니다. 뒤의 코드 규칙은 그대로 본다."""
    ok = await gate.execute(act("review_pr", "dev", "review-ok", connection_id="mock"))
    assert (ok.grade, ok.status) == ("A", "executed")
    big = await gate.execute(
        act(
            "generate_pr",
            "dev",
            "pr-big",
            connection_id="conn-1",
            changed_lines=99999,
            changed_files=1,
            changed_paths=["docs/spec.md"],
        )
    )
    assert (big.status, big.hold_reason) == ("held", HoldReason.OVER_THRESHOLD)


@pytest.mark.asyncio
async def test_other_actions_do_not_need_a_connection(gate: Gate, act: Any) -> None:
    decision = await gate.execute(act("send_message", "planning", "msg-1"))
    assert (decision.grade, decision.status) == ("A", "executed")
    notice = next(a for a in load_registry().actions if a.action == "publish_notice")
    assert notice.connection_field is None


@pytest.mark.asyncio
async def test_a_person_can_release_the_hold(gate: Gate, act: Any) -> None:
    """보류는 거절이 아니다. 사람이 승인하면 같은 키로 다시 불렀을 때 실행된다."""
    request = act("repo_comment", "dev", "comment-no-conn")
    held = await gate.execute(request)
    assert held.status == "held" and held.review_id is not None
    await gate.decide_review(held.review_id, "approved", "taeyoon", None)
    again = await gate.execute(request)
    assert again.status == "executed"
