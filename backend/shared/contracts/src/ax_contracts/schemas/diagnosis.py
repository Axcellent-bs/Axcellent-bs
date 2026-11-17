"""에이전트 평가 진단 요청 (인사 → 총괄).

에이전트 평가에서 같은 단계의 "확인 필요"가 반복되면 인사 AI가 총괄 AI에 진단을 요청한다.
인사는 시간만 본 결과(어느 에이전트의 어느 단계가 몇 번 확인 필요였는지, 근거 이슈)까지만 보내고,
왜 그런지(원인)는 총괄이 진단한다. 이 파일의 모양은 추가만 한다.

- `request_agent_diagnosis`(인사 skill): 아카이브가 이슈 판정을 카드에 저장한 뒤 부른다.
- `diagnose_agent_issue`(총괄 skill): 인사가 반복을 찾았을 때 보낸다.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from ax_contracts.schemas.common import AxModel, UtcDatetime


class DiagnosisEvidence(AxModel):
    """반복 판정의 근거가 된 이슈 하나. 인사가 저장된 판정에서 그대로 옮긴다."""

    context_id: str
    title: str
    verdict: str  # 그 이슈에서 이 단계의 판정(check 또는 silent)
    work_seconds: float | None = None
    attempts: int = Field(default=0, ge=0)
    limit_seconds: float
    reason: str | None = None  # 늦은 이유(시간 계산 결과 문장)
    at: UtcDatetime | None = None


class DiagnoseAgentIssueInput(AxModel):
    """총괄에 보내는 진단 요청. 원인은 담지 않는다(총괄이 밝힌다)."""

    agent: str  # 문제가 반복된 에이전트(부서) 이름
    step_id: str
    step_label: str
    window: int = Field(ge=1)  # 살펴본 최근 이슈 수
    flagged: int = Field(ge=1)  # 그중 확인 필요·응답 끊김이었던 이슈 수
    criteria_version: int = Field(ge=1)
    evidence: list[DiagnosisEvidence] = Field(min_length=1)  # 새로운 이슈부터
    requested_at: UtcDatetime


DiagnosisStatus = Literal["received", "updated", "duplicate", "limited"]


class DiagnoseAgentIssueOutput(AxModel):
    """총괄이 요청을 받은 결과.

    - `received`: 새 진단 요청으로 접수
    - `updated`: 열린 요청에 새 근거를 더함
    - `duplicate`: 이미 받은 근거라 그대로 둠
    - `limited`: 에이전트당 하루 한도를 넘어 받지 않음(다음 판정 때 다시 보낸다)
    """

    request_id: str | None = None
    status: DiagnosisStatus
    duplicate: bool = False


class RequestAgentDiagnosisInput(AxModel):
    """아카이브가 판정을 저장한 이슈. 인사는 이 이슈를 포함한 최근 평가를 본다."""

    context_id: str


class SentDiagnosis(AxModel):
    agent: str
    step_id: str
    status: DiagnosisStatus | Literal["failed"]
    request_id: str | None = None


class RequestAgentDiagnosisOutput(AxModel):
    """인사가 반복을 찾아 총괄에 보낸 결과. 반복이 없으면 `sent`가 비어 있다."""

    checked_events: int = Field(ge=0)
    found: int = Field(ge=0)  # 조건을 채운 (에이전트, 단계) 수
    sent: list[SentDiagnosis] = Field(default_factory=list)


# ---- 총괄 → 에이전트 개선 노트 (진단한 결과를 해당 에이전트에 전달) ------------------------------
class ReceiveImprovementNoteInput(AxModel):
    """총괄이 진단(`diagnosed`)을 해당 에이전트에 보내는 개선 노트.

    노트는 기록·알림이다. 받은 에이전트의 동작을 바꾸지 않고, 에이전트가 다음 일에서 참고하거나
    (예: LLM 단계의 입력) 담당자가 읽는다. `revoke`가 참이면 이미 보낸 같은 노트를 쓰지 않게 거둔다.
    """

    note_id: str  # 진단 번호에서 만든다(같은 진단은 같은 노트)
    diagnosis_id: str
    agent: str  # 노트를 받을 에이전트(부서) 이름. 받는 쪽이 자기 이름과 같은지 확인한다
    step_id: str
    step_label: str
    summary: str  # 진단 문장(규칙 또는 LLM)
    findings: list[str] = Field(default_factory=list)  # 찾은 겹침 문장
    evidence_contexts: list[str] = Field(default_factory=list)  # 근거 이슈 번호
    method: Literal["rules", "llm"] = "rules"
    issued_at: UtcDatetime
    revoke: bool = False
    # 거짓이면 원인 미확정(근거 부족)이다. 같은 단계가 반복해 느렸다는 사실만 담는다
    cause_known: bool = True


NoteStatus = Literal["accepted", "duplicate", "revoked", "rejected"]


class ReceiveImprovementNoteOutput(AxModel):
    """받은 에이전트의 응답. `rejected`면 이유(`reason`)를 적는다(예: 자기 일이 아님)."""

    note_id: str
    status: NoteStatus
    reason: str | None = None
