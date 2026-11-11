"""장애 배너 내리기: 정상화 공지 없이 닫힌 이벤트도 쇼핑몰이 복구되면 장애 배너가 남지 않는다.

배너를 내리는 길은 마케팅의 정상화 공지 게시뿐이었다. 복구 확인이 사람에게 넘어가 정상화 공지가
건너뛰어지면 장애 배너가 계속 떠 있었다. 총괄은 복구 신호(shop.recovered)가 있을 때만 마케팅
`end_incident_notice`를 부른다(복구 전에 내리면 장애가 계속 중인데 공지가 사라진다).
"""

from __future__ import annotations

import asyncio

import pytest

from ax_contracts.schemas import EventEnvelope, EventType
from ax_orchestrator.manager import IncidentManager
from ax_orchestrator.mock.harness import new_incident
from ax_orchestrator.models import now
from ax_orchestrator.plans.scenario_a import scenario_a


def event(kind: EventType, event_id: str) -> EventEnvelope:
    return EventEnvelope(
        event_id=event_id,
        type=kind,
        occurred_at=now(),
        source="shop-api",
        idempotency_key=event_id,
        data={"component": "payment", "metric_code": "p95_latency_ms", "severity": "high"},
    )


def end_calls(harness) -> list[tuple[str, object]]:
    return [(d, r) for d, r in harness.fakes.calls if r.skill == "end_incident_notice"]


async def _closed_without_recovery_notice(harness):
    """복구 확인이 사람에게 넘어가 정상화 공지 없이 닫힌 이벤트(복구 신호는 아직 없음)."""
    engine = await harness.start()
    manager = IncidentManager(engine, scenario_a)
    harness.fakes.script("infra", "verify_recovery", "not_recovered")
    inc = await manager.handle(event(EventType.SHOP_DEGRADED, "evt_deg_1"))
    await manager.wait(inc.context_id)
    return manager, inc


@pytest.mark.asyncio
async def test_a_healthy_incident_does_not_end_banners_again(harness) -> None:
    """정상화 공지가 장애 배너를 이미 내렸다. 정상화 배너까지 내리면 안 되므로 부르지 않는다."""
    engine = await harness.start()
    inc = new_incident()
    await engine.run(inc, scenario_a())
    step = inc.steps["end_notice"]
    assert (step.status, step.reason_code) == ("skipped", "notice_already_ended")
    assert end_calls(harness) == []
    assert inc.status == "resolved"
    assert inc.metrics["auto_rate"] == 1.0  # 이 건너뜀은 사람이 개입한 일이 아니다


@pytest.mark.asyncio
async def test_without_a_recovery_signal_the_banner_stays(harness) -> None:
    """복구 신호가 없으면 장애가 계속 중일 수 있어 장애 배너를 내리지 않는다."""
    _, inc = await _closed_without_recovery_notice(harness)
    assert inc.steps["recovered_notice"].status == "skipped"
    step = inc.steps["end_notice"]
    assert (step.status, step.reason_code) == ("skipped", "waiting_recovery")
    assert end_calls(harness) == []
    assert inc.status == "degraded"


@pytest.mark.asyncio
async def test_a_recovery_signal_after_the_incident_closed_takes_the_banner_down(harness) -> None:
    manager, inc = await _closed_without_recovery_notice(harness)
    completed_before = inc.metrics["steps_completed"]
    outcome, _ = await manager.process(event(EventType.SHOP_RECOVERED, "evt_rec_1"))
    assert outcome == "noted"
    step = inc.steps["end_notice"]
    assert (step.status, step.attempts) == ("completed", 1)
    ((dept, request),) = end_calls(harness)
    assert dept == "marketing"
    assert request.meta.context_id == inc.context_id
    assert request.input == {"reason": "recovery_not_announced"}
    # 정상화 공지는 여전히 나가지 않았고(복구 검증은 사람이 본다) 이벤트 결과·지표만 갱신된다
    assert inc.steps["recovered_notice"].status == "skipped"
    assert inc.metrics["steps_completed"] == completed_before + 1
    line = next(s for s in inc.summary if "장애 공지 배너 내리기" in s)
    assert "완료" in line


@pytest.mark.asyncio
async def test_a_second_recovery_signal_does_not_end_banners_again(harness) -> None:
    manager, inc = await _closed_without_recovery_notice(harness)
    await manager.process(event(EventType.SHOP_RECOVERED, "evt_rec_1"))
    await manager.process(event(EventType.SHOP_RECOVERED, "evt_rec_2"))
    assert len(end_calls(harness)) == 1


@pytest.mark.asyncio
async def test_a_recovery_signal_for_a_healthy_incident_changes_nothing(harness) -> None:
    """정상화 공지로 이미 내렸다면 복구 신호가 와도 마케팅을 다시 부르지 않는다."""
    engine = await harness.start()
    manager = IncidentManager(engine, scenario_a)
    inc = await manager.handle(event(EventType.SHOP_DEGRADED, "evt_deg_1"))
    await manager.wait(inc.context_id)
    await manager.process(event(EventType.SHOP_RECOVERED, "evt_rec_1"))
    step = inc.steps["end_notice"]
    assert (step.status, step.reason_code) == ("skipped", "notice_already_ended")
    assert end_calls(harness) == []


