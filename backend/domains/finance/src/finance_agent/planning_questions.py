"""기획 → 재무 되묻기 (`answer_planning_question`, 이슈 #269).

기획이 재무가 보낸 요청(`create_ticket`)의 기획서를 쓰다가 근거가 모자라면 재무에 묻는다.
재무는 **자기가 보낸 요청의 기록**으로만 답한다. 지어내지 않는다.

- 재무는 아직 기획에 요청을 보내지 않아 기록이 비어 있고, 지금은 모든 질문이 `unknown`이다.
  요청을 보내는 코드가 생기면 성공한 뒤 `record_sent_request`로 남긴다.
- 답하는 규칙 (코드 규칙, LLM 없음)
  - 이 요청을 재무가 보낸 기록이 없으면 `unknown`
  - 근거(`about: evidence`) 질문이고 기록에 사실이 있으면 `answered` (사실 + 근거 ID)
  - 재무가 보낸 요청이지만 기록으로 답할 수 없으면 `needs_human`
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ax_contracts.schemas.planning import (
    AnswerPlanningQuestionInput,
    AnswerPlanningQuestionOutput,
)

ANSWERABLE_ABOUT = "evidence"


@dataclass(frozen=True)
class SentRequest:
    """재무가 기획에 보낸 요청 한 건의 기록."""

    tenant_id: str
    context_id: str  # 기획이 돌려준 ctx_req_
    request_id: str  # REQ-n
    facts: tuple[str, ...] = ()  # 코드가 만든 사실 문장 (건수·시각)
    evidence_ids: tuple[str, ...] = ()


@dataclass
class SentRequestStore:
    """보낸 요청 기록. 프로세스 안에만 둔다 (다시 띄우면 비고, 그때 질문은 `unknown`)."""

    items: dict[tuple[str, str], SentRequest] = field(default_factory=dict)

    def find(self, tenant_id: str, request_id: str, context_id: str) -> SentRequest | None:
        """요청 번호와 사건 번호가 모두 맞아야 재무가 보낸 요청이다."""
        item = self.items.get((tenant_id, request_id))
        return item if item is not None and item.context_id == context_id else None


STORE = SentRequestStore()


def record_sent_request(
    tenant_id: str,
    context_id: str,
    request_id: str,
    facts: list[str] | None = None,
    evidence_ids: list[str] | None = None,
) -> None:
    """`create_ticket`이 성공한 뒤 부른다. 돌려받은 `context_id`·`request_id`로 남긴다."""
    STORE.items[(tenant_id, request_id)] = SentRequest(
        tenant_id, context_id, request_id, tuple(facts or ()), tuple(evidence_ids or ())
    )


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
