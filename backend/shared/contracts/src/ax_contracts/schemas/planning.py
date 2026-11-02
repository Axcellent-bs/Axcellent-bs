"""planning 에이전트 skill 입출력 (시나리오 A 필수 3개). 이슈 #10 요청(예진).

- `find_related_prs`: infra·data·orchestrator → planning, 등급 read.
  후보만 돌려주고 원인은 판정하지 않는다.
- `link_incident`: orchestrator·infra → planning, 등급 A. 사건에 PR·기획 문단을 연결한다.
- `verify_rollback_target`: safety → planning. infra `rollback_deployment`(B)의 교차검증이다.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, model_validator

from ax_contracts.schemas.common import AxModel, UtcDatetime


class RelatedPr(AxModel):
    pr_number: int
    title: str
    merged_at: UtcDatetime
    spec_id: str | None = None  # 근거 기획 문서
    para_id: str | None = None  # 요구사항·완료 기준 문단 id (R1, C2 …)
    changed_paths: list[str] = Field(default_factory=list)


class FindRelatedPrsInput(AxModel):
    window_from: UtcDatetime
    window_to: UtcDatetime
    component: str | None = None


class FindRelatedPrsOutput(AxModel):
    candidates: list[RelatedPr] = Field(default_factory=list)


class LinkIncidentInput(AxModel):
    incident_context_id: str
    pr_numbers: list[int]
    follow_up_title: str | None = None  # 있으면 재발 방지 요청을 등록한다


class LinkIncidentOutput(AxModel):
    linked_paras: list[str] = Field(default_factory=list)
    # 같은 요청이 이미 있으면 그 ID (outcome은 linked_duplicate)
    follow_up_request_id: str | None = None


class VerifyRollbackTargetInput(AxModel):
    service: str
    target_version: str
    deployed_at: UtcDatetime
    incident_opened_at: UtcDatetime


class VerifyRollbackTargetOutput(AxModel):
    approved: bool
    reason_code: Literal["deploy_before_incident", "no_deploy_in_window", "deploy_after_incident"]
    related_prs: list[RelatedPr] = Field(default_factory=list)


# ---- hr 연동 (이슈 #10 이후, 인사 구성안 HR2 연동 ④·인수인계 검증)
#
# - `list_in_progress`: hr → planning, 읽기. 담당자·영역의 진행 중 배포·미완료 티켓·열린 PR을
#   돌려준다(인수인계 초안의 "진행 중 항목"). 공지된 제약은 1차에서 뺐다.
# - `verify_handover`: hr `confirm_handover`(B)의 검증 skill. 인계 목록에 진행 중 항목이
#   빠짐없이 있는지 사실만 확인한다(내용 판단은 하지 않는다).
#
# 값의 의미 (planning·hr이 같은 값을 쓴다):
# - `owner_id`: 명부의 `RosterEntry.member_id`와 같은 값이다(GitHub 로그인·부서 id가 아니다).
#   planning은 GitHub 로그인과의 대응을 자기 쪽에서 `member_id`로 바꿔 거른다.
# - `area`: 명부 `RosterEntry.area`와 같은 값 집합이다(변경 경로 접두사가 아니다).
#   planning이 작업·PR을 영역에 대응시키는 방법(기획서 문단·변경 경로)은 planning 내부 구현이다.
# - `kind="deployment"`: planning은 배포를 직접 하지 않고 머지된 PR만 안다. 그래서
#   "dev에 머지됐지만 복구·확인이 끝나지 않은 PR"을 돌려준다. 배포 상태 자체는 infra 소관이라
#   이 skill이 돌려주지 않는다.


class InProgressItem(AxModel):
    item_id: str
    kind: Literal["deployment", "ticket", "pr"]
    title: str
    status: str
    owner_id: str  # `RosterEntry.member_id`
    area: str | None = None  # `RosterEntry.area`
    updated_at: UtcDatetime | None = None


class ListInProgressInput(AxModel):
    owner_id: str  # `RosterEntry.member_id`
    area: str | None = None  # `RosterEntry.area`


class ListInProgressOutput(AxModel):
    items: list[InProgressItem] = Field(default_factory=list)
    collected_at: UtcDatetime  # 조회 시각 (근거)
    # 조회하지 못했을 때의 이유 (예: 받는 서비스가 꺼져 있음). 비어 있으면 정상 조회다.
    # 이때 `items`는 "없음"이 아니라 "확인할 수 없음"이다
    unavailable: str | None = None


class VerifyHandoverInput(AxModel):
    handover_id: str
    owner_id: str  # `RosterEntry.member_id`
    area: str | None = None  # `RosterEntry.area`
    handover_item_ids: list[str] = Field(default_factory=list)  # 인계 목록에 적힌 항목 ID


class VerifyHandoverOutput(AxModel):
    """`approved` · `reason_code="complete"` · `missing=[]`는 서로 같은 뜻이다."""

    approved: bool
    reason_code: Literal["complete", "missing_items"]
    missing: list[InProgressItem] = Field(default_factory=list)  # 인계 목록에 빠진 진행 중 항목
    # 인계 목록에는 있지만 이미 끝난 항목의 ID. 정보로만 돌려주고 판정에는 쓰지 않는다.
    stale_item_ids: list[str] = Field(default_factory=list)
    checked_at: UtcDatetime

    @model_validator(mode="after")
    def _verdict_is_consistent(self) -> VerifyHandoverOutput:
        complete = self.reason_code == "complete"
        if self.approved != complete or complete != (not self.missing):
            raise ValueError("approved, reason_code, missing이 서로 맞지 않는다")
        return self


# ---- 변경 요청 접수 (이슈 #172 요청, planning)
#
# - `create_ticket`: support·marketing·hr·data·finance·orchestrator → planning, 등급 A.
#   반복 문의 같은 근거로 기획에 변경 요청을 접수한다.
# - `ctx_req_`는 planning이 발급한다. 같은 요청을 여러 부서가 보내면 하나로 묶어야 해서다.
#   요청 부서는 돌려받은 `context_id`로 자기 쪽 문의와 요청을 연결한다.
# - 결과 통지(머지 뒤 요청 부서 회신)는 원래 요청의 meta를 재사용하지 않고, 같은 `context_id`에
#   `path`를 비운 새 요청으로 보낸다(LOOP_DETECTED 방지).
# - `untrusted`: 고객 문의 원문은 넣지 않고 `evidence` 요약만 보낸다(필요하면 `untrusted.text`).


class CreateTicketInput(AxModel):
    title: str  # 바꿀 내용 한 줄
    evidence: str  # 근거 요약 (예: 반복 문의 42건)
    source_ref: str | None = None  # 요청 부서 쪽 참조 ID (예: 고객지원 문의 ID). 판정에 안 쓴다
    evidence_ids: list[str] = Field(default_factory=list)  # 근거 ID


class CreateTicketOutput(AxModel):
    request_id: str  # REQ-n
    context_id: str  # ctx_req_ (planning이 발급)
    outcome: Literal["created", "linked_duplicate"]
    duplicate_of: str | None = None  # 같은 요청이 이미 있으면 그 REQ ID

    @model_validator(mode="after")
    def _duplicate_is_consistent(self) -> CreateTicketOutput:
        if (self.outcome == "linked_duplicate") != (self.duplicate_of is not None):
            raise ValueError("duplicate_of는 outcome이 linked_duplicate일 때만 채운다")
        return self


# ---- 변경 요청 결과 통지 (이슈 #280 요청, planning)
#
# - `receive_change_result`: planning → 요청 부서(support·hr·data), 등급 A.
#   부서마다 같은 이름이다.
# - planning이 요청을 닫을 때(완료·거절·기각) 보낸다.
#   같은 `ctx_req_` 사건에 `path`를 비운 새 요청이다.
#   중복 연결된 요청은 원래 요청이 닫힐 때 같은 사건으로 한 번에 보낸다.
# - 멱등성 키: `ctx_req_…:change_result:REQ-n`.
#   같은 결과가 두 번 와도 한 번만 적용한다.
# - `reason_code`는 열어 둔 코드 값이다 (예: rejected_by_review, out_of_scope).


class ChangeResultInput(AxModel):
    request_id: str  # REQ-n
    status: Literal["completed", "declined"]
    pr_numbers: list[int] = Field(default_factory=list)  # 머지된 PR (completed일 때)
    merged_at: UtcDatetime | None = None
    spec_id: str | None = None
    reason_code: str | None = None  # declined일 때 코드 값
    summary: str  # 코드가 만든 한 줄 (자유 판단 문장 아님)

    @model_validator(mode="after")
    def _status_fields_are_consistent(self) -> ChangeResultInput:
        if self.status == "declined" and (self.pr_numbers or self.merged_at is not None):
            raise ValueError("pr_numbers·merged_at은 status가 completed일 때만 채운다")
        if self.status == "completed" and self.reason_code is not None:
            raise ValueError("reason_code는 status가 declined일 때만 채운다")
        return self


class ChangeResultOutput(AxModel):
    applied: int  # 이 결과를 적용한 요청 부서 쪽 항목 수 (문의 n건 등)


# ---- 기획 → 개발 넘기기 (서비스 나누기, planning·dev)
#
# - `start_dev`: planning 서비스 → dev 서비스, 등급 A. 확정된 기획서와 작업을 넘긴다.
# - `receive_dev_result`: dev 서비스 → planning 서비스, 등급 A. 작업·PR·요청 상태를 돌려준다.
# - request·spec·tasks·prs는 planning 도메인 모델(`planning_agent.domain.models`)을
#   `dump()`(model_dump(mode="json", by_alias=True))한 dict 그대로다. 같은 부서 안이라
#   모양을 여기서 다시 정하지 않는다.
# - 결과는 같은 `ctx_req_` 사건에 `path`를 비운 새 요청으로 보낸다(LOOP_DETECTED 방지).
# - 멱등성 키: `ctx_req_…:start_dev:SPEC-n`, `ctx_req_…:dev_result:SPEC-n:<지문>`.


class DevHandoffInput(AxModel):
    request: dict[str, Any]  # Request dump
    spec: dict[str, Any]  # Spec dump (status 확정)
    tasks: list[dict[str, Any]] = Field(default_factory=list)  # Task dump


class DevHandoffOutput(AxModel):
    accepted: bool
    spec_id: str
    task_ids: list[str] = Field(default_factory=list)


class DevResultInput(AxModel):
    # 빈 값이면 기획서 없이 요청만 보낸다 (개발이 장애 연결로 만든 재발 방지 요청 → `request`)
    spec_id: str
    request_id: str
    request_state: str  # Request.state 값 (작업 중·완료 …)
    tasks: list[dict[str, Any]] = Field(default_factory=list)  # Task dump
    prs: list[dict[str, Any]] = Field(default_factory=list)  # PR dump
    # 개발팀이 직접 낸 기획(기획 서비스를 거치지 않음)이면 요청·기획서 자체도 보낸다.
    # 기획 서비스가 바뀌는 일을 모두 알도록 (planning 쪽은 없으면 새로 넣는다)
    request: dict[str, Any] | None = None  # Request dump
    spec: dict[str, Any] | None = None  # Spec dump
    # 자동 처리 범위(AutoPolicy dump). 넓히기·줄이기는 dev가 하고(간단 요청 연속 성공·장애 연결),
    # 간단 요청 판정은 planning이 하니 바뀌었을 때 실어 보낸다 (2026-11-02 기획·개발 분리)
    auto_policy: dict[str, Any] | None = None


class DevResultOutput(AxModel):
    applied: int  # planning 쪽에 반영한 작업·PR 수


# ---- 기획 → 요청 서비스 되묻기 (planning, 2026-11-02)
#
# 기획서를 쓰다가 정보가 모자라면 요청을 보낸 **서비스**에 되묻는다 (부서가 아니라 서비스 단위).
#
# - `answer_planning_question`: planning → 요청 서비스(예: inquiry·coupon·data), 등급 read.
#   받은 서비스는 자기 기록으로 답할 수 있으면 `answered`, 사람에게 물어야 하면 `needs_human`,
#   모르면 `unknown`으로 답한다. 지어내지 않는다. 서비스는 상태를 바꾸지 않는다.
# - `receive_planning_answer`: 요청 서비스 → planning, 등급 A. `needs_human`으로 답한 질문에
#   사람이 나중에 답하면 이것으로 보낸다. 같은 `question_id`의 두 번째 답은 `duplicate`.
# - 질문은 요청의 `ctx_req_` 사건으로 보낸다. 답이 없거나 창구가 없으면 planning은 기다리지 않고
#   예전처럼 기획서를 이어 쓴다 (`needs_human`일 때만 답 대기).
# - 질문 문구는 planning 코드가 만든 것이다. 답의 `answer`는 외부 문구라 planning은 데이터로만 쓴다.


class PlanningQuestion(AxModel):
    question_id: str  # Q-<REQ>-n (planning 발급)
    request_id: str  # REQ-n
    spec_id: str | None = None  # 쓰고 있는 기획서 (없으면 아직 초안 전)
    question: str
    about: str | None = None  # 묻는 칸 (예: evidence·done·R1)
    options: list[str] = Field(default_factory=list)  # 고를 답이 있으면


class AnswerPlanningQuestionInput(PlanningQuestion):
    """planning이 요청 서비스에 보내는 질문."""


class AnswerPlanningQuestionOutput(AxModel):
    status: Literal["answered", "needs_human", "unknown"]
    answer: str | None = None
    answered_by: Literal["agent", "human"] | None = None
    evidence_ids: list[str] = Field(default_factory=list)  # 답의 근거 ID (문의 ID 등)

    @model_validator(mode="after")
    def _answer_is_consistent(self) -> AnswerPlanningQuestionOutput:
        if (self.status == "answered") != bool(self.answer):
            raise ValueError("answer는 status가 answered일 때만 채운다")
        return self


class ReceivePlanningAnswerInput(AxModel):
    """요청 서비스가 나중에 보내는 답 (사람이 답한 것)."""

    question_id: str
    request_id: str
    answer: str = Field(min_length=1)
    answered_by: Literal["agent", "human"] = "human"
    evidence_ids: list[str] = Field(default_factory=list)


class ReceivePlanningAnswerOutput(AxModel):
    outcome: Literal["applied", "duplicate", "unknown_question"]
    spec_id: str | None = None


# ---- 배포 전 기획서 대조 → 배포 지시 (planning·dev, 2026-11-02 초안)
#
# 개발이 "완료"라고 하면, 배포하기 전에 기획이 결과가 기획서와 맞는지 보고 개발에 배포를 지시한다.
#
# - `request_release_check`: dev → planning, 등급 A. 기획서 하나에 묶인 작업·PR을 보낸다.
#   planning은 코드 규칙으로 대조하고(LLM 아님) `ready` / `hold` / `needs_human`으로 답한다.
#   대조 규칙(planning): 기획서가 확정돼 있다 · 요구사항 문단마다 작업이 있다 · 작업마다 머지된 PR이
#   있다 · 기획서 밖 PR이 섞이지 않았다 · 문단에 열린 장애 표시가 없다 · 남은 질문이 없다.
# - 내용 대조(11/2 추가): 위 규칙은 연결만 본다. PR이 기획서 문단·완료 기준대로 구현됐는지는
#   개발이 PR마다 이미 하는 LLM 검수(`review_pr`) 결과를 `ReleasePr.review`로 실어 보내 쓴다
#   (LLM을 두 번 부르지 않는다). 연결 규칙에 걸리면 검수와 상관없이 `hold`. 연결은 통과했는데
#   검수가 하나라도 `mismatch`·`none`이면 `needs_human`. 전부 `match`일 때만 `ready`.
#   LLM 검수 결과만으로 배포를 열지 않는다(코드 규칙이 먼저, 검수는 근거).
# - `ready`면 planning이 바로 `receive_release_order`(dev, A)로 배포 지시를 보낸다.
#   `needs_human`이면 사람이 기획 화면에서 확인한 뒤 같은 지시를 보낸다(`decided_by=human`).
#   `hold`면 지시를 보내지 않고 이유 코드만 돌려준다.
# - 배포 실행·prod 승인·보호 경로 승인은 지금처럼 개발 서비스와 안전 레이어가 한다. 배포 지시는
#   "기획서와 맞으니 배포해도 된다"는 뜻이지, 사람 승인을 건너뛰라는 뜻이 아니다.
# - 멱등성 키: 대조 `ctx_req_…:release_check:SPEC-n:<PR 지문>`,
#   지시 `ctx_req_…:release_order:<check_id>`.

ReleaseEnv = Literal["dev", "prod"]
ReleaseVerdict = Literal["ready", "hold", "needs_human"]
# 열어 둔 코드 값. 새 이유가 생기면 여기에 추가만 한다
ReleaseHoldReason = Literal[
    "spec_not_confirmed",  # 기획서가 확정되지 않음
    "para_without_task",  # 작업이 없는 요구사항 문단
    "task_not_merged",  # 머지된 PR이 없는 작업
    "pr_outside_spec",  # 기획서 작업에 묶이지 않은 PR이 섞임
    "linked_incident_open",  # 문단에 열린 장애 표시
    "open_question",  # 기획서에 답 안 된 질문
    "spec_unknown",  # 기획 쪽에 없는 기획서
]


class ReleasePr(AxModel):
    number: int
    task_id: str | None = None  # T-n (없으면 기획서 밖 PR로 본다)
    merged: bool
    changed_paths: list[str] = Field(default_factory=list)
    # 개발 검수(`review_pr`, 완료 기준 비교) 결과. 없으면 `none` → planning은 사람 확인으로 둔다
    review: Literal["match", "mismatch", "none"] = "none"
    review_note: str | None = None  # 검수 근거 한 줄 (LLM 문장이라 데이터로만 쓴다)
    reviewed_paras: list[str] = Field(default_factory=list)  # 검수가 본 문단 id (R1 …)


class RequestReleaseCheckInput(AxModel):
    spec_id: str  # SPEC-n
    request_id: str  # REQ-n
    env: ReleaseEnv = "dev"
    prs: list[ReleasePr] = Field(min_length=1)
    summary: str | None = None  # 개발이 쓴 한 줄 (판정에 쓰지 않는다)


class ReleaseCheckItem(AxModel):
    """문단 하나의 대조 결과."""

    para_id: str  # R1 …
    status: Literal["covered", "missing_task", "not_merged", "incident_open"]
    task_ids: list[str] = Field(default_factory=list)
    pr_numbers: list[int] = Field(default_factory=list)
    # 내용 대조: 이 문단을 본 PR 검수가 모두 match면 match, 하나라도 mismatch면 mismatch,
    # 검수가 없으면 none (연결 status와 따로 본다)
    content: Literal["match", "mismatch", "none"] = "none"
    content_notes: list[str] = Field(default_factory=list)  # PR 검수 근거 (데이터)


class RequestReleaseCheckOutput(AxModel):
    check_id: str  # RC-n (planning 발급)
    verdict: ReleaseVerdict
    items: list[ReleaseCheckItem] = Field(default_factory=list)
    hold_reasons: list[ReleaseHoldReason] = Field(default_factory=list)
    order_sent: bool = False  # ready여서 배포 지시를 바로 보냈는지

    @model_validator(mode="after")
    def _verdict_is_consistent(self) -> RequestReleaseCheckOutput:
        if self.verdict == "hold" and not self.hold_reasons:
            raise ValueError("hold면 hold_reasons가 하나 이상 있어야 한다")
        if self.verdict != "ready" and self.order_sent:
            raise ValueError("order_sent는 verdict가 ready일 때만 true")
        return self


class ReceiveReleaseOrderInput(AxModel):
    """planning → dev 배포 지시.

    dev는 지금의 배포 요청 흐름(prod·보호 경로는 사람 승인)을 그대로 탄다.
    """

    check_id: str
    spec_id: str
    request_id: str
    env: ReleaseEnv
    pr_numbers: list[int] = Field(min_length=1)
    decided_by: Literal["agent", "human"]
    note: str | None = None


class ReceiveReleaseOrderOutput(AxModel):
    outcome: Literal["accepted", "duplicate", "rejected"]
    deployment_id: str | None = None  # DEP-n (accepted일 때)
    needs_approval: bool = False  # prod·보호 경로라 사람 승인 대기
    reason_code: str | None = None  # rejected일 때 (예: pr_not_merged, unknown_pr)

    @model_validator(mode="after")
    def _outcome_is_consistent(self) -> ReceiveReleaseOrderOutput:
        if self.outcome == "accepted" and not self.deployment_id:
            raise ValueError("accepted면 deployment_id가 있어야 한다")
        if self.outcome == "rejected" and not self.reason_code:
            raise ValueError("rejected면 reason_code가 있어야 한다")
        return self