@pytest.mark.asyncio
async def test_a_recovery_signal_before_closing_takes_the_banner_down_at_close(harness) -> None:
    engine = await harness.start()
    harness.fakes.script("infra", "verify_recovery", "not_recovered")
    inc = new_incident()
    inc.recovered_at = now()  # 이벤트가 도는 동안 복구 신호를 받았다
    await engine.run(inc, scenario_a())
    assert inc.steps["recovered_notice"].status == "skipped"
    assert inc.steps["end_notice"].status == "completed"
    assert len(end_calls(harness)) == 1


@pytest.mark.asyncio
async def test_no_banner_was_posted_means_nothing_to_end(harness) -> None:
    engine = await harness.start()
    harness.fakes.script("infra", "verify_recovery", "not_recovered")  # 정상화 공지도 없다
    harness.fakes.script("marketing", "publish_notice", "fail", "fail", "fail")
    inc = new_incident()
    inc.recovered_at = now()
    await engine.run(inc, scenario_a())
    assert inc.steps["publish_notice"].status == "failed"
    assert inc.steps["recovered_notice"].status == "skipped"
    step = inc.steps["end_notice"]
    assert (step.status, step.reason_code) == ("skipped", "no_incident_notice")
    assert end_calls(harness) == []


@pytest.mark.asyncio
async def test_failing_to_end_the_banner_does_not_change_the_result(harness) -> None:
    """배너 내리기는 필수가 아니다. 실패해도 이벤트 결과와 다른 단계(이벤트 기록)는 그대로다."""
    engine = await harness.start()
    harness.fakes.script("infra", "verify_recovery", "not_recovered")
    harness.fakes.script("marketing", "end_incident_notice", "fail", "fail", "fail")
    inc = new_incident()
    inc.recovered_at = now()
    await engine.run(inc, scenario_a())
    step = inc.steps["end_notice"]
    assert (step.status, step.attempts) == ("failed", 3)
    assert inc.steps["record"].status == "completed"
    assert inc.status == "resolved"  # 복구 신호가 있어 장애 중으로 남기지 않는다(기존 동작 그대로)


@pytest.mark.asyncio
async def test_a_failure_after_the_recovery_signal_does_not_break_recovery_handling(
    harness,
) -> None:
    manager, inc = await _closed_without_recovery_notice(harness)
    harness.fakes.script("marketing", "end_incident_notice", "fail", "fail", "fail")
    outcome, target = await manager.process(event(EventType.SHOP_RECOVERED, "evt_rec_1"))
    assert (outcome, target) == ("noted", inc)
    assert inc.steps["end_notice"].status == "failed"
    assert inc.recovered_at is not None and inc.status == "resolved"


# --- 실패한 배너 내리기는 나중에 다시 시도한다 -------------------------------------------------


@pytest.mark.asyncio
async def test_a_failed_banner_end_is_retried_later_and_succeeds(harness) -> None:
    """판매 서버가 잠깐 멈춰 세 번 다 실패해도, 나중 재시도에서 배너를 내린다."""
    engine = await harness.start()
    engine.late_retry_delays = (0.01, 0.01)
    harness.fakes.script("infra", "verify_recovery", "not_recovered")
    harness.fakes.script("marketing", "end_incident_notice", "fail", "fail", "fail")
    inc = new_incident()
    inc.recovered_at = now()
    await engine.run(inc, scenario_a())
    await asyncio.sleep(0.1)
    step = inc.steps["end_notice"]
    assert step.status == "completed"
    assert len(end_calls(harness)) == 4  # 처음 세 번 + 나중 한 번
    assert any("다시 시도" in m for m in [e["message"] for e in inc.events])


@pytest.mark.asyncio
async def test_late_retry_stops_after_the_listed_delays(harness) -> None:
    engine = await harness.start()
    engine.late_retry_delays = (0.01, 0.01)
    harness.fakes.script("infra", "verify_recovery", "not_recovered")
    harness.fakes.script("marketing", "end_incident_notice", *(["fail"] * 20))
    inc = new_incident()
    inc.recovered_at = now()
    await engine.run(inc, scenario_a())
    await asyncio.sleep(0.2)
    assert inc.steps["end_notice"].status == "failed"
    assert len(end_calls(harness)) == 3 * 3  # 처음 + 나중 두 번, 매번 재시도 포함 세 번


@pytest.mark.asyncio
async def test_without_late_delays_a_failed_banner_end_stays_failed(harness) -> None:
    """기본값(간격 없음)이면 지금처럼 다시 시도하지 않는다."""
    engine = await harness.start()
    harness.fakes.script("infra", "verify_recovery", "not_recovered")
    harness.fakes.script("marketing", "end_incident_notice", "fail", "fail", "fail")
    inc = new_incident()
    inc.recovered_at = now()
    await engine.run(inc, scenario_a())
    await asyncio.sleep(0.05)
    assert inc.steps["end_notice"].status == "failed"
    assert len(end_calls(harness)) == 3
