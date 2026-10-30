"""과거 근거 기반 추천: 해결된 과거 이벤트의 원인·조치만 참고로 붙인다."""

import pytest

from ax_orchestrator.mock.harness import new_incident
from ax_orchestrator.plans.scenario_a import scenario_a
from ax_orchestrator.recommend import CAVEAT, recommend


def case(card_id, similarity, outcome=None, **resolution):
    return {
        "card_id": card_id,
        "title": f"이벤트 {card_id}",
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
        "reason": "비슷한 과거 이벤트가 없음",
    }


def test_other_type_cases_are_not_recommended() -> None:
    other = case("a", 0.9, "resolved", root_cause="배포 후 설정 누락", action="롤백")
    other["same_type"] = False
    out = recommend({"cases": [other]})
    assert out["status"] == "no_evidence" and "같은 유형" in out["reason"]


def test_same_type_wins_even_when_an_other_type_scores_higher() -> None:
    other = case("a", 0.9, "resolved", root_cause="다른 유형 원인", action="롤백")
    other["same_type"] = False
    mine = case("b", 0.78, "resolved", root_cause="결제 대행사 응답 지연", action="타임아웃 조정")
    mine["same_type"] = True
    out = recommend({"cases": [other, mine]})
    assert out["status"] == "found" and out["card_id"] == "b"


def test_unknown_type_is_still_usable() -> None:
    unknown = case("a", 0.8, "resolved", root_cause="원인", action="조치")
    assert recommend({"cases": [unknown]})["status"] == "found"
