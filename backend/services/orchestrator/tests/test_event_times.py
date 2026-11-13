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
    assert "정상화까지" not in hint  # 걸린 시간이 없으면 쓰지 않는다
    inc.outputs["similar_cases"]["cases"][0]["resolution"]["minutes_to_resolve"] = 3
    assert "정상화까지 3분" in (past_hint(inc) or "")
    inc.outputs["similar_cases"]["cases"][0]["resolution"]["minutes_to_resolve"] = 0
    assert "1분 안에 정상화" in (past_hint(inc) or "")


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
    reason = record["decision_reason"]
    assert "(infra)" not in reason and "_" not in reason
    assert reason == "모든 단계를 끝냈어요" or reason.startswith("끝내지 못한 일: ")


@pytest.mark.asyncio
async def test_a_company_without_hr_does_not_get_time_records_sent_again(harness) -> None:
    engine = await harness.start()
    harness.fakes.script("hr", "record_event_times", "disabled", "disabled", "disabled")
    inc = new_incident()
    await engine.run(inc, scenario_a())
    assert inc.closed_at is not None and inc.times_off and inc.times_sent == {}
    assert len(times_calls(harness)) == 1  # 꺼진 걸 알았으면 같은 이벤트에서는 더 보내지 않는다
    await engine.sync_times(inc)  # 정상 복귀 신호 같은 뒤늦은 시각이 와도
    assert len(times_calls(harness)) == 1


@pytest.mark.asyncio
async def test_with_the_hr_service_off_nothing_is_sent_and_nothing_piles_up(
    harness, monkeypatch
) -> None:
    """인사가 꺼진 회사: 시각 기록은 사람 할 일함으로도 가지 않게 아예 보내지 않는다."""
    from ax_agent_template import routing

    monkeypatch.setenv(routing.ENABLED_ENV, "planning,dev,inquiry")  # decisions(인사)는 끔
    engine = await harness.start()
    inc = new_incident()
    await engine.run(inc, scenario_a())
    assert inc.closed_at is not None and inc.times_off
    assert times_calls(harness) == []
    await engine.sync_times(inc)
    assert times_calls(harness) == []


@pytest.mark.asyncio
async def test_with_the_hr_service_on_times_are_still_sent(harness, monkeypatch) -> None:
    from ax_agent_template import routing

    monkeypatch.setenv(routing.ENABLED_ENV, "decisions,planning")
    engine = await harness.start()
    inc = new_incident()
    await engine.run(inc, scenario_a())
    assert not inc.times_off and len(times_calls(harness)) == 1


@pytest.mark.asyncio
async def test_recovery_reruns_impact_to_the_recovery_time(harness, monkeypatch) -> None:
    """첫 분석(이벤트를 열고 2초 뒤) 뒤의 실패가 복구 때 다시 센 결과에 들어온다."""
    from ax_orchestrator.mock import fake_departments as fd

    # 대응이 끝날 때 복구를 확인하지 못했고(장애 중으로 닫힘), 쇼핑몰 복구 신호가 나중에 온다
    harness.fakes.script("infra", "verify_recovery", "not_recovered")
    engine = await harness.start()
    manager = IncidentManager(engine, scenario_a)
    inc = await manager.handle(event(EventType.SHOP_DEGRADED, "evt_deg_1"))
    await manager.wait(inc.context_id)
    first = set(inc.outputs["impact"]["affected_customer_ids"])
    first_finished = inc.steps["impact"].finished_at
    sent = harness.fakes.calls
    comp_before = len([1 for _, r in sent if r.skill == "request_incident_compensation"])
    seen = []
    more = [*first, "buyer-late-1", "buyer-late-2"]

    def impact(r):
        seen.append(r.input)
        return fd._ok(affected_customer_ids=more, affected_customer_count=len(more))

    monkeypatch.setitem(fd.DEFAULT_OUTPUTS, "impact_analysis", impact)
    recovered_at = inc.opened_at + timedelta(seconds=40)
    await manager.process(event(EventType.SHOP_RECOVERED, "evt_rec_1", at=recovered_at))

    assert len(seen) == 1 and seen[0]["window_to"].endswith("Z")
    assert seen[0]["window_to"] == recovered_at.strftime("%Y-%m-%dT%H:%M:%SZ")
    assert inc.outputs["impact"]["added_customer_ids"] == ["buyer-late-1", "buyer-late-2"]
    assert inc.steps["impact"].output is inc.outputs["impact"]
    # 다시 계산한 시각은 따로 남기고, 단계 시간(첫 분석의 시작~끝)은 그대로다
    impact_run = inc.steps["impact"]
    assert impact_run.refreshed_at is not None and impact_run.finished_at == first_finished
    assert impact_run.refreshed_at > first_finished
    keys = [r.meta.idempotency_key for _, r in harness.fakes.calls if r.skill == "impact_analysis"]
    assert len(keys) == 2 and len(set(keys)) == 2
    # 보상은 자동으로 다시 보내지 않는다
    comp = [r for _, r in harness.fakes.calls if r.skill == "request_incident_compensation"]
    assert len(comp) == comp_before
    # 새로 잡힌 고객만 보상 "대기"에 올린다(쿠폰 요청은 없다)
    queued = [r for _, r in harness.fakes.calls if r.skill == "propose_incident_compensation"]
    assert len(queued) == 1
    assert queued[0].input["buyer_ids"] == ["buyer-late-1", "buyer-late-2"]
    assert queued[0].input["reason_code"] == "payment_failure"
    assert queued[0].meta.context_id == inc.context_id
    late = [r for k, r in inc.steps.items() if k.startswith("late_compensation_")]
    assert len(late) == 1 and late[0].status == "completed" and late[0].output["queued"] == 2


