"""서비스별 최근 배포 읽기 (`GET /api/infra/deployments/latest`, dashboard-api·운영자 토큰만).

도구 연결 화면이 "최근 배포 <버전> · <시각>"을 그린다. 받아 둔 배포 이력(`DeploymentHistory`)에서
서비스마다 되돌리지 않은 가장 최근 배포만 준다.
이력이 없으면 그 서비스는 목록에 없다(지어내지 않는다).
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from infra_agent.deploy_tools import ToolRun
from infra_agent.main import app

PATH = "/api/infra/deployments/latest"
DASHBOARD = {"Authorization": "Bearer dashboard-test-token"}
ADMIN = {"Authorization": "Bearer admin-test-token"}
AGENT = {"Authorization": "Bearer orchestrator-test-token"}
NOW = datetime(2026, 10, 21, 5, 10, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _tokens(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "AX_AGENT_TOKENS",
        "dashboard-api=dashboard-test-token,admin=admin-test-token,"
        "orchestrator=orchestrator-test-token",
    )


def _get(headers: dict[str, str]) -> list[dict]:
    res = TestClient(app).get(PATH, headers=headers)
    assert res.status_code == 200, res.text
    return res.json()["data"]


def _run(run_id: str, version: str, minutes_ago: int, *, ok: bool = True) -> ToolRun:
    return ToolRun(
        version=version,
        at=NOW - timedelta(minutes=minutes_ago),
        ok=ok,
        actor="alice",
        url=f"https://github.com/acme/shop/actions/runs/{run_id}",
        run_id=run_id,
    )


def test_latest_demo_deploy_per_service() -> None:
    assert _get(DASHBOARD) == [
        {
            "service": "shop-api",
            "version": "v2.4.1",
            "deployed_at": "2026-10-21T05:02:00Z",
            "source": "demo",
            "is_rollback": False,
            "version_from_sha": False,
        }
    ]


def test_admin_can_read_too() -> None:
    assert [r["version"] for r in _get(ADMIN)] == ["v2.4.1"]


@pytest.mark.parametrize(
    ("headers", "status"),
    [({}, 401), ({"Authorization": "Bearer nobody"}, 401), (AGENT, 403)],
)
def test_only_dashboard_or_admin_can_read(headers, status) -> None:
    assert TestClient(app).get(PATH, headers=headers).status_code == status


def test_connected_service_shows_tool_run_not_demo(runtime) -> None:
    runtime.history.connected = lambda: {"shop-api"}
    # 연결 직후 첫 받기 전: 시연 이력은 쓰지 않으니 기록 없음
    assert _get(DASHBOARD) == []
    runtime.history.replace_service(
        "shop-api",
        [_run("12", "v3.0.1", 5), _run("11", "v3.0.0", 60), _run("13", "v3.0.2", 1, ok=False)],
        tool="github_actions",
    )
    [row] = _get(DASHBOARD)
    # 실패한 실행(v3.0.2)은 배포가 아니다
    assert (row["service"], row["version"], row["source"]) == (
        "shop-api",
        "v3.0.1",
        "github_actions",
    )
    assert row["deployed_at"] == "2026-10-21T05:05:00Z"


def test_rolled_back_deploy_is_not_latest(runtime) -> None:
    target = runtime.history.find("shop-api", "v2.4.1")
    runtime.history.mark_rolled_back(target)
    assert [r["version"] for r in _get(DASHBOARD)] == ["v2.4.0"]


def test_services_sorted_by_name(runtime) -> None:
    runtime.history.record(
        service="api-gw",
        version="v1.0.0",
        components=("gw",),
        change_summary="다른 서비스",
        at=NOW,
    )
    assert [r["service"] for r in _get(DASHBOARD)] == ["api-gw", "shop-api"]
