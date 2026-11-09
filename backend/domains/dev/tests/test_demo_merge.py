"""시연 준비: 장애 직전 배포로 나간 머지 PR 기록 (`POST /api/planning/demo/incident-merge`).

`scripts/demo_deploy.sh`가 infra 시연 배포와 같이 부른다. 이 PR이 있어야 롤백 교차검증
(`verify_rollback_target`)이 새 배포 버전을 "배포 직전 머지"로 보고 승인한다.
"""

from __future__ import annotations

from datetime import timedelta

import pytest
from fastapi.testclient import TestClient

from ax_contracts.schemas.planning import VerifyRollbackTargetOutput
from dev_agent.main import app

pytestmark = pytest.mark.role("dev")

PATH = "/api/planning/demo/incident-merge"
ADMIN = {"Authorization": "Bearer mock:admin"}
HUMAN = {"Authorization": "Bearer human:tester"}


def _verify(a2a_body, version: str, opened_at: str) -> dict:
    """safety `Gate._cross_verify` 형식. planning이 모르는 사건이라 시각은 params 값을 쓴다."""
    payload = {
        "execution_id": "exe_demo_1",
        "action": "rollback_deployment",
        "requester": "infra",
        "params": {
            "service": "shop-api",
            "from_version": version,
            "to_version": "v2.4.1",
            "incident_opened_at": opened_at,
        },
        "computed_amount": None,
        "threshold_version": "2026-10-23",
    }
    body = a2a_body(
        "verify_rollback_target", payload, token="mock:safety", context_id="ctx_inc_0099"
    )
    return TestClient(app).post("/a2a", json=body).json()


def test_admin_records_merged_pr_for_deploy(service) -> None:
    before = len(service.state.prs)
    res = TestClient(app).post(PATH, json={"deployed_version": "shop-api v2.4.2"}, headers=ADMIN)
    assert res.status_code == 200
    pr = res.json()["data"]
    assert pr["merged"] is True
    assert pr["deployed_version"] == "shop-api v2.4.2"
    assert pr["changed_paths"] == ["shop-api/payment/config.py"]
    assert len(service.state.prs) == before + 1
    assert pr["number"] == max(p.number for p in service.state.prs)


def test_rollback_verify_approves_recorded_deploy(service, a2a_body) -> None:
    TestClient(app).post(PATH, json={"deployed_version": "shop-api v2.4.2"}, headers=ADMIN)
    merged_at = service.state.prs[-1].merged_at
    opened = (merged_at + timedelta(minutes=5)).isoformat()
    result = _verify(a2a_body, "v2.4.2", opened)
    assert (result["status"], result["outcome"]) == ("completed", None)
    out = VerifyRollbackTargetOutput.model_validate(result["output"])
    assert (out.approved, out.reason_code) == (True, "deploy_before_incident")
    assert service.state.prs[-1].number in [p.pr_number for p in out.related_prs]


def test_without_record_new_version_is_rejected(service, a2a_body) -> None:
    # 기록이 없으면 새 버전 배포 시각을 모른다 → 판정하지 않음(보류) 또는 거절
    opened = (service.clock() + timedelta(minutes=5)).isoformat()
    result = _verify(a2a_body, "v2.4.2", opened)
    approved = result["status"] == "completed" and result["output"].get("approved") is True
    assert not approved


@pytest.mark.parametrize(("headers", "status"), [(HUMAN, 403), ({"Authorization": ""}, 401)])
def test_only_admin_can_record(headers, status) -> None:
    res = TestClient(app).post(PATH, json={"deployed_version": "shop-api v2.4.2"}, headers=headers)
    assert res.status_code == status


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"deployed_version": "v2.4.2"},
        {"deployed_version": "shop-api v2.4.2", "changed_paths": []},
    ],
)
def test_rejects_bad_body(body) -> None:
    res = TestClient(app).post(PATH, json=body, headers=ADMIN)
    assert res.status_code == 400
