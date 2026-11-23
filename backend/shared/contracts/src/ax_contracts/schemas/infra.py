"""infra(DejaFix 어댑터) 에이전트 skill 입출력 (시나리오 A 필수 3개). 이슈 #10 요청(예진).

- `diagnose_incident`: orchestrator·marketing·support → infra, 등급 read.
- `rollback_deployment`: 상태를 바꾸므로 `execute(action="rollback_deployment")`의 params다.
  등급 B, 검증자 planning.
- `verify_recovery`: orchestrator → infra, 등급 read.
- `deploy_version`: dev → infra. 승인된 개발 배포를 연결된 배포 도구(GitHub Actions·Jenkins)로
  띄운다. 상태를 바꾸므로 `execute(action="deploy_version")`를 거친다. 멱등 키는 dev 배포 기록 id.
- `deploy_run_status`: dev → infra, 등급 read. 띄운 실행의 지금 상태를 도구에서 읽는다.
- `deploy_tool_services`: dev → infra, 등급 read. 배포 도구가 연결된 서비스 목록.
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


# ---------------------------------------------------------------- 배포 실행 (11/23)
# dev 배포 기록(DEP-n)을 사람이 승인하면 dev가 부른다. 배포 도구는 도구 연결(공용 보관함)에서
# 서비스마다 하나이고 환경 구분이 없어 운영 배포로 본다. 연결이 없으면 실패(`no_deploy_tool`)로
# 돌려주고 성공을 지어내지 않는다.

DeployRunState = Literal["dispatched", "running", "succeeded", "failed", "unknown"]


class DeployVersionInput(AxModel):
    service: str = Field(min_length=1, max_length=120)
    version: str = Field(min_length=1, max_length=120)
    # dev 배포 기록 id + 고유 꼬리 (예: "DEP-3.1a2b3c4d"). 멱등 키에 들어간다:
    # 같은 id로 다시 불러도 두 번 띄우지 않는다
    request_id: str = Field(min_length=1, max_length=64)
    env: Literal["dev", "prod"] = "dev"
    requester: str | None = Field(default=None, max_length=80)  # 배포를 요청한 쪽 (사람·AI)
    # 승인한 사람. infra는 비어 있으면 띄우지 않는다 (배포 실행은 사람 승인이 꼭 있어야 한다)
    approved_by: str | None = Field(default=None, max_length=80)


class DeployVersionOutput(AxModel):
    service: str
    version: str
    request_id: str
    tool: str | None = None  # "github_actions" | "jenkins"
    run_id: str | None = None  # 도구가 아직 실행 번호를 주지 않으면 None
    url: str | None = None
    status: DeployRunState = "dispatched"
    execution_id: str | None = None
    grade: str | None = None


class DeployRunStatusInput(AxModel):
    service: str = Field(min_length=1, max_length=120)
    run_id: str = Field(min_length=1, max_length=120)


class DeployRunStatusOutput(AxModel):
    service: str
    # 실행 번호. Jenkins 대기열 id(`queue-N`)를 빌드 번호로 바꿀 수 있으면 바꾼 값
    run_id: str
    tool: str | None = None
    # 도구 목록에서 그 실행을 못 찾으면 unknown (지어내지 않는다)
    status: DeployRunState
    url: str | None = None
    checked_at: UtcDatetime
    # Jenkins 대기열에 있어 아직 빌드 번호를 모른다 (도구에서 확인 중)
    resolving: bool = False


class DeployToolServicesInput(AxModel):
    pass


class DeployToolService(AxModel):
    service: str
    tool: str  # "github_actions" | "jenkins"


class DeployToolServicesOutput(AxModel):
    services: list[DeployToolService] = Field(default_factory=list)


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
