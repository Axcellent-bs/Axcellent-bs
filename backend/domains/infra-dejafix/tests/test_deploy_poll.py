"""배포 이력 미리 받기: 연결된 도구 이력 → DeploymentHistory → 진단 후보.

받기가 실패하거나 보관함이 깨지면 이전 이력을 그대로 두고 진단을 이어 간다.
"""

from __future__ import annotations

import asyncio
import json
import time
from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from ax_agent_template import tool_connections as tc
from ax_agent_template.tool_connections import ToolConnectionError
from infra_agent import deploy_poll, deploy_tools, main
from infra_agent.deploy_poll import poll_once
from infra_agent.deploy_tools import RunRef, ToolRun
from infra_agent.deployments import DeploymentHistory
from infra_agent.diagnosis import diagnose

NOW = datetime(2026, 10, 21, 5, 10, tzinfo=UTC)  # conftest 시계와 같다


class FakeTool:
    """`DeployTool` 대역. `runs`를 돌려주거나 `error`를 던진다."""

    tool = "github_actions"

    def __init__(self, service: str = "shop-api", runs: list[ToolRun] | None = None) -> None:
        self.service = service
        self.runs = runs or []
        self.error: Exception | None = None
        self.calls = 0

    async def recent(self, limit: int = 20) -> list[ToolRun]:
        self.calls += 1
        if self.error is not None:
            raise self.error
        return list(self.runs)

    async def redeploy(self, version: str) -> RunRef:
        raise AssertionError("이력 받기는 다시 배포하지 않는다")

    def describe(self, version: str) -> str:
        return f"가짜 도구에서 {version}으로 다시 실행"


def _run(run_id: str, version: str, minutes_ago: int, *, ok: bool = True) -> ToolRun:
    return ToolRun(
        version=version,
        at=NOW - timedelta(minutes=minutes_ago),
        ok=ok,
        actor="alice",
        url=f"https://github.com/acme/shop/actions/runs/{run_id}",
        run_id=run_id,
    )


def _versions(history: DeploymentHistory, service: str = "shop-api") -> list[str]:
    rows = sorted(
        (d for d in history.deployments if d.service == service), key=lambda d: d.deployed_at
    )
    return [d.version for d in rows]


def test_poll_replaces_demo_rows_for_connected_service() -> None:
    history = DeploymentHistory.load()
    other = history.record(
        service="support-api",
        version="v1.0.0",
        components=("api",),
        change_summary="연결 없는 서비스",
        at=NOW - timedelta(days=1),
    )
    tool = FakeTool(
        runs=[_run("12", "v3.0.1", 5), _run("11", "v3.0.0", 60), _run("13", "v3.0.2", 1, ok=False)]
    )
    asyncio.run(poll_once(history, [tool]))
    # 성공한 실행만 배포로, 시연 이력(v2.4.x)은 빠진다
    assert _versions(history) == ["v3.0.0", "v3.0.1"]
    latest = history.running("shop-api")
    assert latest is not None
    assert (latest.deploy_id, latest.source, latest.is_rollback) == (
        "shop-api:12",
        "github_actions",
        False,
    )
    # 연결 없는 서비스는 그대로
    assert other in history.deployments


def test_poll_keeps_rolled_back_mark_by_run_id() -> None:
    history = DeploymentHistory.load()
    tool = FakeTool(runs=[_run("12", "v3.0.1", 5), _run("11", "v3.0.0", 60)])
    asyncio.run(poll_once(history, [tool]))
    target = history.find("shop-api", "v3.0.1")
    assert target is not None
    history.mark_rolled_back(target)
    # 롤백으로 v3.0.0을 다시 배포했다 (도구가 실행 id를 아직 모름)
    history.record_redeploy("shop-api", "v3.0.0", at=NOW, run_id=None)
    tool.runs = [_run("14", "v3.0.0", -1), *tool.runs]
    asyncio.run(poll_once(history, [tool]))
    assert "shop-api:12" in history.rolled_back  # 다시 받아도 되돌림 표시가 남는다
    redo = next(d for d in history.deployments if d.deploy_id == "shop-api:14")
    assert redo.is_rollback is True
    running = history.running("shop-api")
    assert running is not None and running.version == "v3.0.0"


