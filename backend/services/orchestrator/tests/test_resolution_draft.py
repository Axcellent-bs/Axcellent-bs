"""해결 내용 초안: 총괄이 아는 사실만 옮기고, 모르면 만들지 않는다."""

from __future__ import annotations

from ax_orchestrator.draft import resolution_draft
from ax_orchestrator.models import Incident, StepRun


def incident(**steps: str) -> Incident:
    inc = Incident("ctx_inc_t", "scenario_a", "결제가 응답이 느려졌어요", "payment")
    for step_id, status in steps.items():
        run = StepRun(step_id, step_id, "라벨")
        run.status = status
        inc.steps[step_id] = run
    return inc


def test_nothing_known_means_no_draft() -> None:
    assert resolution_draft(incident()) is None


def test_cause_candidate_with_highest_confidence_is_used() -> None:
    inc = incident()
    inc.outputs["root_cause"] = {
        "hypotheses": [
            {"confidence": "low", "explanation": "트래픽 증가"},
            {"confidence": "mid", "explanation": "결제 대행사 응답 지연"},
        ]
    }
    draft = resolution_draft(inc)
    assert draft is not None
    assert draft["root_cause"] == "결제 대행사 응답 지연"
    assert draft["action"] is None and draft["outcome"] is None
    assert "확신도 보통" in draft["basis"][0]


def test_completed_rollback_becomes_the_action() -> None:
    inc = incident(rollback="completed", recovery="completed")
    inc.outputs["rollback"] = {
        "service": "shop-api",
        "from_version": "v2.14",
        "to_version": "v2.13",
    }
    draft = resolution_draft(inc)
    assert draft is not None
    assert draft["outcome"] == "resolved" and draft["root_cause"] is None
    assert draft["action"] == "shop-api 롤백 (v2.14 → v2.13)"
    assert draft["basis"] == ["조치는 총괄이 끝낸 롤백"] and draft["source"] == "this_event"


def test_rollback_that_did_not_finish_is_not_an_action() -> None:
    inc = incident(rollback="skipped")
    inc.outputs["rollback"] = {"service": "shop-api"}
    assert resolution_draft(inc) is None


def test_utc_times_in_the_cause_are_shown_in_korean_time() -> None:
    inc = incident()
    inc.outputs["root_cause"] = {
        "hypotheses": [
            {
                "confidence": "mid",
                "explanation": "이상이 2026-10-30T06:17:54Z에 시작되었고 직전에 배포됨",
            }
        ]
    }
    draft = resolution_draft(inc)
    assert draft is not None and "10월 30일 15:17:54(한국 시간)" in draft["root_cause"]
    assert "T06:" not in draft["root_cause"]


def past_case(similarity: float = 0.86) -> dict:
    return {
        "cases": [
            {
                "card_id": "card_old",
                "title": "t",
                "similarity": similarity,
                "same_type": True,
                "resolution": {
                    "outcome": "resolved",
                    "root_cause": "배포 후 결제 설정 누락",
                    "action": "직전 버전으로 롤백",
                },
            }
        ]
    }


def test_a_repeat_event_is_drafted_from_the_same_type_past_event() -> None:
    inc = incident()
    inc.outputs["similar_cases"] = past_case()
    inc.outputs["root_cause"] = {
        "hypotheses": [{"confidence": "high", "explanation": "PR #31 배포가 원인일 수 있음"}]
    }
    draft = resolution_draft(inc)
    assert draft is not None and draft["source"] == "past_case"
    assert draft["root_cause"] == "배포 후 결제 설정 누락"
    assert draft["action"] == "직전 버전으로 롤백"
    assert draft["past_card_id"] == "card_old" and draft["similarity"] == 0.86
    assert "86%" in draft["basis"][0] and "같은지 확인" in draft["basis"][0]
    # 이번 이벤트에서 AI가 찾은 후보는 칸에 넣지 않고 따로 둔다
    assert draft["this_event_cause"] == "PR #31 배포가 원인일 수 있음"


def test_a_rollback_finished_in_this_event_wins_over_the_past_action() -> None:
    inc = incident(rollback="completed")
    inc.outputs["similar_cases"] = past_case()
    inc.outputs["rollback"] = {
        "service": "shop-api",
        "from_version": "v2.14",
        "to_version": "v2.13",
    }
    draft = resolution_draft(inc)
    assert draft is not None and draft["source"] == "past_case"
    assert draft["action"] == "shop-api 롤백 (v2.14 → v2.13)"
    assert draft["root_cause"] == "배포 후 결제 설정 누락" and len(draft["basis"]) == 2


def test_other_type_past_events_are_not_used_for_the_draft() -> None:
    inc = incident()
    other = past_case()
    other["cases"][0]["same_type"] = False
    inc.outputs["similar_cases"] = other
    assert resolution_draft(inc) is None


def test_past_event_without_a_resolution_is_not_used_for_the_draft() -> None:
    inc = incident()
    unresolved = past_case()
    unresolved["cases"][0]["resolution"] = {"outcome": "unresolved"}
    inc.outputs["similar_cases"] = unresolved
    assert resolution_draft(inc) is None


def test_the_automatic_resolution_record_also_shows_korean_time() -> None:
    from ax_orchestrator.plans.scenario_a import _resolution

    inc = incident(recovery="skipped", rollback="skipped")
    inc.outputs["root_cause"] = {
        "hypotheses": [
            {"confidence": "high", "explanation": "이상이 2026-10-30T06:17:54Z에 시작됨"}
        ]
    }
    record = _resolution(inc)
    assert "15:17:54(한국 시간)" in record["root_cause"] and "T06:" not in record["root_cause"]