@pytest.mark.asyncio
async def test_recovery_without_new_customers_queues_nothing(harness) -> None:
    engine = await harness.start()
    manager = IncidentManager(engine, scenario_a)
    inc = await manager.handle(event(EventType.SHOP_DEGRADED, "evt_deg_2"))
    await manager.wait(inc.context_id)
    at = inc.opened_at + timedelta(seconds=40)
    await manager.process(event(EventType.SHOP_RECOVERED, "evt_rec_2", at=at))
    assert not [1 for _, r in harness.fakes.calls if r.skill == "propose_incident_compensation"]


def resolution_calls(harness) -> list[dict]:
    return [r.input for _, r in harness.fakes.calls if r.skill == "record_resolution"]


def recovered(event_id: str, at, **how) -> EventEnvelope:
    env = event(EventType.SHOP_RECOVERED, event_id, at=at)
    return env.model_copy(update={"data": {**env.data, **how}})


@pytest.mark.asyncio
async def test_a_handed_over_incident_is_recorded_as_resolved_when_the_shop_recovers(
    harness,
) -> None:
    """원인 미확정으로 사람에게 넘긴 장애가 나중에 풀리면 해결·조치·걸린 시간을 다시 남긴다."""
    engine = await harness.start()
    harness.fakes.script("infra", "diagnose_incident", "undetermined")
    manager = IncidentManager(engine, scenario_a)
    inc = await manager.handle(event(EventType.SHOP_DEGRADED, "evt_deg_h"))
    await manager.wait(inc.context_id)
    first = resolution_calls(harness)
    assert [c["outcome"] for c in first] == ["unresolved"] and first[0]["action"] is None

    at = inc.opened_at + timedelta(minutes=4)
    how = {"recovery": "fault_released", "released_by": "admin"}
    await manager.process(recovered("evt_rec_h", at, **how))
    later = resolution_calls(harness)[1]
    assert later["outcome"] == "resolved" and later["only_if_unresolved"] is True
    assert later["action"] == "사람(쇼핑몰 운영자)이 결제 장애 상태를 해제해 정상화"
    assert later["minutes_to_resolve"] == 4
    await manager.process(recovered("evt_rec_h2", at, **how))  # 같은 복구가 다시 와도 한 번
    assert len(resolution_calls(harness)) == 2


@pytest.mark.asyncio
async def test_a_recovery_without_how_it_recovered_does_not_invent_an_action(harness) -> None:
    engine = await harness.start()
    harness.fakes.script("infra", "diagnose_incident", "undetermined")
    manager = IncidentManager(engine, scenario_a)
    inc = await manager.handle(event(EventType.SHOP_DEGRADED, "evt_deg_n"))
    await manager.wait(inc.context_id)
    await manager.process(recovered("evt_rec_n", inc.opened_at + timedelta(minutes=2)))
    later = resolution_calls(harness)[-1]
    assert later["outcome"] == "resolved" and later["action"] is None  # 모르면 비운다


@pytest.mark.asyncio
async def test_an_incident_the_ai_rolled_back_is_not_recorded_again(harness) -> None:
    engine = await harness.start()
    manager = IncidentManager(engine, scenario_a)
    inc = await manager.handle(event(EventType.SHOP_DEGRADED, "evt_deg_a"))
    await manager.wait(inc.context_id)
    how = {"recovery": "fault_released", "released_by": "infra"}
    await manager.process(recovered("evt_rec_a", now(), **how))
    assert [c["outcome"] for c in resolution_calls(harness)] == ["resolved"]  # 자동 복구 때 한 번
