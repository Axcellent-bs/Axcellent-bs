"""release_fault: 같은 유형 과거 장애를 푼 처리 방식(장애 상태 해제)을 다시 한다."""

from __future__ import annotations

from fastapi.testclient import TestClient

from infra_agent.main import app

PAST = {"component": "payment", "past_case_id": "card_past", "similarity": 0.92}


def _call(a2a_body, payload: dict, **kw) -> dict:
    return TestClient(app).post("/a2a", json=a2a_body("release_fault", payload, **kw)).json()


def test_releases_the_fault_through_the_gate_when_the_server_is_abnormal(
    a2a_body, gate, runtime
) -> None:
    fake = gate("executed")
    result = _call(a2a_body, PAST)
    assert result["status"] == "completed", result
    out = result["output"]
    assert out["fault_released"] is True and out["post_check_ok"] is True
    assert out["past_case_id"] == "card_past"
    assert [c["action"] for c in fake.calls] == ["release_fault"]
    assert fake.calls[0]["params"]["past_case_id"] == "card_past"
    assert runtime.shop.released == 1
    assert "card:card_past" in result["evidence"]["ref_ids"]
    again = _call(a2a_body, PAST)  # 같은 사건에서 다시 와도 한 번만 해제한다
    assert again["output"]["fault_released"] is True
    assert runtime.shop.released == 1 and len(fake.calls) == 1


def test_a_held_request_waits_for_a_person_and_releases_nothing(a2a_body, gate, runtime) -> None:
    gate("held")
    result = _call(a2a_body, PAST)
    assert (result["status"], result["outcome"]) == ("input-required", "held")
    assert runtime.shop.released == 0


def test_does_nothing_when_the_server_is_already_fine(a2a_body, gate, runtime) -> None:
    fake = gate("executed")
    runtime.shop.state.update(active=False, failing=False, degraded=False)
    result = _call(a2a_body, PAST)
    assert (result["status"], result["outcome"]) == ("completed", "not_needed")
    assert fake.calls == [] and runtime.shop.released == 0


def test_bad_input_is_refused(a2a_body, gate, runtime) -> None:
    fake = gate("executed")
    result = _call(a2a_body, {"component": "payment", "similarity": 2})
    assert result["outcome"] == "invalid_input"
    assert fake.calls == [] and runtime.shop.released == 0


def test_only_the_orchestrator_may_ask(a2a_body, runtime) -> None:
    result = _call(a2a_body, PAST, token="mock:marketing")
    assert result["status"] != "completed"
    assert runtime.shop.released == 0
