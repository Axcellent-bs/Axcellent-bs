"""배포 실행 `deploy_version`: 관문을 거쳐 연결된 배포 도구로 한 번만 띄운다.

- 배포 도구가 없으면 관문도 도구도 부르지 않고 `no_deploy_tool` 실패.
- 같은 요청(request_id)을 다시 보내도 도구는 한 번만 부른다 (실패도 다시 하지 않는다).
- 보류면 도구를 부르지 않고 input-required. 승인 뒤 같은 요청을 다시 보내면 그때 띄운다.
- `deploy_run_status`는 도구의 최근 실행에서 상태를 읽는다 (못 찾으면 unknown).
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient

from ax_agent_template.tool_connections import ToolConnectionError
from ax_contracts.schemas import ExecuteDecision
from infra_agent import deploy_run, deploy_tools
from infra_agent.deploy_tools import RunRef, ToolRun
from infra_agent.main import app

DEPLOY = {
    "service": "shop-api",
    "version": "v2.5.0",
    "request_id": "DEP-3.1a2b3c4d",
    "env": "prod",
    "requester": "dev",
    "approved_by": "예진",
}
SECRET = "ghs_should_never_show"


class FakeTool:
    tool = "github_actions"

    def __init__(self, service: str = "shop-api") -> None:
        self.service = service
        self.components: tuple[str, ...] = ()
        self.dispatched: list[str] = []
        self.runs: list[ToolRun] = []
        self.error: Exception | None = None
        self.wait: asyncio.Event | None = None
        self.recent_error: Exception | None = None

    async def recent(self, limit: int = 20) -> list[ToolRun]:
        if self.recent_error is not None:
            raise self.recent_error
        return list(self.runs)

    async def redeploy(self, version: str) -> RunRef:
        self.dispatched.append(version)
        if self.wait is not None:
            await self.wait.wait()
        if self.error is not None:
            raise self.error
        return RunRef(
            tool=self.tool, run_id="901", url="https://github.com/acme/shop/actions/runs/901"
        )

    def describe(self, version: str) -> str:
        return (
            f"GitHub Actions에서 acme/shop의 deploy.yml 워크플로를 version={version}으로 다시 실행"
        )


class Gate:
    def __init__(self, *decisions: ExecuteDecision) -> None:
        self.decisions = list(decisions)
        self.calls: list[dict[str, Any]] = []

    async def __call__(self, action, params, context, evidence) -> ExecuteDecision:
        self.calls.append({"action": action, "params": params, "key": context.idempotency_key})
        return self.decisions.pop(0) if len(self.decisions) > 1 else self.decisions[0]


def executed() -> ExecuteDecision:
    return ExecuteDecision(execution_id="exe_d1", grade="A", status="executed")


def held() -> ExecuteDecision:
    return ExecuteDecision(
        execution_id="exe_d2",
        grade="C",
        status="held",
        hold_reason="over_threshold",
        review_id="rev_d1",
    )


@pytest.fixture
def tool(monkeypatch: pytest.MonkeyPatch) -> FakeTool:
    fake = FakeTool()
    monkeypatch.setattr(
        deploy_tools, "tool_for", lambda service: fake if service == fake.service else None
    )
    return fake


@pytest.fixture
def gate(monkeypatch: pytest.MonkeyPatch):
    def install(*decisions: ExecuteDecision) -> Gate:
        fake = Gate(*decisions)
        monkeypatch.setattr(deploy_run, "execute", fake)
        return fake

    return install


def _call(a2a_body, skill: str = "deploy_version", payload: dict | None = None) -> dict:
    body = a2a_body(
        skill,
        DEPLOY if payload is None else payload,
        token="mock:dev",
        path=["dev"],
        context_id="ctx_dep_0003",
    )
    return TestClient(app).post("/a2a", json=body).json()


def test_dispatches_once_through_gate(a2a_body, tool, gate) -> None:
    g = gate(executed())
    out = _call(a2a_body)
    assert out["status"] == "completed", out
    assert tool.dispatched == ["v2.5.0"]
    assert out["output"]["tool"] == "github_actions"
    assert out["output"]["run_id"] == "901"
    assert out["output"]["url"].endswith("/runs/901")
    assert out["output"]["status"] == "dispatched"
    assert [c["action"] for c in g.calls] == ["deploy_version"]
    assert g.calls[0]["key"] == "deploy:DEP-3.1a2b3c4d:deploy_version:shop-api:v2.5.0"
    assert g.calls[0]["params"]["approved_by"] == "예진"
    assert g.calls[0]["params"]["tool"] == "github_actions"
    assert "운영 배포 도구로 실행" in out["evidence"]["data_summary"]
    assert "shop-api v2.5.0" in out["evidence"]["data_summary"]


@pytest.mark.parametrize("who", [None, "", "  "])
def test_without_human_approval_nothing_runs(a2a_body, tool, gate, who) -> None:
    g = gate(executed())
    out = _call(a2a_body, payload={**DEPLOY, "approved_by": who})
    assert (out["status"], out["outcome"]) == ("failed", "approval_required")
    assert g.calls == [] and tool.dispatched == []


def test_other_version_is_a_different_request(a2a_body, tool, gate) -> None:
    gate(executed())
    _call(a2a_body)
    _call(a2a_body, payload={**DEPLOY, "version": "v2.5.1"})
    assert tool.dispatched == ["v2.5.0", "v2.5.1"]


def test_retry_does_not_dispatch_again(a2a_body, tool, gate) -> None:
    gate(executed())
    first = _call(a2a_body)
    second = _call(a2a_body)
    assert tool.dispatched == ["v2.5.0"]
    assert second["output"]["run_id"] == first["output"]["run_id"]


def test_failed_dispatch_is_not_retried(a2a_body, tool, gate) -> None:
    gate(executed())
    tool.error = ToolConnectionError("timeout", "POST .../dispatches 연결 실패 (ReadTimeout)")
    out = _call(a2a_body)
    assert (out["status"], out["outcome"]) == ("failed", "deploy_failed")
    assert out["output"]["retryable"] is False
    assert out["output"]["handoff_steps"][0] == deploy_run.CHECK_FIRST
    again = _call(a2a_body)
    assert again["outcome"] == "deploy_failed"
    assert tool.dispatched == ["v2.5.0"]  # 두 번째 요청은 도구를 부르지 않는다


def test_unexpected_error_hides_message(a2a_body, tool, gate) -> None:
    gate(executed())
    tool.error = RuntimeError(f"boom {SECRET}")
    out = _call(a2a_body)
    assert out["outcome"] == "deploy_failed"
    assert SECRET not in str(out)


def test_no_tool_fails_without_gate(a2a_body, monkeypatch, gate) -> None:
    monkeypatch.setattr(deploy_tools, "tool_for", lambda service: None)
    g = gate(executed())
    out = _call(a2a_body)
    assert (out["status"], out["outcome"]) == ("failed", "no_deploy_tool")
    assert "도구 연결" in out["output"]["message"]
    assert g.calls == []


def test_held_does_not_dispatch_then_resumes(a2a_body, tool, gate) -> None:
    gate(held(), executed())
    out = _call(a2a_body)
    assert (out["status"], out["outcome"]) == ("input-required", "held")
    assert out["output"]["review_id"] == "rev_d1"
    assert tool.dispatched == []
    # 사람이 안전 확인에서 승인한 뒤 같은 요청 → 그때 한 번 띄운다
    out = _call(a2a_body)
    assert out["status"] == "completed"
    assert tool.dispatched == ["v2.5.0"]


def test_rejected_and_unavailable_do_not_dispatch(a2a_body, tool, gate) -> None:
    gate(ExecuteDecision(execution_id="exe_unavailable", grade="C", status="rejected"))
    out = _call(a2a_body)
    assert (out["status"], out["outcome"]) == ("failed", "safety_unavailable")
    gate(ExecuteDecision(execution_id="exe_r", grade="C", status="rejected"))
    out = _call(a2a_body)
    assert out["outcome"] == "rejected"
    assert tool.dispatched == []


def test_concurrent_same_request_dispatches_once(a2a_body, tool, gate, runtime) -> None:
    from ax_contracts.schemas import AgentRequest

    gate(executed())

    async def run() -> list[Any]:
        tool.wait = asyncio.Event()
        body = a2a_body("deploy_version", DEPLOY, token="mock:dev", path=["dev"])
        req = AgentRequest.model_validate(body)
        first = asyncio.create_task(deploy_run.deploy_version(req))
        second = asyncio.create_task(deploy_run.deploy_version(req))
        await asyncio.sleep(0.01)
        tool.wait.set()
        return await asyncio.gather(first, second)

    results = asyncio.run(run())
    assert tool.dispatched == ["v2.5.0"]
    assert {r.output["run_id"] for r in results} == {"901"}
    assert runtime.deploy_locks.empty()


def test_other_requesters_are_refused(a2a_body, tool, gate) -> None:
    body = a2a_body("deploy_version", DEPLOY, token="mock:orchestrator")
    out = TestClient(app).post("/a2a", json=body).json()
    assert (out["status"], out["outcome"]) == ("rejected", "FORBIDDEN_SKILL")
    assert tool.dispatched == []


def _run(run_id: str, *, ok: bool, finished: bool | None) -> ToolRun:
    return ToolRun(
        version="v2.5.0",
        at=datetime(2026, 10, 21, 5, 0, tzinfo=UTC),
        ok=ok,
        actor=None,
        url=f"https://github.com/acme/shop/actions/runs/{run_id}",
        run_id=run_id,
        finished=finished,
    )


@pytest.mark.parametrize(
    ("ok", "finished", "state"),
    [(False, False, "running"), (True, True, "succeeded"), (False, True, "failed")],
)
def test_run_status_reads_tool(a2a_body, tool, ok, finished, state) -> None:
    tool.runs = [_run("900", ok=True, finished=True), _run("901", ok=ok, finished=finished)]
    out = _call(a2a_body, "deploy_run_status", {"service": "shop-api", "run_id": "901"})
    assert out["status"] == "completed", out
    assert out["output"]["status"] == state
    assert out["output"]["url"].endswith("/runs/901")


def test_run_status_unknown_and_no_tool(a2a_body, tool, monkeypatch) -> None:
    out = _call(a2a_body, "deploy_run_status", {"service": "shop-api", "run_id": "999"})
    assert out["output"]["status"] == "unknown"
    assert out["output"]["url"] is None
    monkeypatch.setattr(deploy_tools, "tool_for", lambda service: None)
    out = _call(a2a_body, "deploy_run_status", {"service": "shop-api", "run_id": "901"})
    assert out["outcome"] == "no_deploy_tool"


def test_run_status_tool_error(a2a_body, tool) -> None:
    tool.recent_error = ToolConnectionError("forbidden", "GET .../runs → 403")
    out = _call(a2a_body, "deploy_run_status", {"service": "shop-api", "run_id": "901"})
    assert (out["status"], out["outcome"]) == ("failed", "tool_unavailable")


class QueueTool(FakeTool):
    tool = "jenkins"

    def __init__(self, number: str | None) -> None:
        super().__init__()
        self.number = number

    async def resolve_run(self, run_id: str) -> str | None:
        return self.number


def test_run_status_resolves_jenkins_queue(a2a_body, monkeypatch) -> None:
    fake = QueueTool("57")
    fake.runs = [_run("57", ok=True, finished=True)]
    monkeypatch.setattr(deploy_tools, "tool_for", lambda service: fake)
    out = _call(a2a_body, "deploy_run_status", {"service": "shop-api", "run_id": "queue-12"})
    assert (out["output"]["run_id"], out["output"]["status"]) == ("57", "succeeded")
    assert out["output"]["resolving"] is False
    fake.number = None  # 아직 대기열: 도구에서 확인 중
    out = _call(a2a_body, "deploy_run_status", {"service": "shop-api", "run_id": "queue-12"})
    assert (out["output"]["run_id"], out["output"]["status"]) == ("queue-12", "unknown")
    assert out["output"]["resolving"] is True


def test_tool_services_lists_connected(a2a_body, monkeypatch) -> None:
    monkeypatch.setattr(deploy_tools, "all_tools", lambda: [FakeTool("shop-api"), QueueTool(None)])
    out = _call(a2a_body, "deploy_tool_services", {})
    assert out["status"] == "completed"
    assert out["output"]["services"] == [
        {"service": "shop-api", "tool": "github_actions"},
        {"service": "shop-api", "tool": "jenkins"},
    ]
    monkeypatch.setattr(deploy_tools, "all_tools", lambda: [])
    assert _call(a2a_body, "deploy_tool_services", {})["output"]["services"] == []
