"""배포 기록과 배포 후 확인 (순수 함수).

- 배포는 머지된 PR 묶음 하나다. prod 배포나 보호 경로가 닿는 배포는 사람 승인을 기다린다.
- 배포 후 확인은 신호(장애·이상 지표)를 배포 시각 기준 구간으로 맞춰 본다.
  원인은 판정하지 않는다. 근거 줄에는 시간 관계와 건수만 쓴다.
- 롤백 제안은 infra `rollback_deployment` params와 같은 정수 4개를 만든다.
  B/C는 safety 기준표(thresholds.toml rollback_*)와 같은 기준으로 미리 계산해 보여 준다.
  판정은 safety가 다시 한다. 값이 없거나 정수가 아니면 C (fail-closed).
- 롤백 실행은 infra가 한다. 이 모듈은 제안만 만든다.
- 배포 실행은 infra `deploy_version`(도구 연결의 배포 도구: GitHub Actions·Jenkins)이 한다.
  도구 연결의 배포 도구는 서비스마다 하나이고 환경 구분이 없어 운영 배포로 본다. 그래서 실제 실행은
  늘 사람이 눌러야 한다: 승인 이유(prod·보호 경로)가 없는 배포도 `ready`(사람이 눌러야 함)로 두고
  자동으로 보내지 않는다. 사람이 승인(`approved`)한 배포를 한 번만 보내고 결과를 `run`(DeployRun)에
  남긴다 (`deploy_store`).
  도구가 띄웠으면 `running`, 도구에서 끝난 것을 읽으면 `succeeded`·`failed`.
  못 띄웠으면(도구 미연결·관문 보류·거절·도구 오류) `approved`(실행 안 함) + 이유로 남는다.
  `succeeded`는 도구가 실제로 끝냈다고 읽었을 때만 쓴다 (목 시드 DEP-1은 테스트 전용).
"""

from __future__ import annotations

import uuid
from collections.abc import Iterable
from datetime import datetime, timedelta
from typing import Any, Literal

from pydantic import Field

from ax_contracts.schemas import UtcDatetime
from ax_workflow.autocheck import irreversible_paths, merge_risk_paths
from ax_workflow.models import PR, DomainModel

DeployEnv = Literal["dev", "prod"]
# approved: 배포 허가됨(사람 승인 또는 승인 불필요). 배포 도구가 없어 실제 배포는 하지 않았다
# ready: 승인 이유(prod·보호 경로)는 없지만 배포 실행은 사람이 눌러야 한다 (운영 배포 도구)
DeployStatus = Literal[
    "requested", "ready", "approved", "running", "succeeded", "failed", "rolled_back"
]
# 실행되지 않은 배포. 배포 후 확인을 하지 않는다 (없는 배포의 "정상"을 만들지 않는다)
NOT_DEPLOYED: tuple[str, ...] = ("requested", "ready", "approved", "running")
READY_NOTE = "배포 실행은 사람이 눌러야 해요"
NO_EXECUTOR_NOTE = "배포 도구 미연결 — 실제 배포는 안 함"
# 배포 실행 기록 상태 (infra deploy_version 호출)
# sending: 보내는 중(파일에 먼저 남김) · dispatched: 도구가 띄움 · no_tool: 그 서비스 배포 도구 없음
# held: 안전 확인에서 사람 승인 대기 · rejected: 관문 거절 · failed: 못 띄움(도구 오류·닿지 못함 등)
# unknown: 보냈지만 결과를 모름(시간 초과·재시작) → 다시 보내지 않고 사람이 도구에서 확인
# handed: 인프라 AI가 꺼져 사람 할 일함으로 넘어감 · not_sent: 승인했지만 보내지 않음(다시 켬 등)
DispatchState = Literal[
    "sending",
    "dispatched",
    "no_tool",
    "held",
    "rejected",
    "failed",
    "unknown",
    "handed",
    "not_sent",
]
RunStatus = Literal["dispatched", "running", "succeeded", "failed", "unknown"]
NO_TOOL_NOTE = "배포 도구가 연결되지 않았어요 — 도구 연결에서 설정 (실제 배포는 안 함)"
UNKNOWN_NOTE = "배포가 시작됐을 수 있어요 — 배포 도구에서 먼저 확인 (다시 보내지 않음)"
HANDED_NOTE = "사람 할 일함으로 넘어감 — 사람이 실행"
NOT_SENT_NOTE = "승인했지만 배포 도구로 보내지 않았어요 — 사람이 '배포 실행'을 눌러야 해요"
# 서비스·버전 한도 (infra DeployVersionInput과 같다)
NAME_MAX = 120
Verdict = Literal["ok", "watch", "suspect"]
SignalKind = Literal["incident", "anomaly", "unavailable"]
# unavailable 신호의 이유: 연결이 없음 · 읽다가 오류
UnavailableReason = Literal["not_connected", "error"]

