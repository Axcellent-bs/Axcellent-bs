"""같은 되돌리기가 동시에 두 번 와도 다시 배포는 한 번만 한다.

관문(execute)은 같은 멱등 키에 같은 "executed" 판정을 두 호출 모두에 돌려준다. 그래서 핸들러가
같은 키를 한 번에 하나씩만 처리하고, 기다린 요청은 앞 요청의 결과를 그대로 돌려줘야 한다.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from ax_agent_template.tool_connections import ToolConnectionError
from ax_contracts.schemas import AgentRequest, AgentResult
from infra_agent import deploy_tools, handlers
from infra_agent.deploy_poll import poll_once
from infra_agent.deploy_tools import RunRef, ToolRun

NOW = datetime(2026, 10, 21, 5, 10, tzinfo=UTC)  # conftest 시계와 같다
TOOL_ROLLBACK = {"service": "shop-api", "from_version": "v3.0.1"}


class SlowTool:
    """다시 배포가 끝나기 전에 다른 요청이 끼어들 수 있게 기다리는 가짜 배포 도구."""

    tool = "github_actions"
    service = "shop-api"
    components: tuple[str, ...] = ()

    def __init__(self) -> None:
        self.runs = [_run("12", "v3.0.1", 3), _run("11", "v3.0.0", 600)]
        self.redeployed: list[str] = []
        self.error: Exception | None = None

    async def recent(self, limit: int = 20) -> list[ToolRun]:
        return list(self.runs)

    async def redeploy(self, version: str) -> RunRef:
        self.redeployed.append(version)
        for _ in range(5):  # 다른 요청에 차례를 넘긴다
            await asyncio.sleep(0.01)
        if self.error is not None:
            raise self.error
        return RunRef(tool=self.tool, run_id="777", url="https://example.invalid/runs/777")

    def describe(self, version: str) -> str:
        return f"deploy.yml을 version={version}으로 다시 실행"


def _run(run_id: str, version: str, minutes_ago: int) -> ToolRun:
    return ToolRun(
        version=version,
        at=NOW - timedelta(minutes=minutes_ago),
        ok=True,
        actor="alice",
        url=None,
        run_id=run_id,
    )


@pytest.fixture
def tool(monkeypatch: pytest.MonkeyPatch, runtime) -> SlowTool:
    fake = SlowTool()
    monkeypatch.setattr(
        deploy_tools, "tool_for", lambda service: fake if service == fake.service else None
    )
    asyncio.run(poll_once(runtime.history, [fake]))
    return fake


def _request(a2a_body) -> AgentRequest:
    return AgentRequest.model_validate(a2a_body("rollback_deployment", TOOL_ROLLBACK))


def _twice(a2a_body) -> list[AgentResult]:
    async def run() -> list[AgentResult]:
        return list(
            await asyncio.gather(
                handlers.rollback_deployment(_request(a2a_body)),
                handlers.rollback_deployment(_request(a2a_body)),
            )
        )

    return asyncio.run(run())


def test_concurrent_identical_rollback_redeploys_once(a2a_body, gate, runtime, tool) -> None:
    fake = gate("executed")
    first, second = _twice(a2a_body)
    assert tool.redeployed == ["v3.0.0"]
    assert len(fake.calls) == 1
    assert first.status == second.status == "completed"
    assert first.output == second.output
    assert [(n.version, n.run_id) for n in runtime.history.redeploys] == [("v3.0.0", "777")]
    assert runtime.rollback_locks.empty()


def test_concurrent_duplicate_gets_first_failure_and_later_retry_runs(
    a2a_body, gate, runtime, tool
) -> None:
    fake = gate("executed")
    tool.error = ToolConnectionError("timeout", "POST .../dispatches 시간 초과")
    first, second = _twice(a2a_body)
    # 기다린 요청은 앞 요청의 실패를 그대로 받는다 (도구에서 이미 시작됐을 수 있어 또 부르지 않는다)
    assert tool.redeployed == ["v3.0.0"]
    assert first.outcome == second.outcome == "redeploy_failed"
    assert first.output == second.output
    assert runtime.rollback_locks.empty()

    # 실패가 키를 영영 막지 않는다: 나중에 다시 보낸 요청은 다시 시도한다
    tool.error = None
    later = asyncio.run(handlers.rollback_deployment(_request(a2a_body)))
    assert later.status == "completed", later
    assert tool.redeployed == ["v3.0.0", "v3.0.0"]
    assert len(fake.calls) == 2
    assert runtime.rollback_locks.empty()
