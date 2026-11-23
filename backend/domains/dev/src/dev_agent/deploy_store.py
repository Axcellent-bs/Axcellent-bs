"""배포 기록·배포 후 확인 저장소.

기획·개발 상태(`State`)와 따로 둔다. PR은 요청 때 서비스 상태에서 읽는다.
- 목 모드(테스트 전용)만 시드를 넣는다: 머지된 PR #28(shop-api v2.4.1)을 dev 배포 DEP-1로 두고,
  시드 사건 ctx_inc_0001로 배포 후 확인 하나(suspect)를 미리 계산해 둔다.
  실제 모드는 빈 목록에서 시작한다 (시드를 실제 배포처럼 보이지 않게).
- `PLANNING_STATE_DIR`가 있으면 `deploy_state_dev.json`에 이어 쓴다(서비스 상태 `store.py`와
  같은 방식: 임시 파일에 쓰고 바꿔 끼움). 없으면 프로세스 메모리만.
  파일을 못 읽거나 다른 모드(mock·real)가 쓴 파일이면 처음 상태로 시작한다.
  저장에 실패해도 배포 API는 계속 동작한다 (로그만 남김).
- 배포 실행: 도구 연결의 배포 도구는 운영 배포로 본다. 그래서 사람이 승인한 배포만 보낸다
  (승인 버튼·'배포 실행' 버튼, 세션 모드면 로그인한 사람). 승인 이유가 없는 배포도 `ready`로 두고
  저절로 보내지 않는다. 보낼 때는 infra `deploy_version`(A2A `call_agent`)을 한 번만 부르고,
  infra가 관문 `execute(deploy_version)`를 거쳐 배포 도구로 띄운다.
  결과는 `Deployment.run`에 남는다.
  - 한 번만: 보내기 전에 `run.state = sending`을 파일에 먼저 쓴다. 이미 `run`이 있으면 다시 보내지
    않는다(두 번 승인·동시 요청). 다시 켰을 때 `sending`으로 남은 기록은 `unknown`("결과 모름 →
    도구에서 확인")으로 바꾸고 다시 보내지 않는다. 승인했는데 run이 없는 기록은 `not_sent`로 두고
    사람이 '배포 실행'을 눌러야 보낸다. infra 쪽도 같은 요청 id(배포 id + 고유 꼬리)·서비스·버전은
    한 번만 띄운다.
  - 결과 모름(시간 초과·연결·응답 오류)은 `unknown`, 사람 할 일함으로 넘어가면 `handed`,
    분명한 거절·실패(도구 미연결·관문 거절·도구 오류)는 `approved`(실행 안 함) + 이유로 남긴다.
  - 잠금 없이 "확인 후 표시"로 막는 것은 한 프로세스 안에서만 된다 (개발 서버는 워커 하나로 돈다).
  - 관문 보류(held)는 보류 건을 `hold_resume`에 적어 둔다. 사람이 안전 확인에서 승인하면 같은 요청
    id로 한 번 다시 보낸다 (infra 관문이 승인된 판정을 돌려준다).
  - 목 모드(테스트 전용)에서 부를 곳을 끼우지 않으면 infra를 부르지 않는다 (실행 안 함).
- 실행 상태: `refresh`가 infra `deploy_run_status`로 도구 실행 상태를 읽어
  `running` → `succeeded`·`failed`로 바꾼다.

상태를 바꾸는 단계는 서비스와 같은 템플릿 실행 관문을 거친다.
- `request_deploy` (B): 배포 요청. prod·보호 경로면 `requested`로 두고 사람 승인을 기다린다.
- `approve_deploy` (사람): 승인 버튼.
- `post_deploy_check` (read): 관문을 거치지 않는다. 결과만 쌓는다.
"""

from __future__ import annotations

import json
import logging
import os
import re
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import TYPE_CHECKING

from pydantic import ValidationError

