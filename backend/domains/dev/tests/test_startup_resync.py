"""켤 때 기획과 맞추기가 정말 돈다 (11/23). 템플릿 앱은 lifespan을 써서 on_startup이 돌지 않는다.

기획 main처럼 lifespan을 감싸 뒷일로 띄우고, 기획이 뜰 때까지 기다린 뒤 맞춘다(wait_peer)."""

from __future__ import annotations

import time

import pytest
from fastapi.testclient import TestClient


def test_resync_peer_is_scheduled_when_the_server_starts(monkeypatch: pytest.MonkeyPatch) -> None:
    import dev_agent.main as dev_main
    from dev_agent.runtime import get_service

    calls: list[bool] = []

    async def fake_resync(wait_peer: bool = False) -> None:
        calls.append(wait_peer)

    monkeypatch.setattr(get_service(), "resync_peer", fake_resync)
    with TestClient(dev_main.app) as client:
        for _ in range(50):
            client.get("/health")
            if calls:
                break
            time.sleep(0.02)
    assert calls == [True]


def test_lifespan_starts_and_stops_pr_sync_and_inventory(monkeypatch: pytest.MonkeyPatch) -> None:
    """PR 맞추기 루프·연결 대장도 lifespan으로 켜고 끌 때 멈춘다 (on_startup은 돌지 않는다)."""
    import dev_agent.main as dev_main
    from dev_agent.runtime import get_service
    from dev_agent.vcs import inventory

    svc = get_service()
    calls: list[str] = []

    async def fake_resync(wait_peer: bool = False) -> None:
        return None

    monkeypatch.setattr(svc, "resync_peer", fake_resync)
    monkeypatch.setattr(dev_main, "_runs_loops", lambda: True)
    monkeypatch.setattr(svc, "start_pr_sync", lambda: calls.append("pr_sync"))
    monkeypatch.setattr(svc, "stop_pr_sync", lambda: calls.append("stop_pr_sync"))
    monkeypatch.setattr(inventory, "start_daily", lambda: calls.append("daily"))
    monkeypatch.setattr(inventory, "stop_daily", lambda: calls.append("stop_daily"))
    with TestClient(dev_main.app) as client:
        client.get("/health")
        assert calls == ["pr_sync", "daily"]
    assert calls == ["pr_sync", "daily", "stop_pr_sync", "stop_daily"]


def test_lifespan_does_not_start_loops_in_mock_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    """목 모드(시험 전용)에서는 주기 루프를 켜지 않는다."""
    import dev_agent.main as dev_main
    from dev_agent.runtime import get_service

    svc = get_service()

    async def fake_resync(wait_peer: bool = False) -> None:
        return None

    monkeypatch.setattr(svc, "resync_peer", fake_resync)
    with TestClient(dev_main.app) as client:
        client.get("/health")
        assert svc._pr_sync is None


def test_pr_sync_starts_once_and_stop_lets_it_start_again(monkeypatch: pytest.MonkeyPatch) -> None:
    import asyncio

    from dev_agent.service import DevService
    from dev_agent.vcs import inventory

    async def no_check() -> list:
        return []

    monkeypatch.setattr(inventory, "check_all", no_check)

    async def run() -> None:
        svc = DevService()
        svc.start_pr_sync()
        first = svc._pr_sync
        svc.start_pr_sync()  # 이미 돌고 있으면 그대로
        assert first is not None and svc._pr_sync is first
        svc.stop_pr_sync()
        await asyncio.sleep(0)
        assert svc._pr_sync is None and first.cancelled()
        svc.stop_pr_sync()  # 두 번 꺼도 괜찮다

        inventory.start_daily()
        task = inventory._task
        inventory.start_daily()  # 이미 돌고 있으면 그대로
        assert task is not None and inventory._task is task
        inventory.stop_daily()
        await asyncio.sleep(0)
        assert inventory._task is None and task.cancelled()

    asyncio.run(run())
