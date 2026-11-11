"""원인을 못 찾아 사람에게 넘긴 장애를 사람이 해제했을 때 (#909, 시연 ①).

예전에는 롤백이 없으면 복구 검증을 바로 건너뛰어, 사람이 장애를 풀어도 정상화 공지가 나가지
않았다. 이제 복구 검증이 사람의 해제 신호(shop.recovered, recovery=fault_released)를 기다렸다가
롤백 때처럼 복구 검증 → 정상화 공지 → 재무 → 인사 기록으로 이어진다.
"""

import asyncio
from datetime import UTC, datetime

import pytest

from ax_contracts.schemas import EventEnvelope, EventType
from ax_orchestrator.manager import IncidentManager
from ax_orchestrator.models import Incident, iso
from ax_orchestrator.plans.scenario_a import scenario_a

pytestmark = pytest.mark.asyncio


def _event(event_id: str, type_: EventType, **data) -> EventEnvelope:
    return EventEnvelope(
        event_id=event_id,
        type=type_,
        occurred_at=datetime.now(UTC),
        source="shop-api",
        idempotency_key=event_id,
        data={"component": "payment", **data},
    )


def _called(harness, skill: str) -> list:
    return [req for _, req in harness.fakes.calls if req.skill == skill]


async def _waiting_for_release(inc: Incident) -> None:
    for _ in range(200):
        if any("해제하길 기다림" in e["message"] for e in inc.events):
            return
        await asyncio.sleep(0.01)
    raise AssertionError("복구 검증이 사람의 해제를 기다리지 않았다")


async def _open_undetermined(harness, wait: float) -> tuple[IncidentManager, Incident]:
    harness.fakes.script("infra", "diagnose_incident", "undetermined")
    engine = await harness.start()
    engine.release_wait = wait
    mgr = IncidentManager(engine, scenario_a)
    inc = await mgr.handle(_event("evt_deg", EventType.SHOP_DEGRADED, metric_code="error_rate_5xx"))
    assert inc is not None
    return mgr, inc


async def _release(mgr: IncidentManager) -> None:
    await mgr.handle(
        _event(
            "evt_rec",
            EventType.SHOP_RECOVERED,
            metric_code="error_rate_5xx",
            recovery="fault_released",
            released_by="admin",
        )
    )


async def test_person_releases_then_recovery_notice_finance_and_hr_follow(harness) -> None:
    mgr, inc = await _open_undetermined(harness, wait=5.0)
    await _waiting_for_release(inc)
    assert _called(harness, "verify_recovery") == []  # 해제 전에는 복구 검증을 부르지 않는다
    await _release(mgr)
    await mgr.wait(inc.context_id)

    assert inc.steps["rollback"].status == "transfer_queued"  # 사람에게 넘긴 것은 그대로
    assert inc.steps["recovery"].status == "completed"
    verify = _called(harness, "verify_recovery")
    assert len(verify) == 1 and verify[0].input["since"] == iso(inc.recovered_at)
    followed = ("recovered_draft", "recovered_notice", "finance_record", "record", "resolution")
    for step_id in followed:
        assert inc.steps[step_id].status == "completed", step_id
    assert len(_called(harness, "publish_notice")) == 2  # 점검 공지 + 정상화 공지

    order = [r.skill for _, r in harness.fakes.calls]
    assert order.index("verify_recovery") < order.index("record_incident_finance")
    assert order.index("record_incident_finance") < order.index("record_case")
    resolution = _called(harness, "record_resolution")[0].input
    assert resolution["outcome"] == "resolved"
    assert resolution["action_code"] == "release_fault"  # 다음 같은 장애에서 과거 처리 재사용 근거
    assert inc.status == "resolved"


async def test_fault_still_going_after_release_goes_to_a_person_without_resolved_notice(
    harness,
) -> None:
    harness.fakes.script("infra", "verify_recovery", "not_recovered")
    mgr, inc = await _open_undetermined(harness, wait=5.0)
    await _waiting_for_release(inc)
    await _release(mgr)
    await mgr.wait(inc.context_id)

    recovery = inc.steps["recovery"]
    assert recovery.status == "transfer_queued" and recovery.reason_code == "not_recovered"
    assert inc.steps["recovered_notice"].status == "skipped"
    assert len(_called(harness, "publish_notice")) == 1  # 점검 공지만
    assert inc.steps["record"].status == "completed"


async def test_no_release_within_the_wait_skips_recovery_as_before(harness) -> None:
    mgr, inc = await _open_undetermined(harness, wait=0.2)
    await mgr.wait(inc.context_id)
    recovery = inc.steps["recovery"]
    assert recovery.status == "skipped" and recovery.reason_code == "no_fix_applied"
    assert _called(harness, "verify_recovery") == []
    assert inc.status == "degraded"


async def test_metrics_back_to_normal_is_not_a_release(harness) -> None:
    """해제가 아니라 지표가 저절로 돌아온 신호는 복구 검증을 시작하지 않는다(기다림 계속)."""
    mgr, inc = await _open_undetermined(harness, wait=0.5)
    await _waiting_for_release(inc)
    await mgr.handle(
        _event("evt_norm", EventType.SHOP_RECOVERED, metric_code="x", recovery="metrics_normal")
    )
    await mgr.wait(inc.context_id)
    assert inc.steps["recovery"].reason_code == "no_fix_applied"
    assert _called(harness, "verify_recovery") == []


async def test_no_wait_when_nothing_was_handed_to_a_person(harness) -> None:
    """진단이 실패해 롤백까지 가지 못했으면(사람에게 넘긴 것 없음) 기다리지 않는다."""
    harness.fakes.script("infra", "diagnose_incident", "fail", "fail", "fail")
    engine = await harness.start()
    engine.release_wait = 30.0
    mgr = IncidentManager(engine, scenario_a)
    inc = await mgr.handle(_event("evt_deg", EventType.SHOP_DEGRADED))
    await asyncio.wait_for(mgr.wait(inc.context_id), timeout=10)
    assert inc.steps["rollback"].status == "skipped"
    assert inc.steps["recovery"].reason_code == "no_fix_applied"
    assert not any("해제하길 기다림" in e["message"] for e in inc.events)


async def test_no_wait_by_default_keeps_the_old_behaviour(harness) -> None:
    """엔진 기본값(release_wait=0)은 예전처럼 바로 건너뛴다(기존 시나리오 A 시험과 같다)."""
    harness.fakes.script("infra", "diagnose_incident", "undetermined")
    engine = await harness.start()
    assert engine.release_wait == 0
    mgr = IncidentManager(engine, scenario_a)
    inc = await mgr.handle(_event("evt_deg", EventType.SHOP_DEGRADED))
    await mgr.wait(inc.context_id)
    assert inc.steps["recovery"].reason_code == "no_fix_applied"
    assert not any("해제하길 기다림" in e["message"] for e in inc.events)
