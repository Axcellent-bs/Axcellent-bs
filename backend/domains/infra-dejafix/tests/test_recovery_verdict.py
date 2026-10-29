"""복구 확인이 data 판정 상태(normal·anomaly·unknown)를 따르는지 (#64 이후)."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from ax_contracts.schemas import TaskStatus
from infra_agent import clients
from infra_agent.clients import A2AData, business_verdict
from infra_agent.main import app

RECOVERY = {"component": "payment", "since": "2026-10-21T05:04:00Z"}


def _verdict(metric: str, status: str, note: str | None = None) -> dict[str, Any]:
    return {
        "metric": metric,
        "observed": 0.0,
        "baseline": 0.0,
        "is_anomaly": status == "anomaly",
        "status": status,
        "note": note,
    }


NORMAL = {
    "verdicts": [
        _verdict("payment_fail_rate", "normal"),
        _verdict("order_fail_count", "normal"),
    ],
    "any_anomaly": False,
    "summary": "결제 지표 정상",
    "status": "normal",
    "unknown_metrics": [],
}
ANOMALY = {
    "verdicts": [
        _verdict("payment_fail_rate", "anomaly", "기준 대비 8배"),
        _verdict("order_fail_count", "normal"),
    ],
    "any_anomaly": True,
    "summary": "결제 실패율 이상",
    "status": "anomaly",
    "unknown_metrics": [],
}
UNKNOWN = {
    "verdicts": [
        _verdict("payment_fail_rate", "unknown", "확인할 수 없음: 결제 표본 부족(3건)"),
        _verdict("order_fail_count", "unknown", "확인할 수 없음: 결제 표본 부족(3건)"),
    ],
    "any_anomaly": False,
    "summary": "확인할 수 없음",
    "status": "unknown",
    "unknown_metrics": ["payment_fail_rate", "order_fail_count"],
}
PARTLY_UNKNOWN = {
    "verdicts": [
        _verdict("payment_fail_rate", "unknown", "확인할 수 없음: 결제 표본 부족(3건)"),
        _verdict("order_fail_count", "normal"),
    ],
    "any_anomaly": False,
    "summary": "이상 없음 (확인할 수 없음: payment_fail_rate)",
    "status": "normal",
    "unknown_metrics": ["payment_fail_rate"],
}
OLD_OK = {"verdicts": [], "any_anomaly": False, "summary": "예전 응답 정상"}
OLD_BAD = {"verdicts": [], "any_anomaly": True, "summary": "예전 응답 이상"}


# ---- 판정 해석


def test_normal_is_ok() -> None:
    assert business_verdict(NORMAL) == (True, "결제 지표 정상")


def test_anomaly_is_not_ok() -> None:
    assert business_verdict(ANOMALY) == (False, "결제 실패율 이상")


def test_unknown_is_not_confirmed_with_reason() -> None:
    ok, note = business_verdict(UNKNOWN)
    assert ok is None
    assert note.startswith("업무 지표를 판정할 수 없음: ")
    assert "결제 표본 부족(3건)" in note


def test_partly_unknown_is_not_confirmed() -> None:
    ok, note = business_verdict(PARTLY_UNKNOWN)
    assert ok is None
    assert "payment_fail_rate 결제 표본 부족(3건)" in note


def test_missing_status_keeps_old_behaviour() -> None:
    assert business_verdict(OLD_OK) == (True, "예전 응답 정상")
    assert business_verdict(OLD_BAD) == (False, "예전 응답 이상")
    assert business_verdict({"summary": "x"})[0] is None


# ---- verify_recovery 끝까지 (data는 A2A 응답만 바꿔 끼움)


@pytest.fixture
def data_says(monkeypatch: pytest.MonkeyPatch, runtime):
    def install(output: dict[str, Any]) -> None:
        async def fake_call_agent(dept: str, request) -> SimpleNamespace:
            assert (dept, request.skill) == ("data", "anomaly_detection")
            return SimpleNamespace(status=TaskStatus.COMPLETED, outcome="ok", output=output)

        monkeypatch.setattr(clients, "call_agent", fake_call_agent)
        runtime.data = A2AData()
        runtime.shop.state = {"active": False, "degraded": False}

    return install


def _verify(a2a_body) -> dict[str, Any]:
    return TestClient(app).post("/a2a", json=a2a_body("verify_recovery", RECOVERY)).json()


def test_verify_normal_is_recovered(a2a_body, data_says, runtime) -> None:
    data_says(NORMAL)
    result = _verify(a2a_body)
    assert result["outcome"] == "recovered"
    assert result["output"]["recovered"] is True
    assert result["output"]["business_metrics_ok"] is True
    assert runtime.sleep.waits == []


def test_verify_anomaly_is_not_recovered(a2a_body, data_says) -> None:
    data_says(ANOMALY)
    result = _verify(a2a_body)
    assert result["outcome"] == "not_recovered"
    assert result["output"]["recovered"] is False
    assert result["output"]["business_metrics_ok"] is False


def test_verify_unknown_is_not_recovered_with_reason(a2a_body, data_says, runtime) -> None:
    data_says(UNKNOWN)
    result = _verify(a2a_body)
    assert result["outcome"] == "not_recovered"
    assert result["output"]["recovered"] is False
    assert result["output"]["infra_metrics_ok"] is True
    assert result["output"]["business_metrics_ok"] is None
    summary = result["evidence"]["data_summary"]
    assert "업무 지표를 판정할 수 없음: " in summary
    assert "결제 표본 부족(3건)" in summary
    assert runtime.sleep.waits == [30.0, 30.0, 30.0, 30.0]  # 표본이 쌓이길 기다려 다시 확인


def test_verify_missing_status_keeps_old_behaviour(a2a_body, data_says) -> None:
    data_says(OLD_OK)
    assert _verify(a2a_body)["output"]["recovered"] is True
    data_says(OLD_BAD)
    assert _verify(a2a_body)["output"]["recovered"] is False
