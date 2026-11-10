"""기획 질문 목록 skill(`list_planning_questions`)의 입출력."""

from __future__ import annotations

from pydantic import Field

from ax_contracts.schemas.common import AxModel
from support_agent.planning_question_inbox import PlanningQuestionRecord, QuestionStatus


class ListPlanningQuestionsInput(AxModel):
    status: QuestionStatus | None = "open"  # 기본은 아직 답하지 못한 질문만
    request_id: str | None = None


class ListPlanningQuestionsOutput(AxModel):
    items: list[PlanningQuestionRecord] = Field(default_factory=list)
    open_count: int = 0  # 요청한 회사의 아직 답하지 못한 질문 수 (거른 뒤가 아니라 전체)
