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
# 도구 이력(받아 둔 v3.0.1 → 직전 v3.0.0)의 롤백
TOOL_ROLLBACK = {"service": "shop-api", "from_version": "v3.0.1"}
SECRET = "ghs_should_never_show"


class FakeTool:
    tool = "github_actions"

    def __init__(self, service: str = "shop-api", runs: list[ToolRun] | None = None) -> None:
        self.service = service
        self.runs = runs or []
        self.redeployed: list[str] = []
        self.components: tuple[str, ...] = ()
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


@pytest.fixture
def polled(tool: FakeTool, runtime) -> FakeTool:
    """도구 이력을 한 번 받아 둔 상태 (시연 이력 대신 v3.0.0·v3.0.1)."""
    tool.runs = [_run("12", "v3.0.1", 3), _run("11", "v3.0.0", 600)]
    asyncio.run(poll_once(runtime.history, [tool]))
    return tool


def _rollback(a2a_body, payload: dict | None = None) -> dict:
    return (
        TestClient(app)
        .post("/a2a", json=a2a_body("rollback_deployment", payload or ROLLBACK))
        .json()
    )


def test_executed_rollback_redeploys_previous_good(a2a_body, gate, runtime, polled) -> None:
    tool = polled
    gate("executed")
    out = _rollback(a2a_body, TOOL_ROLLBACK)
    assert out["status"] == "completed", out
    assert tool.redeployed == ["v3.0.0"]
    result = out["output"]
    assert result["rolled_back_to"] == "v3.0.0"
    assert result["redeploy"] == {
        "tool": "github_actions",
        "run_id": "777",
        "url": "https://github.com/acme/shop/actions/runs/777",
    }
    assert result["fault_released"] is False
    assert runtime.shop.released == 0  # 장애 해제(시연 경로)는 부르지 않는다
    target = runtime.history.find("shop-api", "v3.0.1")
    assert target is not None and target.deploy_id in runtime.history.rolled_back
    assert [(n.version, n.run_id) for n in runtime.history.redeploys] == [("v3.0.0", "777")]


def test_held_rollback_does_not_redeploy(a2a_body, gate, runtime, polled) -> None:
    tool = polled
    gate("held")
    out = _rollback(a2a_body, TOOL_ROLLBACK)
    assert out["status"] == "input-required"
    assert tool.redeployed == []
    assert runtime.history.redeploys == []


def test_redeploy_failure_returns_handoff_steps(a2a_body, gate, runtime, polled) -> None:
    tool = polled
    gate("executed")
    tool.error = ToolConnectionError("forbidden", "POST .../dispatches → 403")
    out = _rollback(a2a_body, TOOL_ROLLBACK)
    assert out["status"] == "failed"
    assert out["outcome"] == "redeploy_failed"
    result = out["output"]
    assert result["handoff_steps"] == [tool.describe("v3.0.0")]
    assert "403" in result["message"]
    assert result["retryable"] is False  # 다시 부르면 배포가 두 번 돌 수 있다
    assert "rolled_back_to" not in result
    assert runtime.shop.released == 0  # 다른 길(장애 해제)로 넘어가지 않는다
    assert runtime.history.rolled_back == set()


def test_redeploy_unexpected_error_hides_message(a2a_body, gate, runtime, polled) -> None:
    tool = polled
    gate("executed")
    tool.error = RuntimeError(f"boom {SECRET}")
    out = _rollback(a2a_body, TOOL_ROLLBACK)
    assert out["outcome"] == "redeploy_failed"
    assert out["output"]["retryable"] is False
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


def test_idempotent_retry_does_not_redeploy_twice(a2a_body, gate, runtime, polled) -> None:
    tool = polled
    fake = gate("executed")
    first = _rollback(a2a_body, TOOL_ROLLBACK)
    second = _rollback(a2a_body, TOOL_ROLLBACK)
    assert first["output"] == second["output"]
    assert tool.redeployed == ["v3.0.0"]
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


def test_demo_row_is_not_dispatched_to_a_connected_tool(a2a_body, gate, runtime, tool) -> None:
    # 도구는 연결됐지만 이력을 아직 받지 못했다:
    # 시연 이력 버전(v2.4.0)을 실제 파이프라인에 보내지 않는다
    gate("executed")
    out = _rollback(a2a_body)
    assert out["outcome"] == "redeploy_failed"
    assert "아직 받지 못해" in out["output"]["message"]
    assert out["output"]["handoff_steps"] == [tool.describe("v2.4.0")]
    assert tool.redeployed == [] and runtime.shop.released == 0


