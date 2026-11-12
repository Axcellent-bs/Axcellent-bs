"""이벤트가 해결로 끝나면 공통 로그에 incident.resolved를 사건마다 한 번 남긴다.

payload의 detected_at·recovered_at은 이벤트 값(opened_at·복구 시간의 끝 시각)과 같고, 둘의 차이가
`metrics.mttr_seconds`다. 데이터 AI kpi_summary는 이 값으로 복구 시간을 센다
(데이터 쪽 테스트: domains/data/tests/test_kpi_resolved_payload.py).
"""

import asyncio
import contextlib
from collections.abc import Iterator
from datetime import UTC, datetime

import pytest

from ax_agent_template import common_log
from ax_agent_template.common_log import SqliteLogStore
from ax_contracts.data_access.common_log import CommonLogQuery, LogRecord
from ax_contracts.schemas import EventEnvelope, EventType
from ax_orchestrator.manager import IncidentManager
from ax_orchestrator.mock.harness import Harness, new_incident
from ax_orchestrator.plans.scenario_a import scenario_a
from ax_orchestrator.store import IncidentStore, incident_from_dict, incident_to_dict


@pytest.fixture
def logs(tmp_path) -> Iterator[SqliteLogStore]:
    store = SqliteLogStore(str(tmp_path / "common_log.db"))
    common_log.set_store(store)
    yield store
    common_log.reset_store()
    store.close()


def resolved(store: SqliteLogStore, context_id: str) -> list[LogRecord]:
    found = store.query(CommonLogQuery(context_ids=[context_id], limit=100)).records
    return [
        r
        for r in found
        if r.kind == "event_published" and r.payload.get("event_type") == "incident.resolved"
    ]


def event(event_id: str, type_: EventType, **data) -> EventEnvelope:
    return EventEnvelope(
        event_id=event_id,
        type=type_,
        occurred_at=datetime.now(UTC),
        source="shop-api",
        idempotency_key=event_id,
        data={"component": "payment", "metric_code": "error_rate_5xx", **data},
    )


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


async def closed_degraded(manager: IncidentManager):
    """되돌리기 승인이 오지 않아 degraded로 닫힌 이벤트(복구 신호 없음)."""
    _, inc = await manager.process(event("evt_down", EventType.SHOP_DEGRADED))
    assert inc is not None
    await manager._tasks[inc.context_id]
    assert inc.status == "degraded" and inc.recovered_at is None
    return inc


@pytest.mark.asyncio
async def test_degraded_without_recovery_signal_leaves_nothing(logs, tmp_path) -> None:
    manager = await start_manager(str(tmp_path / "orc.db"), hold_rollback=True)
    inc = await closed_degraded(manager)
    assert resolved(logs, inc.context_id) == []
    assert inc.resolved_logged is False


@pytest.mark.asyncio
async def test_shop_recovery_signal_leaves_one_record_with_incident_times(logs, tmp_path) -> None:
    manager = await start_manager(str(tmp_path / "orc.db"), hold_rollback=True)
    inc = await closed_degraded(manager)

    outcome, target = await manager.process(event("evt_up", EventType.SHOP_RECOVERED))
    assert outcome == "noted" and target is inc
    assert inc.status == "resolved" and inc.recovered_at is not None

    [record] = resolved(logs, inc.context_id)
    assert record.source == "orchestrator"
    detected = datetime.fromisoformat(record.payload["detected_at"])
    recovered = datetime.fromisoformat(record.payload["recovered_at"])
    assert detected == inc.opened_at
    assert recovered == inc.recovered_at
    assert round((recovered - detected).total_seconds(), 2) == inc.metrics["mttr_seconds"]

    # 복구 신호가 또 와도 다시 남기지 않는다
    await manager.process(event("evt_up_again", EventType.SHOP_RECOVERED))
    assert len(resolved(logs, inc.context_id)) == 1


@pytest.mark.asyncio
async def test_recovery_before_close_leaves_only_one_record(logs, tmp_path) -> None:
    path = str(tmp_path / "orc.db")
    manager = await start_manager(path, hold_rollback=True, hold_timeout=1.0)
    _, inc = await manager.process(event("evt_down", EventType.SHOP_DEGRADED))
    assert inc is not None
    await wait_held(inc, "rollback")

    await manager.process(event("evt_up", EventType.SHOP_RECOVERED))
    assert inc.recovered_at is not None and inc.status == "open"
    assert resolved(logs, inc.context_id) == []  # 열려 있는 동안은 남기지 않는다

    await manager._tasks[inc.context_id]  # 승인 대기가 끝나고 이벤트가 닫힌다
    assert inc.status == "resolved"
    [record] = resolved(logs, inc.context_id)
    detected = datetime.fromisoformat(record.payload["detected_at"])
    recovered = datetime.fromisoformat(record.payload["recovered_at"])
    assert detected == inc.opened_at
    assert recovered == inc.recovery_end()
    assert round((recovered - detected).total_seconds(), 2) == inc.metrics["mttr_seconds"]

    # 다시 켜도(저장·복원) 남긴 표시가 유지되어 또 남기지 않는다
    again = await start_manager(path)
    again.restore()
    restored = again.incidents[inc.context_id]
    assert restored.resolved_logged is True
    await again.process(event("evt_up_late", EventType.SHOP_RECOVERED))
    assert len(resolved(logs, inc.context_id)) == 1


@pytest.mark.asyncio
async def test_open_incident_recovered_then_restarted_leaves_one_record(logs, tmp_path) -> None:
    """복구 신호를 받은 채 재시작으로 중단되면 해결로 바뀌고, 그때 한 번 남긴다."""
    path = str(tmp_path / "orc.db")
    first = await start_manager(path, hold_rollback=True, hold_timeout=30)
    _, inc = await first.process(event("evt_down", EventType.SHOP_DEGRADED))
    assert inc is not None
    await wait_held(inc, "rollback")
    await first.process(event("evt_up", EventType.SHOP_RECOVERED))
    assert resolved(logs, inc.context_id) == []
    try:
        second = await start_manager(path)
        second.restore()
        restored = second.incidents[inc.context_id]
        assert restored.status == "resolved"
        [record] = resolved(logs, inc.context_id)
        assert datetime.fromisoformat(record.payload["recovered_at"]) == restored.recovered_at
        assert IncidentStore(path).load_all()[0].resolved_logged is True
    finally:
        task = first._tasks[inc.context_id]
        task.cancel()
        with contextlib.suppress(BaseException):
            await task


def test_old_saved_incident_has_not_logged_yet() -> None:
    raw = incident_to_dict(new_incident())
    assert raw["resolved_logged"] is False
    raw.pop("resolved_logged")  # 이 칸이 생기기 전 저장분
    assert incident_from_dict(raw).resolved_logged is False
    raw["resolved_logged"] = True
    assert incident_from_dict(raw).resolved_logged is True
