"""release_fault: 같은 유형 과거 장애를 푼 처리 방식(장애 상태 해제)을 다시 한다."""

from __future__ import annotations

from fastapi.testclient import TestClient

from infra_agent.handlers import switched_off
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


def test_switch_off_with_a_degraded_monitor_is_not_released(a2a_body, gate, runtime) -> None:
    """감시는 이상인데 스위치는 꺼져 있다(예: 결제사 지연): 꺼진 스위치를 끄지 않는다."""
    fake = gate("executed")
    runtime.shop.state.update(active=False, failing=False, degraded=True)
    result = _call(a2a_body, PAST)
    assert (result["status"], result["outcome"]) == ("completed", "not_needed")
    assert "스위치가 꺼져" in result["output"]["message"]
    # 총괄이 과거 처리 재사용을 끝낸 것으로 보지 않는다
    assert "fault_released" not in result["output"]
    assert fake.calls == [] and runtime.shop.released == 0


def test_pg_side_latency_is_not_released(a2a_body, gate, runtime) -> None:
    """시험용 결제사 쪽 지연(pg_slow): 스위치가 아니라 AI가 끌 수 없다 → 해제하지 않는다."""
    fake = gate("executed")
    runtime.shop.state.update(active=False, failing=False, degraded=True, pg_delay_ms=1500)
    result = _call(a2a_body, PAST)
    assert (result["status"], result["outcome"]) == ("completed", "not_needed")
    assert result["output"]["fault_state"]["pg_delay_ms"] == 1500
    assert fake.calls == [] and runtime.shop.released == 0


def test_post_check_fails_when_the_shop_did_not_turn_a_switch_off(
    a2a_body, gate, runtime, monkeypatch
) -> None:
    """확인과 해제 사이에 다른 쪽이 먼저 껐으면 이번 해제는 아무것도 바꾸지 않았다."""
    gate("executed")

    async def already_off() -> dict:
        runtime.shop.released += 1
        runtime.shop.state.update(active=False, failing=False)
        return {**runtime.shop.state, "released": False}

    monkeypatch.setattr(runtime.shop, "release_fault", already_off)
    out = _call(a2a_body, PAST)["output"]
    assert out["fault_released"] is False and out["post_check_ok"] is False


def test_switched_off_needs_on_before_and_off_after() -> None:
    on, off = {"active": True}, {"active": False}
    assert switched_off(on, {**off, "released": True}) is True
    assert switched_off(off, {**off, "released": False}) is False  # 원래 꺼져 있었다
    assert switched_off(on, {"active": True, "released": True}) is False  # 끈 뒤에도 켜져 있다
    assert switched_off(on, {**off, "released": False}) is False  # 다른 쪽이 먼저 껐다
    assert switched_off(on, off) is True  # 예전 쇼핑몰(released 없음)은 해제 전 상태로 본다
    assert switched_off(None, {**off, "released": True}) is True  # 되돌리기: 쇼핑몰 응답만 본다
    assert switched_off(None, {**off, "released": False}) is False


def test_bad_input_is_refused(a2a_body, gate, runtime) -> None:
    fake = gate("executed")
    result = _call(a2a_body, {"component": "payment", "similarity": 2})
    assert result["outcome"] == "invalid_input"
    assert fake.calls == [] and runtime.shop.released == 0


def test_only_the_orchestrator_may_ask(a2a_body, runtime) -> None:
    result = _call(a2a_body, PAST, token="mock:marketing")
    assert result["status"] != "completed"
    assert runtime.shop.released == 0