DEFAULT_WINDOW_MINUTES = 30
# 배포 후 이 시간 안에 장애가 열리고 후보 PR이 하나면 확신도 high로 본다
CLOSE_MINUTES = 10

# safety thresholds.toml rollback_* 와 같은 기준 (값 > 기준이면 C)
ROLLBACK_INT_KEYS = ("suspect_count", "low_confidence", "irreversible_count", "prior_rollbacks")
ROLLBACK_LIMITS: dict[str, int] = {
    "suspect_count": 1,
    "low_confidence": 0,
    "irreversible_count": 0,
    "prior_rollbacks": 0,
}
ROLLBACK_RULES: dict[str, str] = {
    "suspect_count": "rollback_multi_suspect",
    "low_confidence": "rollback_low_confidence",
    "irreversible_count": "rollback_irreversible",
    "prior_rollbacks": "rollback_repeat",
}


class Deployment(DomainModel):
    id: str  # "DEP-1"
    pr_numbers: list[int]
    version: str | None = None
    env: DeployEnv
    at: UtcDatetime  # 요청 시각. 실행되면 실행 시각으로 바꾼다
    status: DeployStatus
    approved_by: str | None = None
    changed_paths: list[str] = Field(default_factory=list)
    # 사람 승인이 필요한 이유 키: "prod", "protected_path"
    approval_reasons: list[str] = Field(default_factory=list)
    protected_paths: list[str] = Field(default_factory=list)
    # 배포 실행기가 실제로 배포했나. 실행기가 없으면 늘 False (status도 succeeded가 되지 않는다)
    executed: bool = False
    note: str | None = None  # 실행하지 않은 이유 등 사람이 읽는 한 줄
    # 배포 실행 기록 (infra deploy_version). 보내기 전이면 None
    run: DeployRun | None = None
    # 고유 꼬리 (uuid 8자리). 저장소를 비워 DEP 번호가 다시 쓰여도 관문·infra 멱등 키가 겹치지 않게
    uid: str = ""


def request_id(dep: Deployment) -> str:
    """infra에 보내는 배포 요청 id ("DEP-3.1a2b3c4d")."""
    return f"{dep.id}.{dep.uid}" if dep.uid else dep.id


class DeployRun(DomainModel):
    """infra `deploy_version`으로 보낸 배포 실행 한 번 (한 배포에 하나뿐: 다시 보내지 않는다)."""

    state: DispatchState
    service: str | None = None
    version: str | None = None
    tool: str | None = None  # "github_actions" | "jenkins"
    run_id: str | None = None
    url: str | None = None
    run_status: RunStatus | None = None  # 도구에서 읽은 실행 상태 (dispatched 뒤)
    code: str | None = None  # 실패 종류 (infra outcome 등)
    reason: str | None = None  # 사람이 읽는 한 줄
    review_id: str | None = None  # 관문 보류 건 (안전 확인에서 승인하면 이어서 보낸다)
    request_id: str | None = None  # infra에 보낸 요청 id (배포 id + 고유 꼬리)
    resolving: bool = False  # Jenkins 대기열이라 아직 빌드 번호를 모름 (도구에서 확인 중)
    at: UtcDatetime
    checked_at: UtcDatetime | None = None


