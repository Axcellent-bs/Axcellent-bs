"""기획 질문 목록 skill(`list_planning_questions`)의 입출력."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from ax_contracts.schemas.common import AxModel
from support_agent.planning_question_inbox import PlanningQuestionRecord, QuestionStatus


class ListPlanningQuestionsInput(AxModel):
    status: QuestionStatus | None = "open"  # 기본은 아직 답하지 못한 질문만
    request_id: str | None = None


class SendPlanningAnswerInput(AxModel):
    """사람이 기획 질문에 답한다. 답 문구에 개인정보가 있으면 보내지 않는다."""

    question_id: str
    answer: str = Field(min_length=1, max_length=1000)
    evidence_ids: list[str] = Field(default_factory=list, max_length=20)  # 답의 근거 ID
    author: str = Field(min_length=1, max_length=80)  # 답한 사람. 서버가 사람 토큰에서 채운다


class SendPlanningAnswerOutput(AxModel):
    question_id: str
    request_id: str
    # applied: 기획이 받아 반영 / duplicate: 이미 받은 답 / unknown_question: 기획이 모르는 질문
    planning_outcome: Literal["applied", "duplicate", "unknown_question"]
    status: QuestionStatus  # 이 질문의 지금 상태 (기획이 받았으면 answered)
    spec_id: str | None = None


class ListPlanningQuestionsOutput(AxModel):
    items: list[PlanningQuestionRecord] = Field(default_factory=list)
    open_count: int = 0  # 요청한 회사의 아직 답하지 못한 질문 수 (거른 뒤가 아니라 전체)
