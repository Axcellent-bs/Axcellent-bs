"""배포 기록·배포 후 확인 저장소 (프로세스 메모리, 목 모드).

기획개발 상태(`State`)와 따로 둔다. PR은 요청 때 서비스 상태에서 읽는다.
시드: 머지된 PR #28(shop-api v2.4.1)을 dev 배포 DEP-1로 두고, 시드 사건 ctx_inc_0001로
배포 후 확인 하나(suspect)를 미리 계산해 둔다.

상태를 바꾸는 단계는 서비스와 같은 템플릿 실행 관문을 거친다.
- `request_deploy` (B): 배포 요청. prod·보호 경로면 `requested`로 두고 사람 승인을 기다린다.
- `approve_deploy` (사람): 승인 버튼.
- `post_deploy_check` (read): 관문을 거치지 않는다. 결과만 쌓는다.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from ax_agent_template import config as template_config
from ax_contracts.schemas import Evidence, ExecuteDecision, RequestMeta
from planning_agent.deploy_signals import (
    MockSignalSource,
    SignalSource,
    default_source,
    mock_signals,
)
from planning_agent.domain.clock import kst
from planning_agent.domain.deploy import (
    DEFAULT_WINDOW_MINUTES,
    DeployEnv,
    DeployError,
    Deployment,
    PostDeployCheck,
    Signal,
    build_deployment,
    deploy_params,
    evaluate_check,
)
from planning_agent.domain.seed import initial_state
from planning_agent.domain.service import AGENT_NAME, GateCall, GateRefused, PlanningService

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


class DeployStore:
    def __init__(self, source: SignalSource | None = None):
        self._source_override = source
        self.reset()

    # ---- 시드·조회

    def reset(self) -> None:
        state = initial_state()
        self._mock = MockSignalSource(state.incidents, SEED_ANOMALIES)
        self.deployments: list[Deployment] = []
        self.checks: dict[str, list[PostDeployCheck]] = {}
        self._seq = 0
        for pr in state.prs:
            if not pr.merged or not pr.merged_at:
                continue
            self._seq += 1
            dep = build_deployment(self._seq, [pr], "dev", pr.merged_at)
            self.deployments.append(dep)
            window_end = dep.at + timedelta(minutes=DEFAULT_WINDOW_MINUTES)
            self.checks[dep.id] = [
                evaluate_check(dep, mock_signals(state.incidents, SEED_ANOMALIES), at=window_end)
            ]

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
        self, svc: PlanningService, action: str, ctx: str, target: str, summary: str, params: dict
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

    async def request(
        self, svc: PlanningService, pr_numbers: list[int], env: DeployEnv
    ) -> Deployment:
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
        return dep

    async def approve(self, svc: PlanningService, dep_id: str, approved_by: str) -> Deployment:
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
        updated = dep.model_copy(
            update={"status": "succeeded", "approved_by": approved_by, "at": svc.clock()}
        )
        self.deployments = [updated if d.id == dep_id else d for d in self.deployments]
        return updated

    async def check(
        self, svc: PlanningService, dep_id: str, window_minutes: int = DEFAULT_WINDOW_MINUTES
    ) -> PostDeployCheck:
        dep = self.get(dep_id)
        if dep is None:
            raise DeployError("NOT_FOUND", f"배포 {dep_id}가 없다")
        if dep.status in ("requested", "running"):
            raise DeployError("NOT_DEPLOYED", f"{dep_id}는 아직 배포되지 않았다 ({dep.status})")
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
