"""배포 기록과 배포 후 확인 (순수 함수).

- 배포는 머지된 PR 묶음 하나다. prod 배포나 보호 경로가 닿는 배포는 사람 승인을 기다린다.
- 배포 후 확인은 신호(장애·이상 지표)를 배포 시각 기준 구간으로 맞춰 본다.
  원인은 판정하지 않는다. 근거 줄에는 시간 관계와 건수만 쓴다.
- 롤백 제안은 infra `rollback_deployment` params와 같은 정수 4개를 만든다.
  B/C는 safety 기준표(thresholds.toml rollback_*)와 같은 기준으로 미리 계산해 보여 준다.
  판정은 safety가 다시 한다. 값이 없거나 정수가 아니면 C (fail-closed).
- 롤백 실행은 infra가 한다. 이 모듈은 제안만 만든다.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import datetime, timedelta
from typing import Any, Literal

from pydantic import Field

from ax_contracts.schemas import UtcDatetime
from planning_agent.domain.autocheck import irreversible_paths, merge_risk_paths
from planning_agent.domain.models import PR, DomainModel

DeployEnv = Literal["dev", "prod"]
DeployStatus = Literal["requested", "running", "succeeded", "failed", "rolled_back"]
Verdict = Literal["ok", "watch", "suspect"]
SignalKind = Literal["incident", "anomaly", "unavailable"]

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


class Signal(DomainModel):
    kind: SignalKind
    source: Literal["dejafix", "data", "mock"]
    ref: str | None = None  # 사건 id·지표 이름
    title: str = ""
    at: UtcDatetime | None = None
    severity: Literal["low", "mid", "high"] | None = None


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
    """머지된 PR로 배포 기록을 만든다. 승인 이유가 있으면 `requested`, 없으면 `succeeded`.

    목 모드에는 실제 배포 실행기가 없다. 승인이 필요 없는 배포는 바로 `succeeded`로 둔다
    (데모: 머지 = dev 배포).
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
        status="requested" if reasons else "succeeded",
        changed_paths=paths,
        approval_reasons=reasons,
        protected_paths=protected,
    )


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
    for sig in anomalies:
        when = f"배포 후 {_minutes(sig.at - start)}분 " if sig.at else ""
        evidence.append(f"{when}이상 지표 {sig.ref or sig.title}".strip())
    for sig in unavailable:
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
