"""infra(DejaFix 어댑터) 에이전트 skill 입출력 (시나리오 A 필수 3개). 이슈 #10 요청(예진).

- `diagnose_incident`: orchestrator·marketing·support → infra, 등급 read.
- `rollback_deployment`: 상태를 바꾸므로 `execute(action="rollback_deployment")`의 params다.
  등급 B, 검증자 planning.
- `verify_recovery`: orchestrator → infra, 등급 read.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field

from ax_contracts.schemas.common import AxModel, UtcDatetime


class SuspectDeployment(AxModel):
    service: str
    version: str
    deployed_at: UtcDatetime
    change_summary: str
    confidence: Literal["low", "mid", "high"]


class DiagnoseIncidentInput(AxModel):
    component: str | None = None


class DiagnoseIncidentOutput(AxModel):
    """원인이 정해지지 않으면 `undetermined`이다. 복구 예상 시각은 주지 않는다."""

    status: Literal["suspected", "undetermined"]
    suspects: list[SuspectDeployment] = Field(default_factory=list)
    impact_scope: list[str] = Field(default_factory=list)


class RollbackDeploymentParams(AxModel):
    service: str
    from_version: str
    to_version: str
    release_fault_injection: bool = True  # 데모: 롤백 = shop-api 장애 주입 해제 호출


class ReleaseFaultParams(AxModel):
    """`release_fault` 입력: 같은 유형 과거 장애를 푼 처리 방식(장애 상태 해제)을 다시 한다.

    총괄이 원인 미확정 장애에서 과거 사건 카드를 근거로 요청한다. 근거 카드와 유사도를 함께 보낸다.
    """

    component: str
    past_case_id: str
    similarity: float = Field(ge=0, le=1)


class ReleaseFaultOutput(AxModel):
    """`release_fault` 결과. 해제 뒤 장애 상태가 꺼졌으면 `post_check_ok`."""

    fault_released: bool
    post_check_ok: bool
    past_case_id: str
    execution_id: str | None = None
    grade: str | None = None


class VerifyRecoveryInput(AxModel):
    component: str
    since: UtcDatetime


class VerifyRecoveryOutput(AxModel):
    """둘 다 정상이면 orchestrator가 `incident.resolved`를 발행한다."""

    recovered: bool
    infra_metrics_ok: bool
    business_metrics_ok: bool | None = None  # data anomaly_detection 결과, 못 받으면 None
    checked_at: UtcDatetime


# ---------------------------------------------------------------- 인프라 AI 직접 대화 (11/9)
# dashboard-api `/api/dashboard/infra-chat` → incident `infra_chat_*`.
# 재무·기획 AI 대화와 같은 모양이다.
# 대화로는 조회·설명만 한다(롤백·장애 넣기·빼기·재시작은 하지 않는다).
# who는 dashboard-api가 사람 토큰에서 채운다.
INFRA_CHAT_MESSAGE_MAX = 2000


class InfraChatSendInput(AxModel):
    who: str = Field(min_length=1, max_length=80)
    thread_id: str | None = Field(default=None, min_length=1, max_length=64)
    message: str = Field(min_length=1, max_length=INFRA_CHAT_MESSAGE_MAX)


class InfraChatListInput(AxModel):
    who: str = Field(min_length=1, max_length=80)
    limit: int = Field(default=50, ge=1, le=100)


class InfraChatRef(AxModel):
    who: str = Field(min_length=1, max_length=80)
    thread_id: str = Field(min_length=1, max_length=64)


class InfraChatThreadOutput(AxModel):
    thread: dict[str, Any]


class InfraChatListOutput(AxModel):
    threads: list[dict[str, Any]]
