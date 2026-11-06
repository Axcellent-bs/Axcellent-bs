"""보유기간 지난 개인정보 지우기 (#736). 평소 건수는 A로 바로, 한꺼번에 많으면 사람이 본다."""

from __future__ import annotations

import pytest

from ax_contracts.schemas import HoldReason
from ax_safety.gate import Gate


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("action", "limit", "key"),
    [
        ("purge_expired_inquiries", 200, "purge_inquiries_batch"),
        ("purge_expired_claims", 100, "purge_claims_batch"),
    ],
)
async def test_usual_batch_runs_and_a_big_one_waits(
    gate: Gate, act, action: str, limit: int, key: str
) -> None:
    ok = await gate.execute(act(action, "support", "p1", count=limit))
    assert (ok.grade, ok.status) == ("A", "executed")

    held = await gate.execute(act(action, "support", "p2", count=limit + 1))
    assert (held.grade, held.status, held.hold_reason) == ("C", "held", HoldReason.OVER_THRESHOLD)
    review = gate.store.get_review(held.review_id or "")
    assert review is not None and {h["key"] for h in review.report["rules"]} == {key}


@pytest.mark.asyncio
@pytest.mark.parametrize("action", ["purge_expired_inquiries", "purge_expired_claims"])
async def test_missing_count_waits_and_only_support_asks(gate: Gate, act, action: str) -> None:
    missing = await gate.execute(act(action, "support", "p1"))
    assert missing.status == "held"
    other = await gate.execute(act(action, "marketing", "p2", count=1))
    assert other.hold_reason == HoldReason.UNREGISTERED_ACTION
