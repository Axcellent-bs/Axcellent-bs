"""프로세스 안의 상태 하나: 배포 이력, 사건별 진단·롤백 기록, 바깥 호출 클라이언트.

v0은 DB 없이 프로세스 메모리에만 둔다(재시작하면 배포 이력 파일 상태로 돌아간다).
테스트는 `set_runtime`으로 시계·대기·클라이언트를 바꾼다.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
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