class Signal(DomainModel):
    kind: SignalKind
    source: Literal["dejafix", "data", "mock"]
    ref: str | None = None  # 사건 id·지표 이름
    title: str = ""
    at: UtcDatetime | None = None
    severity: Literal["low", "mid", "high"] | None = None
    reason: UnavailableReason | None = None  # kind == "unavailable"일 때만


class RollbackSuggestion(DomainModel):
    suspect_count: int
    low_confidence: int
    irreversible_count: int
    prior_rollbacks: int
    grade: Literal["B", "C"]
    rules: list[str] = Field(default_factory=list)  # 걸린 기준 키


class PostDeployCheck(DomainModel):
    deployment: str
    at: UtcDatetime
    window_minutes: int
    signals: list[Signal] = Field(default_factory=list)
    verdict: Verdict
    evidence: list[str] = Field(default_factory=list)
    candidate_prs: list[str] = Field(default_factory=list)
    rollback: RollbackSuggestion | None = None


# ---------------------------------------------------------------- 배포


class DeployError(ValueError):
    """배포를 만들 수 없다 (PR 없음·미머지)."""

    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def approval_reasons(env: str, paths: Iterable[str]) -> tuple[list[str], list[str]]:
    """사람 승인 이유와 걸린 보호 경로."""
    protected = merge_risk_paths(sorted(set(paths)))
    reasons: list[str] = []
    if env == "prod":
        reasons.append("prod")
    if protected:
        reasons.append("protected_path")
    return reasons, protected


def build_deployment(
    seq: int, prs: list[PR], env: DeployEnv, at: datetime, version: str | None = None
) -> Deployment:
    """머지된 PR로 배포 기록을 만든다. 승인 이유가 있으면 `requested`, 없으면 `ready`.

    어느 쪽도 자동으로 보내지 않는다 (배포 실행은 사람이 누른다).
    """
    if not prs:
        raise DeployError("INVALID_DEPLOY", "배포할 PR이 없다")
    unmerged = [p.id for p in prs if not p.merged]
    if unmerged:
        raise DeployError("PR_NOT_MERGED", f"머지되지 않은 PR: {', '.join(unmerged)}")
    paths = sorted({path for p in prs for path in p.changed_paths})
    reasons, protected = approval_reasons(env, paths)
    if version is None:
        versioned = [p for p in prs if p.deployed_version]
        if versioned:
            latest = max(versioned, key=lambda p: p.merged_at or at)
            version = latest.deployed_version
    return Deployment(
        id=f"DEP-{seq}",
        pr_numbers=sorted({p.number for p in prs}),
        version=version,
        env=env,
        at=at,
        status="requested" if reasons else "ready",
        changed_paths=paths,
        approval_reasons=reasons,
        protected_paths=protected,
        note=None if reasons else READY_NOTE,
        uid=uuid.uuid4().hex[:8],
    )


def service_version(version: str | None) -> tuple[str, str] | None:
    """배포 버전 "shop-api v2.4.1" → ("shop-api", "v2.4.1"). 꼴이 다르면 None (지어내지 않는다)."""
    parts = (version or "").split()
    if len(parts) != 2 or any(len(p) > NAME_MAX for p in parts):
        return None
    return parts[0], parts[1]


def deploy_params(dep: Deployment) -> dict[str, Any]:
    """`request_deploy` execute params."""
    return {
        "env": dep.env,
        "version": dep.version,
        "pr_numbers": dep.pr_numbers,
        "changed_paths": dep.changed_paths,
        "protected_count": len(dep.protected_paths),
        "needs_approval": bool(dep.approval_reasons),
    }


# ---------------------------------------------------------------- 롤백 등급


def rollback_grade(params: dict[str, Any]) -> tuple[Literal["B", "C"], list[str]]:
    """정수 4개로 B/C를 가른다. 빠졌거나 음수·정수가 아니면 C."""
    rules: list[str] = []
    for key in ROLLBACK_INT_KEYS:
        value = params.get(key)
        if isinstance(value, bool) or not isinstance(value, int) or value < 0:
            rules.append(f"invalid_{key}")
        elif value > ROLLBACK_LIMITS[key]:
            rules.append(ROLLBACK_RULES[key])
    return ("C" if rules else "B"), rules


