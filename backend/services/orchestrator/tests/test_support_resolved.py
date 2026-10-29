"""고객지원 처리 결과 plan: 문의가 닫히면 재무가 비용 기록을 남기고, 인사가 처리 결과를 기록한다."""

from datetime import UTC, datetime

import pytest

from ax_contracts.schemas import EventEnvelope, EventType
from ax_orchestrator.manager import SUBSCRIBED, IncidentManager
from ax_orchestrator.plans.scenario_a import scenario_a


def resolved(event_id: str, inquiry_id: int, **data) -> EventEnvelope:
    """실제 `support.inquiry_resolved` 이벤트 모양 (component가 없다)."""
    base = {"inquiry_id": inquiry_id, "status": "resolved", "resolution": "answered"}
    return EventEnvelope(
        event_id=event_id,
        type=EventType.SUPPORT_INQUIRY_RESOLVED,
        occurred_at=datetime.now(UTC),
        source="support",
        idempotency_key=event_id,
        data=base | data,
    )


def test_the_orchestrator_subscribes_to_resolved_inquiries() -> None:
    assert EventType.SUPPORT_INQUIRY_RESOLVED in SUBSCRIBED


@pytest.mark.asyncio
async def test_a_resolved_inquiry_goes_to_finance_then_hr(harness) -> None:
    mgr = IncidentManager(await harness.start(), scenario_a)
    inc = await mgr.handle(
        resolved("evt_r1", 7, order_id="ord_0216", resolution="claim_decided", refund_amount=24000)
    )
    assert inc is not None
    await mgr.wait(inc.context_id)

    assert inc.kind == "support_resolved"
    assert inc.title == "고객 문의 #7 처리 결과 기록"
    assert [r.dept for r in inc.steps.values()] == ["finance", "hr", "hr"]
    assert [r.status for r in inc.steps.values()] == ["completed"] * 3
    assert inc.status == "resolved"
    # 재무가 받은 입력은 이벤트가 알려 준 사실뿐이다
    finance_call = next(c for c in harness.fakes.calls if c[1].skill == "record_support_cost")
    assert finance_call[1].input == {
        "inquiry_id": 7,
        "order_id": "ord_0216",
        "resolution": "claim_decided",
        "refund_amount": 24000,
    }
    # 재무가 남긴 기록이 사건에 보관돼 총괄 화면에서 보이고 인사 기록에 붙는다
    assert inc.shared["expense_used"] is True
    assert inc.shared["refund_amount"] == 24000
    assert inc.shared["shipping_fee"] == 3000
    assert "문의 #7" in inc.shared["record_note"]


@pytest.mark.asyncio
async def test_an_inquiry_without_expense_is_still_recorded(harness) -> None:
    """경비를 쓰지 않아도(답변만 한 문의) 처리 결과는 재무 기록과 인사 기록으로 남는다."""
    mgr = IncidentManager(await harness.start(), scenario_a)
    inc = await mgr.handle(resolved("evt_r2", 8, order_id="ord_0216"))
    await mgr.wait(inc.context_id)

    assert [r.status for r in inc.steps.values()] == ["completed"] * 3
    assert inc.shared["expense_used"] is False  # False·0도 보관한다 (None만 비운다)
    assert inc.shared["refund_amount"] == 0
    record_call = next(c for c in harness.fakes.calls if c[1].skill == "record_case")
    assert record_call[1].input["kind"] == "case"
    assert record_call[1].input["area"] == "support-desk"
    assert "문의 #8" in record_call[1].input["summary"]  # 재무가 남긴 설명이 붙는다


@pytest.mark.asyncio
async def test_the_record_is_still_made_when_finance_cannot_record(harness) -> None:
    """재무 기록이 실패해도 상담 처리 결과는 인사 기록으로 남는다."""
    harness.fakes.script("finance", "record_support_cost", "fail", "fail", "fail")
    mgr = IncidentManager(await harness.start(), scenario_a)
    inc = await mgr.handle(resolved("evt_r3", 9))
    await mgr.wait(inc.context_id)

    assert inc.steps["cost"].status == "failed"
    assert inc.steps["record"].status == "completed"
    assert "record_note" not in inc.shared  # 지어내지 않는다


@pytest.mark.asyncio
async def test_inquiries_closed_together_are_not_merged_into_one_incident(harness) -> None:
    """component가 없는 해결 이벤트가 하나의 열린 사건에 합쳐지면 기록이 사라진다."""
    harness.fakes.latency = 0.05
    mgr = IncidentManager(await harness.start(), scenario_a)
    first = await mgr.handle(resolved("evt_a", 1))
    second = await mgr.handle(resolved("evt_b", 2))
    third = await mgr.handle(resolved("evt_c", 3))
    assert len({first.context_id, second.context_id, third.context_id}) == 3
    for inc in (first, second, third):
        await mgr.wait(inc.context_id)
    assert len(mgr.incidents) == 3
    assert sorted(i.title for i in mgr.incidents.values()) == [
        f"고객 문의 #{n} 처리 결과 기록" for n in (1, 2, 3)
    ]


@pytest.mark.asyncio
async def test_the_same_event_twice_records_once(harness) -> None:
    mgr = IncidentManager(await harness.start(), scenario_a)
    first = await mgr.handle(resolved("evt_dup", 4))
    assert await mgr.handle(resolved("evt_dup", 4)) is None
    await mgr.wait(first.context_id)
    assert len(mgr.incidents) == 1
    assert sum(1 for c in harness.fakes.calls if c[1].skill == "record_case") == 1


@pytest.mark.asyncio
async def test_the_hr_record_is_read_back_by_this_incident(harness) -> None:
    mgr = IncidentManager(await harness.start(), scenario_a)
    inc = await mgr.handle(resolved("evt_v1", 5))
    await mgr.wait(inc.context_id)

    verify_call = next(c for c in harness.fakes.calls if c[1].skill == "get_audit_timeline")
    assert verify_call[0] == "hr"
    assert verify_call[1].input == {"context_id": inc.context_id}  # 이 사건의 기록만 읽는다
    assert inc.steps["verify"].status == "completed"
    assert inc.status == "resolved"
    # 기록을 쓴 뒤에 읽는다
    skills = [c[1].skill for c in harness.fakes.calls]
    assert skills.index("record_case") < skills.index("get_audit_timeline")


@pytest.mark.asyncio
async def test_a_missing_hr_record_is_sent_to_a_human_instead_of_assumed(harness) -> None:
    harness.fakes.script("hr", "get_audit_timeline", "no_record")
    mgr = IncidentManager(await harness.start(), scenario_a)
    inc = await mgr.handle(resolved("evt_v2", 6))
    await mgr.wait(inc.context_id)

    verify = inc.steps["verify"]
    assert verify.status == "transfer_queued"
    assert verify.reason_code == "record_not_found"
    assert inc.status == "degraded"  # 기록을 확인하지 못한 사건은 해결로 세지 않는다
