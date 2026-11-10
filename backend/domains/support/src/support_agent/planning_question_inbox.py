"""기획이 되물었는데 기록으로 답하지 못한 질문(`needs_human`)을 사람이 답할 때까지 모아 둔다.

- `answer_planning_question`이 `needs_human`으로 답한 질문만 쌓는다.
  `answered`·`unknown`은 쌓지 않는다.
  기획은 멈추지 않고 기획서를 이어 쓰지만 질문이 열려 있는 동안 그 기획서를 확정하지 않는다
  ("답 대기").
  이 목록은 상담원이 답해야 할 질문을 보여 주는 용도다.
- 같은 질문(`question_id`)이 다시 오면 새 줄을 만들지 않고, 처음 받은 시각을 그대로 둔다.
- 질문 문구는 기획이 쓴 글이지만 개인정보가 섞일 수 있어 저장 전에 가린다(`guard.mask_pii`).
- 지금은 메모리에만 둔다(재시작하면 비워진다). DB 저장은 후속이다.
  사람이 답하는 skill과 화면도 후속이다.
- 이 저장소는 받은 질문을 적어 두는 기록이다. 사업 상태를 바꾸는 일(답을 기획에 보내는 일)은 여기서
  하지 않는다. 그 일은 `execute()`를 거치는 별도 skill이 한다.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from ax_contracts.schemas.common import AxModel
from ax_contracts.schemas.planning import AnswerPlanningQuestionInput
from support_agent import guard
from support_agent.store import now_iso

QuestionStatus = Literal["open", "answered"]
MAX_QUESTION_CHARS = 500


class PlanningQuestionRecord(AxModel):
    question_id: str  # Q-<REQ>-n (기획 발급)
    request_id: str  # REQ-n
    context_id: str  # 기획이 발급한 변경 요청 사건 ctx_req_…
    tenant_id: str
    spec_id: str | None = None
    about: str | None = None  # 묻는 칸 (예: evidence·done·R1)
    question: str  # 개인정보를 가린 질문 문구
    options: list[str] = Field(default_factory=list)  # 고를 답이 있으면
    status: QuestionStatus = "open"
    received_at: str
    answered_at: str | None = None  # 사람이 답해 기획이 받은 시각
    answered_by: str | None = None  # 답한 사람(사람 토큰의 이름, 본문 값이 아니다)


class PlanningQuestionInbox:
    def __init__(self) -> None:
        self._items: dict[str, PlanningQuestionRecord] = {}

    def record(
        self, q: AnswerPlanningQuestionInput, tenant_id: str, context_id: str
    ) -> PlanningQuestionRecord:
        """`needs_human`으로 답한 질문을 적는다. 같은 질문이 다시 오면 처음 기록을 돌려준다."""
        key = _key(tenant_id, q.question_id)
        found = self._items.get(key)
        if found is not None:
            return found
        record = PlanningQuestionRecord(
            question_id=q.question_id,
            request_id=q.request_id,
            context_id=context_id,
            tenant_id=tenant_id,
            spec_id=q.spec_id,
            about=q.about,
            question=guard.mask_pii(q.question)[0][:MAX_QUESTION_CHARS],
            options=[guard.mask_pii(option)[0] for option in q.options],
            received_at=now_iso(),
        )
        self._items[key] = record
        return record

    def mark_answered(
        self, tenant_id: str, question_id: str, author: str
    ) -> PlanningQuestionRecord | None:
        """기획이 사람의 답을 받았으면 질문을 닫는다. 이미 닫혔으면 처음 닫은 기록을 그대로 둔다."""
        key = _key(tenant_id, question_id)
        found = self._items.get(key)
        if found is None or found.status == "answered":
            return found
        closed = found.model_copy(
            update={"status": "answered", "answered_at": now_iso(), "answered_by": author}
        )
        self._items[key] = closed
        return closed

    def get(self, tenant_id: str, question_id: str) -> PlanningQuestionRecord | None:
        return self._items.get(_key(tenant_id, question_id))

    def all(self, tenant_id: str | None = None) -> list[PlanningQuestionRecord]:
        """받은 순서(오래된 질문이 앞)."""
        items = [r for r in self._items.values() if tenant_id is None or r.tenant_id == tenant_id]
        return sorted(items, key=lambda r: r.received_at)

    def open(self, tenant_id: str | None = None) -> list[PlanningQuestionRecord]:
        return [r for r in self.all(tenant_id) if r.status == "open"]


def _key(tenant_id: str, question_id: str) -> str:
    return f"{tenant_id}:{question_id}"
