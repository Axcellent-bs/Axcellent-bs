"""안전 관문의 사람 결정(`safety.review_decided`)으로 승인 대기 단계를 잇는다."""

import asyncio
import uuid
from datetime import UTC, datetime

import pytest
from fastapi.testclient import TestClient

from ax_contracts.schemas import EventEnvelope, EventType
from ax_orchestrator.main import app, state
from ax_orchestrator.manager import IncidentManager
from ax_orchestrator.mock.harness import Harness
from ax_orchestrator.plans.scenario_a import scenario_a

REVIEW = "rev_rollback_deployment"  # 모의 부서가 돌려주는 보류 건 번호


def degraded(event_id: str = "evt_down") -> EventEnvelope:
    return EventEnvelope(
        event_id=event_id,
        type=EventType.SHOP_DEGRADED,
        occurred_at=datetime.now(UTC),
        source="shop-api",
        idempotency_key=event_id,
        data={"component": "payment", "metric_code": "error_rate_5xx"},
    )


def decided(
    review_id: str = REVIEW, decision: str = "approved", source: str = "safety"
) -> EventEnvelope:
    event_id = f"evt_{uuid.uuid4().hex[:10]}"
    return EventEnvelope(
        event_id=event_id,
        type=EventType.SAFETY_REVIEW_DECIDED,
        occurred_at=datetime.now(UTC),
        source=source,
        idempotency_key=event_id,
        data={
            "review_id": review_id,
            "execution_id": "exe_1",
            "action": "rollback_deployment",
            "requester": "infra",
            "decision": decision,
            "decided_by": "kim",
        },
    )


def safety_says(status: str | None, by: str | None = "kim"):
    """안전 관문이 이렇게 답하는 척하는 조회. `None`이면 안전 관문을 못 읽은 것."""
    calls: list[str] = []

    async def check(review_id: str):
        calls.append(review_id)
        return (
            None if status is None else {"review_id": review_id, "status": status, "decided_by": by}
        )

    check.calls = calls  # type: ignore[attr-defined]
    return check


async def wait_held(inc, seconds: float = 5.0) -> None:
    for _ in range(int(seconds / 0.02)):
        run = inc.steps.get("rollback")
        if run is not None and run.status == "held":
            return
        await asyncio.sleep(0.02)
    raise AssertionError("rollback이 승인 대기가 되지 않았다")


async def start(hold_timeout: float = 30.0):
    harness = Harness(hold_timeout=hold_timeout)
    harness.fakes.script("infra", "rollback_deployment", "hold")
    engine = await harness.start()
    return engine, IncidentManager(engine, scenario_a)


async def open_held(engine, manager, check):
    engine.review_check = check
    _, inc = await manager.process(degraded())
    await wait_held(inc)
    return inc


@pytest.mark.asyncio
async def test_approval_resumes_the_held_step() -> None:
    engine, manager = await start()
    check = safety_says("approved")
    inc = await open_held(engine, manager, check)
    assert inc.holds["rollback"].review_id == REVIEW

    outcome, got = await manager.process(decided())
    assert outcome == "resumed" and got is inc
    await manager.wait(inc.context_id)
    assert inc.steps["rollback"].status == "completed"
    assert inc.status == "resolved"
    assert check.calls == [REVIEW]  # 이벤트를 믿지 않고 안전 관문에 되물었다
    assert any("승인" in e["message"] and "kim" in e["message"] for e in inc.events)


@pytest.mark.asyncio
async def test_rejection_ends_the_step_as_rejected() -> None:
    engine, manager = await start()
    inc = await open_held(engine, manager, safety_says("rejected"))
    outcome, _ = await manager.process(decided(decision="rejected"))
    assert outcome == "resumed"
    await manager.wait(inc.context_id)
    run = inc.steps["rollback"]
    assert run.status == "rejected_by_human" and run.note == "사람이 거절"


@pytest.mark.asyncio
async def test_expiry_by_safety_is_a_timeout_rejection() -> None:
    engine, manager = await start()
    inc = await open_held(engine, manager, safety_says("expired", by="system"))
    await manager.process(decided(decision="expired"))
    await manager.wait(inc.context_id)
    run = inc.steps["rollback"]
    assert run.status == "rejected_by_human" and run.note == "시간 초과로 자동 거절"


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["pending", None])
async def test_approval_that_safety_does_not_confirm_is_ignored(status) -> None:
    engine, manager = await start()
    inc = await open_held(engine, manager, safety_says(status))
    outcome, _ = await manager.process(decided(decision="approved"))
    assert outcome == "unverified"
    assert inc.holds["rollback"].pending  # 승인되지 않았다
    assert any("확인하지 못해 무시" in e["message"] for e in inc.events)
    engine.decide_hold(inc, "rollback", False)  # 정리
    await manager.wait(inc.context_id)


