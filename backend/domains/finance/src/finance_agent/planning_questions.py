"""기획 → 재무 되묻기 (`answer_planning_question`, 이슈 #269).

기획이 재무가 보낸 요청(`create_ticket`)의 기획서를 쓰다가 근거가 모자라면 재무에 묻는다.
재무는 **자기가 보낸 요청의 기록**으로만 답한다. 지어내지 않는다.

- 기록(`SentRequest`)은 재무가 `create_ticket`을 보낸 뒤 `record_sent_request`로 남긴다.
  사실 문장(`facts`)과 근거 ID(`evidence_ids`)는 보낼 때 코드가 만든 것만 담는다.
  (아직 재무에 요청을 보내는 코드가 없어 지금은 기록이 비어 있고, 모든 질문이 `unknown`이다.)
- 답하는 규칙 (코드 규칙, LLM 없음)
  - 이 요청을 재무가 보낸 기록이 없으면 `unknown`
  - 근거(`about: evidence`) 질문이고 기록에 사실이 있으면 `answered` (사실 + 근거 ID)
  - 재무가 보낸 요청이지만 기록으로 답할 수 없으면(사실이 없음·다른 칸·고를 답) `needs_human`.
    사람이 답하면 `human_answer_request`로 만든 요청을
    planning `receive_planning_answer`(A)에 보낸다.
- 상태를 바꾸지 않는 read skill이라 `execute()`를 거치지 않는다.
- 질문 문구는 planning 코드가 만든 것이지만 답 판단에는 쓰지 않는다 (`about`·`options`만 본다).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from ax_contracts.schemas import AgentRequest, RequestMeta
from ax_contracts.schemas.planning import (
    AnswerPlanningQuestionInput,
    AnswerPlanningQuestionOutput,
    ReceivePlanningAnswerInput,
)

PLANNING = "planning"
RECEIVE_SKILL = "receive_planning_answer"
ANSWERABLE_ABOUT = "evidence"
SEND_DEADLINE = timedelta(seconds=30)


@dataclass(frozen=True)
class SentRequest:
    """재무가 planning에 보낸 요청 한 건의 기록."""

    tenant_id: str
    context_id: str  # planning이 돌려준 ctx_req_
    request_id: str  # REQ-n
    facts: tuple[str, ...] = ()  # 코드가 만든 사실 문장 (건수·시각)
    evidence_ids: tuple[str, ...] = ()  # 사례 ID 등


@dataclass
class SentRequestStore:
    """보낸 요청 기록. 프로세스 안에만 둔다 (다시 띄우면 비고, 그때 질문은 `unknown`)."""

    _items: dict[tuple[str, str], SentRequest] = field(default_factory=dict)

    def add(self, item: SentRequest) -> None:
        self._items[(item.tenant_id, item.request_id)] = item

    def find(self, tenant_id: str, request_id: str, context_id: str) -> SentRequest | None:
        """요청 번호와 사건 번호가 모두 맞아야 재무가 보낸 요청이다."""
        item = self._items.get((tenant_id, request_id))
        if item is None or item.context_id != context_id:
            return None
        return item

    def remove(self, tenant_id: str, request_id: str) -> None:
        """거둬들인 요청은 기획이 되물을 일이 없으니 지운다."""
        self._items.pop((tenant_id, request_id), None)

    def clear(self) -> None:
        self._items.clear()


STORE = SentRequestStore()


def record_sent_request(
    tenant_id: str,
    context_id: str,
    request_id: str,
    facts: list[str] | None = None,
    evidence_ids: list[str] | None = None,
) -> SentRequest:
    """`create_ticket`이 성공한 뒤 부른다. 돌려받은 `context_id`·`request_id`로 남긴다."""
    item = SentRequest(
        tenant_id, context_id, request_id, tuple(facts or ()), tuple(evidence_ids or ())
    )
    STORE.add(item)
    return item


def answer_question(
    q: AnswerPlanningQuestionInput, tenant_id: str, context_id: str
) -> AnswerPlanningQuestionOutput:
    """기록으로 답한다. 기록에 없으면 `unknown`, 기록으로 못 답하면 `needs_human`."""
    sent = STORE.find(tenant_id, q.request_id, context_id)
    if sent is None:
        return AnswerPlanningQuestionOutput(status="unknown")
    if q.about != ANSWERABLE_ABOUT or q.options or not sent.facts:
        return AnswerPlanningQuestionOutput(status="needs_human")
    return AnswerPlanningQuestionOutput(
        status="answered",
        answer="; ".join(sent.facts),
        answered_by="agent",
        evidence_ids=list(sent.evidence_ids),
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
