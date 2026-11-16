"""롤백에서 다시 배포: 관문(execute)을 통과하면 배포 도구로 직전 정상 버전을 다시 실행한다.

실패하면 장애 해제 같은 다른 길로 넘어가지 않고 `redeploy_failed` + 사람에게 넘길 처리 순서를 낸다.
배포 도구가 없는 서비스는 지금처럼 시연 경로(장애 주입 해제)를 쓴다.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from ax_agent_template.tool_connections import ToolConnectionError
from infra_agent import deploy_tools
from infra_agent.deploy_poll import poll_once
from infra_agent.deploy_tools import RunRef, ToolRun
from infra_agent.main import app

NOW = datetime(2026, 10, 21, 5, 10, tzinfo=UTC)  # conftest 시계와 같다
ROLLBACK = {"service": "shop-api", "from_version": "v2.4.1", "release_fault_injection": True}
SECRET = "ghs_should_never_show"


class FakeTool:
    tool = "github_actions"

    def __init__(self, service: str = "shop-api", runs: list[ToolRun] | None = None) -> None:
        self.service = service
        self.runs = runs or []
        self.redeployed: list[str] = []
        self.error: Exception | None = None

    async def recent(self, limit: int = 20) -> list[ToolRun]:
        return list(self.runs)

    async def redeploy(self, version: str) -> RunRef:
        self.redeployed.append(version)
        if self.error is not None:
            raise self.error
        return RunRef(
            tool=self.tool, run_id="777", url="https://github.com/acme/shop/actions/runs/777"
        )

    def describe(self, version: str) -> str:
        return (
            f"GitHub Actions에서 acme/shop의 deploy.yml 워크플로를 version={version}으로 다시 실행"
        )


@pytest.fixture
def tool(monkeypatch: pytest.MonkeyPatch) -> FakeTool:
    fake = FakeTool()
    monkeypatch.setattr(
        deploy_tools, "tool_for", lambda service: fake if service == fake.service else None
    )
    return fake


def _rollback(a2a_body, payload: dict | None = None) -> dict:
    return (
        TestClient(app)
        .post("/a2a", json=a2a_body("rollback_deployment", payload or ROLLBACK))
        .json()
    )


def test_executed_rollback_redeploys_previous_good(a2a_body, gate, runtime, tool) -> None:
    gate("executed")
    out = _rollback(a2a_body)
    assert out["status"] == "completed", out
    assert tool.redeployed == ["v2.4.0"]
    result = out["output"]
    assert result["rolled_back_to"] == "v2.4.0"
    assert result["redeploy"] == {
        "tool": "github_actions",
        "run_id": "777",
        "url": "https://github.com/acme/shop/actions/runs/777",
    }
    assert result["fault_released"] is False
    assert runtime.shop.released == 0  # 장애 해제(시연 경로)는 부르지 않는다
    target = runtime.history.find("shop-api", "v2.4.1")
    assert target is not None and target.deploy_id in runtime.history.rolled_back
    assert [(n.version, n.run_id) for n in runtime.history.redeploys] == [("v2.4.0", "777")]


def test_held_rollback_does_not_redeploy(a2a_body, gate, runtime, tool) -> None:
    gate("held")
    out = _rollback(a2a_body)
    assert out["status"] == "input-required"
    assert tool.redeployed == []
    assert runtime.history.redeploys == []


def test_redeploy_failure_returns_handoff_steps(a2a_body, gate, runtime, tool) -> None:
    gate("executed")
    tool.error = ToolConnectionError("forbidden", "POST .../dispatches → 403")
    out = _rollback(a2a_body)
    assert out["status"] == "failed"
    assert out["outcome"] == "redeploy_failed"
    result = out["output"]
    assert result["handoff_steps"] == [tool.describe("v2.4.0")]
    assert "403" in result["message"]
    assert "rolled_back_to" not in result
    assert runtime.shop.released == 0  # 다른 길(장애 해제)로 넘어가지 않는다
    assert runtime.history.rolled_back == set()


def test_redeploy_unexpected_error_hides_message(a2a_body, gate, runtime, tool) -> None:
    gate("executed")
    tool.error = RuntimeError(f"boom {SECRET}")
    out = _rollback(a2a_body)
    assert out["outcome"] == "redeploy_failed"
    assert SECRET not in str(out)
    assert runtime.shop.released == 0


def test_service_without_tool_keeps_fault_release_path(
    a2a_body, gate, runtime, monkeypatch
) -> None:
    monkeypatch.setattr(deploy_tools, "tool_for", lambda service: None)
    gate("executed")
    out = _rollback(a2a_body)
    assert out["status"] == "completed"
    assert "redeploy" not in out["output"]
    assert out["output"]["fault_released"] is True
    assert runtime.shop.released == 1


def test_idempotent_retry_does_not_redeploy_twice(a2a_body, gate, runtime, tool) -> None:
    fake = gate("executed")
    first = _rollback(a2a_body)
    second = _rollback(a2a_body)
    assert first["output"] == second["output"]
    assert tool.redeployed == ["v2.4.0"]
    assert len(fake.calls) == 1


def _run(run_id: str, version: str, minutes_ago: int) -> ToolRun:
    return ToolRun(
        version=version,
        at=NOW - timedelta(minutes=minutes_ago),
        ok=True,
        actor="alice",
        url=None,
        run_id=run_id,
    )


def test_polled_history_rolls_back_to_previous_tool_run(a2a_body, gate, runtime, tool) -> None:
    tool.runs = [_run("12", "v3.0.1", 3), _run("11", "v3.0.0", 600)]
    asyncio.run(poll_once(runtime.history, [tool]))
    fake = gate("executed")
    out = _rollback(a2a_body, {"service": "shop-api", "from_version": "v3.0.1"})
    assert out["status"] == "completed", out
    assert fake.calls[0]["params"]["to_version"] == "v3.0.0"
    assert tool.redeployed == ["v3.0.0"]
    # 다음 이력 받기에서 우리가 띄운 실행(777)은 롤백으로 표시되고 원인 후보가 아니다
    tool.runs = [_run("777", "abc1234", -1), *tool.runs]
    asyncio.run(poll_once(runtime.history, [tool]))
    redo = next(d for d in runtime.history.deployments if d.deploy_id == "shop-api:777")
    assert (redo.version, redo.is_rollback) == ("v3.0.0", True)
    assert "shop-api:12" in runtime.history.rolled_back


def test_tool_history_without_connection_does_not_fall_back(
    a2a_body, gate, runtime, tool, monkeypatch
) -> None:
    tool.runs = [_run("12", "v3.0.1", 3), _run("11", "v3.0.0", 600)]
    asyncio.run(poll_once(runtime.history, [tool]))
    monkeypatch.setattr(deploy_tools, "tool_for", lambda service: None)  # 보관함이 깨짐
    gate("executed")
    out = _rollback(a2a_body, {"service": "shop-api", "from_version": "v3.0.1"})
    assert out["outcome"] == "redeploy_failed"
    assert out["output"]["handoff_steps"] == ["배포 도구에서 shop-api를 v3.0.0으로 다시 실행"]
    assert runtime.shop.released == 0
