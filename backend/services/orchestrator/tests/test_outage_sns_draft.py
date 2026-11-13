"""장애 때 사과 공지·보상 쿠폰이 나가면 마케팅에 SNS 글 초안을 바로 맡긴다(게시는 하지 않는다)."""

import pytest

from ax_orchestrator.mock import fake_departments
from ax_orchestrator.mock.harness import new_incident
from ax_orchestrator.plans.scenario_a import scenario_a

# 마케팅에 넘겨도 되는 칸(코드·참·거짓뿐). 원인 원문·버전·지표·영향 숫자는 넘기지 않는다
ALLOWED_KEYS = {
    "trigger",
    "notice_id",
    "component",
    "cause_code",
    "action_code",
    "recovered",
    "compensation_issued",
}


async def run(harness):
    engine = await harness.start()
    inc = new_incident()
    await engine.run(inc, scenario_a())
    return inc


def drafts(harness) -> dict[str, dict]:
    calls = [r for _, r in harness.fakes.calls if r.skill == "draft_outage_sns_post"]
    return {r.input["trigger"]: r.input for r in calls}


@pytest.mark.asyncio
async def test_apology_and_compensation_drafts_are_requested_from_marketing(harness) -> None:
    inc = await run(harness)
    sent = drafts(harness)
    assert set(sent) == {"apology_notice", "compensation"}
    for step_id in ("apology_sns_draft", "compensation_sns_draft"):
        assert inc.steps[step_id].status == "completed", step_id
        assert inc.steps[step_id].dept == "marketing"
    # 사과 초안: 게시된 정상화 공지에 묶이고, 되돌려서 복구가 확인됐으므로 원인을 말할 수 있다
    apology = sent["apology_notice"]
    assert apology["notice_id"] == inc.outputs["recovered_notice"]["notice_id"]
    assert apology["cause_code"] == "recent_update"
    assert apology["action_code"] == "reverted_update"
    assert apology["recovered"] is True and apology["compensation_issued"] is True
    assert apology["component"] == inc.component
    # 결과(post_draft_id)가 이벤트에 남는다
    assert inc.outputs["apology_sns_draft"]["post_draft_id"]
    assert inc.outputs["compensation_sns_draft"]["post_draft_id"]


@pytest.mark.asyncio
async def test_only_codes_are_sent_and_nothing_is_published(harness) -> None:
    inc = await run(harness)
    for payload in drafts(harness).values():
        assert set(payload) <= ALLOWED_KEYS
        for value in payload.values():
            assert value is None or isinstance(value, bool | str)
        # 원인으로 본 배포의 이름·버전이 글 재료로 넘어가지 않는다
        suspect = inc.outputs["diagnose"]["suspects"][0]
        assert suspect["version"] not in str(payload)
    skills = {r.skill for _, r in harness.fakes.calls}
    assert "publish_sns" not in skills and "generate_media" not in skills


@pytest.mark.asyncio
async def test_compensation_draft_never_claims_an_unconfirmed_cause(harness) -> None:
    harness.fakes.script("infra", "diagnose_incident", "undetermined")
    inc = await run(harness)
    sent = drafts(harness)
    # 정상화(사과) 공지가 없으니 사과 초안도 없다
    assert set(sent) == {"compensation"}
    assert inc.steps["apology_sns_draft"].status == "skipped"
    comp = sent["compensation"]
    assert comp["cause_code"] == "unknown" and comp["action_code"] == "none"
    assert comp["recovered"] is False and comp["compensation_issued"] is True


@pytest.mark.asyncio
async def test_recovery_not_confirmed_means_no_apology_draft(harness) -> None:
    harness.fakes.script("infra", "verify_recovery", "not_recovered")
    inc = await run(harness)
    assert inc.steps["apology_sns_draft"].status == "skipped"
    comp = drafts(harness)["compensation"]
    assert comp["cause_code"] == "unknown" and comp["recovered"] is False


@pytest.mark.asyncio
async def test_no_coupon_sent_means_no_compensation_draft(harness, monkeypatch) -> None:
    held = fake_departments.DEFAULT_OUTPUTS["request_incident_compensation"]

    def all_held(request):
        result = held(request)
        for row in result["results"]:
            row["status"] = "held"
        return result

    monkeypatch.setitem(fake_departments.DEFAULT_OUTPUTS, "request_incident_compensation", all_held)
    inc = await run(harness)
    step = inc.steps["compensation_sns_draft"]
    assert step.status == "skipped" and step.reason_code == "no_coupon_sent"
    sent = drafts(harness)
    assert set(sent) == {"apology_notice"}
    assert sent["apology_notice"]["compensation_issued"] is False


@pytest.mark.asyncio
async def test_draft_failure_does_not_break_the_incident(harness) -> None:
    harness.fakes.script("marketing", "draft_outage_sns_post", *["fail"] * 6)
    inc = await run(harness)
    assert inc.steps["apology_sns_draft"].status == "failed"
    assert inc.status == "resolved"  # SNS 초안은 필수 단계가 아니다
    assert inc.steps["record"].status == "completed"
