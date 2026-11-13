"""반복 장애: 이벤트 시작 때 비슷한 과거 이벤트를 찾고, 끝나면 해결 내용을 인사 카드에 남긴다."""

import pytest

from ax_orchestrator.mock.harness import new_incident
from ax_orchestrator.plans.scenario_a import scenario_a


def called(harness, skill: str) -> list:
    return [req for _, req in harness.fakes.calls if req.skill == skill]


async def run(harness):
    engine = await harness.start()
    inc = new_incident()
    await engine.run(inc, scenario_a())
    return inc


@pytest.mark.asyncio
async def test_similar_cases_are_searched_first_with_title_and_component(harness) -> None:
    inc = await run(harness)
    order = [r.skill for _, r in harness.fakes.calls]
    assert order.index("find_similar_cases") < order.index("record_case")
    request = called(harness, "find_similar_cases")[0]
    assert inc.title in request.input["query"] and inc.component in request.input["query"]
    assert inc.steps["similar_cases"].status == "completed"
    assert "cases" in inc.outputs["similar_cases"]


@pytest.mark.asyncio
async def test_search_failure_does_not_stop_the_incident(harness) -> None:
    harness.fakes.script("hr", "find_similar_cases", "fail", "fail", "fail")
    inc = await run(harness)
    assert inc.steps["similar_cases"].status == "failed"
    assert inc.status == "resolved"  # 참고용 조회라 필수 단계가 아니다


@pytest.mark.asyncio
async def test_resolution_is_recorded_after_the_case_card(harness) -> None:
    inc = await run(harness)
    order = [r.skill for _, r in harness.fakes.calls]
    assert order.index("record_resolution") > order.index("record_case")
    sent = called(harness, "record_resolution")[0].input
    assert sent["context_id"] == inc.context_id and sent["outcome"] == "resolved"
    assert sent["root_cause"] == "PR #412 배포가 원인일 수 있음"
    assert "되돌림" in sent["action"]
    assert isinstance(sent["minutes_to_resolve"], int) and sent["minutes_to_resolve"] >= 0


@pytest.mark.asyncio
async def test_unrecovered_incident_is_recorded_as_unresolved_without_a_time(harness) -> None:
    harness.fakes.script("infra", "verify_recovery", "not_recovered")
    await run(harness)
    sent = called(harness, "record_resolution")[0].input
    assert sent["outcome"] == "unresolved" and sent["minutes_to_resolve"] is None


@pytest.mark.asyncio
async def test_unknown_cause_and_action_are_left_empty(harness) -> None:
    harness.fakes.script("infra", "diagnose_incident", "undetermined")
    harness.fakes.script("data", "root_cause_hypotheses", "fail", "fail", "fail")
    await run(harness)
    sent = called(harness, "record_resolution")[0].input
    assert sent["root_cause"] is None and sent["action"] is None


@pytest.mark.asyncio
async def test_the_handover_waits_for_the_past_case_so_the_hint_is_never_missed(harness) -> None:
    """원인 미확정으로 넘길 때 과거 조치 한 줄이 붙으려면 찾기가 먼저 끝나 있어야 한다."""
    harness.fakes.script("infra", "diagnose_incident", "undetermined")
    inc = await run(harness)
    rollback, similar = inc.steps["rollback"], inc.steps["similar_cases"]
    assert rollback.status == "transfer_queued"
    assert similar.finished_at is not None and rollback.started_at >= similar.finished_at
    step = next(s for s in scenario_a().steps if s.id == "rollback")
    assert "similar_cases" in step.also_after  # 필수 선행(after)이 아니라 끝나기만 기다린다
    assert "similar_cases" not in step.after


@pytest.mark.asyncio
async def test_a_repeat_incident_is_fixed_the_same_way_right_away(harness) -> None:
    """원인 미확정이어도 같은 유형 과거 장애가 장애 상태 해제로 풀렸으면 바로 같은 처리를 한다."""
    harness.fakes.script("infra", "diagnose_incident", "undetermined")
    harness.fakes.script("hr", "find_similar_cases", "past_release")
    inc = await run(harness)
    fix = called(harness, "release_fault")
    assert len(fix) == 1 and fix[0].input["past_case_id"] == "card_past_release"
    assert inc.steps["past_fix"].status == "completed"
    rollback = inc.steps["rollback"]
    assert (rollback.status, rollback.reason_code) == ("skipped", "past_fix_applied")
    assert inc.steps["recovery"].status == "completed"
    assert inc.status == "resolved"  # 사람에게 넘기지 않았다
    sent = called(harness, "record_resolution")[0].input
    assert sent["outcome"] == "resolved" and sent["action_code"] == "release_fault"


@pytest.mark.asyncio
async def test_switch_off_fault_is_not_fixed_by_release_and_goes_to_a_person(harness) -> None:
    """스위치가 꺼진 채 난 장애(지연 등): 인프라가 해제하지 않는다.

    그러면 사람에게 넘기고 복구 시각이 없다."""
    harness.fakes.script("infra", "diagnose_incident", "undetermined")
    harness.fakes.script("hr", "find_similar_cases", "past_release")
    harness.fakes.script("infra", "release_fault", "switch_off")
    inc = await run(harness)
    assert len(called(harness, "release_fault")) == 1
    assert "fault_released" not in inc.outputs["past_fix"]
    assert inc.steps["rollback"].status == "transfer_queued"  # 원인 확인·사람 이관으로 간다
    assert called(harness, "verify_recovery") == []  # 해제한 사람이 없으니 복구 검증도 기다린다
    assert inc.status == "degraded"
    assert inc.recovered_at is None and inc.mttr_seconds() is None
    assert inc.shared.get("recovered_notice_id") is None  # 정상화 공지도 없다
    sent = called(harness, "record_resolution")[0].input
    assert sent["outcome"] == "unresolved" and sent["action_code"] is None


@pytest.mark.asyncio
async def test_a_less_similar_past_case_is_not_replayed(harness) -> None:
    harness.fakes.script("infra", "diagnose_incident", "undetermined")
    harness.fakes.script("hr", "find_similar_cases", "past_release_low")
    inc = await run(harness)
    assert called(harness, "release_fault") == []
    past = inc.steps["past_fix"]
    assert (past.status, past.reason_code) == ("skipped", "not_similar_enough")
    assert inc.steps["rollback"].status == "transfer_queued"  # 지금처럼 사람에게 넘긴다


@pytest.mark.asyncio
async def test_without_a_past_case_the_incident_goes_to_a_person_as_before(harness) -> None:
    harness.fakes.script("infra", "diagnose_incident", "undetermined")
    inc = await run(harness)
    assert called(harness, "release_fault") == []
    assert inc.steps["past_fix"].reason_code == "no_past_case"
    assert inc.steps["rollback"].status == "transfer_queued"
    assert inc.status == "degraded"
