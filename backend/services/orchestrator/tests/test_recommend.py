"""과거 근거 기반 추천: 해결된 과거 사건의 원인·조치만 참고로 붙인다."""

import pytest

from ax_orchestrator.mock.harness import new_incident
from ax_orchestrator.plans.scenario_a import scenario_a
from ax_orchestrator.recommend import CAVEAT, recommend


def case(card_id, similarity, outcome=None, **resolution):
    return {
        "card_id": card_id,
        "title": f"사건 {card_id}",
        "similarity": similarity,
        "resolved": outcome is not None,
        "resolution": {"outcome": outcome, **resolution} if outcome else None,
    }


def test_no_search_yet_gives_nothing() -> None:
    assert recommend(None) is None


def test_no_past_incident_is_no_evidence() -> None:
    out = recommend({"cases": []})
    assert out["status"] == "no_evidence" and "없음" in out["reason"]


def test_unresolved_past_incident_is_not_used_as_evidence() -> None:
    out = recommend({"cases": [case("a", 0.8, "unresolved", root_cause=None, action=None)]})
    assert out["status"] == "no_evidence" and "해결된 기록" in out["reason"]


def test_picks_the_most_similar_resolved_case() -> None:
    out = recommend(
        {
            "cases": [
                case("a", 0.9, "unresolved"),
                case(
                    "b",
                    0.75,
                    "resolved",
                    root_cause="설정 누락",
                    action="롤백",
                    minutes_to_resolve=14,
                ),
                case("c", 0.72, "resolved", root_cause="대행사 지연", action="타임아웃 조정"),
            ]
        }
    )
    assert out["status"] == "found" and out["card_id"] == "b"
    assert (out["root_cause"], out["action"], out["minutes_to_resolve"]) == (
        "설정 누락",
        "롤백",
        14,
    )
    assert out["caveat"] == CAVEAT


def test_missing_values_stay_empty() -> None:
    out = recommend({"cases": [case("a", 0.8, "workaround", action="재시작")]})
    assert out["root_cause"] is None and out["action"] == "재시작"


@pytest.mark.asyncio
async def test_incident_view_carries_the_recommendation(harness) -> None:
    engine = await harness.start()
    inc = new_incident()
    await engine.run(inc, scenario_a())
    assert inc.view()["recommendation"] == {
        "status": "no_evidence",
        "reason": "비슷한 과거 사건이 없음",
    }