def test_poll_labels_our_redeploy_by_run_id() -> None:
    history = DeploymentHistory.load()
    # GitHub는 디스패치 입력을 목록에 주지 않아 커밋 앞자리로 보인다: 우리가 남긴 버전으로 바꾼다
    history.record_redeploy("shop-api", "v3.0.0", at=NOW, run_id="20")
    tool = FakeTool(runs=[_run("20", "abc1234", -2), _run("11", "v3.0.0", 60)])
    asyncio.run(poll_once(history, [tool]))
    redo = next(d for d in history.deployments if d.deploy_id == "shop-api:20")
    assert (redo.version, redo.is_rollback) == ("v3.0.0", True)
    # 롤백으로 띄운 실행은 다음 진단의 원인 후보가 아니다
    diag = diagnose(history, "payment", NOW + timedelta(minutes=5), mock=False)
    assert all(d.deploy_id != "shop-api:20" for d in diag.candidates)


def test_poll_keeps_previous_rows_on_error() -> None:
    history = DeploymentHistory.load()
    tool = FakeTool(runs=[_run("12", "v3.0.1", 5)])
    asyncio.run(poll_once(history, [tool]))
    before = list(history.deployments)
    tool.error = ToolConnectionError("unauthorized", "GET .../runs → 401")
    asyncio.run(poll_once(history, [tool]))
    tool.error = RuntimeError("뜻밖의 오류")
    asyncio.run(poll_once(history, [tool]))
    assert history.deployments == before and tool.calls == 3


def test_poll_with_broken_vault_keeps_rows(tmp_path, monkeypatch) -> None:
    history = DeploymentHistory.load()
    asyncio.run(poll_once(history, [FakeTool(runs=[_run("12", "v3.0.1", 5)])]))
    before = list(history.deployments)

    vault = tmp_path / "tool_connections.json"
    vault.write_text("{깨진 파일", encoding="utf-8")
    monkeypatch.setenv(tc.FILE_ENV, str(vault))
    tc._cache = None
    asyncio.run(poll_once(history, deploy_tools.all_tools()))
    assert history.deployments == before

    vault.unlink()  # 사라져도 같다
    tc._cache = None
    asyncio.run(poll_once(history, deploy_tools.all_tools()))
    assert history.deployments == before
    diag = diagnose(history, "payment", NOW, mock=False)
    assert [s.version for s in diag.output.suspects] == ["v3.0.1"]


def test_diagnose_uses_polled_rows(a2a_body, runtime) -> None:
    # 시연 이력의 v2.4.1(8분 전) 대신 도구가 알려 준 v3.0.1(3분 전)이 원인 후보다
    asyncio.run(
        poll_once(
            runtime.history, [FakeTool(runs=[_run("12", "v3.0.1", 3), _run("11", "v3.0.0", 600)])]
        )
    )
    diag = diagnose(runtime.history, "payment", NOW, mock=False)
    assert [(s.version, s.confidence) for s in diag.output.suspects] == [("v3.0.1", "high")]
    out = (
        TestClient(main.app)
        .post("/a2a", json=a2a_body("diagnose_incident", {"component": "payment"}))
        .json()
    )
    assert [s["version"] for s in out["output"]["suspects"]] == ["v3.0.1"]
    assert out["evidence"]["ref_ids"] == ["deploy:shop-api:12"]


def test_interval_default_and_floor() -> None:
    assert deploy_poll.interval_seconds({}) == 120
    assert deploy_poll.interval_seconds({deploy_poll.POLL_ENV: "1"}) == deploy_poll.MIN_SECONDS
    assert deploy_poll.interval_seconds({deploy_poll.POLL_ENV: "300"}) == 300


