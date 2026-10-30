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
    assert draft == {
        "outcome": "resolved",
        "root_cause": None,
        "action": "shop-api 롤백 (v2.14 → v2.13)",
        "basis": ["조치는 총괄이 끝낸 롤백"],
    }


def test_rollback_that_did_not_finish_is_not_an_action() -> None:
    inc = incident(rollback="skipped")
    inc.outputs["rollback"] = {"service": "shop-api"}
    assert resolution_draft(inc) is None
