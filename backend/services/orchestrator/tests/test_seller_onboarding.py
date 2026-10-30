"""판매자 입점 심사 plan.

새 판매자가 들어오면 재무가 심사하고, 거절되면 정산을 멈추고, 인사가 기록한다.
"""

from datetime import UTC, datetime

import pytest

from ax_contracts.schemas import EventEnvelope, EventType
from ax_orchestrator.manager import SUBSCRIBED, IncidentManager
from ax_orchestrator.plans.scenario_a import scenario_a


def registered(event_id: str, seller_id: str, documents_verified: bool) -> EventEnvelope:
    """실제 shop-api `seller.registered` 이벤트 모양 (component가 없다)."""
    return EventEnvelope(
        event_id=event_id,
        type=EventType.SELLER_REGISTERED,
        occurred_at=datetime.now(UTC),
        source="shop-api",
        idempotency_key=f"seller.registered:{seller_id}",
        data={
            "seller_id": seller_id,
            "category_codes": ["book"],
            "documents_verified": documents_verified,
        },
    )


def test_the_orchestrator_subscribes_to_new_sellers() -> None:
    assert EventType.SELLER_REGISTERED in SUBSCRIBED


@pytest.mark.asyncio
async def test_an_approved_seller_is_reviewed_and_recorded(harness) -> None:
    mgr = IncidentManager(await harness.start(), scenario_a)
    inc = await mgr.handle(registered("evt_s1", "sel_0101", True))
    assert inc is not None
    await mgr.wait(inc.context_id)

    assert (inc.kind, inc.title) == ("seller_onboarding", "새 판매자 sel_0101 입점 심사")
    assert inc.component == "seller-sel_0101"
    statuses = {sid: r.status for sid, r in inc.steps.items()}
    assert statuses == {"review": "completed", "hold": "skipped", "record": "completed"}
    review = next(c for c in harness.fakes.calls if c[1].skill == "seller_review")
    assert review[0] == "finance"
    assert review[1].input == {
        "seller_id": "sel_0101",
        "category_codes": ["book"],
        "documents_verified": True,
    }
    assert inc.shared["review_decision"] == "approved"
    assert not any(c[1].skill == "settlement_hold" for c in harness.fakes.calls)


@pytest.mark.asyncio
async def test_a_rejected_seller_gets_settlement_hold(harness) -> None:
    mgr = IncidentManager(await harness.start(), scenario_a)
    inc = await mgr.handle(registered("evt_s2", "sel_0102", False))
    await mgr.wait(inc.context_id)

    assert inc.steps["hold"].status == "completed"
    hold = next(c for c in harness.fakes.calls if c[1].skill == "settlement_hold")
    assert hold[1].input == {"seller_id": "sel_0102", "reason_code": "documents_missing"}
    record = next(c for c in harness.fakes.calls if c[1].skill == "record_case")
    assert (record[1].input["kind"], record[1].input["area"]) == ("case", "catalog")


@pytest.mark.asyncio
async def test_each_seller_gets_its_own_case(harness) -> None:
    mgr = IncidentManager(await harness.start(), scenario_a)
    a = await mgr.handle(registered("evt_s3", "sel_0103", True))
    b = await mgr.handle(registered("evt_s4", "sel_0104", True))
    assert a is not None and b is not None and a.context_id != b.context_id
    await mgr.wait(a.context_id)
    await mgr.wait(b.context_id)
