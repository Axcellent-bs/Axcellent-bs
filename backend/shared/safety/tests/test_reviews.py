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


# ---------------------------------------------------------- 공용 토큰의 승인자 이름 (decided_as)
@pytest.mark.asyncio
async def test_shared_token_records_the_named_approver(gate, act) -> None:
    held = await gate.execute(_settlement(act, "n1", "support-ai"))
    done = await gate.decide_review(held.review_id, "approved", "공용", "W44 확인", "병환")
    assert (done.status, done.decided_by, done.decided_as) == ("approved", "공용", "병환")
    audit = gate.store.list_audit()[-1]
    assert audit.step == "review_approved"
    assert (audit.detail["by"], audit.detail["decided_as"]) == ("공용", "병환")


@pytest.mark.asyncio
async def test_shared_token_cannot_self_approve_by_typing_own_name(gate, act) -> None:
    from ax_safety.gate import SelfApprovalError

    held = await gate.execute(_settlement(act, "n2", "태윤"))
    with pytest.raises(SelfApprovalError):
        await gate.decide_review(held.review_id, "approved", "공용", None, " 태윤 ")
    assert gate.store.get_review(held.review_id).status == "pending"
    done = await gate.decide_review(held.review_id, "approved", "공용", None, "병환")
    assert (done.status, done.decided_as) == ("approved", "병환")


@pytest.mark.asyncio
async def test_personal_token_ignores_decided_as(gate, act) -> None:
    from ax_safety.gate import SelfApprovalError

    held = await gate.execute(_settlement(act, "n3", "태윤"))
    with pytest.raises(SelfApprovalError):  # 다른 이름을 적어도 토큰이 곧 사람이다
        await gate.decide_review(held.review_id, "approved", "태윤", None, "병환")
    done = await gate.decide_review(held.review_id, "approved", "byunghwan", None, "아무개")
    assert (done.decided_by, done.decided_as) == ("byunghwan", None)
    assert "decided_as" not in gate.store.list_audit()[-1].detail


def test_decided_as_over_http(client, act, gate, monkeypatch) -> None:
    import asyncio

    monkeypatch.setenv("SAFETY_HUMAN_TOKENS", "공용:shared-token")
    held = asyncio.run(gate.execute(_settlement(act, "n4", "support-ai")))
    url = f"/api/safety/reviews/{held.review_id}/decision"
    shared = {"Authorization": "Bearer shared-token"}
    for bad in ("", "   ", "가" * 41):
        response = client.post(
            url, json={"decision": "approved", "decided_as": bad}, headers=shared
        )
        assert response.status_code == 422
    response = client.post(
        url, json={"decision": "approved", "decided_as": "  병환 "}, headers=shared
    )
    assert response.status_code == 200
    body = response.json()["data"]
    assert (body["decided_by"], body["decided_as"]) == ("공용", "병환")


