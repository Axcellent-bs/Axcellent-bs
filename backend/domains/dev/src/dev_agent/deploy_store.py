"""배포 기록·배포 후 확인 저장소.

기획·개발 상태(`State`)와 따로 둔다. PR은 요청 때 서비스 상태에서 읽는다.
- 목 모드(테스트 전용)만 시드를 넣는다: 머지된 PR #28(shop-api v2.4.1)을 dev 배포 DEP-1로 두고,
  시드 사건 ctx_inc_0001로 배포 후 확인 하나(suspect)를 미리 계산해 둔다.
  실제 모드는 빈 목록에서 시작한다 (시드를 실제 배포처럼 보이지 않게).
- `PLANNING_STATE_DIR`가 있으면 `deploy_state_dev.json`에 이어 쓴다(서비스 상태 `store.py`와
  같은 방식: 임시 파일에 쓰고 바꿔 끼움). 없으면 프로세스 메모리만.
  파일을 못 읽거나 다른 모드(mock·real)가 쓴 파일이면 처음 상태로 시작한다.
  저장에 실패해도 배포 API는 계속 동작한다 (로그만 남김).
- 배포 실행기(배포 도구 연결)가 없다. 요청·승인한 배포는 `approved`(실행 안 함)로 남는다.

상태를 바꾸는 단계는 서비스와 같은 템플릿 실행 관문을 거친다.
- `request_deploy` (B): 배포 요청. prod·보호 경로면 `requested`로 두고 사람 승인을 기다린다.
- `approve_deploy` (사람): 승인 버튼.
- `post_deploy_check` (read): 관문을 거치지 않는다. 결과만 쌓는다.
"""

from __future__ import annotations

import json
import logging
import os
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from ax_agent_template import config as template_config
from ax_contracts.schemas import Evidence, ExecuteDecision, RequestMeta
from ax_workflow.base import AGENT_NAME
from ax_workflow.clock import kst
from ax_workflow.core import GateCall, GateRefused
from ax_workflow.seed import initial_state
from ax_workflow.store import ENV_DIR
from dev_agent.deploy import (
    DEFAULT_WINDOW_MINUTES,
    NO_EXECUTOR_NOTE,
    NOT_DEPLOYED,
    DeployEnv,
    DeployError,
    Deployment,
    PostDeployCheck,
    Signal,
    build_deployment,
    deploy_params,
    evaluate_check,
)
from dev_agent.deploy_signals import (
    MockSignalSource,
    SignalSource,
    default_source,
    mock_signals,
)

log = logging.getLogger(__name__)
MOCK_NAME = "planning"  # 개발 서비스도 `PLANNING_MOCK`을 따른다 (ax_workflow.auth와 같음)
FILE_NAME = "deploy_state_dev.json"

if TYPE_CHECKING:
    from dev_agent.service import DevService

# 시드 이상 지표 (TH-5: 14:04부터 결제 실패율 4배)
SEED_ANOMALIES = [
    Signal(
        kind="anomaly",
        source="mock",
        ref="payment_fail_rate",
        title="payment_fail_rate 4.0배",
        at=kst("10-21 14:04"),
        severity="high",
    )
]


def _token() -> str:
    try:
        return template_config.agent_token(AGENT_NAME)
    except RuntimeError:
        return "unconfigured"  # 관문 쪽에서 거부된다 (fail-closed)


def _path() -> str | None:
    folder = (os.getenv(ENV_DIR) or "").strip()
    return os.path.join(folder, FILE_NAME) if folder else None


def _num(dep_id: str) -> int:
    tail = dep_id.rsplit("-", 1)[-1]
    return int(tail) if tail.isdigit() else 0


