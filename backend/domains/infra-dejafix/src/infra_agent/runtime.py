"""프로세스 안의 상태 하나: 배포 이력, 사건별 진단·롤백 기록, 바깥 호출 클라이언트.

v0은 DB 없이 프로세스 메모리에만 둔다(재시작하면 배포 이력 파일 상태로 돌아간다).
테스트는 `set_runtime`으로 시계·대기·클라이언트를 바꾼다.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from infra_agent.clients import (
    DataClient,
    ShopClient,
    default_data,
    default_shop,
    orchestrator_opened_at,
)
from infra_agent.deployments import DeploymentHistory
from infra_agent.diagnosis import Diagnosis
from infra_agent.pr_suspects import A2ADev, DevClient, NoDev, PrSuspect


def _now() -> datetime:
    return datetime.now(UTC)


class KeyLocks:
    """키마다 하나씩만 들어가는 잠금. 그 키를 기다리는 요청이 없으면 잠금을 지운다(쌓이지 않음).

    프로세스 안에서만 막는다 (infra 서버는 워커 하나로 돈다. 여러 프로세스로 띄우면 막지 못한다).
    """

    def __init__(self) -> None:
        self._locks: dict[str, tuple[asyncio.Lock, int]] = {}

    @asynccontextmanager
    async def hold(self, key: str) -> AsyncIterator[None]:
        lock, users = self._locks.get(key) or (asyncio.Lock(), 0)
        self._locks[key] = (lock, users + 1)
        try:
            async with lock:
                yield
        finally:
            lock, users = self._locks[key]
            if users <= 1:
                del self._locks[key]
            else:
                self._locks[key] = (lock, users - 1)

    def empty(self) -> bool:
        return not self._locks


@dataclass
class Runtime:
    history: DeploymentHistory = field(default_factory=DeploymentHistory.load)
    shop: ShopClient = field(default_factory=default_shop)
    data: DataClient = field(default_factory=default_data)
    clock: Callable[[], datetime] = _now
    sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep
    # 사건이 열린 시각 (총괄 사건 기록). 롤백 요청에 incident_opened_at이 없을 때 채운다
    incident_opened_at: Callable[[str], Awaitable[str | None]] = orchestrator_opened_at
    diagnoses: dict[str, Diagnosis] = field(default_factory=dict)  # context_id → 마지막 진단
    dev: DevClient = field(default_factory=lambda: default_dev())
    # context_id → (service, version) → 원인 PR. 진단이 넣고 되돌리기가 읽는다
    pr_suspects: dict[str, dict[tuple[str, str], PrSuspect]] = field(default_factory=dict)
    rollbacks: dict[str, dict[str, Any]] = field(default_factory=dict)  # idempotency_key → 결과
    # 같은 롤백(멱등 키)을 한 번에 하나씩만 처리한다: 동시에 온 같은 요청이 두 번 배포하지 않게
    rollback_locks: KeyLocks = field(default_factory=KeyLocks)
    # 배포 실행(deploy_version): 멱등 키 → 도구를 부른 결과. 같은 키는 다시 띄우지 않는다
    deploys: dict[str, Any] = field(default_factory=dict)
    deploy_locks: KeyLocks = field(default_factory=KeyLocks)
    releases: dict[str, dict[str, Any]] = field(
        default_factory=dict
    )  # 과거 처리 방식 다시 하기 결과

    def executed_rollbacks(self, context_id: str) -> int:
        return sum(
            1
            for r in self.rollbacks.values()
            if r["context_id"] == context_id and r["status"] == "executed"
        )


def default_dev() -> DevClient:
    """목 모드는 실제 레포 후보를 찾지 않는다(기존 배포 기록 진단만). 실제 모드는 A2A."""
    from ax_agent_template.config import is_mock

    return NoDev() if is_mock("infra") else A2ADev()


_runtime: Runtime | None = None


def get_runtime() -> Runtime:
    global _runtime
    if _runtime is None:
        _runtime = Runtime()
    return _runtime


def set_runtime(runtime: Runtime) -> Runtime:
    global _runtime
    _runtime = runtime
    return runtime
