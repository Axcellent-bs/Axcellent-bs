"""게시 일시 중지 스위치(`set_publish_control`): 중지는 A로 바로, 재개는 사람 승인(C)."""

from __future__ import annotations

import pytest

from ax_contracts.schemas import HoldReason
from ax_safety.gate import Gate


@pytest.mark.asyncio
async def test_pause_is_a_and_resume_waits_for_a_person(gate: Gate, act) -> None:
    pause = await gate.execute(
        act("set_publish_control", "marketing", "pc-1", channel="instagram", paused=True, resume=0)
    )
    assert (pause.grade, pause.status) == ("A", "executed")
    resume = await gate.execute(
        act("set_publish_control", "marketing", "pc-2", channel="instagram", paused=False, resume=1)
    )
    assert (resume.grade, resume.status) == ("C", "held")
    assert resume.hold_reason == HoldReason.OVER_THRESHOLD
    review = gate.store.get_review(resume.review_id)
    assert {r["key"] for r in review.report["rules"]} >= {"publish_resume_needs_human"}


@pytest.mark.asyncio
async def test_only_marketing_can_request_it_and_a_missing_flag_is_held(gate: Gate, act) -> None:
    other = await gate.execute(act("set_publish_control", "support", "pc-3", resume=0))
    assert (other.status, other.hold_reason) == ("held", HoldReason.UNREGISTERED_ACTION)
    missing = await gate.execute(act("set_publish_control", "marketing", "pc-4", paused=True))
    assert (missing.grade, missing.status) == ("C", "held")  # resume 필드가 없으면 fail-closed
