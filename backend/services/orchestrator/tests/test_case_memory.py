"""반복 장애: 사건 시작 때 비슷한 과거 사건을 찾고, 끝나면 해결 내용을 인사 카드에 남긴다."""

import pytest

from ax_orchestrator.mock.harness import new_incident
from ax_orchestrator.plans.finance_anomaly import finance_anomaly
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
    assert "롤백" in sent["action"]
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
async def test_finance_anomaly_also_searches_similar_past_incidents(harness) -> None:
    engine = await harness.start()
    inc = new_incident()
    await engine.run(inc, finance_anomaly())
    order = [r.skill for _, r in harness.fakes.calls]
    assert order.index("find_similar_cases") < order.index("record_case")
    assert inc.steps["similar_cases"].status == "completed"
    assert inc.view()["recommendation"]["status"] == "no_evidence"
