"""hr(인사) 에이전트 skill 입출력 (인사 구성안 HR2, 2026-10-22 팀 공유·합의).

skill과 등급(등급 신고는 안전 레이어 레지스트리가 원본이다):

| skill | 등급 | 하는 일 |
| --- | --- | --- |
| `record_case` | A | 사건·결정 기록 → 결정 카드 (입력 `RecordCase`, 출력 `RecordCaseOutput`) |
| `start_handover` | A | 인수인계 초안·빈 자리·완성도, 빈 자리 질문 |
| `answer_handover_question` | A | 인계자의 답변을 받아 초안을 이어서 보완 |
| `confirm_handover` | B(검증자 planning), 완성도 미달이면 C | 인수인계 확정 |
| `request_assignment_change` | A | 교대·발령·퇴직·입사 요청 접수, 체크리스트·재배정 후보 |
| `confirm_reassignment` | B(코드 규칙 검증) | 담당 재배정 확정 |
| `confirm_offboarding` | C | 퇴직 확정(안전 레이어 보류 큐) |
| `start_onboarding` | A | 읽기 순서·입문 체크리스트 |
| `check_plan` | 읽기 | 새 계획이 과거에 기각된 안인지 판정 |
| `get_roster` | 읽기 | 담당·교대 명부 (개인정보 제외) |
| `find_similar_fraud_cards` | 읽기 | 비슷한 지난 사기 카드와 유사도 (요청자 data) |
| `update_onboarding_step` | A | 온보딩 단계 완료 표시(진행률은 코드가 다시 계산) |
| `decide_candidate_card` | A | 확신 낮은 후보 카드를 사람이 승인·폐기 |
| `list_handovers`·`list_open_questions`·`list_onboardings` | 읽기 | 인수인계·질문·온보딩 현황 |
| `list_changes`·`list_candidate_cards`·`get_audit_timeline` | 읽기 | 변경·후보 카드·감사 기록 |
| `list_cards` | 읽기 | 확정된(후보가 아닌) 결정 카드 목록 |
| `record_resolution` | A | 사건 카드에 해결 내용(원인·조치·결과·소요 시간) 기록 |
| `find_similar_cases` | 읽기 | 비슷한 과거 사건·결정과 당시 해결 내용 |
| `record_event_times` | A | 이벤트 카드에 감지·원인 후보 도착·사람 작업·정상 복귀 시각 기록 |
| `list_duplicate_groups` | 읽기 | 사람 승인을 기다리는 중복 결정 묶음과 최근 병합(되돌리기용) |
| `decide_duplicate_group` | A | 중복 묶음을 사람이 병합하거나 그대로 둠 |
| `restore_merged_card` | A | 병합으로 대체됨이 된 카드를 되돌림 |
| `record_org_change` | A | 조직 변동(발령) 초안 저장. 승인 전에는 인원에 세지 않는다 |
| `decide_org_change` | A | 인사 담당자가 발령 초안을 확정·반려 (요청자 dashboard-api) |
| `list_org_changes` | 읽기 | 발령 기록 목록과 명부 등록 상태 |
| `find_org_change_basis` | 읽기 | 발령 사유와 비슷한 과거 업무 결정 카드 후보(RAG) |
| `parse_org_change_text` | 읽기 | 발령 문장을 입력 칸으로 정리하는 제안(LLM, 저장 안 함) |
| `describe_org_change` | 읽기 | 발령 요약 설명문(숫자는 코드, 문장만 LLM) |
| `set_org_roster_complete` | A | 인사 담당자가 "전 직원 등록 완료"를 표시 (요청자 dashboard-api) |
| `team_headcount` | 읽기 | 그 달 1일 기준 팀별 인원수 (이름·직원 ID 없음, 요청자 finance 팀 예산) |
| `record_leave` | A | 휴가 기간 기록(사유·종류 없음, 요청자 인사 서비스, 사람 토큰만) |
| `cancel_leave` | A | 휴가 기록에 취소 표시(취소한 사람·시각)만 남김(지우지 않음), 사람 토큰만 |
| `get_presence` | 읽기 | 교대 명부와 휴가를 합친 상태. 휴가 기간·입력자는 상세 요청일 때만 |

`RecordCaseOutput`은 모델만 추가했고, hr의 출력 전환은 hr 담당 PR에서 한다.

인계자 질문은 `input-required`(+ `reason_code: human_input`) 결과의 `output`에 담는다.
질문을 기다리는 동안은 hr이 상태를 저장하고, 답변은 새 요청(`answer_handover_question`)으로
받는다. 요청마다 `deadline`은 그대로 짧게 둔다(사람 답변을 기다리며 요청을 열어 두지 않는다).
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, model_validator

from ax_contracts.schemas.common import AxModel, UtcDatetime
from ax_contracts.schemas.record import Card

# ---- record_case ------------------------------------------------------------


class RecordCaseOutput(AxModel):
    """`record_case`의 결과.

    - `card_created`: 결정 카드를 만들어 저장했다.
    - `candidate`: 확신이 낮아 후보(`status=candidate`)로 두었다.
    - `record_only`: 결정이 없는 기록이라 기록만 남겼다.
    - `duplicate`: 같은 `context_id`·종류의 요청이 이미 처리되었다.
    """

    outcome: Literal["card_created", "candidate", "record_only", "duplicate"]
    card_id: str | None = None
    confidence: Literal["low", "mid", "high"] | None = None


# ---- 인수인계 ---------------------------------------------------------------

HandoverStatus = Literal["draft", "input_required", "ready", "confirmed", "held"]


class HandoverItem(AxModel):
    """인계 목록의 한 항목. `source_ref`는 planning 항목 ID나 결정 카드 ID다."""

    item_id: str
    kind: Literal["deployment", "ticket", "pr", "document", "decision", "other"]
    title: str
    source_ref: str | None = None


class HandoverQuestion(AxModel):
    """인계자에게 묻는 빈 자리 질문. 대시보드가 보여 주고 답을 받는다."""

    question_id: str
    context_id: str
    assignee_id: str  # 대상 인계자
    question: str
    gap_field: str  # 빈 자리 필드 이름


class StartHandoverInput(AxModel):
    owner_id: str  # 인계자 (`RosterEntry.member_id`)
    successor_id: str | None = None
    area: str | None = None
    reason: Literal["shift", "transfer", "offboarding", "other"] = "other"


class StartHandoverOutput(AxModel):
    """초안과 완성도. 완성도·빈 자리 수는 코드가 계산한다(LLM 값이 아니다)."""

    handover_id: str
    status: HandoverStatus
    items: list[HandoverItem] = Field(default_factory=list)
    completeness: int = Field(ge=0, le=100)
    gap_count: int = Field(ge=0)
    questions: list[HandoverQuestion] = Field(default_factory=list)


class AnswerHandoverQuestionInput(AxModel):
    question_id: str
    answer: str


class HandoverAnswerRequest(AxModel):
    """dashboard-api가 받는 답변 본문. 같은 답변을 두 번 보내도 한 번만 반영한다."""

    question_id: str
    answer: str
    idempotency_key: str


class AnswerHandoverQuestionOutput(AxModel):
    handover_id: str
    status: HandoverStatus
    completeness: int = Field(ge=0, le=100)
    remaining_questions: list[HandoverQuestion] = Field(default_factory=list)


class ConfirmHandoverInput(AxModel):
    handover_id: str


class ConfirmHandoverOutput(AxModel):
    """`confirmed`: 확정 / `held`: 완성도 미달로 보류 큐 / `rejected`: 검증자가 거절."""

    handover_id: str
    status: Literal["confirmed", "held", "rejected"]
    completeness: int = Field(ge=0, le=100)
    missing_item_ids: list[str] = Field(default_factory=list)
    hold_id: str | None = None


# ---- 교대·발령·퇴직·입사 ------------------------------------------------------


class ChecklistItem(AxModel):
    item_id: str
    title: str
    done: bool = False


class RequestAssignmentChangeInput(AxModel):
    change_kind: Literal["shift", "transfer", "offboarding", "join"]
    member_id: str
    area: str
    effective_at: UtcDatetime | None = None
    successor_id: str | None = None


class RequestAssignmentChangeOutput(AxModel):
    """권한·인계 목록은 전달만 한다. 회수는 각 시스템을 가진 부서가 실행한다."""

    change_id: str
    checklist: list[ChecklistItem] = Field(default_factory=list)
    successor_candidates: list[str] = Field(default_factory=list)
    access_list: list[str] = Field(default_factory=list)
    handover_id: str | None = None
    onboarding_id: str | None = None


class ConfirmReassignmentInput(AxModel):
    change_id: str
    successor_id: str


class ConfirmReassignmentOutput(AxModel):
    """코드 규칙 검증 결과. 담당 공백·중복이 있으면 `rejected`다."""

    change_id: str
    status: Literal["confirmed", "rejected"]
    gap_areas: list[str] = Field(default_factory=list)
    duplicate_areas: list[str] = Field(default_factory=list)


class ConfirmOffboardingInput(AxModel):
    change_id: str


class ConfirmOffboardingOutput(AxModel):
    """퇴직 확정은 C 등급이라 `held`(안전 레이어 보류 큐)로 시작한다."""

    change_id: str
    status: Literal["confirmed", "held"]
    hold_id: str | None = None


# ---- 온보딩 -----------------------------------------------------------------


class OnboardingStep(AxModel):
    step_id: str
    order: int = Field(ge=1)
    title: str
    card_id: str | None = None  # 읽을 결정 카드
    done: bool = False


class StartOnboardingInput(AxModel):
    member_id: str
    area: str
    handover_id: str | None = None


class StartOnboardingOutput(AxModel):
    onboarding_id: str
    steps: list[OnboardingStep] = Field(default_factory=list)
    progress: int = Field(ge=0, le=100)


# ---- 계획 비교 --------------------------------------------------------------

FindingCode = Literal["MATCH", "CONFLICT", "REJECTED_BEFORE"]


class PlanDraft(AxModel):
    title: str
    summary: str
    area: str  # `RosterEntry.area`와 같은 값 집합 (기획서 문단 id를 넣지 않는다)
    kind: Literal["spec", "ticket", "deploy_plan"]
    ref: str | None = None  # 호출 측 참조 id(예: 기획서 문단). 판정에는 쓰지 않는다


class CheckPlanInput(AxModel):
    """새 계획 초안. 초안 속 외부 문구는 `AgentRequest.untrusted`로 따로 보낸다."""

    draft: PlanDraft


class PlanFinding(AxModel):
    code: FindingCode
    level: Literal["info", "warn"]
    message: str
    card_ids: list[str] = Field(default_factory=list)  # 근거 카드
    # LLM이 쓴 설명(참고용). 판정에는 쓰지 않고, 검증에 실패하면 비운다
    explanation: str | None = None


class SimilarCard(AxModel):
    card_id: str
    summary: str
    status: str


class CheckPlanComputed(AxModel):
    """숫자는 코드가 계산한다."""

    days_since_rejected: int | None = Field(default=None, ge=0)
    basis: str | None = None


class CheckPlanOutput(AxModel):
    """`MATCH`와 관련 카드 없음은 `ok`, 그 밖은 `warn`이다. 근거 카드가 없으면 판정하지 않는다."""

    verdict: Literal["ok", "warn"]
    findings: list[PlanFinding] = Field(default_factory=list)
    similar_cards: list[SimilarCard] = Field(default_factory=list)
    computed: CheckPlanComputed = Field(default_factory=CheckPlanComputed)
    no_related_cards: bool = False  # 관련 카드가 없으면 true ("기록에 없음")


# ---- 명부 -------------------------------------------------------------------


class RosterEntry(AxModel):
    """담당 영역과 역할만 담는다(개인 인사 정보 제외)."""

    area: str
    role: str
    member_id: str
    shift_start: UtcDatetime | None = None
    shift_end: UtcDatetime | None = None


class GetRosterInput(AxModel):
    area: str | None = None


class GetRosterOutput(AxModel):
    entries: list[RosterEntry] = Field(default_factory=list)
    gap_areas: list[str] = Field(default_factory=list)  # 담당 공백 영역
    collected_at: UtcDatetime


# ---- 팀 인원수 (재무 팀 예산용, 2026-11-02 #2-재무 합의) ---------------------------------


class TeamHeadcountInput(AxModel):
    """`team_headcount` 입력. 그 달 1일에 어느 팀에 몇 명이 있었는지 묻는다."""

    month: str = Field(pattern=r"^\d{4}-(0[1-9]|1[0-2])$")  # YYYY-MM


class TeamHeadcountRow(AxModel):
    """팀 하나의 인원수. 이름·직원 ID는 넣지 않는다."""

    team: str = Field(min_length=1)  # 조직도(회사 폴더 org.yaml)의 부서 이름
    headcount: int = Field(ge=0)


class TeamHeadcountOutput(AxModel):
    """`team_headcount` 결과. 발령은 발령일이 1일이면 그 달부터, 아니면 다음 달 1일부터 센다.

    파견은 세지 않는다(파견 중이어도 원래 팀). `basis`는 화면에 그대로 보여 줄 기준 문장
    (예: "가상 명부(예시) 기준, 2026-11-01 소속").
    `example`: 실제 명부가 아니라 가상 명부(예시) 인원이면 True.
    재무는 보여 주기만 하고 확정하지 않는다.
    """

    month: str = Field(pattern=r"^\d{4}-(0[1-9]|1[0-2])$")
    rows: list[TeamHeadcountRow] = Field(default_factory=list)
    basis: str | None = None
    collected_at: UtcDatetime
    example: bool = False


# ---- 조직 변동(발령) 기록 (재무 팀 예산용, 2026-11-03) ----------------------------------
# 초안은 승인 없이 저장하고 인원에 세지 않는다. 인사 담당자가 확정해야 `team_headcount`에 센다.

OrgChangeKind = Literal["transfer", "join", "leave"]  # 팀 이동 · 입사 · 퇴사
OrgChangeStatus = Literal["draft", "confirmed", "rejected"]
Date = Field(pattern=r"^\d{4}-(0[1-9]|1[0-2])-(0[1-9]|[12]\d|3[01])$")  # YYYY-MM-DD


class RecordOrgChangeInput(AxModel):
    """`record_org_change` 입력. 팀 이동은 두 팀, 입사는 들어가는 팀, 퇴사는 나가는 팀을 적는다."""

    member_id: str = Field(min_length=1, max_length=64)
    kind: OrgChangeKind
    from_team: str | None = None
    to_team: str | None = None
    effective_date: str = Date
    note: str | None = Field(default=None, max_length=500)
    basis_card_id: str | None = None  # 사람이 고른 근거 결정 카드(선택). 없어도 저장된다

    @model_validator(mode="after")
    def _teams_match_kind(self) -> RecordOrgChangeInput:
        if self.kind == "transfer":
            ok = bool(self.from_team and self.to_team and self.from_team != self.to_team)
        elif self.kind == "join":
            ok = bool(self.to_team) and self.from_team is None
        else:
            ok = bool(self.from_team) and self.to_team is None
        if not ok:
            raise ValueError(f"{self.kind} 발령의 from_team·to_team이 맞지 않는다")
        return self


class OrgChangeView(AxModel):
    """발령 기록 하나. 인원에 세는 첫 달(`counts_from_month`)을 같이 보여 준다."""

    change_id: str
    member_id: str
    kind: OrgChangeKind
    from_team: str | None = None
    to_team: str | None = None
    effective_date: str = Date
    counts_from_month: str = Field(pattern=r"^\d{4}-(0[1-9]|1[0-2])$")
    status: OrgChangeStatus
    note: str | None = None
    basis_card_id: str | None = None
    basis_title: str | None = None  # 근거 카드를 고른 때의 제목
    created_at: UtcDatetime
    decided_at: UtcDatetime | None = None


class RecordOrgChangeOutput(AxModel):
    change: OrgChangeView


class DecideOrgChangeInput(AxModel):
    """인사 담당자가 초안을 확정(`approve`)하거나 반려(`reject`)한다."""

    change_id: str
    decision: Literal["approve", "reject"]
    note: str | None = Field(default=None, max_length=500)


class DecideOrgChangeOutput(AxModel):
    change: OrgChangeView


class ListOrgChangesInput(AxModel):
    status: OrgChangeStatus | None = None
    limit: int = Field(default=100, ge=1, le=500)


class ListOrgChangesOutput(AxModel):
    """발령 기록과 명부 등록 상태. `roster_complete`가 참이어야 확정 기록으로 센다."""

    changes: list[OrgChangeView] = Field(default_factory=list)
    registered_members: int = Field(default=0, ge=0)  # 확정 기록으로 지금 명부에 있는 직원 수
    expected_members: int = Field(default=0, ge=0)  # 전체 직원 수(가정)
    roster_complete: bool = False
    collected_at: UtcDatetime


class SetOrgRosterCompleteInput(AxModel):
    """인사 담당자가 "전 직원 등록 완료"를 표시(`complete=True`)하거나 다시 연다(`False`)."""

    complete: bool = True


class SetOrgRosterCompleteOutput(AxModel):
    roster_complete: bool
    registered_members: int = Field(ge=0)
    expected_members: int = Field(ge=0)


# ---- 조직 변동 RAG·LLM: 근거 카드 찾기(9번) · 문장 정리(10번) · 요약 설명문(11번) ----------
# 모두 조회 전용이다. 저장은 사람이 확인한 뒤 `record_org_change`로 한다.


class FindOrgChangeBasisInput(AxModel):
    """발령 사유와 비슷한 과거 업무 결정 카드를 찾는다."""

    reason: str = Field(min_length=2, max_length=500)
    limit: int = Field(default=3, ge=1, le=5)


class OrgChangeBasisCandidate(AxModel):
    card_id: str
    title: str
    summary: str  # 카드의 결정 한 줄(없으면 제목)
    similarity: float = Field(ge=0.0, le=1.0)  # 검색 점수 그대로
    rejected: bool = False  # 과거에 기각된 안이면 true: 사람이 알고 판단하게 표시한다


class FindOrgChangeBasisOutput(AxModel):
    """기준 미만이면 `candidates`가 비어 있다(근거 카드 없음). 자동으로 연결하지 않는다."""

    candidates: list[OrgChangeBasisCandidate] = Field(default_factory=list)
    method: Literal["dense", "dice"]
    collected_at: UtcDatetime


class ParseOrgChangeTextInput(AxModel):
    """발령 문장(직원 번호로 적은 것)을 입력 칸으로 나눠 달라는 요청."""

    text: str = Field(min_length=2, max_length=500)


class OrgChangeSuggestion(AxModel):
    """폼에 채워 줄 제안. 문장에 없거나 확인하지 못한 칸은 비운다."""

    kind: OrgChangeKind | None = None
    member_id: str | None = None
    from_team: str | None = None
    to_team: str | None = None
    effective_date: str | None = None
    from_team_from_roster: bool = False  # 원래 팀을 문장이 아니라 확정 명부의 현재 소속으로 채웠다


class ParseOrgChangeTextOutput(AxModel):
    """`available`이 false면 문장 정리를 쓸 수 없는 것이다(LLM 꺼짐·실패): 직접 입력한다."""

    available: bool
    suggestion: OrgChangeSuggestion | None = None
    warnings: list[str] = Field(default_factory=list)
    message: str | None = None


class DescribeOrgChangeInput(AxModel):
    change_id: str


class OrgChangeDelta(AxModel):
    team: str
    delta: int  # 그 달 팀 인원 변화(코드가 센 값)


class DescribeOrgChangeOutput(AxModel):
    """승인하는 사람이 읽을 설명문. 숫자는 코드가 정하고, 다르면 템플릿 문장이다."""

    change_id: str
    month: str = Field(pattern=r"^\d{4}-(0[1-9]|1[0-2])$")
    deltas: list[OrgChangeDelta] = Field(default_factory=list)
    text: str
    fallback: bool = False  # LLM 문장을 버리고 템플릿으로 대체했다
    llm_used: bool = False


# ---- 유사 사기 카드 조회 (data의 사기 점수 FRAUD_SIMILAR 규칙용) ------------------------------


class SellerFraudFacts(AxModel):
    """판매자 사실. 개인정보(이름·연락처·사업자번호)는 보내지 않는다."""

    joined_days: int = Field(ge=0)  # 가입 후 경과 일수
    low_price_ratio: float = Field(ge=0, le=1)  # 저가 상품 비율
    listings_last_24h: int = Field(ge=0)  # 24시간 등록 수
    business_hash_duplicated: bool | None = None  # 사업자 중복 여부(모르면 None)


class FindSimilarFraudCardsInput(AxModel):
    facts: SellerFraudFacts
    limit: int = Field(default=3, ge=1, le=10)


class SimilarFraudCard(AxModel):
    card_id: str
    title: str
    similarity: float = Field(ge=0, le=1)  # 높을수록 비슷하다. 방식(method)이 다르면 척도가 다르다


class FindSimilarFraudCardsOutput(AxModel):
    cards: list[SimilarFraudCard] = Field(default_factory=list)  # 유사도 높은 순, 기준 미만은 뺀다
    max_similarity: float = Field(default=0.0, ge=0, le=1)  # cards가 없으면 0
    method: Literal["dense", "dice"]  # dense=임베딩(코사인), dice=글자 2-gram 대체
    collected_at: UtcDatetime


# ---- 온보딩 단계 완료 표시 --------------------------------------------------------------


class UpdateOnboardingStepInput(AxModel):
    onboarding_id: str
    step_id: str
    done: bool = True


# 출력은 `StartOnboardingOutput`(갱신된 단계 목록과 코드가 다시 센 진행률)을 쓴다.


# ---- 후보 카드 승인·거절 ----------------------------------------------------------------


class DecideCandidateCardInput(AxModel):
    """확신이 낮아 후보로 둔 카드를 사람이 승인(`approve`)하거나 폐기(`reject`)한다."""

    card_id: str
    decision: Literal["approve", "reject"]
    note: str | None = None


class DecideCandidateCardOutput(AxModel):
    card_id: str
    status: Literal["active", "retired"]


# ---- 대시보드 인사 화면용 조회 ------------------------------------------------------------
# 모두 상태를 바꾸지 않는다. 시각은 조회한 시각(`collected_at`)이고, 값은 저장된 그대로다.

ChangeStatus = Literal["requested", "confirmed", "offboarded", "cancelled", "expired"]


class HandoverView(AxModel):
    handover_id: str
    context_id: str
    owner_id: str
    successor_id: str | None = None
    area: str | None = None
    reason: Literal["shift", "transfer", "offboarding", "other"] = "other"
    status: HandoverStatus
    completeness: int = Field(ge=0, le=100)
    items: list[HandoverItem] = Field(default_factory=list)
    open_questions: list[HandoverQuestion] = Field(default_factory=list)
    created_at: UtcDatetime
    updated_at: UtcDatetime


class ListHandoversInput(AxModel):
    status: HandoverStatus | None = None
    area: str | None = None
    limit: int = Field(default=50, ge=1, le=200)


class ListHandoversOutput(AxModel):
    handovers: list[HandoverView] = Field(default_factory=list)
    collected_at: UtcDatetime


class ListOpenQuestionsInput(AxModel):
    assignee_id: str | None = None
    handover_id: str | None = None


class ListOpenQuestionsOutput(AxModel):
    questions: list[HandoverQuestion] = Field(default_factory=list)
    collected_at: UtcDatetime


class OnboardingView(AxModel):
    onboarding_id: str
    context_id: str
    member_id: str
    area: str
    handover_id: str | None = None
    steps: list[OnboardingStep] = Field(default_factory=list)
    progress: int = Field(ge=0, le=100)
    created_at: UtcDatetime


class ListOnboardingsInput(AxModel):
    member_id: str | None = None
    area: str | None = None


class ListOnboardingsOutput(AxModel):
    onboardings: list[OnboardingView] = Field(default_factory=list)
    collected_at: UtcDatetime


class ChangeView(AxModel):
    change_id: str
    context_id: str
    change_kind: Literal["shift", "transfer", "offboarding", "join"]
    member_id: str
    area: str
    successor_id: str | None = None
    status: ChangeStatus
    handover_id: str | None = None
    onboarding_id: str | None = None
    checklist: list[ChecklistItem] = Field(default_factory=list)
    created_at: UtcDatetime
    updated_at: UtcDatetime


class ListChangesInput(AxModel):
    status: ChangeStatus | None = None
    area: str | None = None
    member_id: str | None = None


class ListChangesOutput(AxModel):
    changes: list[ChangeView] = Field(default_factory=list)
    collected_at: UtcDatetime


class ListCandidateCardsInput(AxModel):
    area: str | None = None


class ListCandidateCardsOutput(AxModel):
    cards: list[Card] = Field(default_factory=list)  # `status == "candidate"`인 카드
    collected_at: UtcDatetime


class ListCardsInput(AxModel):
    """확정된 결정 카드 목록 조회. `context_id`를 주면 그 사건에서 만든 카드만 돌려준다."""

    area: str | None = None
    context_id: str | None = None
    limit: int = Field(default=50, ge=1, le=200)


class ListCardsOutput(AxModel):
    cards: list[Card] = Field(default_factory=list)  # 후보가 아닌 `active` 카드, 최근 순
    collected_at: UtcDatetime


class CaseResolution(AxModel):
    """사건이 어떻게 해결됐는지. 값이 없으면 지어내지 않고 비워 둔다.

    카드 본문에는 빈 값이 "확인할 수 없음"으로 적힌다.
    """

    outcome: Literal["resolved", "workaround", "unresolved"]
    root_cause: str | None = None
    action: str | None = None
    minutes_to_resolve: int | None = Field(default=None, ge=0)
    resolved_at: UtcDatetime | None = None
    opened_at: UtcDatetime | None = None  # 사건이 열린 시각. 소요 시간 계산과 처음·반복 비교에 쓴다
    prior_case_id: str | None = None  # 이 사건을 시작할 때 근거로 보여 준 과거 사건 카드(반복 장애)
    # 사람이 직접 적었는지, AI 초안을 그대로 썼는지·고쳤는지. 초안은 사람이 확인해야 저장된다
    draft_source: Literal["written_by_person", "ai_draft_kept", "ai_draft_edited"] | None = None
    # 처리 방식을 AI가 다시 할 수 있는 이름으로: `rollback_deployment`(이전 버전으로 되돌림),
    # `release_fault`(장애 상태 해제). 같은 유형 장애가 다시 나면 총괄이 이 값으로 같은 처리를 한다
    action_code: str | None = None


class EventTimeline(AxModel):
    """이벤트가 어떻게 흘렀는지의 시각. 시스템이 재고 사람이 적지 않는다. 모르는 시각은 비워 둔다.

    구간: AI 처리 = 감지 → 원인 후보 도착, 사람 작업 = 시작 → 완료, 복구 대기 = 완료 → 정상 복귀.
    `prior_case_id`가 있으면 시작할 때 비슷한 과거 이벤트를 근거로 본 이벤트(반복 장애)다.
    """

    detected_at: UtcDatetime | None = None
    cause_candidate_at: UtcDatetime | None = None  # AI가 원인 후보나 과거 근거를 처음 내놓은 시각
    human_work_started_at: UtcDatetime | None = None  # 사람에게 원인 확인·조치를 요청한 시각
    human_work_done_at: UtcDatetime | None = None  # 사람이 그 일을 끝낸 시각
    recovered_at: UtcDatetime | None = None  # 쇼핑몰이 정상 복귀를 알린 시각
    prior_case_id: str | None = None


class StepTiming(AxModel):
    """장애 대응 단계 하나의 시간. 총괄이 재고 사람이 적지 않는다. 모르는 값은 비워 둔다.

    - `work_seconds`: 맡긴 때부터 끝날 때까지에서 승인 대기와 사람 할 일함 대기를 뺀 시간.
      부서가 일한 시간이라 부서 평가는 이 값만 쓴다. 단계가 아직 끝나지 않았거나 시각을 모르면 None.
    - `approval_wait_seconds`: 안전 관문 보류에 올린 때부터 사람이 결정한 때까지.
    - `human_wait_seconds`: 사람 할 일함에 넘긴 때부터 사람이 끝낸 때까지.
    - 부서 안에서 외부(쇼핑몰 서버 등)를 기다린 시간은 총괄이 구분할 수 없어
      `work_seconds`에 들어간다.
    """

    step_id: str
    dept: str | None = None
    status: str
    work_seconds: float | None = Field(default=None, ge=0)
    approval_wait_seconds: float | None = Field(default=None, ge=0)
    human_wait_seconds: float | None = Field(default=None, ge=0)
    attempts: int = Field(default=0, ge=0)
    reason_code: str | None = None


class RecordEventTimesInput(EventTimeline):
    """`record_event_times` 입력. 이벤트(`context_id`)의 카드에 시각을 채운다.

    이미 값이 있는 시각은 덮어쓰지 않고, 비어 있던 시각만 채운다.
    `step_timings`(선택)는 단계별 시간이다. 시각과 달리 장애가 끝난 뒤 값이 바뀔 수 있어
    (사람 처리가 늦게 끝나는 경우) 마지막으로 받은 값으로 바꿔 둔다.
    """

    context_id: str
    step_timings: list[StepTiming] | None = Field(default=None, max_length=64)


class RecordEventTimesOutput(AxModel):
    """`record_event_times` 결과.

    - `recorded`: 새 시각을 카드에 채웠다.
    - `duplicate`: 채울 새 시각이 없다(이미 있는 값이거나 비어 있음).
    - `no_card`: 그 이벤트에서 만든 카드가 없어 기록할 곳이 없다(호출한 쪽이 나중에 다시 보낸다).
    """

    outcome: Literal["recorded", "duplicate", "no_card"]
    card_id: str | None = None
    timeline: EventTimeline | None = None


class RecordResolutionInput(AxModel):
    """`record_resolution` 입력. 사건(`context_id`)의 카드에 해결 내용을 채운다."""

    context_id: str
    outcome: Literal["resolved", "workaround", "unresolved"]
    root_cause: str | None = None
    action: str | None = None
    minutes_to_resolve: int | None = Field(default=None, ge=0)
    resolved_at: UtcDatetime | None = None  # 없으면 요청을 받은 시각
    # 사건이 열린 시각. 소요 시간을 안 주면 resolved_at - opened_at 분으로 hr이 계산한다
    opened_at: UtcDatetime | None = None
    prior_case_id: str | None = None  # 이 사건을 시작할 때 근거로 보여 준 과거 사건 카드
    # 사람이 직접 적었는지, AI 초안을 그대로 썼는지·고쳤는지. 초안은 사람이 확인해야 저장된다
    draft_source: Literal["written_by_person", "ai_draft_kept", "ai_draft_edited"] | None = None
    # True면 카드에 이미 해결 기록(결과가 미해결이 아닌 것)이 있을 때 바꾸지 않는다(`duplicate`).
    # 총괄이 장애가 나중에 풀린 것을 자동으로 남길 때 쓴다. 사람이 적은 기록을 덮지 않게 한다
    only_if_unresolved: bool = False
    action_code: str | None = None  # `CaseResolution.action_code`와 같다


class RecordResolutionOutput(AxModel):
    """`record_resolution` 결과.

    - `recorded`: 사건의 카드에 해결 내용을 채웠다.
    - `duplicate`: 같은 내용이 이미 기록되어 있다. `only_if_unresolved`인데 이미 해결 기록이
      있어 바꾸지 않은 때도 이 값이다.
    - `no_card`: 그 사건에서 만든 카드가 없어 기록할 곳이 없다.
    """

    outcome: Literal["recorded", "duplicate", "no_card"]
    card_id: str | None = None


class FindSimilarCasesInput(AxModel):
    """`find_similar_cases` 입력. 질의는 현재 사건을 설명하는 글이다(증상·영역 등)."""

    query: str = Field(min_length=1)
    area: str | None = None
    only_resolved: bool = False  # true면 해결 내용이 기록된 카드만
    # 현재 이벤트의 유형. 주면 카드의 유형과 견주어 `same_type`을 채우고 같은 유형을 앞에 둔다
    event_type: str | None = None
    limit: int = Field(default=3, ge=1, le=10)


class SimilarCase(AxModel):
    card_id: str
    title: str
    summary: str  # 카드의 결정 한 줄(없으면 제목)
    similarity: float = Field(ge=0.0, le=1.0)  # 검색 점수 그대로
    resolved: bool
    resolution: CaseResolution | None = None
    timeline: EventTimeline | None = None  # 그 이벤트의 시각(시스템이 잰 값). 없으면 None
    event_type: str | None = None  # 이 카드의 이벤트 유형(없으면 모름)
    same_type: bool | None = None  # 질의 유형과 같은지. 어느 쪽이든 유형을 모르면 None
    created_at: UtcDatetime


class FindSimilarCasesOutput(AxModel):
    """비슷한 과거 사건·결정(최대 `limit`건). 기준 미만이면 비어 있고 `max_similarity`는 0이다."""

    cases: list[SimilarCase] = Field(default_factory=list)
    max_similarity: float = Field(default=0.0, ge=0.0, le=1.0)
    method: Literal["dense", "dice"]
    collected_at: UtcDatetime


# ---- 중복 결정 병합 (15번) -------------------------------------------------------------
# 같은 결정을 다시 쓴 카드는 확신이 매우 높을 때만 AI가 자동으로 병합하고(원래 카드는 지우지 않고
# 대체됨으로 표시, 복원 가능), 애매하면 사람 승인 목록에 올린다. 이벤트 기록 카드는 대상이 아니다.


class DuplicateMember(AxModel):
    card_id: str
    title: str
    decision: str  # 카드의 결정 문장
    cosine: float | None = None  # 대표 카드와의 유사도(대표 카드는 None)
    verdict: Literal["same", "changed", "separate"] | None = None  # LLM 판정(없으면 None)


class DuplicateGroup(AxModel):
    """사람이 병합할지 정하는 묶음. 대표 카드(`representative`)는 새 결정, 나머지는 옛 결정이다."""

    group_id: str
    area: str
    representative: DuplicateMember
    olders: list[DuplicateMember]
    suggested_title: str | None = None  # LLM이 묶음 카드 내용으로만 만든 제안(검증 통과분)
    created_at: UtcDatetime


class MergedCard(AxModel):
    card_id: str
    title: str
    merged_into: str
    how: Literal["auto", "human"]
    cosine: float | None = None
    at: UtcDatetime


class ListDuplicateGroupsInput(AxModel):
    area: str | None = None
    limit: int = Field(default=20, ge=1, le=100)


class ListDuplicateGroupsOutput(AxModel):
    groups: list[DuplicateGroup] = Field(default_factory=list)
    merged: list[MergedCard] = Field(default_factory=list)  # 병합된 카드(최근 순)
    collected_at: UtcDatetime


class DecideDuplicateGroupInput(AxModel):
    """`merge`: 옛 카드들을 대표 카드로 병합(대체됨 표시). `dismiss`: 병합 없이 목록에서 뺀다."""

    group_id: str
    representative_id: str
    decision: Literal["merge", "dismiss"]
    title: str | None = Field(default=None, max_length=80)  # 사람이 확정한 대표 제목(없으면 그대로)


class DecideDuplicateGroupOutput(AxModel):
    outcome: Literal["merged", "dismissed", "duplicate", "not_found"]
    merged_ids: list[str] = Field(default_factory=list)


class RestoreMergedCardInput(AxModel):
    card_id: str


class RestoreMergedCardOutput(AxModel):
    outcome: Literal["restored", "not_merged", "not_found"]
    card_id: str | None = None


class AuditEntry(AxModel):
    seq: int = Field(ge=1)
    at: UtcDatetime
    kind: str
    payload: dict[str, Any] = Field(default_factory=dict)


class GetAuditTimelineInput(AxModel):
    context_id: str


class GetAuditTimelineOutput(AxModel):
    context_id: str
    entries: list[AuditEntry] = Field(default_factory=list)
    collected_at: UtcDatetime


# ---- 에이전트 평가 (장애 대응 시간으로 부서 AI를 보는 화면) -------------------------------------
# 점수·등급·순위는 없다. 사람이 "제때 끝냈다 / 늦었다"를 한눈에 보도록 건수와 이유만 모아 준다.

AgentVerdict = Literal[
    "normal",
    "check",
    "no_record",
    "blocked",
    "approval_expired",
    "in_progress",
    "absent",
    "handed_off",  # 스스로 끝내지 않고 사람이 보도록 넘김. 실패도 지연도 아니다
]


class EvaluateAgentsInput(AxModel):
    """에이전트별로 모아 볼 최근 이벤트 수(새로운 순). 읽기 전용."""

    limit: int = Field(default=30, ge=1, le=200)


class AgentEventDot(AxModel):
    """에이전트 하나가 이벤트 하나에서 한 일을 점 하나로. 그 이벤트에 참여하지 않았으면 absent."""

    context_id: str
    title: str
    at: UtcDatetime | None = None
    verdict: AgentVerdict
    reason: str | None = None
    steps: int = Field(default=0, ge=0)


class RepeatEventType(AxModel):
    event_type: str
    title: str  # 이 유형의 가장 최근 이벤트 제목(화면에는 영어 유형 코드 대신 이걸 보인다)
    count: int = Field(ge=2)


class AgentSummary(AxModel):
    dept: str
    events: int = Field(ge=0)  # 이 에이전트가 참여한 이벤트 수
    normal: int = Field(ge=0)
    check: int = Field(ge=0)
    no_record: int = Field(ge=0)
    blocked: int = Field(ge=0)
    handed_off: int = Field(default=0, ge=0)
    approval_expired: int = Field(ge=0)
    in_progress: int = Field(ge=0)
    retried: int = Field(ge=0)  # 확인 필요 중 다시 시도가 있었던 이벤트 수
    slow: int = Field(ge=0)  # 확인 필요 중 기준 시간을 넘긴 이벤트 수
    avg_work_seconds: float | None = None
    max_work_seconds: float | None = None
    repeats: list[RepeatEventType] = Field(default_factory=list)
    recent: list[AgentEventDot] = Field(default_factory=list)  # 새로운 이벤트부터


class EvaluatedStep(AxModel):
    step_id: str
    label: str
    dept: str
    group: str
    verdict: AgentVerdict
    work_seconds: float | None = None
    approval_wait_seconds: float | None = None
    human_wait_seconds: float | None = None
    attempts: int = Field(default=0, ge=0)
    limit_seconds: float
    reason: str | None = None


class EvaluatedEvent(AxModel):
    context_id: str
    title: str
    event_type: str | None = None
    at: UtcDatetime | None = None
    criteria_version: int = Field(ge=1)
    steps: list[EvaluatedStep] = Field(default_factory=list)


class DailyTrend(AxModel):
    day: str  # 서울 시간 기준 YYYY-MM-DD
    events: int = Field(ge=0)
    with_check: int = Field(ge=0)  # 확인이 필요했던 에이전트가 하나라도 있던 이벤트 수


class EvaluateAgentsOutput(AxModel):
    criteria_version: int = Field(ge=1)
    criteria_updated: str
    normal_max_seconds: float
    normal_max_attempts: int = Field(ge=1)
    events: list[EvaluatedEvent] = Field(default_factory=list)  # 새로운 이벤트부터
    agents: list[AgentSummary] = Field(default_factory=list)
    trend: list[DailyTrend] = Field(default_factory=list)
    collected_at: UtcDatetime


# ---- 근무 상태 · 휴가 (AI 사무실 머리 위 표시, 2026-11-09 팀 합의) --------------------------
# 상태는 3가지뿐이다. 출퇴근 시각·접속 중 표시는 만들지 않는다.
# 휴가는 기간만 받고 사유·종류는 받지 않는다.
# 상태 코드(근무 중 · 교대 외 · 휴가 중)는 모두에게 보이고, 휴가 기간·입력자는 `details`에만 담는다.

PresenceStatus = Literal["working", "off_shift", "on_leave"]  # 근무 중 · 교대 외 · 휴가 중


class RecordLeaveInput(AxModel):
    """`record_leave` 입력(등급 A, 요청자 인사 서비스, 사람 토큰만). 사유·종류 칸은 없다."""

    member_id: str = Field(min_length=1, max_length=64)
    start_date: str = Date  # 서울 날짜, 포함
    end_date: str = Date  # 서울 날짜, 포함
    recorded_by: str = Field(
        min_length=1, max_length=64
    )  # 입력한 사람. dashboard-api가 로그인에서 채운다

    @model_validator(mode="after")
    def _end_not_before_start(self) -> RecordLeaveInput:
        if self.end_date < self.start_date:
            raise ValueError("휴가 끝 날짜가 시작 날짜보다 빠르다")
        return self


class LeaveView(AxModel):
    """휴가 기록 한 건. 사유·종류는 없다. 기간은 서울 날짜(포함)다."""

    leave_id: str
    member_id: str
    start_date: str = Date
    end_date: str = Date
    recorded_by: str  # 입력한 사람(사람 토큰의 주체)
    recorded_at: UtcDatetime
    # 취소한 기록만 채운다. 지우지 않고 취소 표시만 남긴다(취소한 기록은 상태 계산에서 뺀다)
    cancelled_by: str | None = None
    cancelled_at: UtcDatetime | None = None


class RecordLeaveOutput(AxModel):
    leave: LeaveView


class CancelLeaveInput(AxModel):
    """`cancel_leave` 입력(등급 A, 요청자 dashboard-api, 사람 토큰만).

    휴가 기록을 지우지 않고 취소 표시만 남긴다. 수정은 "취소 + 새 기록"이다. 취소한 기록은
    같은 사람·같은 기간을 다시 넣을 때의 중복으로 보지 않는다.
    """

    leave_id: str = Field(min_length=1, max_length=64)
    # 취소한 사람. dashboard-api가 로그인에서 채운다
    cancelled_by: str = Field(min_length=1, max_length=64)


class CancelLeaveOutput(AxModel):
    """이미 취소된 기록을 다시 취소해도 성공(`duplicate`)으로 같은 기록을 돌려준다."""

    leave: LeaveView


class GetPresenceInput(AxModel):
    """`get_presence` 입력. `at`이 없으면 지금. `include_details`는 관리자 요청일 때만 true."""

    area: str | None = None
    at: UtcDatetime | None = None
    include_details: bool = False


class PresenceEntry(AxModel):
    """사람(담당) 한 명의 상태. 상태 코드만 담는다."""

    area: str
    role: str
    member_id: str
    status: PresenceStatus


class GetPresenceOutput(AxModel):
    """교대 명부와 휴가를 합쳐 계산한 상태. 휴가가 교대보다 먼저다(휴가 중이면 휴가 중).

    `leaves`는 `include_details`가 true일 때만 채운다(휴가 기간·입력자).
    """

    entries: list[PresenceEntry] = Field(default_factory=list)
    leaves: list[LeaveView] = Field(default_factory=list)
    collected_at: UtcDatetime
