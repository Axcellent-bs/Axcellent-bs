"""배포 이력·배포 감시를 회사마다 (11/26 회사 분리, 묶음 2).

프로세스 하나의 배포 이력(`DeploymentHistory`)을 모든 회사가 같이 봐서 누비투어 "도구 연결 › 배포"에
캣북스 배포 도구 이력이 보였다. 이제 캣북스는 `Runtime.history`(예전 그대로), 다른 회사는
`Runtime.history_for(회사)` 칸을 쓰고, 배포 감시는 회사마다 그 회사 보관함의 도구로 돈다.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from ax_agent_template import company_jobs, company_scope
from infra_agent import deploy_poll, deploy_tools
from infra_agent.deploy_tools import RunRef, ToolRun
from infra_agent.main import app
from infra_agent.runtime import Runtime

PATH = "/api/infra/deployments/latest"
DASHBOARD = {"Authorization": "Bearer dashboard-test-token"}
NUBI = "c-cf2f0d"
NOW = datetime(2026, 10, 21, 5, 10, tzinfo=UTC)


@pytest.fixture(autouse=True)
def _tokens(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(
        "AX_AGENT_TOKENS", "dashboard-api=dashboard-test-token,admin=admin-test-token"
    )


def _run(run_id: str, version: str, minutes_ago: int) -> ToolRun:
    return ToolRun(
        version=version,
        at=NOW - timedelta(minutes=minutes_ago),
        ok=True,
        actor="alice",
        url=f"https://github.com/acme/shop/actions/runs/{run_id}",
        run_id=run_id,
    )


class FakeTool:
    tool = "github_actions"

    def __init__(self, service: str, runs: list[ToolRun], *, fail: bool = False) -> None:
        self.service = service
        self.runs = runs
        self.fail = fail
        self.components: tuple[str, ...] = ()

    async def recent(self, limit: int = 20) -> list[ToolRun]:
        if self.fail:
            raise RuntimeError("도구가 안 됨")
        return list(self.runs)

    async def redeploy(self, version: str) -> RunRef:
        raise AssertionError("받기는 다시 배포하지 않는다")

    def describe(self, version: str) -> str:
        return version


def _get(headers: dict[str, str]) -> list[dict]:
    res = TestClient(app).get(PATH, headers=headers)
    assert res.status_code == 200, res.text
    return res.json()["data"]


def _catbooks_deploy(runtime: Runtime) -> None:
    runtime.history.connected = lambda: {"shop-api"}
    runtime.history.replace_service("shop-api", [_run("1", "v3.0.1", 5)], tool="github_actions")


def test_nubi_screen_does_not_see_catbooks_deploys(runtime: Runtime) -> None:
    _catbooks_deploy(runtime)
    nubi = _get({**DASHBOARD, "X-AX-Company": NUBI})
    assert nubi == []
    # 캣북스는 그대로: 회사 머리 없음(예전) · 캣북스 · 별칭 shop-demo
    for headers in (DASHBOARD, {**DASHBOARD, "X-AX-Company": "catbooks"},
                    {**DASHBOARD, "X-AX-Company": "shop-demo"}):  # fmt: skip
        assert [r["version"] for r in _get(headers)] == ["v3.0.1"]


def test_bad_company_header_does_not_fall_back_to_catbooks(runtime: Runtime) -> None:
    _catbooks_deploy(runtime)
    assert _get({**DASHBOARD, "X-AX-Company": "../catbooks"}) == []


def test_company_header_needs_a_verified_reader_token(runtime: Runtime) -> None:
    res = TestClient(app).get(PATH, headers={"X-AX-Company": NUBI})
    assert res.status_code == 401


def test_nubi_sees_only_its_own_deploys(runtime: Runtime) -> None:
    _catbooks_deploy(runtime)
    nubi = runtime.history_for(NUBI)
    nubi.connected = lambda: {"tour-api"}
    nubi.replace_service("tour-api", [_run("9", "v1.0.0", 3)], tool="github_actions")
    assert [r["service"] for r in _get({**DASHBOARD, "X-AX-Company": NUBI})] == ["tour-api"]
    assert [r["service"] for r in _get(DASHBOARD)] == ["shop-api"]


def test_history_for_follows_request_company(runtime: Runtime) -> None:
    assert runtime.history_for() is runtime.history  # 요청 밖 = 캣북스(예전 그대로)
    with company_scope.using(NUBI):
        nubi = runtime.history_for()
    assert nubi is not runtime.history and nubi is runtime.history_for(NUBI)
    # 새 회사는 패키지 시연 이력·캣북스 이력을 물려받지 않는다
    assert nubi.deployments == []
    with company_scope.using("catbooks"):
        assert runtime.history_for() is runtime.history


def test_poll_runs_per_company_and_one_failure_does_not_stop_others(
    runtime: Runtime, monkeypatch: pytest.MonkeyPatch
) -> None:
    other = "c-f6c188"
    monkeypatch.setattr(company_jobs, "job_companies", lambda: ["catbooks", NUBI, other])
    tools = {
        None: [FakeTool("shop-api", [_run("1", "v3.0.1", 5)])],
        NUBI: [FakeTool("tour-api", [_run("2", "v1.0.0", 4)], fail=True)],
        other: [FakeTool("course-api", [_run("3", "v2.0.0", 3)])],
    }

    def all_tools() -> list:
        return tools[company_scope.active()]

    def broken_company(cid: str):  # 한 회사는 보관함 읽기부터 실패
        raise RuntimeError("보관함이 깨짐")

    monkeypatch.setattr(deploy_tools, "all_tools", all_tools)
    asyncio.run(deploy_poll.poll_all(runtime))

    cat = {d.service for d in runtime.history.deployments if d.source}
    assert cat == {"shop-api"}
    assert [d for d in runtime.history_for(NUBI).deployments if d.source] == []  # 실패: 비어 있음
    assert {d.service for d in runtime.history_for(other).deployments if d.source} == {"course-api"}
    # 회사끼리 섞이지 않는다
    assert all(d.service != "course-api" for d in runtime.history.deployments)

    # 보관함 읽기가 실패한 회사가 있어도 다음 회사는 돈다
    tools[NUBI] = broken_company  # type: ignore[assignment]

    def all_tools_breaking() -> list:
        found = tools[company_scope.active()]
        return found(company_scope.active()) if callable(found) else found

    monkeypatch.setattr(deploy_tools, "all_tools", all_tools_breaking)
    tools[other] = [FakeTool("course-api", [_run("4", "v2.1.0", 1)])]
    asyncio.run(deploy_poll.poll_all(runtime))
    versions = {d.version for d in runtime.history_for(other).deployments if d.source}
    assert "v2.1.0" in versions
