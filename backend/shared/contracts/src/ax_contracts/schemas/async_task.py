"""긴 작업(비동기): 몇 분에서 몇 시간 걸리는 부서 작업을 총괄이 붙잡고 기다리지 않는 방식.

지금까지는 총괄이 부서를 한 번 부르고 응답이 올 때까지(기본 30초, 약속이 긴 단계는 최대 300초)
기다렸다. 그보다 오래 걸리는 일은 이 방식으로 돌릴 수 없다. 그래서 부서가 일을 **접수만** 알리고,
일하는 동안 **진행 알림**을 보내고, 끝나면 **완료 이벤트**로 알린다.

1. 접수: 총괄의 요청에 부서가 `AgentResult(status=working, outcome="accepted_async")`로 답한다.
   `output`은 `AsyncAcceptedOutput`(`task_id` 필수). 부서는 이 일을 이어서 한다.
2. 진행 알림: 일하는 동안 `task.progress` 이벤트를 주기적으로 발행한다(`TaskProgressData`).
   총괄은 마지막 알림 뒤 정해진 시간 동안 아무 알림이 없으면 "응답 끊김"으로 보고 단계를
   실패로 끝낸다(사유 코드 `no_response`). 인사 에이전트 평가는 이 끊김을 따로 센다.
3. 완료: 끝나면 `task.completed` 이벤트를 한 번 발행한다(`TaskCompletedData`). `done`이면
   `output`이 단계의 결과가 되고, `failed`면 단계가 실패로 끝난다.

규칙
- 이벤트의 `source`와 `context_id`는 총괄이 요청한 부서와 이벤트여야 한다. 아니면 무시한다
  (이벤트는 누가 보냈든 위조될 수 있어서, 총괄은 자기가 일을 맡긴 부서의 것만 받는다).
- 같은 `task_id`의 완료는 한 번만 반영한다. 끝난 뒤 온 알림과 완료는 무시한다.
- 기본은 지금처럼 동기다. 부서가 `working`으로 답할 때만 이 방식이 돈다.
- 총괄이 재시작하면 기다리던 단계는 이어 가지 않고 "재시작으로 중단됨"으로 남긴다
  (사람 할 일함 대기와 같은 원칙).

부서가 쓰는 도구는 `ax_agent_template.async_task`(`accepted`, `report_progress`,
`report_completed`)다.
"""

from __future__ import annotations

from pydantic import Field

from ax_contracts.schemas.common import AxModel

# `AgentResult.outcome` 값: status는 working. output은 `AsyncAcceptedOutput`
ACCEPTED_ASYNC = "accepted_async"


class AsyncAcceptedOutput(AxModel):
    """`outcome="accepted_async"`일 때 `AgentResult.output` 형식.

    - `task_id`: 이 일의 번호. 진행 알림과 완료 이벤트가 같은 값을 쓴다.
    - `expected_seconds`: 부서가 말하는 예상 소요 시간(초). 없으면 총괄은 단계의 약속 시간을 쓴다.
    """

    task_id: str = Field(min_length=1)
    expected_seconds: float | None = Field(default=None, gt=0)
