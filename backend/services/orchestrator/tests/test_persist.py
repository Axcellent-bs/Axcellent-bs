"""이벤트 저장·복구: 총괄이 다시 켜져도 이벤트 기록과 처리한 event_id가 남는다."""

import asyncio
import contextlib
from datetime import UTC, datetime

import pytest

from ax_contracts.schemas import EventEnvelope, EventType
from ax_orchestrator.manager import INTERRUPTED_NOTE, IncidentManager
from ax_orchestrator.mock.harness import Harness, new_incident
from ax_orchestrator.plans.scenario_a import scenario_a
from ax_orchestrator.store import IncidentStore


def event(event_id: str, type_: EventType, **data) -> EventEnvelope:
    return EventEnvelope(
        event_id=event_id,
        type=type_,
        occurred_at=datetime.now(UTC),
        source="shop-api",
        idempotency_key=event_id,
        data={"component": "payment", **data},
    )


async def wait_held(inc, step_id: str, seconds: float = 5.0) -> None:
    for _ in range(int(seconds / 0.02)):
        run = inc.steps.get(step_id)
        if run is not None and run.status == "held":
            return
        await asyncio.sleep(0.02)
    raise AssertionError(f"{step_id}가 승인 대기가 되지 않았다")


async def start_manager(path: str, *, hold_rollback: bool = False, hold_timeout: float = 0.2):
    """저장소 파일 하나에 붙은 총괄(엔진+매니저). 다시 켜는 것은 같은 파일로 새로 만드는 것이다."""
    harness = Harness(hold_timeout=hold_timeout)
    if hold_rollback:
        harness.fakes.script("infra", "rollback_deployment", "hold")
    store = IncidentStore(path)
    engine = await harness.start()
    engine.persist = store.save
    return IncidentManager(engine, scenario_a, store=store)


@pytest.mark.asyncio
async def test_saved_incident_reads_back_the_same(harness, tmp_path) -> None:
    path = str(tmp_path / "orc.db")
    store = IncidentStore(path)
    engine = await harness.start()
    engine.persist = store.save
    inc = new_incident()
    await engine.run(inc, scenario_a())

    loaded = IncidentStore(path).load_all()
    assert len(loaded) == 1
    assert loaded[0].status == inc.status == "resolved"
    assert loaded[0].view() == inc.view()


@pytest.mark.asyncio
async def test_unfinished_incident_is_marked_interrupted_after_restart(tmp_path) -> None:
    path = str(tmp_path / "orc.db")
    first = await start_manager(path, hold_rollback=True, hold_timeout=30)
    _, inc = await first.process(event("evt_a", EventType.SHOP_DEGRADED))
    await wait_held(inc, "rollback")

    second = await start_manager(path)  # 다시 켠다
    assert second.restore() == 1
    got = second.incidents[inc.context_id]
    assert got.status == "degraded" and got.closed_at is not None
    rollback = got.steps["rollback"]
    assert rollback.status == "failed" and INTERRUPTED_NOTE in rollback.note
    assert got.steps["diagnose"].status == "completed"  # 이미 끝난 단계는 그대로
    assert all(
        r.status not in {"pending", "running", "held", "waiting_human"} for r in got.steps.values()
    )
    assert any("다시 켜져" in e["message"] for e in got.events)
    assert got.metrics["steps_total"] == len(got.steps)
    assert got.view()["pending_holds"] == []
    assert second.listing()[0]["status"] == "degraded"

    # 저장도 갱신되어 한 번 더 켜도 같은 모습이다
    third = await start_manager(path)
    assert third.restore() == 0
    assert third.incidents[inc.context_id].view() == got.view()

    task = first._tasks[inc.context_id]
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_processed_event_ids_survive_restart(tmp_path) -> None:
    path = str(tmp_path / "orc.db")
    first = await start_manager(path)
    outcome, inc = await first.process(event("evt_dup", EventType.SHOP_DEGRADED))
    assert outcome == "opened"
    await first.wait(inc.context_id)

    second = await start_manager(path)
    second.restore()
    again, none = await second.process(event("evt_dup", EventType.SHOP_DEGRADED))
    assert again == "duplicate" and none is None
    assert list(second.incidents) == [inc.context_id]


@pytest.mark.asyncio
async def test_event_is_saved_before_its_id_is_marked(tmp_path) -> None:
    """event_id만 적히고 이벤트가 없는 일이 없어야 한다(꺼지면 그 이벤트를 잃는다)."""
    path = str(tmp_path / "orc.db")
    first = await start_manager(path, hold_rollback=True, hold_timeout=30)
    _, inc = await first.process(event("evt_order", EventType.SHOP_DEGRADED))
    store = IncidentStore(path)
    assert "evt_order" in store.seen_ids()
    assert [i.context_id for i in store.load_all()] == [inc.context_id]
    first._tasks[inc.context_id].cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await first._tasks[inc.context_id]


@pytest.mark.asyncio
async def test_save_failure_does_not_stop_the_incident(harness) -> None:
    engine = await harness.start()

    def boom(inc) -> None:
        raise OSError("disk full")

    engine.persist = boom
    inc = new_incident()
    await engine.run(inc, scenario_a())
    assert inc.status == "resolved"


@pytest.mark.asyncio
async def test_recovery_signal_reaches_a_restored_incident(tmp_path) -> None:
    path = str(tmp_path / "orc.db")
    first = await start_manager(path, hold_rollback=True, hold_timeout=30)
    _, inc = await first.process(
        event("evt_down", EventType.SHOP_DEGRADED, metric_code="error_rate_5xx")
    )
    await wait_held(inc, "rollback")

    second = await start_manager(path)
    second.restore()
    outcome, target = await second.process(event("evt_up", EventType.SHOP_RECOVERED))
    assert outcome == "noted" and target is not None
    assert target.context_id == inc.context_id
    assert target.recovered_at is not None and target.status == "resolved"
    assert IncidentStore(path).load_all()[0].recovered_at is not None  # 복구 시각도 저장된다

    task = first._tasks[inc.context_id]
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task
