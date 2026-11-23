"""인프라(DejaFix) 복구 신호 `infra.recovered`를 총괄이 받는다 (여러 회사 2단계, 2026-11-23).

- 열린 이벤트에 복귀를 붙인다(쇼핑몰 `shop.recovered`와 같은 길)
- 복구 시각은 DejaFix가 알려 준 `recovered_at`이다(감시가 알아챈 시각이 아님)
- 같은 부위에 장애가 둘이면 `source_ref`가 같은 장애에만 붙인다
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from ax_contracts.schemas import EventEnvelope, EventType
from ax_orchestrator.manager import SUBSCRIBED, IncidentManager
from ax_orchestrator.models import now
from ax_orchestrator.plans.scenario_a import scenario_a


def anomaly(event_id: str, ref: str | None = "zbx-1", at: datetime | None = None) -> EventEnvelope:
    data = {
        "component": "payment",
        "metric_code": "zabbix_problem",
        "detected_at": (at or now()).isoformat(),
        "source_ref": ref,
    }
    return EventEnvelope(
        event_id=event_id,
        type=EventType.INFRA_ANOMALY_DETECTED,
        occurred_at=at or now(),
        source="infra",
        idempotency_key=event_id,
        data=data,
    )


def recovered(
    event_id: str,
    *,
    ref: str | None = "zbx-1",
    at: datetime | None = None,
    resolved: datetime | None = None,
    recovery: str | None = None,
    component: str = "payment",
) -> EventEnvelope:
    sent = at or now()
    data: dict = {
        "component": component,
        "metric_code": "zabbix_problem",
        "detected_at": sent.isoformat(),
        "source_ref": ref,
    }
    if resolved is not None:
        data["recovered_at"] = resolved.isoformat()
    if recovery is not None:
        data["recovery"] = recovery
    return EventEnvelope(
        event_id=event_id,
        type=EventType.INFRA_RECOVERED,
        occurred_at=sent,
        source="infra",
        idempotency_key=event_id,
        data=data,
    )


def test_the_orchestrator_subscribes_to_infra_recovered() -> None:
    assert EventType.INFRA_RECOVERED in SUBSCRIBED


@pytest.mark.asyncio
async def test_it_attaches_the_recovery_with_the_time_dejafix_reported(harness) -> None:
    engine = await harness.start()
    manager = IncidentManager(engine, scenario_a)
    opened = now()
    inc = await manager.handle(anomaly("evt_a1", at=opened))
    await manager.wait(inc.context_id)
    resolved = opened + timedelta(seconds=40)
    noticed = opened + timedelta(seconds=95)  # 감시가 알아챈 시각은 더 늦다
    outcome, same = await manager.process(
        recovered("evt_r1", at=noticed, resolved=resolved, recovery="metrics_normal")
    )
    assert outcome == "noted" and same is inc
    assert inc.recovered_at == resolved  # 알아챈 시각이 아니라 DejaFix가 알려 준 해소 시각
    assert inc.shared["recovery"]["recovery"] == "metrics_normal"


@pytest.mark.asyncio
async def test_without_a_resolved_time_the_signal_time_is_used(harness) -> None:
    engine = await harness.start()
    manager = IncidentManager(engine, scenario_a)
    opened = now()
    inc = await manager.handle(anomaly("evt_a1", at=opened))
    await manager.wait(inc.context_id)
    sent = opened + timedelta(seconds=60)
    await manager.process(recovered("evt_r1", at=sent))
    assert inc.recovered_at == sent


@pytest.mark.asyncio
async def test_a_resolved_time_before_the_incident_opened_is_clamped_to_the_open_time(
    harness,
) -> None:
    """DejaFix가 감시보다 먼저 풀렸다고 알려도 열린 시각보다 앞선 복귀는 만들지 않는다."""
    engine = await harness.start()
    manager = IncidentManager(engine, scenario_a)
    opened = now()
    inc = await manager.handle(anomaly("evt_a1", at=opened))
    await manager.wait(inc.context_id)
    await manager.process(
        recovered(
            "evt_r1",
            at=opened + timedelta(seconds=30),
            resolved=opened - timedelta(seconds=10),
        )
    )
    assert inc.recovered_at == opened


@pytest.mark.asyncio
async def test_a_resolved_time_later_than_the_signal_is_not_trusted(harness) -> None:
    engine = await harness.start()
    manager = IncidentManager(engine, scenario_a)
    opened = now()
    inc = await manager.handle(anomaly("evt_a1", at=opened))
    await manager.wait(inc.context_id)
    sent = opened + timedelta(seconds=30)
    await manager.process(recovered("evt_r1", at=sent, resolved=sent + timedelta(hours=1)))
    assert inc.recovered_at == sent


@pytest.mark.asyncio
async def test_two_incidents_on_one_component_recover_by_source_ref(harness) -> None:
    engine = await harness.start()
    manager = IncidentManager(engine, scenario_a)
    base = now()
    first = await manager.handle(anomaly("evt_a1", ref="zbx-1", at=base))
    await manager.wait(first.context_id)
    second = await manager.handle(anomaly("evt_a2", ref="zbx-2", at=base + timedelta(seconds=5)))
    await manager.wait(second.context_id)
    assert first is not second  # 첫 이벤트가 닫혀서 둘째는 새 이벤트로 열린다
    # 첫째 장애(zbx-1)의 복구는 더 최근에 열린 둘째 이벤트(zbx-2)를 풀지 않는다
    await manager.process(recovered("evt_r1", ref="zbx-1", at=base + timedelta(seconds=40)))
    assert first.recovered_at is not None
    assert second.recovered_at is None
    await manager.process(recovered("evt_r2", ref="zbx-2", at=base + timedelta(seconds=50)))
    assert second.recovered_at is not None


@pytest.mark.asyncio
async def test_a_recovery_for_an_unknown_source_ref_does_not_resolve_a_known_one(harness) -> None:
    engine = await harness.start()
    manager = IncidentManager(engine, scenario_a)
    inc = await manager.handle(anomaly("evt_a1", ref="zbx-1"))
    await manager.wait(inc.context_id)
    outcome, target = await manager.process(
        recovered("evt_r1", ref="zbx-999", at=now() + timedelta(seconds=30))
    )
    assert outcome == "noted" and target is None
    assert inc.recovered_at is None


@pytest.mark.asyncio
async def test_an_incident_without_a_source_ref_is_matched_by_component(harness) -> None:
    engine = await harness.start()
    manager = IncidentManager(engine, scenario_a)
    inc = await manager.handle(anomaly("evt_a1", ref=None))
    await manager.wait(inc.context_id)
    await manager.process(recovered("evt_r1", ref="zbx-1", at=now() + timedelta(seconds=30)))
    assert inc.recovered_at is not None


@pytest.mark.asyncio
async def test_another_component_is_not_recovered(harness) -> None:
    engine = await harness.start()
    manager = IncidentManager(engine, scenario_a)
    inc = await manager.handle(anomaly("evt_a1"))
    await manager.wait(inc.context_id)
    await manager.process(recovered("evt_r1", component="search", at=now() + timedelta(seconds=30)))
    assert inc.recovered_at is None


@pytest.mark.asyncio
async def test_the_same_recovery_signal_twice_is_handled_once(harness) -> None:
    engine = await harness.start()
    manager = IncidentManager(engine, scenario_a)
    inc = await manager.handle(anomaly("evt_a1"))
    await manager.wait(inc.context_id)
    sent = now() + timedelta(seconds=30)
    first, _ = await manager.process(recovered("evt_r1", at=sent))
    again, _ = await manager.process(recovered("evt_r1", at=sent))
    assert first == "noted" and again == "duplicate"
    assert inc.recovered_at == sent
