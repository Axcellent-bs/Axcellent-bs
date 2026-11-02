"""기획에 보낼 변경 요청 제안 skill의 입출력 모델. 제안은 급증 감지 결과에서 코드가 만든다."""

from __future__ import annotations

from pydantic import Field

from ax_contracts.schemas.common import AxModel
from support_agent.insight_schemas import SurgeInput
from support_agent.proposal_decisions import DeclineReason


class ProposeChangesInput(SurgeInput):
    """급증 감지와 같은 입력 (창 길이·기준 기간·최소 건수·배수·기준 방식)."""


class ChangeProposal(AxModel):
    """문의 AI가 만든 변경 요청안. 아직 아무것도 보내지 않았다 (보내는 일은 사람이 고른다).

    `title`·`evidence`·`inquiry_id`·`evidence_ids`는 그대로 `request_change`의 입력이 된다.
    """

    topic_code: str
    title: str  # 바꿀 내용 한 줄 (고객 원문 없음)
    evidence: str  # 근거 요약: 건수·평소 대비 배수 같은 사실만
    inquiry_id: int  # 대표 문의: 급증 샘플 중 번호가 가장 작은(가장 오래된) 문의
    evidence_ids: list[str] = Field(default_factory=list)  # 나머지 샘플 문의 (`inq_<번호>`)
    count: int  # 최근 창의 이 분류 문의 수
    ratio: float | None = None  # 평소 대비 배수 (평소가 0이면 None)
    already_requested: bool = False  # 대표 문의에 이미 연결된 변경 요청이 있다
    declined: bool = False  # 상담원이 "보내지 않기"를 골랐다 (이유는 decline_reason)
    decline_reason: DeclineReason | None = None


class DeclineProposalInput(AxModel):
    """ "보내지 않기". 제안은 저장하지 않으므로 분류와 대표 문의로 가리킨다.

    `author`는 서버가 사람 토큰에서 채운 이름이다 (상담원이 본문으로 정하지 않는다).
    자유 글 `note`는 untrusted로 따로 받는다.
    """

    topic_code: str = Field(min_length=1, max_length=40)
    inquiry_id: int
    reason: DeclineReason
    author: str = Field(min_length=1, max_length=60)


class DeclineProposalOutput(AxModel):
    topic_code: str
    inquiry_id: int
    reason: DeclineReason
    decided_by: str
    created: bool  # False면 이미 같은 제안에 기록이 있어 그것을 돌려준 것이다


class ProposeChangesReport(AxModel):
    window_minutes: int
    baseline_hours: int
    evaluated_at: str
    proposals: list[ChangeProposal] = Field(default_factory=list)
