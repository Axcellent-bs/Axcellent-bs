"""보류 큐 테스트: 사람 결정, 기한(데모 10분·운영 24시간), 기한 지나면 자동 거절."""

from __future__ import annotations

from datetime import timedelta
from typing import Any

import pytest

from ax_contracts.schemas import HoldReason
from ax_safety.gate import Gate
from ax_safety.rules import load_registry, load_thresholds
from ax_safety.store import Store


@pytest.mark.asyncio
async def test_review_approval_turns_replay_into_executed(gate: Gate, sent: Any, coupon) -> None:
    held = await gate.execute(coupon(20, 20_000, purpose_code="campaign", key="big"))
    review = await gate.decide_review(held.review_id or "", "approved", "태윤", "확인함")
    assert (review.status, review.decided_by) == ("approved", "태윤")
    replay = await gate.execute(coupon(20, 20_000, purpose_code="campaign", key="big"))
    assert (replay.status, replay.grade) == ("executed", "C")
    assert sent.types()[-1] == "safety.review_decided"
    with pytest.raises(ValueError):
        await gate.decide_review(held.review_id or "", "rejected", "태윤", None)


@pytest.mark.asyncio
async def test_review_auto_rejects_after_timeout(gate: Gate, clock: Any, sent: Any, coupon) -> None:
    """기본 기준표(데모)는 10분: 599초까지는 대기, 600초에 자동 거절."""
    held = await gate.execute(coupon(20, 20_000, purpose_code="campaign", key="slow"))
    clock.advance(599)
    assert await gate.expire_reviews() == []
    clock.advance(1)
    expired = await gate.expire_reviews()
    assert [r.review_id for r in expired] == [held.review_id]
    replay = await gate.execute(coupon(20, 20_000, purpose_code="campaign", key="slow"))
    assert (replay.status, replay.hold_reason) == ("rejected", HoldReason.REVIEW_TIMEOUT)
    event_type, data = sent.sent[-1]
    assert (event_type, data["decision"], data["decided_by"]) == (
        "safety.review_decided",
        "rejected",
        "system",
    )


@pytest.fixture
def ops_gate(monkeypatch: pytest.MonkeyPatch, clock: Any, verifier: Any, sent: Any) -> Gate:
    """운영 설정: `SAFETY_REVIEW_TIMEOUT_SECONDS=86400`(24시간)으로 띄운 관문."""
    monkeypatch.setenv("SAFETY_REVIEW_TIMEOUT_SECONDS", "86400")
    return Gate(load_registry(), load_thresholds(), Store(), verifier, sent, now=clock)


@pytest.mark.asyncio
async def test_ops_review_is_still_pending_after_ten_minutes(
    ops_gate: Gate, clock: Any, coupon
) -> None:
    """운영 기한이 24시간이면 10분·12시간 뒤에도 아직 사람 결정을 기다린다."""
    held = await ops_gate.execute(coupon(20, 20_000, purpose_code="campaign", key="wait"))
    review = ops_gate.store.get_review(held.review_id or "")
    assert review is not None
    assert review.expires_at - review.created_at == timedelta(hours=24)
    for seconds in (600, 12 * 3600, 24 * 3600 - 1):
        clock.now = review.created_at + timedelta(seconds=seconds)
        assert await ops_gate.expire_reviews() == []
    assert ops_gate.store.get_review(held.review_id or "").status == "pending"  # type: ignore[union-attr]
    clock.now = review.created_at + timedelta(hours=24)
    assert [r.review_id for r in await ops_gate.expire_reviews()] == [held.review_id]


@pytest.mark.asyncio
async def test_model_copy_with_24h_deadline(clock: Any, verifier: Any, sent: Any, coupon) -> None:
    """기준표 복사본으로 24시간을 줘도 같은 기한이 잡힌다(환경변수 없이)."""
    table = load_thresholds().model_copy(update={"review_timeout_seconds": 86_400})
    gate = Gate(load_registry(), table, Store(), verifier, sent, now=clock)
    held = await gate.execute(coupon(20, 20_000, purpose_code="campaign", key="copy"))
    review = gate.store.get_review(held.review_id or "")
    assert review is not None
    assert review.expires_at - review.created_at == timedelta(hours=24)


@pytest.mark.asyncio
async def test_decision_after_expiry_is_refused(gate: Gate, clock: Any, coupon) -> None:
    held = await gate.execute(coupon(20, 20_000, purpose_code="campaign", key="late"))
    clock.advance(600)
    with pytest.raises(ValueError, match="expired"):
        await gate.decide_review(held.review_id or "", "approved", "태윤", None)


# ---------------------------------------------------------------- 요청한 사람은 승인하지 못한다
def _settlement(act, key: str, who: str | None):
    return act("confirm_settlement", "support", key, period="w", requested_by=who)


@pytest.mark.asyncio
async def test_requester_cannot_approve_own_request(gate, act) -> None:
    from ax_safety.gate import SelfApprovalError

    held = await gate.execute(_settlement(act, "s1", "태윤"))
    with pytest.raises(SelfApprovalError):
        await gate.decide_review(held.review_id, "approved", "태윤", None)
    assert gate.store.get_review(held.review_id).status == "pending"
    done = await gate.decide_review(held.review_id, "approved", "재무담당", None)
    assert done.status == "approved"


@pytest.mark.asyncio
async def test_requester_can_still_reject_own_request(gate, act) -> None:
    held = await gate.execute(_settlement(act, "s2", "태윤"))
    done = await gate.decide_review(held.review_id, "rejected", "태윤", None)
    assert done.status == "rejected"


@pytest.mark.asyncio
async def test_shared_token_cannot_be_separated_so_it_is_allowed(gate, act) -> None:
    held = await gate.execute(_settlement(act, "s3", "공용"))
    assert (await gate.decide_review(held.review_id, "approved", "공용", None)).status == "approved"


def test_self_approval_is_403_over_http(client, act, gate) -> None:
    import asyncio

    held = asyncio.run(gate.execute(_settlement(act, "s4", "taeyoon")))
    response = client.post(
        f"/api/safety/reviews/{held.review_id}/decision",
        json={"decision": "approved"},
        headers={"Authorization": "Bearer human:taeyoon"},
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "SELF_APPROVAL"


# ---------------------------------------------------------------- 판매자 정산 기한 24시간 (v11)
@pytest.mark.asyncio
async def test_settlement_hold_waits_24_hours_while_coupon_expires_in_10_minutes(
    gate: Gate, clock: Any, act, coupon
) -> None:
    settle = await gate.execute(_settlement(act, "s24", "support-ai"))
    other = await gate.execute(coupon(20, 20_000, purpose_code="campaign", key="c10"))
    review = gate.store.get_review(settle.review_id or "")
    assert review is not None
    assert review.expires_at - review.created_at == timedelta(hours=24)
    clock.advance(600)
    assert [r.review_id for r in await gate.expire_reviews()] == [other.review_id]
    assert gate.store.get_review(settle.review_id or "").status == "pending"  # type: ignore[union-attr]
    clock.advance(24 * 3600 - 600)
    assert [r.review_id for r in await gate.expire_reviews()] == [settle.review_id]


@pytest.mark.asyncio
async def test_ai_requested_settlement_can_be_approved_by_the_support_owner(gate, act) -> None:
    """고객지원 AI가 올린 요청(`requested_by: support-ai`)은 고객지원 담당(병환)이 승인한다."""
    held = await gate.execute(_settlement(act, "s-ai", "support-ai"))
    done = await gate.decide_review(held.review_id, "approved", "byunghwan", None)
    assert (done.status, done.decided_by) == ("approved", "byunghwan")
