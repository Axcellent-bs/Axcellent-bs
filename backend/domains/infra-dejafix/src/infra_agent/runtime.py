"""프로세스 안의 상태 하나: 배포 이력, 사건별 진단·롤백 기록, 바깥 호출 클라이언트.

배포 이력은 회사마다 따로다(11/26 회사 분리): 캣북스는 `history`(예전 그대로), 다른 회사는
`history_for(회사)`가 그 회사 칸을 준다(처음엔 비어 있고, 그 회사 보관함의 배포 도구에서만 채운다).

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

from ax_agent_template import company_scope
from infra_agent.clients import (
    DataClient,
    ShopClient,
    default_data,
    default_shop,
    orchestrator_opened_at,
)
from infra_agent.deployments import DeploymentHistory, connected_services
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


def company_history(company: str) -> DeploymentHistory:
    """캣북스가 아닌 회사의 빈 배포 이력.

    패키지 시연 이력·캣북스 파일은 쓰지 않는다(지어내지 않는다).

    "연결된 서비스"는 그 회사 보관함으로 본다(부르는 쪽 요청 회사와 상관없이)."""

    def connected() -> set[str]:
        with company_scope.using(company):
            return connected_services()

    return DeploymentHistory([], connected=connected)


@dataclass
class Runtime:
    # 캣북스(설치 회사) 배포 이력. 다른 회사는 `company_histories`
    history: DeploymentHistory = field(default_factory=DeploymentHistory.load)
    company_histories: dict[str, DeploymentHistory] = field(default_factory=dict)
    shop: ShopClient = field(default_factory=default_shop)
    data: DataClient = field(default_factory=default_data)
    clock: Callable[[], datetime] = _now
    sleep: Callable[[float], Awaitable[Any]] = asyncio.sleep
    # 사건이 열린 시각 (총괄 사건 기록). 롤백 요청에 incident_opened_at이 없을 때 채운다
    incident_opened_at: Callable[[str], Awaitable[str | None]] = orchestrator_opened_at
    diagnoses: dict[str, Diagnosis] = field(default_factory=dict)  # context_id → 마지막 진단
    # context_id → 진단을 저장한 요청의 회사 칸(`save_diagnosis`가 적는다). 없으면 캣북스
    diagnosis_scopes: dict[str, str] = field(default_factory=dict)
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

    def history_for(self, company: str | None = None) -> DeploymentHistory:
        """그 회사의 배포 이력: 준 값 → 지금 요청의 회사.

        캣북스·회사 없음(스스로 도는 캣북스 작업)은 `history`. 다른 회사는 그 회사 칸
        (없으면 빈 칸을 만든다). 다른 회사 이력은 섞지 않는다."""
        cid = company if company is not None else company_scope.active()
        if cid is None or company_scope.scope_id(cid) == company_scope.CATBOOKS:
            return self.history
        key = company_scope.scope_id(cid)
        found = self.company_histories.get(key)
        if found is None:
            found = self.company_histories[key] = company_history(key)
        return found

    def read_history(self) -> DeploymentHistory:
        """읽기 전용 새 읽기(배포 목록·대화 사실)가 쓰는 이력: 요청 회사 칸만.

        요청 회사가 없으면(`company_scope.active()`가 None) 캣북스 이력으로 떨어지지 않고
        빈 이력을 준다. 캣북스 기본값이 필요한 쓰기·롤백 경로는 `history_for()`를 그대로 쓴다."""
        if company_scope.active() is None:
            return DeploymentHistory([], connected=set)
        return self.history_for()

    def save_diagnosis(self, context_id: str, diag: Diagnosis) -> None:
        """진단을 저장하면서 지금 요청의 회사 칸을 같이 적는다(회사 없음은 캣북스 칸)."""
        self.diagnoses[context_id] = diag
        self.diagnosis_scopes[context_id] = company_scope.scope_id(company_scope.active())

    def _diagnosis_scope(self, context_id: str) -> str:
        return self.diagnosis_scopes.get(context_id, company_scope.CATBOOKS)

    def diagnosis_of(self, context_id: str) -> Diagnosis | None:
        """그 사건의 진단. 다른 회사 요청이 저장한 진단은 없는 것으로 본다."""
        diag = self.diagnoses.get(context_id)
        if diag is None:
            return None
        if self._diagnosis_scope(context_id) != company_scope.scope_id(company_scope.active()):
            return None
        return diag

    def diagnoses_in_scope(self) -> dict[str, Diagnosis]:
        """지금 요청 회사의 진단만(읽기용). 요청 회사가 없으면 아무것도 보지 못한다."""
        active = company_scope.active()
        if active is None:
            return {}
        mine = company_scope.scope_id(active)
        return {k: v for k, v in self.diagnoses.items() if self._diagnosis_scope(k) == mine}

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
