"""시연 준비: 지금 시각 배포 기록 (`POST /api/infra/demo-deploy`, 운영자 admin만).

실제 모드 진단은 장애 직전 60분 안의 배포만 원인 후보로 본다. 시연 데이터의 마지막 배포(10/21)는
오래전이라, 이 기록이 있어야 원인 배포 → 자동 롤백 흐름이 나온다.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from infra_agent.diagnosis import diagnose
from infra_agent.main import app

PATH = "/api/infra/demo-deploy"
ADMIN = {"Authorization": "Bearer admin-test-token"}
AGENT = {"Authorization": "Bearer orchestrator-test-token"}
DEMO_DAY = datetime(2026, 11, 9, 3, 0, tzinfo=UTC)  # 마지막 시연 데이터 배포보다 한참 뒤


@pytest.fixture(autouse=True)
def _tokens(monkeypatch: pytest.MonkeyPatch, runtime) -> None:
    monkeypatch.setenv(
        "AX_AGENT_TOKENS", "admin=admin-test-token,orchestrator=orchestrator-test-token"
    )
    runtime.clock = lambda: DEMO_DAY


def test_records_next_version_now(runtime) -> None:
    res = TestClient(app).post(PATH, headers=ADMIN)
    assert res.status_code == 200
    data = res.json()["data"]
    assert (data["service"], data["version"], data["components"]) == (
        "shop-api",
        "v2.4.2",
        ["payment"],
    )
    assert data["deployed_at"] == "2026-11-09T03:00:00Z"
    assert runtime.history.find("shop-api", "v2.4.2") is not None


def test_second_record_bumps_again() -> None:
    client = TestClient(app)
    client.post(PATH, headers=ADMIN)
    assert client.post(PATH, headers=ADMIN).json()["data"]["version"] == "v2.4.3"


def test_real_mode_diagnosis_finds_it_and_rollback_target_is_previous(runtime) -> None:
    before = diagnose(runtime.history, "payment", DEMO_DAY + timedelta(minutes=3), mock=False)
    assert before.output.status == "undetermined"  # 기록 전: 60분 안 배포 없음

    TestClient(app).post(PATH, headers=ADMIN)
    after = diagnose(runtime.history, "payment", DEMO_DAY + timedelta(minutes=3), mock=False)
    assert after.output.status == "suspected"
    [suspect] = after.output.suspects
    assert (suspect.version, suspect.confidence) == ("v2.4.2", "high")
    target = runtime.history.find("shop-api", "v2.4.2")
    assert runtime.history.previous_good(target).version == "v2.4.1"


@pytest.mark.parametrize(
    ("headers", "status"),
    [({}, 401), ({"Authorization": "Bearer nobody"}, 401), (AGENT, 403)],
)
def test_only_admin_can_record(headers, status, runtime) -> None:
    res = TestClient(app).post(PATH, headers=headers)
    assert res.status_code == status
    assert runtime.history.find("shop-api", "v2.4.2") is None
