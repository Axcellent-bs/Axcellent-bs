"""잘못된 배포 시연: 쇼핑몰 결제 설정 v2 배포 → 진단(high) → 실제 되돌리기(v1).

장애 스위치 없이 배포만 잘못된 경우다. 롤백은 관문(execute)을 거친 뒤에만 쇼핑몰 결제 설정을
배포 기록의 이전 버전으로 되돌리고, 장애 스위치는 건드리지 않는다.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from infra_agent import handlers
from infra_agent.main import app

V1_AT = datetime(2026, 11, 13, 4, 0, tzinfo=UTC)  # 쇼핑몰이 v1로 돌기 시작한 시각
DEPLOYED = datetime(2026, 11, 13, 5, 0, tzinfo=UTC)  # v2 배포
NOW = DEPLOYED + timedelta(minutes=2)  # 장애 감지·진단
ADMIN = {"Authorization": "Bearer mock:admin"}
ROLLBACK = {"service": "shop-api", "from_version": "v2"}


@pytest.fixture
def bad_deploy(runtime, monkeypatch: pytest.MonkeyPatch):
    """`demo_deploy.sh --bad`와 같은 기록과 쇼핑몰 상태(v2, 감시 이상, 스위치 꺼짐)."""
    monkeypatch.setattr(handlers, "is_mock", lambda name=None: False)  # 실제 진단 규칙
    client = TestClient(app)
    runtime.clock = lambda: DEPLOYED
    body = {
        "service": "shop-api",
        "version": "v2",
        "previous_version": "v1",
        "previous_at": V1_AT.isoformat(),
        "applies_to": "shop_payment_config",
        "change_summary": "결제 설정 v2 (잘못된 배포 시연)",
    }
    recorded = client.post("/api/infra/demo-deploy", json=body, headers=ADMIN).json()["data"]
    runtime.clock = lambda: NOW
    runtime.shop.state.update(active=False, failing=False, degraded=True, payment_version="v2")
    return client, recorded


def _a2a(client: TestClient, a2a_body, skill: str, payload: dict, **kw) -> dict:
    return client.post("/a2a", json=a2a_body(skill, payload, **kw)).json()


def test_demo_deploy_records_a_baseline_that_is_not_a_suspect(bad_deploy) -> None:
    _, recorded = bad_deploy
    assert (recorded["version"], recorded["applies_to"]) == ("v2", "shop_payment_config")
    assert recorded["baseline"]["version"] == "v1" and recorded["baseline"]["components"] == []


def test_high_confidence_rollback_returns_the_shop_to_v1(
    bad_deploy, a2a_body, gate, runtime
) -> None:
    client, _ = bad_deploy
    diag = _a2a(client, a2a_body, "diagnose_incident", {"component": "payment"})
    suspects = diag["output"]["suspects"]
    assert [(s["version"], s["confidence"]) for s in suspects] == [("v2", "high")]

    fake = gate("executed")  # 등급은 safety가 정한다(기준표 그대로 B)
    out = _a2a(client, a2a_body, "rollback_deployment", ROLLBACK)
    assert out["status"] == "completed", out
    assert fake.calls[0]["action"] == "rollback_deployment"
    assert fake.calls[0]["params"]["low_confidence"] == 0
    assert fake.calls[0]["params"]["to_version"] == "v1"
    result = out["output"]
    assert result["payment_version"] == {"before": "v2", "after": "v1"}
    assert result["post_check_ok"] is True
    assert runtime.shop.state["payment_version"] == "v1"
    assert runtime.shop.released == 0  # 장애 스위치는 건드리지 않는다
    assert "fault_released" not in result


def test_nothing_happens_before_the_gate_executes(bad_deploy, a2a_body, gate, runtime) -> None:
    client, _ = bad_deploy
    gate("held")
    out = _a2a(client, a2a_body, "rollback_deployment", ROLLBACK)
    assert out["status"] == "input-required"
    assert runtime.shop.state["payment_version"] == "v2" and runtime.shop.payment_rollbacks == 0


def test_rollback_is_idempotent(bad_deploy, a2a_body, gate, runtime) -> None:
    client, _ = bad_deploy
    fake = gate("executed")
    _a2a(client, a2a_body, "rollback_deployment", ROLLBACK)
    again = _a2a(client, a2a_body, "rollback_deployment", ROLLBACK)  # 같은 사건: 처음 결과
    assert again["output"]["payment_version"] == {"before": "v2", "after": "v1"}
    assert len(fake.calls) == 1 and runtime.shop.payment_rollbacks == 1
    other = _a2a(client, a2a_body, "rollback_deployment", ROLLBACK, context_id="ctx_inc_0002")
    # 다른 사건이 같은 배포를 되돌리려 해도 이미 v1이면 할 일이 없다(관문에도 올리지 않는다)
    assert (other["status"], other["outcome"]) == ("completed", "not_needed")
    assert other["output"]["running_version"] == "v1"
    assert len(fake.calls) == 1 and runtime.shop.payment_rollbacks == 1


def test_post_check_needs_v2_before_and_v1_after(
    bad_deploy, a2a_body, gate, runtime, monkeypatch
) -> None:
    client, _ = bad_deploy
    gate("executed")

    async def someone_else_first(to_version: str) -> dict:
        runtime.shop.state["payment_version"] = to_version
        return {"before": "v1", "version": "v1", "changed": False}

    monkeypatch.setattr(runtime.shop, "rollback_payment_version", someone_else_first)
    out = _a2a(client, a2a_body, "rollback_deployment", ROLLBACK)["output"]
    assert out["post_check_ok"] is False


def test_release_fault_does_not_touch_a_bad_deploy(bad_deploy, a2a_body, gate, runtime) -> None:
    client, _ = bad_deploy
    fake = gate("executed")
    past = {"component": "payment", "past_case_id": "card_past", "similarity": 0.92}
    out = _a2a(client, a2a_body, "release_fault", past)
    assert (out["status"], out["outcome"]) == ("completed", "not_needed")
    assert fake.calls == [] and runtime.shop.state["payment_version"] == "v2"


def test_a_manual_reset_takes_the_bad_deploy_off_the_suspects(bad_deploy, a2a_body) -> None:
    client, _ = bad_deploy
    reset = client.post(
        "/api/infra/demo-deploy/reset",
        json={"service": "shop-api", "running_version": "v1"},
        headers=ADMIN,
    ).json()["data"]
    assert [r["version"] for r in reset["rolled_back"]] == ["v2"]
    diag = _a2a(client, a2a_body, "diagnose_incident", {"component": "payment"})
    assert diag["output"]["status"] == "undetermined"


def test_demo_endpoints_are_admin_only(bad_deploy) -> None:
    client, _ = bad_deploy
    infra = {"Authorization": "Bearer mock:infra"}
    assert client.post("/api/infra/demo-deploy", json={}, headers=infra).status_code == 403
    reset = {"service": "shop-api", "running_version": "v1"}
    assert client.post("/api/infra/demo-deploy/reset", json=reset, headers=infra).status_code == 403
