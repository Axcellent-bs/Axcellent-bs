"""이벤트를 연 신호의 출처(detected_by).

새 이벤트에 남고, 합쳐진 신호는 덮어쓰지 않고, 예전 저장분은 None.
"""

import asyncio
import contextlib
from datetime import UTC, datetime, timedelta

import pytest

from ax_contracts.schemas import EventEnvelope, EventType
from ax_orchestrator.manager import IncidentManager
from ax_orchestrator.mock.harness import Harness, new_incident
from ax_orchestrator.models import iso
from ax_orchestrator.plans.scenario_a import scenario_a
from ax_orchestrator.store import IncidentStore, incident_from_dict, incident_to_dict

OPENED = datetime(2026, 11, 12, 1, 0, tzinfo=UTC)
SHOP = {"source": "shop-api", "event": "shop.degraded", "at": iso(OPENED)}


async def start_manager(path: str, *, hold_rollback: bool = False, hold_timeout: float = 0.2):
    harness = Harness(hold_timeout=hold_timeout)
    if hold_rollback:
        harness.fakes.script("infra", "rollback_deployment", "hold")
    store = IncidentStore(path)
    engine = await harness.start()
    engine.persist = store.save
    return IncidentManager(engine, scenario_a, store=store)


async def wait_held(inc, step_id: str, seconds: float = 5.0) -> None:
    for _ in range(int(seconds / 0.02)):
        run = inc.steps.get(step_id)
        if run is not None and run.status == "held":
            return
        await asyncio.sleep(0.02)
    raise AssertionError(f"{step_id}가 승인 대기가 되지 않았다")


def _degraded(event_id: str, at: datetime) -> EventEnvelope:
    return EventEnvelope(
        event_id=event_id,
        type=EventType.SHOP_DEGRADED,
        occurred_at=at,
        source="shop-api",
        idempotency_key=event_id,
        data={"component": "payment", "metric_code": "error_rate_5xx", "value": 0.4},
    )


def _infra(event_id: str, at: datetime, detected: datetime) -> EventEnvelope:
    return EventEnvelope(
        event_id=event_id,
        type=EventType.INFRA_ANOMALY_DETECTED,
        occurred_at=at,
        source="infra",
        idempotency_key=event_id,
        data={"component": "payment", "metric_code": "http_5xx", "detected_at": iso(detected)},
    )


@pytest.mark.asyncio
async def test_new_incident_keeps_the_signal_that_opened_it(tmp_path) -> None:
    manager = await start_manager(str(tmp_path / "orc.db"))
    inc = await manager.handle(_degraded("evt_open", OPENED))
    assert inc is not None
    assert inc.detected_by == {"source": "shop-api", "event": "shop.degraded", "at": iso(OPENED)}
    assert inc.view()["detected_by"] == inc.detected_by


@pytest.mark.asyncio
async def test_infra_signal_uses_its_own_detected_time(tmp_path) -> None:
    """인프라 이상(DejaFix·Zabbix)은 신호에 적힌 감지 시각을 쓴다."""
    manager = await start_manager(str(tmp_path / "orc.db"))
    zabbix_saw = OPENED - timedelta(minutes=2)
    inc = await manager.handle(_infra("evt_infra", OPENED, zabbix_saw))
    assert inc is not None
    assert inc.detected_by == {
        "source": "infra",
        "event": "infra.anomaly_detected",
        "at": iso(zabbix_saw),
    }


@pytest.mark.asyncio
async def test_merged_signal_does_not_overwrite(tmp_path) -> None:
    manager = await start_manager(str(tmp_path / "orc.db"), hold_rollback=True, hold_timeout=5.0)
    inc = await manager.handle(_degraded("evt_first", OPENED))
    assert inc is not None
    try:
        await wait_held(inc, "rollback")
        later = _infra("evt_second", OPENED + timedelta(minutes=1), OPENED)
        outcome, merged = await manager.process(later)
        assert outcome == "merged"
        assert merged is inc
        assert inc.detected_by == SHOP
    finally:
        for task in manager._tasks.values():
            task.cancel()
            with contextlib.suppress(BaseException):
                await task


def test_detected_by_is_saved_and_restored(tmp_path) -> None:
    store = IncidentStore(str(tmp_path / "orc.db"))
    inc = new_incident()
    inc.detected_by = {"source": "shop-api", "event": "shop.degraded", "at": iso(OPENED)}
    store.save(inc)
    assert IncidentStore(str(tmp_path / "orc.db")).load_all()[0].detected_by == inc.detected_by


def test_old_saved_incident_reads_as_none() -> None:
    raw = incident_to_dict(new_incident())
    raw.pop("detected_by")  # 이 칸이 생기기 전 저장분
    assert incident_from_dict(raw).detected_by is None
    raw["detected_by"] = "shop-api"  # 모양이 다른 값도 None
    assert incident_from_dict(raw).detected_by is None