def test_decided_as_self_approval_is_403_over_http(client, act, gate, monkeypatch) -> None:
    import asyncio

    monkeypatch.setenv("SAFETY_HUMAN_TOKENS", "공용:shared-token")
    held = asyncio.run(gate.execute(_settlement(act, "n5", "태윤")))
    response = client.post(
        f"/api/safety/reviews/{held.review_id}/decision",
        json={"decision": "approved", "decided_as": "태윤"},
        headers={"Authorization": "Bearer shared-token"},
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "SELF_APPROVAL"


# ------------------------------------------------- 서비스 이름 확인 `confirm` (롤백 설계 3-b)
def _service_hold(act, key: str, who: str | None = "support-ai", service: str | None = "payment"):
    params: dict[str, Any] = {"period": "w", "requested_by": who}
    if service is not None:
        params["service"] = service
    return act("confirm_settlement", "support", key, **params)


def _decide(client, review_id: str, **body: Any):
    return client.post(
        f"/api/safety/reviews/{review_id}/decision",
        json={"decision": "approved", **body},
        headers={"Authorization": "Bearer human:byunghwan"},
    )


def _pending_unchanged(gate, review_id: str, audit_count: int) -> None:
    assert gate.store.get_review(review_id).status == "pending"
    assert len(gate.store.list_audit()) == audit_count


def test_confirm_omitted_behaves_as_before(client, act, gate) -> None:
    import asyncio

    held = asyncio.run(gate.execute(_service_hold(act, "c0")))
    response = _decide(client, held.review_id)
    assert response.status_code == 200
    assert response.json()["data"]["status"] == "approved"


def test_confirm_match_approves_and_is_not_logged(client, act, gate) -> None:
    import asyncio

    held = asyncio.run(gate.execute(_service_hold(act, "c1")))
    response = _decide(client, held.review_id, confirm="  payment ")
    assert response.status_code == 200
    assert response.json()["data"]["status"] == "approved"
    assert "payment" not in str(gate.store.list_audit()[-1].detail)


@pytest.mark.parametrize("typed", ["Payment", "payments", "", "   "])
def test_confirm_mismatch_is_422_and_changes_nothing(client, act, gate, typed) -> None:
    import asyncio

    held = asyncio.run(gate.execute(_service_hold(act, "c2")))
    before = len(gate.store.list_audit())
    response = _decide(client, held.review_id, confirm=typed)
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "CONFIRM_MISMATCH"
    _pending_unchanged(gate, held.review_id, before)
    assert gate.store.get_execution(held.execution_id).decision.status == "held"
    assert (
        _decide(client, held.review_id, confirm="payment").status_code == 200
    )  # 그 뒤 맞으면 통과


def test_confirm_on_hold_without_service_is_not_applicable(client, act, gate) -> None:
    import asyncio

    held = asyncio.run(gate.execute(_service_hold(act, "c3", service=None)))
    before = len(gate.store.list_audit())
    response = _decide(client, held.review_id, confirm="payment")
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "CONFIRM_NOT_APPLICABLE"
    _pending_unchanged(gate, held.review_id, before)


def test_confirm_is_ignored_on_rejection(client, act, gate) -> None:
    import asyncio

    held = asyncio.run(gate.execute(_service_hold(act, "c4")))
    response = client.post(
        f"/api/safety/reviews/{held.review_id}/decision",
        json={"decision": "rejected", "confirm": "틀린이름"},
        headers={"Authorization": "Bearer human:byunghwan"},
    )
    assert response.status_code == 200
    assert response.json()["data"]["status"] == "rejected"


def test_confirm_too_long_or_unknown_field_is_422(client, act, gate) -> None:
    import asyncio

    held = asyncio.run(gate.execute(_service_hold(act, "c5")))
    assert _decide(client, held.review_id, confirm="a" * 65).status_code == 422
    assert _decide(client, held.review_id, other="x").status_code == 422
    assert gate.store.get_review(held.review_id).status == "pending"


def test_already_decided_is_409_even_with_wrong_confirm(client, act, gate) -> None:
    import asyncio

    held = asyncio.run(gate.execute(_service_hold(act, "c6")))
    assert _decide(client, held.review_id, confirm="payment").status_code == 200
    response = _decide(client, held.review_id, confirm="틀림")
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "ALREADY_DECIDED"


def test_expired_is_409_even_with_wrong_confirm(client, act, gate, clock) -> None:
    import asyncio

    held = asyncio.run(gate.execute(_service_hold(act, "c7")))
    clock.advance(24 * 3600 + 1)
    response = _decide(client, held.review_id, confirm="틀림")
    assert response.status_code == 409


def test_self_approval_stays_403_with_matching_or_wrong_confirm(client, act, gate) -> None:
    import asyncio

    held = asyncio.run(gate.execute(_service_hold(act, "c8", who="byunghwan")))
    for typed in ("payment", "틀림"):
        response = _decide(client, held.review_id, confirm=typed)
        assert response.status_code == 403
        assert response.json()["error"]["code"] == "SELF_APPROVAL"
    assert gate.store.get_review(held.review_id).status == "pending"


def test_other_company_review_is_404_before_confirm(client, act, gate, monkeypatch) -> None:
    import asyncio

    from ax_agent_template import human_auth

    held = asyncio.run(gate.execute(_service_hold(act, "c9")))
    monkeypatch.delenv("AGENT_TEMPLATE_MOCK")
    monkeypatch.setenv(human_auth.SESSION_SECRET_ENV, "s" * 40)
    nubi = human_auth.issue_session(
        account_id="acc-n", name="누비관리", role="admin", company_id="nubi", company_role="admin"
    )[0]
    before = len(gate.store.list_audit())
    for typed in ("payment", "틀림"):
        response = client.post(
            f"/api/safety/reviews/{held.review_id}/decision",
            json={"decision": "approved", "confirm": typed},
            headers={"Authorization": f"Bearer {nubi}"},
        )
        assert response.status_code == 404
    _pending_unchanged(gate, held.review_id, before)
