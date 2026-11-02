"""기획 → 총괄 되묻기 (`answer_planning_question`, 이슈 #268).

총괄은 시나리오 A에서 원인 확인 업무를 기획 `create_ticket`으로 보낸다(`cause_check` 단계).
기획이 그 요청의 근거를 되물으면 총괄은 **그 요청을 보낸 이벤트의 기록**으로만 답한다.

- 이벤트의 `cause_check` 결과에 `request_id`와 `context_id`(기획이 돌려준 ctx_req_)가 있고, 둘 다
  질문과 같아야 총괄이 보낸 요청이다. 하나라도 다르면 `unknown`이다.
- 근거(`about: evidence`) 질문이고 보낼 때 만든 근거 문장이 있으면 `answered`. 문장은
  `_cause_check`가 데이터 분석이 준 요약·후보 설명을 옮긴 것을 다시 만든 값이라 지어내지 않는다.
- 총괄이 보낸 요청인데 기록으로 답할 수 없으면(다른 칸·고를 답·근거 없음) `needs_human`.
  사람이 답하면 `human_answer_request`로 만든 요청을 planning `receive_planning_answer`(A)에 보낸다.
- 상태를 바꾸지 않는 read skill이라 `execute()`를 거치지 않는다.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from ax_contracts.schemas import AgentRequest, RequestMeta
from ax_contracts.schemas.planning import (
    AnswerPlanningQuestionInput,
    AnswerPlanningQuestionOutput,
    ReceivePlanningAnswerInput,
)
from ax_orchestrator.manager import IncidentManager
from ax_orchestrator.models import Incident
from ax_orchestrator.plans.scenario_a import _cause_check

RECEIVE_SKILL = "receive_planning_answer"
ANSWERABLE_ABOUT = "evidence"
SEND_DEADLINE = timedelta(seconds=30)


def find_sent(manager: IncidentManager | None, request_id: str, context_id: str) -> Incident | None:
    """원인 확인 요청(`cause_check`)을 보낸 이벤트. 요청 번호와 요청 ctx가 모두 맞아야 한다."""
    if manager is None:
        return None
    for inc in manager.incidents.values():
        sent = inc.outputs.get("cause_check") or {}
        if sent.get("request_id") == request_id and sent.get("context_id") == context_id:
            return inc
    return None


def answer_question(
    manager: IncidentManager | None, q: AnswerPlanningQuestionInput, context_id: str
) -> AnswerPlanningQuestionOutput:
    """기록으로 답한다. 기록에 없으면 `unknown`, 기록으로 못 답하면 `needs_human`."""
    inc = find_sent(manager, q.request_id, context_id)
    if inc is None:
        return AnswerPlanningQuestionOutput(status="unknown")
    sent = _cause_check(inc)
    if q.about != ANSWERABLE_ABOUT or q.options or not sent["evidence"]:
        return AnswerPlanningQuestionOutput(status="needs_human")
    return AnswerPlanningQuestionOutput(
        status="answered",
        answer=sent["evidence"],
        answered_by="agent",
        evidence_ids=list(dict.fromkeys([*sent["evidence_ids"], inc.context_id])),
    )


def human_answer_request(
    meta: RequestMeta,
    q: AnswerPlanningQuestionInput,
    answer: str,
    evidence_ids: list[str] | None = None,
) -> AgentRequest:
    """`needs_human`으로 둔 질문에 사람이 답하면 planning에 보낼 요청 (A, idempotency_key 필수).

    같은 질문의 재전송은 같은 키라 planning이 한 번만 적용한다(`duplicate`).
    """
    payload = ReceivePlanningAnswerInput(
        question_id=q.question_id,
        request_id=q.request_id,
        answer=answer,
        answered_by="human",
        evidence_ids=evidence_ids or [],
    )
    send_meta = meta.model_copy(
        update={
            "idempotency_key": f"{meta.context_id}:{RECEIVE_SKILL}:{q.question_id}",
            "deadline": datetime.now(UTC) + SEND_DEADLINE,  # 사람이 늦게 답해도 새로 잡는다
        }
    )
    return AgentRequest(meta=send_meta, skill=RECEIVE_SKILL, input=payload.model_dump(mode="json"))
