"""이벤트 시각: 시스템이 아는 시각만 인사 카드에 남기고, 닫힌 이벤트에도 정상 복귀를 붙인다."""

from __future__ import annotations

from datetime import timedelta

import pytest

from ax_contracts.schemas import EventEnvelope, EventType
from ax_orchestrator.manager import IncidentManager
from ax_orchestrator.mock.harness import new_incident
from ax_orchestrator.models import now
from ax_orchestrator.plans.scenario_a import scenario_a
from ax_orchestrator.timeline import past_hint, prior_case_of, times_of


def event(kind: EventType, event_id: str, component: str = "payment", at=None) -> EventEnvelope:
    return EventEnvelope(
        event_id=event_id,
        type=kind,
        occurred_at=at or now(),
        source="shop-api",
        idempotency_key=event_id,
        data={"component": component, "metric_code": "p95_latency_ms", "severity": "high"},
    )


def times_calls(harness) -> list[dict]:
    return [r.input for _, r in harness.fakes.calls if r.skill == "record_event_times"]


@pytest.mark.asyncio
async def test_an_incident_handed_to_a_human_records_when_it_was_handed_over(harness) -> None:
    engine = await harness.start()
    harness.fakes.script("infra", "diagnose_incident", "undetermined")
    inc = new_incident()
    await engine.run(inc, scenario_a())
    sent = times_calls(harness)
    assert len(sent) == 1 and sent[0]["context_id"] == inc.context_id
    assert "detected_at" in sent[0] and "recovered_at" not in sent[0]
    assert "human_work_started_at" in sent[0]  # 이관 큐·원인 확인 요청이 사람에게 넘어간 시각


@pytest.mark.asyncio
async def test_an_incident_the_ai_finished_has_no_human_work(harness) -> None:
    engine = await harness.start()
    inc = new_incident()
    await engine.run(inc, scenario_a())
    sent = times_calls(harness)
    assert len(sent) == 1 and "human_work_started_at" not in sent[0]


@pytest.mark.asyncio
async def test_a_recovery_after_the_incident_closed_is_attached_and_sent_once(harness) -> None:
    engine = await harness.start()
    manager = IncidentManager(engine, scenario_a)
    opened = event(EventType.SHOP_DEGRADED, "evt_deg_1")
    inc = await manager.handle(opened)
    await manager.wait(inc.context_id)
    assert inc.closed_at is not None
    recovered_at = now() + timedelta(seconds=40)
    outcome, same = await manager.process(
        event(EventType.SHOP_RECOVERED, "evt_rec_1", at=recovered_at)
    )
    assert outcome == "noted" and same is inc and inc.recovered_at == recovered_at
    assert any("recovered_at" in c for c in times_calls(harness))
    again, _ = await manager.process(event(EventType.SHOP_RECOVERED, "evt_rec_2", at=recovered_at))
    sent = [c for c in times_calls(harness) if "recovered_at" in c]
    assert len(sent) == 1  # 이미 남긴 시각은 다시 보내지 않는다 (같은 복귀 신호가 또 와도)
    assert again == "noted"


@pytest.mark.asyncio
async def test_recovery_goes_to_the_latest_incident_of_that_component_only(harness) -> None:
    engine = await harness.start()
    manager = IncidentManager(engine, scenario_a)
    first = await manager.handle(event(EventType.SHOP_DEGRADED, "evt_d1", at=now()))
    await manager.wait(first.context_id)
    first.recovered_at = now()
    second = await manager.handle(
        event(EventType.SHOP_DEGRADED, "evt_d2", at=now() + timedelta(seconds=5))
    )
    await manager.wait(second.context_id)
    await manager.process(
        event(EventType.SHOP_RECOVERED, "evt_r", at=now() + timedelta(seconds=30))
    )
    assert second.recovered_at is not None
    other = await manager.handle(event(EventType.SHOP_DEGRADED, "evt_d3", component="search"))
    await manager.wait(other.context_id)
    assert other.recovered_at is None  # 다른 구성요소의 복귀 신호는 붙지 않는다


@pytest.mark.asyncio
async def test_a_failed_time_record_does_not_stop_the_incident(harness) -> None:
    engine = await harness.start()
    harness.fakes.script("hr", "record_event_times", "fail", "fail", "fail")
    inc = new_incident()
    await engine.run(inc, scenario_a())
    assert inc.closed_at is not None and inc.times_sent == {}


def test_without_a_past_case_there_is_no_hint_and_no_prior() -> None:
    inc = new_incident()
    assert past_hint(inc) is None and prior_case_of(inc) is None
    assert set(times_of(inc)) == {"detected_at"}


def test_a_same_type_past_case_becomes_the_hint_and_the_repeat_marker() -> None:
    inc = new_incident()
    inc.outputs["similar_cases"] = {
        "cases": [
            {
                "card_id": "card_old",
                "title": "t",
                "similarity": 0.85,
                "same_type": True,
                "resolution": {
                    "outcome": "resolved",
                    "root_cause": "대행사 지연",
                    "action": "타임아웃 조정",
                },
            }
        ]
    }
    assert prior_case_of(inc) == "card_old"
    hint = past_hint(inc)
    assert hint and "85%" in hint and "대행사 지연" in hint and "참고용" in hint


@pytest.mark.asyncio
async def test_latency_events_do_not_ask_the_data_ai_for_payment_failure_causes(harness) -> None:
    engine = await harness.start()
    inc = new_incident()
    inc.signal = {"metric_code": "p95_latency_ms"}
    await engine.run(inc, scenario_a())
    assert inc.steps["root_cause"].status == "skipped"
    assert "응답 지연" in inc.steps["root_cause"].note
    assert "root_cause_hypotheses" not in [r.skill for _, r in harness.fakes.calls]


@pytest.mark.asyncio
async def test_failure_events_still_get_cause_candidates(harness) -> None:
    engine = await harness.start()
    inc = new_incident()
    inc.signal = {"metric_code": "error_rate_5xx"}
    await engine.run(inc, scenario_a())
    assert inc.steps["root_cause"].status == "completed"


@pytest.mark.asyncio
async def test_the_decision_card_reason_uses_korean_step_names(harness) -> None:
    engine = await harness.start()
    inc = new_incident()
    await engine.run(inc, scenario_a())
    record = next(r.input for _, r in harness.fakes.calls if r.skill == "record_case")
    assert "diagnose_incident" not in record["decision_reason"]
    assert "원인 진단" in record["decision_reason"] and "(infra)" not in record["decision_reason"]