def test_rows_from_another_tool_are_not_dispatched(a2a_body, gate, runtime, tool) -> None:
    # Jenkins 이력을 받아 둔 뒤 연결을 GitHub Actions로 바꿨다
    tool.runs = [_run("12", "v3.0.1", 3), _run("11", "v3.0.0", 600)]
    runtime.history.replace_service("shop-api", tool.runs, tool="jenkins")
    gate("executed")
    out = _rollback(a2a_body, TOOL_ROLLBACK)
    assert out["outcome"] == "redeploy_failed"
    assert tool.redeployed == []


def test_sha_fallback_version_is_not_dispatched(a2a_body, gate, runtime, tool) -> None:
    sha_run = ToolRun(
        version="abc1234",
        at=NOW - timedelta(minutes=600),
        ok=True,
        actor=None,
        url=None,
        run_id="11",
        version_from_sha=True,
    )
    tool.runs = [_run("12", "v3.0.1", 3), sha_run]
    asyncio.run(poll_once(runtime.history, [tool]))
    gate("executed")
    out = _rollback(a2a_body, TOOL_ROLLBACK)
    assert out["outcome"] == "redeploy_failed"
    assert "abc1234" in out["output"]["message"]
    assert tool.redeployed == []  # 커밋 앞자리를 inputs.version으로 보내지 않는다


def test_redeploy_timeout_handoff_says_check_if_already_started(
    a2a_body, gate, runtime, polled
) -> None:
    tool = polled
    gate("executed")
    tool.error = ToolConnectionError("timeout", "POST .../dispatches 연결 실패 (ReadTimeout)")
    out = _rollback(a2a_body, TOOL_ROLLBACK)
    assert out["outcome"] == "redeploy_failed"
    steps = out["output"]["handoff_steps"]
    assert "이미 시작됐는지 확인" in steps[0]
    assert steps[-1] == tool.describe("v3.0.0")


def test_redeploy_non_timeout_handoff_has_no_check_line(a2a_body, gate, runtime, polled) -> None:
    tool = polled
    gate("executed")
    tool.error = ToolConnectionError("forbidden", "POST .../dispatches → 403")
    out = _rollback(a2a_body, TOOL_ROLLBACK)
    assert out["output"]["handoff_steps"] == [tool.describe("v3.0.0")]


def test_send_timeout_becomes_timeout_code() -> None:
    import httpx

    from infra_agent.deploy_tools import _send

    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ReadTimeout("slow", request=request)

    async def run() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as c:
            await _send(c, "POST", "http://x/y", "배포 도구")

    with pytest.raises(ToolConnectionError) as e:
        asyncio.run(run())
    assert e.value.code == "timeout"


def test_poll_run_forever_reads_vault_off_the_event_loop(runtime, monkeypatch) -> None:
    import threading

    from infra_agent import deploy_poll

    seen: list[int] = []

    def fake_all_tools() -> list:
        seen.append(threading.get_ident())
        return []

    async def fake_sleep(_: float) -> None:
        raise asyncio.CancelledError

    monkeypatch.setattr(deploy_tools, "all_tools", fake_all_tools)
    monkeypatch.setattr(deploy_poll.asyncio, "sleep", fake_sleep)

    async def run() -> int:
        main_id = threading.get_ident()
        with pytest.raises(asyncio.CancelledError):
            await deploy_poll.run_forever(runtime.history)
        return main_id

    main_id = asyncio.run(run())
    assert seen and seen[0] != main_id


def test_rollback_reads_tool_vault_off_the_event_loop(
    a2a_body, gate, runtime, polled, monkeypatch
) -> None:
    on_loop: list[bool] = []

    def recording_tool_for(service: str) -> FakeTool | None:
        try:
            asyncio.get_running_loop()
            on_loop.append(True)
        except RuntimeError:
            on_loop.append(False)
        return polled if service == polled.service else None

    monkeypatch.setattr(deploy_tools, "tool_for", recording_tool_for)
    gate("executed")
    out = _rollback(a2a_body, TOOL_ROLLBACK)
    assert out["status"] == "completed", out
    assert on_loop == [False]