@pytest.mark.asyncio
async def test_safety_record_wins_over_what_the_event_claims() -> None:
    """이벤트는 승인이라는데 안전 관문 기록은 거절이면 거절로 끝난다(승인되지 않는다)."""
    engine, manager = await start()
    inc = await open_held(engine, manager, safety_says("rejected"))
    outcome, _ = await manager.process(decided(decision="approved"))
    assert outcome == "resumed"
    await manager.wait(inc.context_id)
    assert inc.steps["rollback"].status == "rejected_by_human"


@pytest.mark.asyncio
async def test_decision_from_a_non_safety_source_is_ignored() -> None:
    engine, manager = await start()
    check = safety_says("approved")
    inc = await open_held(engine, manager, check)
    outcome, got = await manager.process(decided(source="infra"))
    assert outcome == "ignored" and got is None
    assert inc.holds["rollback"].pending and check.calls == []
    engine.decide_hold(inc, "rollback", False)
    await manager.wait(inc.context_id)


@pytest.mark.asyncio
async def test_decision_that_arrives_before_the_hold_is_kept() -> None:
    engine, manager = await start()
    engine.review_check = safety_says("approved")
    outcome, none = await manager.process(decided())
    assert outcome == "kept" and none is None
    _, inc = await manager.process(degraded())
    await manager.wait(inc.context_id)  # 승인 대기에 오르자마자 보관한 결정으로 이어 간다
    assert inc.steps["rollback"].status == "completed"


@pytest.mark.asyncio
async def test_timeout_asks_safety_once_more_so_an_approval_is_not_lost() -> None:
    engine, manager = await start(hold_timeout=0.3)
    engine.review_check = safety_says("approved")  # 결정 이벤트는 놓쳤다
    _, inc = await manager.process(degraded())
    await manager.wait(inc.context_id)
    assert inc.steps["rollback"].status == "completed"


@pytest.mark.asyncio
async def test_timeout_without_a_decision_rejects() -> None:
    engine, manager = await start(hold_timeout=0.3)
    engine.review_check = safety_says("pending")
    _, inc = await manager.process(degraded())
    await manager.wait(inc.context_id)
    run = inc.steps["rollback"]
    assert run.status == "rejected_by_human" and run.note == "시간 초과로 자동 거절"


@pytest.mark.asyncio
async def test_pending_hold_shows_review_and_deadline() -> None:
    engine, manager = await start(hold_timeout=30)
    inc = await open_held(engine, manager, safety_says("pending"))
    hold = inc.view()["pending_holds"][0]
    assert hold["step_id"] == "rollback" and hold["review_id"] == REVIEW
    assert hold["since"] and hold["expires_at"] and hold["expires_at"] > hold["since"]
    engine.decide_hold(inc, "rollback", False)
    await manager.wait(inc.context_id)


@pytest.mark.asyncio
async def test_same_decision_event_is_applied_once() -> None:
    engine, manager = await start()
    check = safety_says("approved")
    inc = await open_held(engine, manager, check)
    event = decided()
    assert (await manager.process(event))[0] == "resumed"
    assert (await manager.process(event))[0] == "duplicate"
    await manager.wait(inc.context_id)
    assert check.calls == [REVIEW]


def _post(client, payload: EventEnvelope, token: str):
    return client.post(
        "/api/events",
        json=payload.model_dump(mode="json"),
        headers={"Authorization": f"Bearer {token}"},
    )


def test_only_safety_may_send_decision_events(monkeypatch) -> None:
    monkeypatch.setenv(
        "AX_AGENT_TOKENS", "admin=admin-test-token,shop=shop-test-token,safety=safety-test-token"
    )
    monkeypatch.setattr(state, "mock", True)
    monkeypatch.setattr(state, "manager", None)
    with TestClient(app) as client:
        denied = _post(client, decided(), "shop-test-token")
        assert denied.status_code == 403
        allowed = _post(client, decided(review_id="rev_none"), "safety-test-token")
        assert allowed.status_code == 200
        assert allowed.json()["data"]["outcome"] == "unverified"  # 목 모드에는 안전 관문이 없다
