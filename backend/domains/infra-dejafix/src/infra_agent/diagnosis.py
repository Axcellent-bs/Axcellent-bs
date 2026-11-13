"""원인 진단 규칙 (코드). LLM을 쓰지 않는다.

- 진단 구간(기본 60분) 안에 그 구성요소를 바꾼 배포를 원인 후보로 본다. 없으면 `undetermined`.
  이미 되돌린 배포(롤백·사람의 수동 되돌리기)는 후보가 아니다.
- 확신도: 후보가 하나이고 배포 뒤 15분 안에 진단하면 `high`, 그보다 오래됐으면 `mid`.
  후보가 여럿이면 가장 최근 것이 `mid`, 나머지는 `low`.
- 목 모드는 시간과 상관없이 가장 최근 배포 1건을 `high` 후보로 돌려준다(SCN-A 체크리스트).
- 실제 모드는 결제 서버 이상도 확인돼야 원인으로 본다(`confirm_with_server`). 배포 후보가 있어도
  서버가 정상이거나 상태를 못 읽으면 `undetermined`로 두어 롤백하지 않고 사람에게 넘긴다
  (고객 문의 급증만으로 열린 장애 등, #소통 2026-11-09 합의).
- 배포 기록에 후보가 없으면(실제 모드) 연결된 레포에서 구간 안에 머지된 PR을 후보로 본다
  (`with_pr_suspects`, `pr_suspects.py`). 확신도 규칙은 배포와 같다(머지 시각 기준).
- 복구 예상 시각은 만들지 않는다.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, replace
from datetime import datetime, timedelta
from typing import Any, Literal

from ax_contracts.schemas.infra import DiagnoseIncidentOutput, SuspectDeployment
from infra_agent.deployments import Deployment, DeploymentHistory
from infra_agent.pr_suspects import PrSuspect

HIGH_CONFIDENCE_WINDOW = timedelta(minutes=15)


def lookback() -> timedelta:
    return timedelta(minutes=int(os.getenv("INFRA_DIAGNOSE_LOOKBACK_MIN") or 60))


@dataclass(frozen=True)
class Diagnosis:
    output: DiagnoseIncidentOutput
    candidates: tuple[Deployment, ...]
    diagnosed_at: datetime
    window_from: datetime | None  # 목 모드는 구간 없음
    # 결제 서버 확인: fault(이상 확인) / ok(정상) / unknown(못 읽음)
    # / None(확인 안 함: 목 모드·후보 없음)
    server: Literal["fault", "ok", "unknown"] | None = None

    def confidence_of(self, service: str, version: str) -> str | None:
        for suspect in self.output.suspects:
            if suspect.service == service and suspect.version == version:
                return suspect.confidence
        return None


def _suspect(deployment: Deployment, confidence: str) -> SuspectDeployment:
    return SuspectDeployment(
        service=deployment.service,
        version=deployment.version,
        deployed_at=deployment.deployed_at,
        change_summary=deployment.change_summary,
        confidence=confidence,  # type: ignore[arg-type]
    )


def diagnose(
    history: DeploymentHistory, component: str | None, now: datetime, *, mock: bool
) -> Diagnosis:
    scope = [component] if component else []
    # 되돌린 배포는 더 이상 돌고 있지 않아 원인 후보가 아니다
    rows = [d for d in history.for_component(component) if d.deploy_id not in history.rolled_back]
    if mock:
        picked = rows[:1]
        suspects = [_suspect(d, "high") for d in picked]
        window_from = None
    else:
        window_from = now - lookback()
        picked = [d for d in rows if window_from <= d.deployed_at <= now]
        if len(picked) == 1:
            fresh = now - picked[0].deployed_at <= HIGH_CONFIDENCE_WINDOW
            suspects = [_suspect(picked[0], "high" if fresh else "mid")]
        else:
            suspects = [_suspect(d, "mid" if i == 0 else "low") for i, d in enumerate(picked)]
    output = DiagnoseIncidentOutput(
        status="suspected" if suspects else "undetermined",
        suspects=suspects,
        impact_scope=scope,
    )
    return Diagnosis(
        output=output, candidates=tuple(picked), diagnosed_at=now, window_from=window_from
    )


def server_state(state: dict[str, Any] | None) -> Literal["fault", "ok", "unknown"]:
    """shop-api 장애 상태(`fault_state`)를 세 가지로 줄인다. 실패·느림이면 fault."""
    if not isinstance(state, dict):
        return "unknown"
    return "fault" if state.get("failing") or state.get("degraded") else "ok"


def confirm_with_server(diag: Diagnosis, server: Literal["fault", "ok", "unknown"]) -> Diagnosis:
    """배포 후보가 있어도 결제 서버 이상이 확인되지 않으면 원인 미확정으로 바꾼다.

    후보 배포(`candidates`)는 근거로 남기고 `suspects`만 비운다. 그래서 롤백 단계로 가지 않는다.
    """
    if diag.output.status != "suspected":
        return diag
    if server == "fault":
        return replace(diag, server=server)
    output = diag.output.model_copy(update={"status": "undetermined", "suspects": []})
    return replace(diag, output=output, server=server)


def with_pr_suspects(diag: Diagnosis, prs: list[PrSuspect], now: datetime) -> Diagnosis:
    """배포 기록으로 원인을 못 찾았을 때 원인 PR을 후보로 넣는다 (최근 머지 먼저).

    배포 기록에 후보가 있으면 그대로 둔다. 서버 이상 확인(`confirm_with_server`)은 이 뒤에 한다.
    """
    if diag.output.status == "suspected" or not prs:
        return diag
    if len(prs) == 1:
        fresh = now - prs[0].merged_at <= HIGH_CONFIDENCE_WINDOW
        levels = ["high" if fresh else "mid"]
    else:
        levels = ["mid"] + ["low"] * (len(prs) - 1)
    suspects = [
        SuspectDeployment(
            service=p.service,
            version=p.version,
            deployed_at=p.merged_at,
            change_summary=f"PR #{p.pr_number} {p.title}".strip(),
            confidence=level,  # type: ignore[arg-type]
        )
        for p, level in zip(prs, levels, strict=True)
    ]
    output = diag.output.model_copy(update={"status": "suspected", "suspects": suspects})
    return replace(diag, output=output)
