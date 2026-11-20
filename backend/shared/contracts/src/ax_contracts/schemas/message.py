"""A2A 요청·결과에 붙는 공통 필드 (공통 구성안 5장).

A2A SDK가 정한 프로토콜 필드는 SDK 그대로 쓰고, 우리가 정의하는 필드는 모두 snake_case다.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any

from pydantic import Field, SerializerFunctionWrapHandler, model_serializer, model_validator

from ax_contracts.schemas.common import TENANT_ID_DEFAULT, AxModel, UtcDatetime


class TaskStatus(StrEnum):
    """Task 상태. 값은 A2A 프로토콜 표기를 따른다(`input-required`).

    정책상 거절(예: 환불 신청을 정책에 따라 거절)은 요청을 정상 처리한 결과이므로
    `completed` + `outcome`으로 표현한다. `rejected`는 에이전트가 요청 자체를 거부한 경우
    (권한·형식·루프·토큰 위반)만 쓴다. 둘을 섞으면 이관률·실패율 지표가 오염된다.
    """

    SUBMITTED = "submitted"
    WORKING = "working"
    INPUT_REQUIRED = "input-required"
    COMPLETED = "completed"
    REJECTED = "rejected"
    CANCELED = "canceled"
    FAILED = "failed"


class InputReason(StrEnum):
    """`input-required`일 때 무엇을 기다리는지 구분한다."""

    DEPT_INPUT = "dept_input"  # 다른 부서의 판단
    CUSTOMER_INPUT = "customer_input"  # 고객 자료
    HUMAN_INPUT = "human_input"  # 사람 확정·승인


class ErrorCode(StrEnum):
    """요청을 거부하거나 취소할 때 `outcome`에 담는 코드."""

    LOOP_DETECTED = "LOOP_DETECTED"
    HOP_LIMIT = "HOP_LIMIT"
    TIMEOUT = "TIMEOUT"
    UNAUTHORIZED = "UNAUTHORIZED"
    FORBIDDEN_SKILL = "FORBIDDEN_SKILL"
    INVALID_OUTPUT = "INVALID_OUTPUT"
    MISSING_CONTEXT = "MISSING_CONTEXT"
    SEQUENCE_GAP = "SEQUENCE_GAP"
    INVALID_INPUT = "INVALID_INPUT"  # 입력이 계약 형식과 다르다(형식 위반은 rejected)
    # 받는 서비스가 꺼져 있고 그 skill이 목록·조회(read, handoff=false)라 사람에게도 넘기지 않았다
    SERVICE_DISABLED = "service_disabled"
    # 여러 회사 (4단계부터 씀): 요청에 회사 ID가 없거나 모르는 회사다
    TENANT_REQUIRED = "TENANT_REQUIRED"
    # 여러 회사 (4단계부터 씀): 사람 토큰의 회사와 요청의 회사가 다르다
    TENANT_MISMATCH = "TENANT_MISMATCH"


class RequestMeta(AxModel):
    """모든 A2A 요청·결과에 붙는 메타.

    - `context_id`: 사건 단위 ID. 없으면 요청을 거부한다.
    - `path`: 거쳐온 에이전트 이름 목록. 새 요청에서 자기 이름이 있으면 루프로 거부하고,
      길이가 3을 넘으면 이관 큐로 넘긴다.
    - `deadline`: 응답 마감. 넘으면 실행하지 않고 보고한다.
    - `idempotency_key`: `context_id + 액션 + 대상`. 상태 변경 요청이면 필수다.
    - `requester`: 토큰에서 채워지는 값이다. 요청 본문에 적힌 값은 믿지 않고 무시한다.
    - `on_behalf_of`: 이 일을 시킨 사람의 계정 ID(개인 MCP 연결, 11/20). MCP 서버(개발 서비스
      `/api/planning/mcp`)가 개인 MCP 연결 토큰을 dashboard-api에서 검사한 뒤 토큰 주인 계정으로
      채운다(`ax_agent_template.on_behalf`로 문맥에 묶으면 `call_agent`가 메타 값을 덮어쓴다).
      요청 본문·도구 입력에 적힌 값은 버린다. `call_agent`는 메타를 복사하므로 다음 에이전트로
      이어진다. 받는 에이전트 서버는 이 값으로 LLM 사용 기록(`LlmCallRecord.account_id`)을 채워
      "내 MCP 사용량"을 센다. **권한 판단에는 쓰지 않는다**(누가 시켰는지 기록·사용량 셈에만).
    """

    context_id: str
    tenant_id: str = TENANT_ID_DEFAULT
    token: str
    path: list[str] = Field(default_factory=list)
    deadline: UtcDatetime
    idempotency_key: str | None = None
    requester: str | None = None
    on_behalf_of: str | None = None

    # 호환: 비어 있는 `on_behalf_of`는 직렬화에서 키째 뺀다(Evidence.notices와 같은 이유).
    # 판매서버(shop-api)의 옛 계약 사본은 extra="forbid"라 `"on_behalf_of": null`만 실려도 거부한다.
    @model_serializer(mode="wrap")
    def _omit_empty_on_behalf_of(self, handler: SerializerFunctionWrapHandler):  # noqa: ANN202
        data = handler(self)
        if isinstance(data, dict) and data.get("on_behalf_of") is None:
            data.pop("on_behalf_of", None)
        return data


class Evidence(AxModel):
    """결과에 붙는 근거. 근거가 없으면 "확인할 수 없음"으로 답한다."""

    queries: list[str] = Field(default_factory=list)
    data_summary: str = ""
    collected_at: UtcDatetime | None = None
    ref_ids: list[str] = Field(default_factory=list)
    # 응답을 읽는 사람·AI에게 알리는 주의 사항. `data_summary`(숫자 검증을 거친 문장)와 섞지 않는다.
    # 예: 총괄이 이 단계의 반복 지연을 진단해 보냈다는 알림(에이전트 평가)
    notices: list[str] = Field(default_factory=list)

    # 호환: 비어 있는 `notices`는 직렬화에서 키째 뺀다.
    # 판매서버(shop-api)는 계약을 자체 사본(11/16판)으로 쓰고 그 Evidence에는 `notices`가 없는데
    # 기반 클래스가 extra="forbid"라, 기본값 `"notices": []`만 실려도 INVALID_OUTPUT으로 거부했다.
    # 받는 쪽(model_validate)은 키가 없으면 기본값 []라 AXcellent 안에서는 차이가 없다.
    # 한계: 노트가 하나라도 있으면 `notices`가 그대로 나가므로 옛 계약 사본을 쓰는 호출자
    # (shop-api)는 같은 이유로 거부할 수 있다. 호출자별로 빼는 옵션은 요청자 식별이 단순하지 않아
    # 이번엔 하지 않았다(후속). 필드는 삭제·이름 변경 없이 그대로 둔다.
    @model_serializer(mode="wrap")
    def _omit_empty_notices(self, handler: SerializerFunctionWrapHandler):  # noqa: ANN202
        data = handler(self)
        if isinstance(data, dict) and not data.get("notices"):
            data.pop("notices", None)
        return data


class AgentRequest(AxModel):
    """에이전트에 일을 맡기는 요청.

    - `untrusted`: 고객 문의·판매자 정보 같은 외부 입력. 프롬프트에는 데이터로만 넣고
      지시로 해석하지 않는다. 다른 에이전트에 넘길 때도 이 구획을 유지한다.
    - `sequence`: 순서가 중요한 요청(예: 보상 쿠폰)에서 쓴다. 번호가 건너뛰면 `SEQUENCE_GAP`.
    """

    meta: RequestMeta
    skill: str
    input: dict[str, Any] = Field(default_factory=dict)
    untrusted: dict[str, Any] = Field(default_factory=dict)
    sequence: int | None = None


class AgentResult(AxModel):
    """에이전트의 결과.

    `outcome` 관례:
    - 정책 거절: `auto_rejected` (status는 `completed`)
    - 재무 교차검증 거절: `rejected_by_review` (status는 `completed`)
    - 중복 연결: `linked_duplicate`
    - 받는 서비스가 꺼져 있어 사람 할 일함으로 넘김: `handed_to_human` (status는 `input-required`,
      `reason_code`는 `dept_input`, output에 `task_id`. `schemas/handoff.py`)
    - 받는 서비스가 꺼져 있고 조직도에도 맡은 부서가 없음: `no_assignee` (status는 `failed`)
    - 받는 서비스가 꺼져 있고 목록·조회 skill이라 넘기지 않음: `service_disabled`
      (status는 `rejected`)
    - 요청 거부(`rejected`): `ErrorCode` 값
    - 마감 초과는 `canceled`(outcome `TIMEOUT`), 실행 오류는 `failed`

    `fallback`은 LLM·스키마 검증이 실패해 고정 템플릿으로 대체했을 때 true다.
    `fallback_reason`은 그 이유 코드다(없으면 None). 예: data `explain`의 `llm_unavailable`
    (모델 설정 없음)·`llm_error`(호출 실패)·`format_error`(응답 형식 불일치)·`number_invalid`
    (계산에 없는 숫자). `fallback_numbers`는 `number_invalid`일 때 걸린 숫자다. LLM 문장 원문은
    넣지 않는다.
    """

    meta: RequestMeta
    status: TaskStatus
    outcome: str | None = None
    output: dict[str, Any] = Field(default_factory=dict)
    evidence: Evidence = Field(default_factory=Evidence)
    reason_code: InputReason | None = None
    decision_reason: str | None = None
    alternatives_considered: list[str] = Field(default_factory=list)
    fallback: bool = False
    fallback_reason: str | None = None
    fallback_numbers: list[float] = Field(default_factory=list)

    @model_validator(mode="after")
    def _check_status_fields(self) -> AgentResult:
        if self.status == TaskStatus.INPUT_REQUIRED and self.reason_code is None:
            raise ValueError("input-required에는 reason_code가 필요하다")
        if self.status != TaskStatus.INPUT_REQUIRED and self.reason_code is not None:
            raise ValueError("reason_code는 input-required일 때만 쓴다")
        if self.status == TaskStatus.REJECTED:
            codes = {code.value for code in ErrorCode}
            if self.outcome not in codes:
                raise ValueError("rejected의 outcome은 ErrorCode 값이어야 한다")
        return self
