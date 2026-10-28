"""사람 할 일함으로 넘기는 업무 (업무 요청 라우터).

고객사는 서비스를 골라서 산다. 다른 서비스에 일을 맡길 때 받는 서비스가 켜져 있으면
A2A로 보내고, 꺼져 있으면 그 업무를 맡은 부서(사람)의 할 일함에 `HandoffTask`로 넣는다.
같은 요청이 받는 쪽만 바뀐다.

- 넣는 쪽: agent-template `call_agent()`가 dashboard-api `POST /api/dashboard/handoffs`로 넣는다
  (에이전트 토큰). 에이전트는 dashboard-api를 import하지 않는다.
- 받는 쪽(사람): 콘솔 할 일함에서 결과를 적고 끝낸다
  (`POST /api/dashboard/handoffs/{task_id}/complete`, 사람 토큰).
  그러면 dashboard-api가 `handoff.completed` 이벤트를 발행한다.
- 맡긴 쪽 에이전트는 `AgentResult(status=input-required, outcome="handed_to_human")`를 받고,
  `output.task_id`로 나중에 온 `handoff.completed`를 이어 붙인다.
- 맡은 부서는 조직도 설정 파일(`backend/config/org.yaml`)에서만 정한다.
  코드에 부서 이름을 쓰지 않고, 조직도에 없으면 지어내지 않고 `failed` + `no_assignee`로 답한다.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import Field, model_validator

from ax_contracts.schemas.common import TENANT_ID_DEFAULT, AxModel, UtcDatetime

# `AgentResult.outcome` 값
HANDED_TO_HUMAN = "handed_to_human"  # status는 input-required, output은 `HandedToHumanOutput`
NO_ASSIGNEE = "no_assignee"  # status는 failed. 받는 서비스가 꺼져 있고 조직도에도 맡은 부서가 없다
INBOX_UNREACHABLE = "inbox_unreachable"  # status는 failed. 할 일함(dashboard-api)에 넣지 못했다


class HandoffStatus(StrEnum):
    OPEN = "open"  # 사람이 아직 처리하지 않음
    DONE = "done"  # 사람이 처리함
    REJECTED = "rejected"  # 사람이 처리하지 않기로 함


class HandoffCreate(AxModel):
    """할 일함에 넣는 요청 본문. `task_id`·`status`·시각은 할 일함이 정한다.

    - `from_service`는 넣는 쪽 토큰에서 다시 채운다(본문 값은 믿지 않는다).
    - `untrusted`: 원래 요청의 외부 입력 구획을 그대로 옮긴다. 화면은 데이터로만 보여 준다.
    - `idempotency_key`: 같은 값으로 다시 넣으면 새로 만들지 않고 처음 만든 일을 돌려준다.
    """

    tenant_id: str = TENANT_ID_DEFAULT
    context_id: str
    from_service: str
    to_service: str
    skill: str
    input: dict[str, Any] = Field(default_factory=dict)
    untrusted: dict[str, Any] = Field(default_factory=dict)
    reason: str
    assignee_unit: str = Field(min_length=1)
    assignee: str | None = None
    idempotency_key: str | None = None


class HandoffTask(AxModel):
    """사람 할 일함의 일 하나. `assignee_unit`은 부서, `assignee`는 담당자(조직도에 없으면 None)."""

    task_id: str  # hof_…
    tenant_id: str = TENANT_ID_DEFAULT
    context_id: str
    from_service: str
    to_service: str
    skill: str
    input: dict[str, Any] = Field(default_factory=dict)
    untrusted: dict[str, Any] = Field(default_factory=dict)
    reason: str
    assignee_unit: str
    assignee: str | None = None
    status: HandoffStatus = HandoffStatus.OPEN
    result: str | None = None  # 사람이 적은 결과
    created_at: UtcDatetime
    closed_at: UtcDatetime | None = None

    @model_validator(mode="after")
    def _closed_fields(self) -> HandoffTask:
        if self.status == HandoffStatus.OPEN and (self.result is not None or self.closed_at):
            raise ValueError("open인 일에는 result·closed_at이 없다")
        if self.status != HandoffStatus.OPEN and self.closed_at is None:
            raise ValueError("끝난 일에는 closed_at이 필요하다")
        return self


class HandoffCompleteBody(AxModel):
    """사람이 일을 끝낼 때 보내는 본문. `result`는 사람이 적은 결과다."""

    result: str = Field(min_length=1, max_length=2000)
    status: Literal["done", "rejected"] = "done"


class HandedToHumanOutput(AxModel):
    """`outcome="handed_to_human"`일 때 `AgentResult.output` 형식."""

    task_id: str
    to_service: str
    assignee_unit: str
    assignee: str | None = None