from ax_agent_template import config as template_config
from ax_agent_template.client import call_agent
from ax_contracts.schemas import (
    HANDED_TO_HUMAN,
    AgentRequest,
    AgentResult,
    ErrorCode,
    Evidence,
    ExecuteDecision,
    RequestMeta,
    TaskStatus,
)
from ax_contracts.schemas.infra import (
    DeployRunStatusInput,
    DeployRunStatusOutput,
    DeployToolServicesInput,
    DeployToolServicesOutput,
    DeployVersionInput,
    DeployVersionOutput,
)
from ax_workflow.base import AGENT_NAME
from ax_workflow.clock import kst
from ax_workflow.core import GateCall, GateRefused
from ax_workflow.seed import initial_state
from ax_workflow.store import ENV_DIR
from dev_agent import hold_resume
from dev_agent.deploy import (
    DEFAULT_WINDOW_MINUTES,
    HANDED_NOTE,
    NO_TOOL_NOTE,
    NOT_DEPLOYED,
    NOT_SENT_NOTE,
    UNKNOWN_NOTE,
    DeployEnv,
    DeployError,
    Deployment,
    DeployRun,
    PostDeployCheck,
    Signal,
    build_deployment,
    deploy_params,
    evaluate_check,
    request_id,
    service_version,
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
INFRA = "infra"
DISPATCH_TIMEOUT_SECONDS = 60
STATUS_TIMEOUT_SECONDS = 20
MOCK_NOTE = "목 모드(테스트 전용): 배포 도구를 부르지 않음 — 실제 배포는 안 함"
INTERRUPTED_NOTE = (
    "보내던 중 서버가 다시 켜져 결과를 몰라요 — 배포 도구에서 먼저 확인 (다시 보내지 않음)"
)
NO_VERSION_NOTE = (
    '배포할 서비스·버전을 몰라요 (PR에 "서비스 버전" 배포 버전이 없음) — 실제 배포는 안 함'
)
# 사람이 눌러야 보내는 상태 (승인 대기 · 승인 이유 없음)
WAITING = ("requested", "ready")
InfraCaller = Callable[[str, AgentRequest], Awaitable[AgentResult]]

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


def _ctx(dep_id: str) -> str:
    return f"ctx_dep_{dep_id.split('-')[-1].zfill(4)}"


def _message(res: AgentResult) -> str:
    msg = res.output.get("message") if isinstance(res.output, dict) else None
    return msg if isinstance(msg, str) and msg else (res.outcome or res.status.value)


def run_from_result(res: AgentResult | None, base: DeployRun) -> DeployRun:
    """infra `deploy_version` 결과 → 배포 실행 기록.

    모르는 모양이면 결과 모름·실패로 둔다 (성공을 짓지 않는다)."""
    if res is None:
        return base.model_copy(update={"state": "unknown", "code": "error", "reason": UNKNOWN_NOTE})
    out = res.output if isinstance(res.output, dict) else {}
    tool = out.get("tool") if isinstance(out.get("tool"), str) else None
    if res.status == TaskStatus.COMPLETED and not res.outcome:
        try:
            got = DeployVersionOutput.model_validate(out)
        except ValidationError:
            return base.model_copy(
                update={"state": "unknown", "code": "invalid_output", "reason": UNKNOWN_NOTE}
            )
        return base.model_copy(
            update={
                "state": "dispatched",
                "tool": got.tool,
                "run_id": got.run_id,
                "url": got.url,
                "run_status": "dispatched",
                "code": None,
                "reason": None,
            }
        )
    if res.status == TaskStatus.COMPLETED and res.outcome == "rejected":
        return base.model_copy(
            update={
                "state": "rejected",
                "code": "rejected",
                "reason": "안전 관문이 거절해 배포하지 않았어요",
            }
        )
    if res.status == TaskStatus.INPUT_REQUIRED and res.outcome == "held":
        rid = out.get("review_id")
        return base.model_copy(
            update={
                "state": "held",
                "code": "held",
                "review_id": rid if isinstance(rid, str) else None,
                "reason": "보류 → 안전 확인에서 승인하면 이어서 배포해요",
            }
        )
    if res.status == TaskStatus.FAILED and res.outcome == "no_deploy_tool":
        return base.model_copy(
            update={"state": "no_tool", "code": "no_deploy_tool", "reason": NO_TOOL_NOTE}
        )
    code = res.outcome or res.status.value
    if res.status == TaskStatus.INPUT_REQUIRED and res.outcome == HANDED_TO_HUMAN:
        # 인프라 AI가 꺼져 있어 사람 할 일함으로 넘어갔다: 사람이 실행한다
        return base.model_copy(update={"state": "handed", "code": code, "reason": HANDED_NOTE})
    if (
        res.status == TaskStatus.CANCELED
        or (res.outcome == "deploy_failed" and out.get("code") == "timeout")
        or res.outcome in ("unreachable", ErrorCode.INVALID_OUTPUT.value)
        or re.fullmatch(r"http_5\d\d", res.outcome or "")
    ):
        # 요청이 이미 닿았을 수 있다 (call_agent로는 연결 실패와 응답 읽기 실패를 가를 수 없다).
        # 다시 보내지 않고 사람이 도구에서 확인한다
        return base.model_copy(
            update={"state": "unknown", "tool": tool, "code": code, "reason": UNKNOWN_NOTE}
        )
    # 그 밖은 받는 쪽이 분명히 거절·실패를 돌려준 경우다
    if res.status == TaskStatus.INPUT_REQUIRED:
        reason = f"인프라 AI가 배포하지 않았어요 ({code}) — 실제 배포는 안 함"
    else:
        reason = f"배포 실패: {_message(res)}"
    return base.model_copy(update={"state": "failed", "tool": tool, "code": code, "reason": reason})


def target_text(dep: Deployment) -> str:
    """승인 문구·근거에 쓰는 대상 한 줄: "shop-api v2.4.1 → 운영 배포 도구로 실행"."""
    pair = service_version(dep.version)
    what = f"{pair[0]} {pair[1]}" if pair else f"버전 모름({dep.version or '-'})"
    return f"{what} → 운영 배포 도구로 실행 (기록 env {dep.env})"


def _on_load(deployments: list[Deployment]) -> tuple[list[Deployment], bool]:
    """다시 켰을 때 기록을 고친다. 고친 게 있으면 True (파일에 다시 쓴다).

    - 보내던 중(`sending`) 꺼진 배포: 결과를 모르니 다시 보내지 않는다 (`unknown`, 도구에서 확인).
    - 승인했지만 보내지 않은 배포(`approved`, run 없음): `not_sent`로 표시한다. 저절로 보내지 않고
      사람이 '배포 실행'(`POST /deployments/{id}/dispatch`)을 누르면 한 번 보낸다.
    - 고유 꼬리(`uid`)가 없는 예전 기록에는 하나 붙인다 (멱등 키가 겹치지 않게).
    """
    out: list[Deployment] = []
    changed = False
    for d in deployments:
        update: dict = {}
        if not d.uid:
            update["uid"] = uuid.uuid4().hex[:8]
        if d.run is not None and d.run.state == "sending":
            update["run"] = d.run.model_copy(
                update={"state": "unknown", "code": "interrupted", "reason": INTERRUPTED_NOTE}
            )
            update["note"] = INTERRUPTED_NOTE
        elif d.run is None and d.status == "approved":
            update["run"] = DeployRun(
                state="not_sent", code="not_sent", reason=NOT_SENT_NOTE, at=d.at
            )
            update["note"] = NOT_SENT_NOTE
        if update:
            changed = True
            d = d.model_copy(update=update)
        out.append(d)
    return out, changed


class DeployStore:
    def __init__(
        self,
        source: SignalSource | None = None,
        *,
        seed: bool | None = None,
        infra: InfraCaller | None = None,
    ):
        """`seed` None이면 목 모드일 때만 시드를 넣는다. 저장 파일이 있으면 그것을 읽는다.

        `infra`는 infra 에이전트를 부르는 함수 (`call_agent`와 같은 모양, 시험에서 바꿔 끼운다).
        """
        self._source_override = source
        self._seed_flag = seed
        self._infra = infra
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
        deployments, changed = _on_load(deployments)
        self.deployments, self.checks = deployments, checks
        self._seq = max([seq, *(_num(d.id) for d in deployments)])
        if self._seeding():  # 목 시드 사건 신호는 목 모드에서만
            self._mock = MockSignalSource(initial_state().incidents, SEED_ANOMALIES)
        if changed:
            self._save()
        return True

    @property
    def source(self) -> SignalSource:
        return self._source_override or default_source(self._mock)

    def get(self, dep_id: str) -> Deployment | None:
        return next((d for d in self.deployments if d.id == dep_id), None)

    def verify_request(
        self, rid: str, service: str, version: str, approved_by: str | None
    ) -> tuple[bool, str, Deployment | None]:
        """infra `deploy_version`(B) 교차검증 (read). (승인 여부, 이유 코드, 찾은 기록).

        요청 id(배포 id + 고유 꼬리)로 기록을 찾고, 사람이 승인한(`approved`) 기록인지, 승인자·
        서비스·버전이 기록과 같은지 코드로 본다. 하나라도 다르면 거절한다 (상태는 바꾸지 않는다).
        """
        dep = next((d for d in self.deployments if request_id(d) == rid), None)
        if dep is None:
            return False, "deploy_not_found", None
        if dep.status != "approved":
            return False, "deploy_not_approved", dep
        recorded = (dep.approved_by or "").strip()
        if not recorded or recorded != (approved_by or "").strip():
            return False, "approver_mismatch", dep
        if service_version(dep.version) != (service, version):
            return False, "target_mismatch", dep
        return True, "deploy_approved", dep

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

    async def request(
        self,
        svc: DevService,
        pr_numbers: list[int],
        env: DeployEnv,
        *,
        version: str | None = None,
    ) -> Deployment:
        prs = []
        for n in pr_numbers:
            pr = next((p for p in svc.state.prs if p.number == n), None)
            if pr is None:
                raise DeployError("NOT_FOUND", f"PR #{n}가 없다")
            prs.append(pr)
        seq = self._seq + 1
        dep = build_deployment(seq, prs, env, svc.clock(), version)
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
        # 자동으로 보내지 않는다: 승인 이유가 없어도(`ready`) 배포 실행은 사람이 누른다
        return dep

    async def approve(self, svc: DevService, dep_id: str, approved_by: str) -> Deployment:
        dep = self.get(dep_id)
        if dep is None:
            raise DeployError("NOT_FOUND", f"배포 {dep_id}가 없다")
        if dep.status not in WAITING:
            raise DeployError("NOT_REQUESTED", f"{dep_id}는 승인 대기가 아니다 ({dep.status})")
        await self._execute(
            svc,
            "approve_deploy",
            _ctx(dep_id),
            dep.id,
            f"{approved_by} 배포 승인 (사람 클릭) · {target_text(dep)}",
            {
                "env": dep.env,
                "approved_by": approved_by,
                "reasons": dep.approval_reasons,
                "version": dep.version,
            },
        )
        current = self.get(dep_id) or dep
        if current.status not in WAITING:  # 관문을 기다리는 동안 다른 승인이 먼저 끝났다
            raise DeployError("NOT_REQUESTED", f"{dep_id}는 승인 대기가 아니다 ({current.status})")
        # 승인만 기록한다. 배포됨(succeeded)은 도구가 실제로 끝냈다고 읽었을 때만
        updated = current.model_copy(
            update={
                "status": "approved",
                "approved_by": approved_by,
                "at": svc.clock(),
                "executed": False,
                "note": None,
            }
        )
        self._put(updated)
        self._save()
        return await self.dispatch(svc, dep_id)

    # ---- 배포 실행 (infra deploy_version)

    def _put(self, dep: Deployment) -> None:
        self.deployments = [dep if d.id == dep.id else d for d in self.deployments]

    def _caller(self) -> InfraCaller | None:
        if self._infra is not None:
            return self._infra
        if template_config.is_mock(MOCK_NAME):
            return None  # 테스트 전용 목: infra를 부르지 않는다
        return lambda target, req: call_agent(target, req)

    def _request(
        self, svc: DevService, skill: str, dep_id: str, payload: dict, timeout: int
    ) -> AgentRequest:
        ctx = _ctx(dep_id)
        meta = RequestMeta(
            context_id=ctx,
            tenant_id=svc.tenant_id,
            token="replaced-by-call_agent",
            path=[],
            deadline=datetime.now(UTC) + timedelta(seconds=timeout),
            idempotency_key=f"{ctx}:{skill}:{dep_id}",
        )
        return AgentRequest(meta=meta, skill=skill, input=payload)

    def _finish(self, dep: Deployment, run: DeployRun) -> Deployment:
        if run.state == "dispatched":
            update = {"run": run, "status": "running", "executed": True, "at": run.at, "note": None}
        else:
            update = {"run": run, "executed": False, "note": run.reason}
        done = dep.model_copy(update=update)
        self._put(done)
        self._save()
        return done

    async def dispatch(self, svc: DevService, dep_id: str, *, resume: bool = False) -> Deployment:
        """승인된 배포를 infra `deploy_version`으로 한 번만 보낸다.

        이미 보냈으면(어떤 결과든) 다시 보내지 않는다. `resume`은 관문 보류(held) 건을 사람이 승인한
        뒤 같은 요청 id로 이어서 보낼 때만 쓴다 (`hold_resume`).
        """
        dep = self.get(dep_id)
        if dep is None:
            raise DeployError("NOT_FOUND", f"배포 {dep_id}가 없다")
        if dep.status != "approved" or not (dep.approved_by or "").strip():
            return dep  # 사람이 승인한 배포만 보낸다
        retry = dep.run is not None and (
            dep.run.state == "not_sent" or (resume and dep.run.state == "held")
        )
        if dep.run is not None and not retry:
            return dep  # 한 번만
        now = svc.clock()
        pair = service_version(dep.version)
        if pair is None:
            run = DeployRun(
                state="failed",
                code="no_version",
                reason=NO_VERSION_NOTE,
                at=now,
            )
            return self._finish(dep, run)
        service, version = pair
        rid = request_id(dep)
        base = DeployRun(
            state="sending",
            service=service,
            version=version,
            review_id=dep.run.review_id if dep.run is not None else None,
            request_id=rid,
            at=now,
        )
        # 보내기 전에 입력을 만들어 검사한다 (틀리면 "보내는 중"을 남기지 않고 실패로)
        try:
            payload = DeployVersionInput(
                service=service,
                version=version,
                request_id=rid,
                env=dep.env,
                requester="dev",
                approved_by=dep.approved_by,
            ).model_dump(mode="json")
        except ValidationError:
            failed = base.model_copy(
                update={
                    "state": "failed",
                    "code": "invalid_input",
                    "reason": "배포 요청 값이 형식과 달라 보내지 않았어요 (서비스·버전 길이)",
                }
            )
            return self._finish(dep, failed)
        caller = self._caller()
        if caller is None:
            return self._finish(
                dep,
                base.model_copy(
                    update={"state": "failed", "code": "mock_mode", "reason": MOCK_NOTE}
                ),
            )
        # 보내기 전에 파일에 먼저 남긴다.
        # 확인과 표시 사이에 await가 없어 같은 배포를 두 번 보내지 않는다
        self._put(dep.model_copy(update={"run": base}))
        self._save()
        req = self._request(svc, "deploy_version", dep.id, payload, DISPATCH_TIMEOUT_SECONDS)
        try:
            res: AgentResult | None = await caller(INFRA, req)
        except Exception:  # noqa: BLE001 - 닿았는지 모른다: 결과 모름으로 남기고 다시 보내지 않는다
            log.exception("배포 실행 요청 실패: %s", dep.id)
            res = None
        run = run_from_result(res, base)
        current = self.get(dep_id) or dep
        done = self._finish(current, run)
        if run.state == "held" and run.review_id:
            self._note_hold(svc, done, res)
        return done

    async def send(self, svc: DevService, dep_id: str, by: str) -> Deployment:
        """사람이 '배포 실행'을 눌렀다 (`POST /deployments/{id}/dispatch`).

        - `ready`·`requested`: 그 사람의 승인으로 기록하고 한 번 보낸다 (`approve`와 같다).
        - `approved`인데 보내지 않음(`not_sent`): 한 번 보낸다. 승인자가 비었으면 누른 사람을 쓴다.
        - 그 밖(이미 보냄·결과 모름·보류 등): 다시 보내지 않는다 (409).
        """
        dep = self.get(dep_id)
        if dep is None:
            raise DeployError("NOT_FOUND", f"배포 {dep_id}가 없다")
        if dep.status in WAITING:
            return await self.approve(svc, dep_id, by)
        if dep.status != "approved" or dep.run is None or dep.run.state != "not_sent":
            state = dep.run.state if dep.run else dep.status
            raise DeployError("ALREADY_SENT", f"{dep_id}는 이미 보냈거나 보낼 수 없다 ({state})")
        if not (dep.approved_by or "").strip():
            dep = dep.model_copy(update={"approved_by": by})
            self._put(dep)
            self._save()
        return await self.dispatch(svc, dep_id)

    async def tool_services(self, svc: DevService) -> list[str]:
        """배포 도구가 연결된 서비스 (infra `deploy_tool_services`, read). 못 읽으면 DeployError."""
        caller = self._caller()
        if caller is None:
            raise DeployError("NOT_AVAILABLE", MOCK_NOTE)
        payload = DeployToolServicesInput().model_dump(mode="json")
        req = self._request(svc, "deploy_tool_services", "tools", payload, STATUS_TIMEOUT_SECONDS)
        try:
            res = await caller(INFRA, req)
            if res.status != TaskStatus.COMPLETED:
                raise ValueError(res.outcome)
            got = DeployToolServicesOutput.model_validate(res.output)
        except Exception as exc:  # noqa: BLE001 - 확인을 못 하면 받지 않는다 (fail-closed)
            raise DeployError(
                "TOOLS_UNAVAILABLE", f"배포 도구 연결을 확인하지 못했어요 ({type(exc).__name__})"
            ) from None
        return [t.service for t in got.services]

    def _note_hold(self, svc: DevService, dep: Deployment, res: AgentResult | None) -> None:
        """infra 관문 보류 건을 `hold_resume`에 적어 둔다.

        사람이 승인하면 같은 요청 id로 다시 보낸다.

        보류 이유를 모르면(응답 모양이 다름) 적지 않는다.
        기록에는 "보류 → 안전 확인에서 승인"이 남는다.
        """
        assert dep.run is not None and dep.run.service is not None
        out = res.output if res is not None and isinstance(res.output, dict) else {}
        try:
            decision = ExecuteDecision(
                execution_id=str(out.get("execution_id") or "exe_infra"),
                grade="C",
                status="held",
                hold_reason=out.get("hold_reason"),
                review_id=dep.run.review_id,
            )
        except ValidationError:
            log.warning("배포 %s 보류 이유를 몰라 이어서 하기를 적지 않음", dep.id)
            return
        key = f"deploy:{dep.run.request_id}:deploy_version:{dep.run.service}:{dep.run.version}"
        with hold_resume.recipe("deploy_dispatch", dep_id=dep.id):
            svc.holds.note("deploy_version", key, _ctx(dep.id), decision)

    async def refresh(self, svc: DevService, dep_id: str) -> Deployment:
        """띄운 실행의 상태를 infra `deploy_run_status`로 읽어 반영한다 (read)."""
        dep = self.get(dep_id)
        if dep is None:
            raise DeployError("NOT_FOUND", f"배포 {dep_id}가 없다")
        run = dep.run
        if run is None or run.state != "dispatched" or not run.service:
            raise DeployError("NOT_DISPATCHED", f"{dep_id}는 배포 도구로 띄운 실행이 없다")
        if not run.run_id:
            raise DeployError("NO_RUN_ID", "도구가 실행 번호를 주지 않았어요 — 배포 도구에서 확인")
        caller = self._caller()
        if caller is None:
            raise DeployError("NOT_AVAILABLE", MOCK_NOTE)
        payload = DeployRunStatusInput(service=run.service, run_id=run.run_id).model_dump(
            mode="json"
        )
        req = self._request(svc, "deploy_run_status", dep.id, payload, STATUS_TIMEOUT_SECONDS)
        try:
            res = await caller(INFRA, req)
        except Exception:  # noqa: BLE001 - 읽기 실패는 상태를 바꾸지 않는다
            res = None
        got: DeployRunStatusOutput | None = None
        if res is not None and res.status == TaskStatus.COMPLETED:
            try:
                got = DeployRunStatusOutput.model_validate(res.output)
            except ValidationError:
                got = None
        if got is None:
            why = _message(res) if res is not None else "응답 없음"
            raise DeployError("STATUS_UNAVAILABLE", f"실행 상태를 읽지 못했어요 ({why})")
        current = self.get(dep_id) or dep
        new_run = run.model_copy(
            update={
                "run_id": got.run_id,  # Jenkins 대기열 id를 빌드 번호로 바꿨으면 그 번호
                "run_status": got.status,
                "resolving": got.resolving,
                "checked_at": got.checked_at,
                "url": run.url or got.url,
            }
        )
        update: dict = {"run": new_run}
        if current.status == "running" and got.status in ("succeeded", "failed"):
            update["status"] = got.status
        done = current.model_copy(update=update)
        self._put(done)
        self._save()
        return done

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
