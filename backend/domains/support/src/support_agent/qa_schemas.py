"""qa_findings skill의 입력·출력 모델. 점수 없이 규칙 코드만 낸다. 고객 원문은 싣지 않는다."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from ax_contracts.schemas.common import AxModel

QaRule = Literal[
    "guard_hit",
    "no_evidence_reply",
    "weak_grounding",
    "unsupported_number",
    "language_mismatch",
    "heavy_edit",
]
QaSeverity = Literal["flag", "info"]


class QaFindingsInput(AxModel):
    window_days: int = Field(default=7, ge=1, le=90)  # 시드
    inquiry_id: int | None = None  # 문의 한 건만 점검한다
    sample_size: int = Field(default=5, ge=0, le=50)  # 사람이 볼 대조군 크기 (시드)


class QaFinding(AxModel):
    """규칙 하나가 문의 한 건(또는 그 안의 답변 한 개)에서 낸 의심. 문장 없이 코드와 값만 담는다."""

    inquiry_id: int
    message_id: int | None = None  # 답변·분석이 달린 메시지 번호
    rule: QaRule
    severity: QaSeverity
    codes: list[str] = Field(default_factory=list)  # 가드 규칙 코드, 근거 없는 숫자 같은 값
    value: float | None = None  # 규칙이 잰 값 (겹침 비율·수정 정도)
    threshold: float | None = None  # 그때의 기준값 (시드)


class QaReport(AxModel):
    window_days: int
    evaluated_at: str
    replies_checked: int  # 점검한 보낸 답변 수 (분모)
    replies_flagged: int  # 보낸 답변 중 `flag` 규칙(no_evidence_reply·language_mismatch)에 걸린 수
    flagged_rate: float | None = None  # 분모가 0이면 None
    rule_counts: dict[str, int] = Field(default_factory=dict)
    findings: list[QaFinding]
    human_review_sample: list[int] = Field(default_factory=list)  # 규칙에 안 걸린 문의 번호만
    notes: list[str] = Field(default_factory=list)