class DeployStore:
    def __init__(self, source: SignalSource | None = None, *, seed: bool | None = None):
        """`seed` None이면 목 모드일 때만 시드를 넣는다. 저장 파일이 있으면 그것을 읽는다."""
        self._source_override = source
        self._seed_flag = seed
        self._mock: SignalSource = MockSignalSource([])
        self.deployments: list[Deployment] = []
        self.checks: dict[str, list[PostDeployCheck]] = {}
        self._seq = 0
        if not self._load():
            self.reset()

    # ---- 시드·조회

    def _seeding(self) -> bool:
        if self._seed_flag is not None:
            return self._seed_flag
        return template_config.is_mock(MOCK_NAME)

    def reset(self) -> None:
        self.deployments = []
        self.checks = {}
        self._seq = 0
        if not self._seeding():
            self._mock = MockSignalSource([])
            self._save()
            return
        state = initial_state()
        self._mock = MockSignalSource(state.incidents, SEED_ANOMALIES)
        for pr in state.prs:
            if not pr.merged or not pr.merged_at:
                continue
            self._seq += 1
            dep = build_deployment(self._seq, [pr], "dev", pr.merged_at)
            # 목 시나리오(테스트 전용): 이 PR은 이미 dev에 나간 것으로 둔다
            dep = dep.model_copy(
                update={"status": "succeeded", "executed": True, "note": "목 시드 (테스트 전용)"}
            )
            self.deployments.append(dep)
            window_end = dep.at + timedelta(minutes=DEFAULT_WINDOW_MINUTES)
            self.checks[dep.id] = [
                evaluate_check(dep, mock_signals(state.incidents, SEED_ANOMALIES), at=window_end)
            ]
        self._save()

    # ---- 파일

    def _mode(self) -> str:
        return "mock" if self._seeding() else "real"

    def _save(self) -> None:
        """파일에 이어 쓴다. 실패해도 동작은 계속한다 (core._save와 같음)."""
        path = _path()
        if path is None:
            return
        try:
            self._write(path)
        except OSError as exc:
            log.warning("배포 기록 저장 실패 (%s): %s", path, exc)

    def _write(self, path: str) -> None:
        data = {
            "mode": self._mode(),
            "seq": self._seq,
            "deployments": [d.model_dump(mode="json") for d in self.deployments],
            "checks": {
                k: [c.model_dump(mode="json") for c in rows] for k, rows in self.checks.items()
            },
        }
        os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
        tmp = f"{path}.tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False)
        os.replace(tmp, path)

    def _load(self) -> bool:
        """저장한 기록을 읽는다. 없거나 못 읽으면 False (처음 상태로 시작)."""
        path = _path()
        if path is None or not os.path.exists(path):
            return False
        try:
            with open(path, encoding="utf-8") as f:
                data = json.load(f)
            if data.get("mode") != self._mode():
                return False  # 다른 모드의 기록(예: 목 시드)은 버린다
            deployments = [Deployment.model_validate(d) for d in data["deployments"]]
            checks = {
                str(k): [PostDeployCheck.model_validate(c) for c in rows]
                for k, rows in dict(data.get("checks") or {}).items()
            }
            seq = int(data.get("seq") or 0)
        except (OSError, ValueError, KeyError, TypeError):
            return False
        self.deployments, self.checks = deployments, checks
        self._seq = max([seq, *(_num(d.id) for d in deployments)])
        if self._seeding():  # 목 시드 사건 신호는 목 모드에서만
            self._mock = MockSignalSource(initial_state().incidents, SEED_ANOMALIES)
        return True

    @property
    def source(self) -> SignalSource:
        return self._source_override or default_source(self._mock)

    def get(self, dep_id: str) -> Deployment | None:
        return next((d for d in self.deployments if d.id == dep_id), None)

    def latest_check(self, dep_id: str) -> PostDeployCheck | None:
        rows = self.checks.get(dep_id) or []
        return rows[-1] if rows else None

    def prior_rollbacks(self, dep: Deployment) -> int:
        """같은 PR이 들어간 배포 중 이미 롤백된 수."""
        return sum(
            1
            for d in self.deployments
            if d.id != dep.id
            and d.status == "rolled_back"
            and set(d.pr_numbers) & set(dep.pr_numbers)
        )

    # ---- 관문

    async def _execute(
        self, svc: DevService, action: str, ctx: str, target: str, summary: str, params: dict
    ) -> ExecuteDecision:
        key = f"{ctx}:{action}:{target}"
        meta = RequestMeta(
            context_id=ctx,
            tenant_id=svc.tenant_id,
            token=_token(),
            deadline=datetime.now(UTC) + timedelta(seconds=30),
            idempotency_key=key,
        )
        evidence = Evidence(data_summary=summary, ref_ids=[target])
        try:
            decision = await svc._gate(action, {"target": target, **params}, meta, evidence)
        except Exception:
            decision = ExecuteDecision(execution_id="exe_unavailable", grade="C", status="rejected")
        svc.gate_calls.append(
            GateCall(action, key, decision.status, decision.grade, decision.execution_id)
        )
        if decision.status != "executed":
            raise GateRefused(action, key, decision)
        return decision

    # ---- 변경

    async def request(self, svc: DevService, pr_numbers: list[int], env: DeployEnv) -> Deployment:
        prs = []
        for n in pr_numbers:
            pr = next((p for p in svc.state.prs if p.number == n), None)
            if pr is None:
                raise DeployError("NOT_FOUND", f"PR #{n}가 없다")
            prs.append(pr)
        seq = self._seq + 1
        dep = build_deployment(seq, prs, env, svc.clock())
        await self._execute(
            svc,
            "request_deploy",
            f"ctx_dep_{seq:04d}",
            dep.id,
            f"{env} 배포 요청 · {', '.join(f'PR #{n}' for n in dep.pr_numbers)}",
            deploy_params(dep),
        )
        self._seq = max(self._seq, seq)
        self.deployments.append(dep)
        self._save()
        return dep

    async def approve(self, svc: DevService, dep_id: str, approved_by: str) -> Deployment:
        dep = self.get(dep_id)
        if dep is None:
            raise DeployError("NOT_FOUND", f"배포 {dep_id}가 없다")
        if dep.status != "requested":
            raise DeployError("NOT_REQUESTED", f"{dep_id}는 승인 대기가 아니다 ({dep.status})")
        await self._execute(
            svc,
            "approve_deploy",
            f"ctx_dep_{dep_id.split('-')[-1].zfill(4)}",
            dep.id,
            f"{approved_by} {dep.env} 배포 승인 (사람 클릭)",
            {"env": dep.env, "approved_by": approved_by, "reasons": dep.approval_reasons},
        )
        # 배포 실행기가 없다: 승인만 기록하고 배포됨(succeeded)으로 바꾸지 않는다
        updated = dep.model_copy(
            update={
                "status": "approved",
                "approved_by": approved_by,
                "at": svc.clock(),
                "executed": False,
                "note": NO_EXECUTOR_NOTE,
            }
        )
        self.deployments = [updated if d.id == dep_id else d for d in self.deployments]
        self._save()
        return updated

    async def check(
        self, svc: DevService, dep_id: str, window_minutes: int = DEFAULT_WINDOW_MINUTES
    ) -> PostDeployCheck:
        dep = self.get(dep_id)
        if dep is None:
            raise DeployError("NOT_FOUND", f"배포 {dep_id}가 없다")
        if dep.status in NOT_DEPLOYED:
            raise DeployError("NOT_DEPLOYED", f"{dep_id}는 실제로 배포되지 않았다 ({dep.status})")
        end = dep.at + timedelta(minutes=window_minutes)
        signals = await self.source.signals(dep, dep.at, end)
        result = evaluate_check(
            dep,
            signals,
            at=svc.clock(),
            window_minutes=window_minutes,
            prior_rollbacks=self.prior_rollbacks(dep),
        )
        self.checks.setdefault(dep_id, []).append(result)
        self._save()
        return result


_store: DeployStore | None = None


def get_deploy_store() -> DeployStore:
    global _store
    if _store is None:
        _store = DeployStore()
    return _store


def set_deploy_store(store: DeployStore) -> DeployStore:
    """테스트에서 신호원을 바꾼 저장소를 끼울 때 쓴다."""
    global _store
    _store = store
    return store