def _minutes(delta: timedelta) -> int:
    return int(delta.total_seconds() // 60)


def evaluate_check(
    dep: Deployment,
    signals: list[Signal],
    *,
    at: datetime,
    window_minutes: int = DEFAULT_WINDOW_MINUTES,
    prior_rollbacks: int = 0,
) -> PostDeployCheck:
    """배포 후 구간 안의 신호로 판정한다.

    - 구간 안에 열린 장애가 있으면 suspect, 롤백 제안을 붙인다.
    - 이상 지표·시각 모르는 장애·못 읽은 신호원이 있으면 watch.
    - 아무것도 없으면 ok.
    """
    start = dep.at
    end = dep.at + timedelta(minutes=window_minutes)
    in_window: list[Signal] = []
    undated: list[Signal] = []
    anomalies: list[Signal] = []
    unavailable: list[Signal] = []
    for sig in signals:
        if sig.kind == "unavailable":
            unavailable.append(sig)
        elif sig.kind == "anomaly":
            if sig.at is None or start <= sig.at <= end:
                anomalies.append(sig)
        elif sig.at is None:
            undated.append(sig)
        elif start <= sig.at <= end:
            in_window.append(sig)
    in_window.sort(key=lambda s: s.at or start)

    evidence: list[str] = []
    for sig in in_window:
        assert sig.at is not None
        ref = f" ({sig.ref})" if sig.ref else ""
        evidence.append(f"배포 후 {_minutes(sig.at - start)}분에 장애 1건 열림{ref}")
    if undated:
        evidence.append(f"열린 시각을 모르는 장애 {len(undated)}건")
    for sig in sorted(anomalies, key=lambda x: x.at is None):
        name = sig.ref or sig.title
        if sig.at is not None:
            evidence.append(f"배포 후 {_minutes(sig.at - start)}분 이상 지표 {name}")
        else:  # data 이상 지표는 구간 전체 판정이라 시각이 없다
            where = f"확인 구간 {window_minutes}분 안 어딘가"
            evidence.append(f"시각을 모르는 이상 지표 {name} ({where})")
    for sig in unavailable:
        # 신호원이 준 글(title)은 옮기지 않는다 (원인처럼 읽힐 수 있다). 이유 종류만 쓴다
        if sig.reason == "not_connected":
            evidence.append(f"{sig.source} 연결 없음 → 신호를 읽지 못함 (도구 연결 필요)")
        elif sig.reason == "error":
            evidence.append(f"{sig.source} 신호를 읽지 못함 (응답 오류)")
        else:
            evidence.append(f"{sig.source} 신호를 읽지 못함")
    if not evidence:
        evidence.append(f"배포 후 {window_minutes}분 안에 열린 장애·이상 지표 없음")

    candidates = [f"PR #{n}" for n in dep.pr_numbers]
    if in_window:
        verdict: Verdict = "suspect"
    elif anomalies or undated or unavailable:
        verdict = "watch"
    else:
        verdict = "ok"

    rollback: RollbackSuggestion | None = None
    if verdict == "suspect":
        first = in_window[0].at
        assert first is not None
        close = first - start <= timedelta(minutes=CLOSE_MINUTES)
        params = {
            "suspect_count": len(candidates),
            "low_confidence": 0 if (len(candidates) == 1 and close) else 1,
            "irreversible_count": len(irreversible_paths(dep.changed_paths)),
            "prior_rollbacks": prior_rollbacks,
        }
        grade, rules = rollback_grade(params)
        rollback = RollbackSuggestion(**params, grade=grade, rules=rules)
        evidence.append(f"배포에 포함된 PR {len(candidates)}개: {', '.join(candidates)}")

    return PostDeployCheck(
        deployment=dep.id,
        at=at,
        window_minutes=window_minutes,
        signals=signals,
        verdict=verdict,
        evidence=evidence,
        candidate_prs=candidates if verdict != "ok" else [],
        rollback=rollback,
    )