def test_poll_really_starts_with_server(runtime, monkeypatch: pytest.MonkeyPatch) -> None:
    tool = FakeTool(runs=[_run("12", "v3.0.1", 3)])
    monkeypatch.setattr(main, "is_mock", lambda name=None: False)
    monkeypatch.setattr(deploy_tools, "all_tools", lambda: [tool])
    with TestClient(main.app):
        deadline = time.monotonic() + 5
        while tool.calls == 0 and time.monotonic() < deadline:
            time.sleep(0.02)
        assert tool.calls >= 1
    assert _versions(runtime.history) == ["v3.0.1"]


def test_poll_does_not_start_in_mock_mode(runtime, monkeypatch: pytest.MonkeyPatch) -> None:
    tool = FakeTool(runs=[_run("12", "v3.0.1", 3)])
    monkeypatch.setattr(deploy_tools, "all_tools", lambda: [tool])
    with TestClient(main.app):
        time.sleep(0.1)
    assert tool.calls == 0
    assert "v2.4.1" in _versions(runtime.history)


def test_connected_service_hides_demo_rows_before_first_poll(
    tmp_path, monkeypatch, a2a_body, gate, runtime
) -> None:
    rows = [
        {
            "id": "github_app-1",
            "kind": "github_app",
            "name": "앱",
            "config": {"app_id": "1", "installation_id": "2"},
            "secrets": {},
        },
        {
            "id": "github_actions-1",
            "kind": "github_actions",
            "name": "쇼핑몰 배포",
            "config": {
                "app": "github_app-1",
                "repo": "acme/shop",
                "workflow": "deploy.yml",
                "service": "shop-api",
            },
            "secrets": {},
        },
    ]
    vault = tmp_path / "tool_connections.json"
    vault.write_text(json.dumps(rows), encoding="utf-8")
    monkeypatch.setenv(tc.FILE_ENV, str(vault))
    tc._cache = None
    # 연결은 됐지만 아직 받지 못했다: shop-api 시연 이력(v2.4.x)은 후보·롤백 대상이 아니다
    diag = diagnose(runtime.history, "payment", NOW, mock=False)
    assert diag.output.status == "undetermined"
    assert runtime.history.find("shop-api", "v2.4.1") is None
    fake = gate("executed")
    out = (
        TestClient(main.app)
        .post(
            "/a2a",
            json=a2a_body("rollback_deployment", {"service": "shop-api", "from_version": "v2.4.1"}),
        )
        .json()
    )
    assert out["outcome"] == "invalid_input" and fake.calls == []


def test_known_run_id_matches_only_that_run() -> None:
    history = DeploymentHistory.load()
    history.record_redeploy("shop-api", "v3.0.0", at=NOW, run_id="20")
    # 사람이 우리 디스패치 직전에 같은 버전을 배포했다(19). 우리 실행은 20
    tool = FakeTool(
        runs=[_run("19", "v3.0.0", 1), _run("20", "abc1234", -2), _run("11", "v3.0.0", 60)]
    )
    asyncio.run(poll_once(history, [tool]))
    marks = {d.deploy_id: d.is_rollback for d in history.deployments if d.service == "shop-api"}
    assert marks == {"shop-api:11": False, "shop-api:19": False, "shop-api:20": True}


def test_unrelated_connected_service_is_not_a_payment_suspect() -> None:
    history = DeploymentHistory.load()
    # 시연 이력에서 payment는 shop-api의 구성요소다.
    # 구성요소를 모르는 다른 서비스의 배포는 후보가 아니다
    asyncio.run(
        poll_once(history, [FakeTool(service="support-api", runs=[_run("5", "v9.0.0", 2)])])
    )
    diag = diagnose(history, "payment", NOW, mock=False)
    assert all(d.service == "shop-api" for d in diag.candidates)
    assert [s.version for s in diag.output.suspects] == ["v2.4.1"]
    # shop-api 도구 실행은 shop-api 구성요소(payment·api)를 단다
    asyncio.run(poll_once(history, [FakeTool(runs=[_run("12", "v3.0.1", 3)])]))
    shop = next(d for d in history.deployments if d.deploy_id == "shop-api:12")
    assert set(shop.components) == {"payment", "api"}
