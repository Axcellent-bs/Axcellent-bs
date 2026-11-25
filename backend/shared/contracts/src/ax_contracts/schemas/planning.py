"""planning 에이전트 skill 입출력 (시나리오 A 필수 3개). 이슈 #10 요청(예진).

- `find_related_prs`: infra·data·orchestrator → planning, 등급 read.
  후보만 돌려주고 원인은 판정하지 않는다.
- `link_incident`: orchestrator·infra → planning, 등급 A. 사건에 PR·기획 문단을 연결한다.
- `verify_rollback_target`: safety → planning. infra `rollback_deployment`(B)의 교차검증이다.
- `verify_deploy_request`: safety → dev. infra `deploy_version`(B)의 교차검증이다.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import ConfigDict, Field, model_validator

from ax_contracts.schemas.common import AxModel, UtcDatetime
from ax_contracts.schemas.finance import ContentFormat


class RelatedPr(AxModel):
    pr_number: int
    title: str
    merged_at: UtcDatetime
    spec_id: str | None = None  # 근거 기획 문서
    para_id: str | None = None  # 요구사항·완료 기준 문단 id (R1, C2 …)
    changed_paths: list[str] = Field(default_factory=list)
    # 형상관리 연결의 실제 레포에서 읽은 PR이면 채운다 (메모리 PR이면 None)
    repo: str | None = None  # owner/name
    connection_id: str | None = None  # 개발 서비스의 형상관리 연결 id
    merge_sha: str | None = None


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
    # `pr_numbers`가 속한 연결 레포 (`RelatedPr.connection_id`). 있으면 그 연결의 레포 PR 번호로만
    # 찾고(거기 없는 번호는 missing), 없으면 예전처럼 개발 서비스 PR 번호로 찾는다
    connection_id: str | None = None


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


# ---- verify_deploy_request: safety → dev, 등급 read
#
# infra `deploy_version`(B)의 교차검증. dev 배포 기록(DEP-n)이 정말 사람이 승인한(approved) 기록이고
# 승인자·서비스·버전이 배포 요청과 같은지 본다. 거절도 completed + outcome rejected_by_review.


class VerifyDeployRequestInput(AxModel):
    # 배포 기록 id + 고유 꼬리 ("DEP-3.1a2b3c4d")
    request_id: str = Field(min_length=1, max_length=64)
    service: str = Field(min_length=1, max_length=120)
    version: str = Field(min_length=1, max_length=120)
    approved_by: str | None = Field(default=None, max_length=80)


class VerifyDeployRequestOutput(AxModel):
    """`approved`는 `reason_code == "deploy_approved"`일 때만 참이다."""

    approved: bool
    reason_code: Literal[
        "deploy_approved",  # 승인된 기록이고 승인자·서비스·버전이 같다
        "deploy_not_found",  # 그 id(고유 꼬리 포함)의 배포 기록이 없다
        "deploy_not_approved",  # 기록이 approved 상태가 아니다
        "approver_mismatch",  # 승인자가 비었거나 기록과 다르다
        "target_mismatch",  # 서비스·버전이 기록과 다르다
    ]
    deployment_id: str | None = None  # 찾은 배포 기록 id ("DEP-3")

    @model_validator(mode="after")
    def _verdict_is_consistent(self) -> VerifyDeployRequestOutput:
        if self.approved != (self.reason_code == "deploy_approved"):
            raise ValueError("approved와 reason_code가 서로 다르다")
        return self


# ---- revert_merged_pr: infra → dev, 등급 B (execute 경유)
#
# 형상관리 연결의 레포에서 병합된 PR을 되돌리는 PR을 열고 머지까지 간다.
# CI를 기다려야 하면 자동 머지를 예약하고 auto_merge_scheduled로 돌려준다.
# 관문이 사람 확인으로 보내면 held. 멱등 키는 connection_id + pr_number.


class RevertMergedPrInput(AxModel):
    connection_id: str
    pr_number: int = Field(gt=0)
    reason: str = Field(min_length=1)
    incident_context_id: str | None = None
    # 되돌리기를 정당화한 교차검증 결과 ID.
    # 있으면 사건의 되돌리기 기록과 근거(`review:<id>`)에 남긴다
    review_id: str | None = None


class RevertMergedPrOutput(AxModel):
    revert_pr_number: int | None = None  # 관문이 되돌리기 PR 열기부터 막으면(held) None
    merged: bool
    merge_sha: str | None = None
    status: Literal["merged", "auto_merge_scheduled", "held"]


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


class ListDevProgressInput(AxModel):
    """개발 진행 중 항목 전체 (총괄의 "개발 진행 상황", read). 출력은 `ListInProgressOutput`.

    `list_in_progress`와 진행 중 판정은 같고 담당자(`owner_id`)로 거르지 않는다. 채팅 계정과 명부
    `member_id`를 잇는 곳이 없어서 총괄은 담당자를 알 수 없다. `area`는 명부 `RosterEntry.area`와
    같은 값이고 없으면 전체다. 명부에 없는 값이면 지어내지 않고 빈 목록이다."""

    area: str | None = None  # `RosterEntry.area`


class ListDevOutcomesInput(AxModel):
    """개발 작업 결과 (인사·총괄의 부서·에이전트 평가 근거, read).

    `since`·`until`은 작업이 마지막으로 바뀐 시각 기준 (둘 다 없으면 전부).
    `coder`: `agent`(서버 AI만) · 사람 이름(그 사람의 내 Claude가 가져간 작업만) · 없으면 전부."""

    since: UtcDatetime | None = None
    until: UtcDatetime | None = None
    coder: str | None = None


class DevOutcome(AxModel):
    """코딩 작업 하나의 결과. 숫자는 개발 서비스 기록 그대로다 (받는 쪽이 다시 만들지 않는다).

    - `mode`: agent = 서버 AI(회사 키·토큰을 셈),
      subscription = 개발자 본인 Claude(토큰은 개인 구독이라 0)
    - `coder`: 서버 AI면 모델 이름, 내 Claude면 가져간 사람 이름
    - `complexity`: simple(바뀐 줄·파일이 적고 한 번에 통과) · complex · unknown(PR 없음)
    - `review_mismatches`: 리뷰가 "불일치"로 돌려보낸 횟수, `attempts`: 코드를 쓴 횟수(첫 작성 1)
    - `held`: 자동으로 못 가고 사람 확인을 거쳤는지 (관문 보류·자동 머지 멈춤)
    """

    task_id: str
    spec_id: str
    title: str
    mode: Literal["agent", "subscription"]
    coder: str | None = None
    status: str
    complexity: Literal["simple", "complex", "unknown"]
    changed_lines: int | None = None
    changed_files: int | None = None
    attempts: int = 0
    llm_calls: int = 0
    llm_tokens: int = 0
    review_mismatches: int = 0
    held: bool = False
    pr_number: int | None = None
    merged: bool = False
    merged_at: UtcDatetime | None = None
    updated_at: UtcDatetime | None = None


class DevOutcomeSummary(AxModel):
    """`outcomes`를 코드로 센 값. 비율은 분모가 0이면 None (확인할 수 없음)."""

    total: int
    merged: int
    failed: int
    covered: int  # 앞 작업이 이미 반영해 PR 없이 끝남
    by_mode: dict[str, int] = Field(default_factory=dict)
    by_complexity: dict[str, int] = Field(default_factory=dict)
    llm_tokens: int = 0  # 서버 AI가 쓴 토큰 합 (내 Claude는 0)
    tokens_per_merged: float | None = None  # 서버 AI 토큰 ÷ 서버 AI 머지 건수
    avg_attempts: float | None = None
    first_pass_rate: float | None = None  # 리뷰 불일치 없이 머지된 비율 (머지된 것 중)
    held_rate: float | None = None  # 사람 확인을 거친 비율


class ListDevOutcomesOutput(AxModel):
    outcomes: list[DevOutcome] = Field(default_factory=list)
    summary: DevOutcomeSummary
    collected_at: UtcDatetime  # 조회 시각 (근거)


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
    # True면 비슷한 요청이 있어도 묶지 않고 따로 받는다 (11/13). 사람이 화면에서 한 건씩 정해 보낸
    # 요청(재무 개선 요청 등)이 남의 요청에 붙지 않게 한다. 비슷한 요청 표시(제안)는 그대로 한다
    separate: bool = False
    # 요청을 어떻게 만들었나: 개발 AI 대화의 코딩 방식과 같은 값 (`DevChatSendInput.mode`).
    # agent = 서버 AI · subscription = 개발자 본인 Claude. 모르면 비운다. 저장만 하고 판정에 안 쓴다
    mode: Literal["agent", "subscription"] | None = None
    # 요청한 사람(계정 id, 없으면 사람 토큰 이름). 부서·서비스가 아니라 사람이다. 저장만 한다
    requested_by: str | None = None


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


# ---- 변경 요청 철회 (2026-11-13, 재무 요청)
#
# - `withdraw_ticket`: 요청 부서 → planning, 등급 A.
# - 요청 부서가 자기가 보낸 요청(create_ticket)을 거둬들인다. planning은 그 요청을 목록에서 지운다.
# - 보낸 부서와 `source_ref`가 맞아야 한다. 다른 부서 요청은 `not_yours`.
# - 이미 개발로 넘어갔거나(작업·PR 있음) 다른 요청이 이 요청에 묶여 있으면 지우지 않는다
#   (`in_progress`). 그때는 기획 화면에서 사람이 중단·기각한다.
# - 없는 요청은 `not_found` (이미 지워졌으면 요청 부서는 자기 기록만 지우면 된다).


class WithdrawTicketInput(AxModel):
    request_id: str  # REQ-n (create_ticket이 돌려준 번호)
    source_ref: str | None = None  # create_ticket에 보낸 요청 부서 참조 ID. 있으면 같아야 한다
    reason: str = ""  # 철회 이유 (활동 기록에만 남긴다)


class WithdrawTicketOutput(AxModel):
    request_id: str
    outcome: Literal["withdrawn", "not_found", "not_yours", "in_progress"]
    message: str = ""
    removed_spec: str | None = None  # 같이 지운 기획서 초안 ID


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


# ---- 넘기기 받았는지 묻기 (planning ↔ dev `handoff_known`, 등급 read, 2026-11-23)
#
# 다시 켰을 때 맞추기가 받았다고 기록한 넘기기를 다 다시 보내지 않고, 받는 쪽에 한 번 묻는다.
# 받는 쪽은 준 멱등 키 중 자기가 반영한(중복 거르기 기록에 있는) 키만 돌려준다.
# 상태를 바꾸지 않는다.
# 빠진 키(받는 쪽이 상태를 잃음)만 보내는 쪽이 다시 보낸다.

HANDOFF_KNOWN_MAX_KEYS = 2000


class HandoffKnownInput(AxModel):
    keys: list[str] = Field(default_factory=list, max_length=HANDOFF_KNOWN_MAX_KEYS)


class HandoffKnownOutput(AxModel):
    known: list[str] = Field(default_factory=list)  # keys 중 받는 쪽이 반영한 것 (준 순서)


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
    "paused_by_human",  # 사람이 요청을 중단함 (재개 전까지 배포 지시 안 함)
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


# ---- 사람 중단·재개 (planning → dev `receive_request_pause`, A, 11/3)
# 사람이 요청을 중단하면 기획은 초안·확정·전달·배포 지시를 멈추고, 이미 개발로 넘어간 요청이면
# 개발에 알린다. 개발은 그 요청의 코딩을 멈추고 열린 PR을 자동 머지하지 않는다. 재개하면 이어서.
# 멱등성 키: `{ctx}:request_pause:{request_id}:{n}` (n = 그 요청의 중단·재개 차례)


class RequestPauseInput(AxModel):
    """planning → dev 요청 중단·재개 알림."""

    request_id: str
    paused: bool
    decided_by: str = "human"
    reason: str | None = None


class RequestPauseOutput(AxModel):
    outcome: Literal["applied", "duplicate", "unknown_request"]
    tasks_stopped: list[str] = Field(default_factory=list)
    prs_held: list[int] = Field(default_factory=list)


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


# ---- 기획 제안 (2026-11-04). 11/4 데이터분석(재현)·기획(예진) 합의
#
# 흐름: 데이터분석(인사이트 + 데이터 근거) → 기획(제안 + 실행 근거 + 목표) → 사람 확인(확정·폐기)
#       → 마케팅 실행 또는 개발(요청) → 데이터분석 성과 측정 → 기획에 결과 기록
# 원칙
# 1. 수치는 데이터분석에서만 만든다. `facts`에 담아 보내고
#    기획·마케팅은 다시 계산하지 않고 인용한다.
# 2. 기획이 넣는 숫자는 목표치뿐이고 `target` 칸에 넣는다.
# 3. `body_md`는 설명용. 판단은 `facts`/`target`만 본다. 본문 숫자가 둘에 없으면 경고한다.
# 4. id로 끝까지 잇는다: insight_id → proposal_id → campaign_id → result
# 5. 모든 메시지에 `schema_version`을 넣는다.
#
# 창구
# - `receive_insight`: data(·marketing) → planning, A. 인사이트 한 건 → 기획 제안(PROP-n)
# - data `trend_insight_brief`(read, 데이터분석이 만드는 중): 기획이 불러 인사이트 목록을 받는다
# - data `campaign_effect`(read): 기획이 성과를 잴 때
#   insight_id·proposal_id·campaign_id를 같이 보낸다
# - `receive_campaign_brief`: planning → marketing, A (marketing이 자기 관문으로 실행)
# - `receive_campaign_update`: marketing(게시) · data(성과) → planning, A

PROPOSAL_SCHEMA_VERSION = "1"
SchemaVersion = Literal["1"]


class InsightFact(AxModel):
    """데이터 근거 한 줄. 값은 보낸 쪽 코드가 낸 값 그대로."""

    label: str = Field(min_length=1, max_length=120)  # 무엇 (예: "독서" 검색 작년 11월)
    value: str = Field(min_length=1, max_length=80)  # 얼마 (예: 평균의 1.6배)
    source: str = Field(min_length=1, max_length=60)  # 그 값을 낸 skill
    link: str | None = Field(default=None, max_length=500)  # 출처 링크 (영상·검색 화면 등)


class ProposalTarget(AxModel):
    """기획이 정한 목표치 (기획이 넣는 유일한 숫자)."""

    label: str = Field(min_length=1, max_length=120)  # 무엇을 (예: 캠페인 기간 주문 증분)
    value: str = Field(min_length=1, max_length=80)  # 얼마나 (예: +10% 이상)
    measure: str | None = Field(default=None, max_length=60)  # 잴 skill (예: campaign_effect)


class InsightPast(AxModel):
    """인사이트의 지난 캠페인 결과 하나 (데이터분석 InsightPastResult에서 기획이 쓰는 칸만).
    조회·매출을 합친 점수는 없고, 문장(summary_line·note_line)은 데이터분석이 만든 그대로다."""

    campaign_id: str = Field(min_length=1, max_length=80)
    views_label: str = Field(default="", max_length=40)  # 조회 달성 · 조회 미달 · 측정 중 …
    sales_label: str = Field(default="", max_length=80)  # 더 팔림 · 덜 팔림 · 효과 확인 안 됨 …
    band: str = Field(default="gray", max_length=20)
    summary_line: str = Field(default="", max_length=300)
    note_line: str | None = Field(default=None, max_length=300)
    actions: list[str] = Field(default_factory=list, max_length=5)  # 개선 방향 문장 그대로
    # 아래는 예전 응답은 없다. 조회수 숫자와 잰 시각
    # (11/18 기획 방향 선택지: 고객사 규모를 숫자로 잰다)
    views: int | None = Field(default=None, ge=0)
    views_at: UtcDatetime | None = None


# 홍보 영상 방향 (11/24). fun_first = 대상층이 좋아하는 형식·소재로 웃기고 브랜드는 끝에
# ("이 재밌는 영상 만든 채널 어디지?"), product = 제품·업종 소개(의뢰가 제품을 직접 말할 때 등).
# 브랜드 노출: end_card(마지막 2~3초 채널 이름·로고) · prop(화면 속 소품으로 살짝)
# · caption(캡션·프로필에만)
PromoStyle = Literal["fun_first", "product"]
BrandExposure = Literal["end_card", "prop", "caption"]


class ReceiveInsightInput(AxModel):
    schema_version: SchemaVersion = PROPOSAL_SCHEMA_VERSION
    insight_id: str = Field(min_length=1, max_length=80)  # 보낸 쪽 인사이트 id (재전송은 중복)
    title: str = Field(min_length=1, max_length=120)
    summary: str = Field(min_length=1, max_length=400)  # 무엇을 발견했는지 한두 문장
    skill: str = Field(min_length=1, max_length=60)  # 인사이트를 낸 skill
    facts: list[InsightFact] = Field(
        min_length=1, max_length=12
    )  # 근거 없는 인사이트는 받지 않는다
    products: list[str] = Field(default_factory=list, max_length=10)  # 관련 회사 상품 이름
    audience: str | None = Field(default=None, max_length=60)  # 타깃 연령대 (예: 20대)
    shorts_format: str | None = Field(default=None, max_length=60)  # 잘 맞는 숏츠 형식
    generated_at: UtcDatetime | None = None  # 어느 시점 데이터 기준인지
    body_md: str | None = Field(default=None, max_length=8000)  # 설명 (마크다운)
    # 이 인사이트로 했던 지난 캠페인 결과 (최근 3개, 데이터분석 brief v4 past_results)
    past: list[InsightPast] = Field(default_factory=list, max_length=3)
    # 아래는 예전 요청은 없다. products와 같은 순서의 회사 상품 ID (데이터분석 InsightProduct)
    product_ids: list[str] = Field(default_factory=list, max_length=10)
    # 아래는 예전 요청은 없다. 캠페인 콘텐츠 카테고리(예: 아재개그)·형식(재무 ContentFormat).
    # 재무 의견 → 기획 제안 → 마케팅 실행까지 같은 카테고리로 잇는다 (11/17 태윤 요청)
    content_category: str | None = Field(default=None, min_length=1, max_length=40)
    content_format: ContentFormat | None = None
    # 아래는 예전 요청은 없다. 총괄 의뢰(홍보 영상)의 고객 문장 그대로 (11/23 재현 님: "30초 이내"가
    # 제작안에 안 갔다). untrusted라 기획은 LLM에 넘기지 않고 길이 같은 숫자만 코드로 읽는다
    request_text: str | None = Field(default=None, max_length=4000)
    # 아래는 예전 요청은 없다. 의뢰에서 사람이 화면으로 고른 회사 캐릭터
    # (마케팅 제작 기록 CR-…, 11/26). 고객 글이 아니다. 기획은 이 번호로 마케팅
    # creative_record_get을 불러 주인공 외형·그림체를 쓴다
    brand_character_record_id: str | None = Field(default=None, pattern=r"^CR-[0-9a-f]{12}$")
    # 아래는 예전 요청은 없다. 홍보 영상 방향 (11/24 재현 님 "재미 먼저, 브랜드는 끝에"). 데이터가
    # 회사 브랜드 설정(tenant.brand)과 의뢰 문장("책 소개"면 product)으로 정한 최종값을 보낸다.
    # None이면 기획은 fun_first·end_card로 본다
    promo_style: Literal["fun_first", "product"] | None = None
    brand_exposure: Literal["end_card", "prop", "caption"] | None = None


class ReceiveInsightOutput(AxModel):
    schema_version: SchemaVersion = PROPOSAL_SCHEMA_VERSION
    # deleted_recently = 사람이 최근 지운 제안과 같은·비슷한 인사이트라 만들지 않음
    outcome: Literal["created", "duplicate", "deleted_recently"]
    proposal_id: str  # PROP-n
    # 총괄 의뢰(홍보 영상 흐름)로 받으면 영상 제작안을 바로 써서 돌려준다 (11/19). 사람 확인 전
    # 초안이라 마케팅에는 아직 안 간다. 그 밖의 요청자는 None (기획안 만들 때 쓴다)
    video_plan: VideoPlan | None = None


class VideoMediaHints(AxModel):
    """마케팅 장면 템플릿 필드에 넣을 기획 힌트 (11/20 재헌 님과 맞춤). 모두 선택 짧은 문자열.

    마케팅이 `check_field_value`로 검사해 통과한 값만 쓰고, 비거나 걸리면 자기 추천값을 쓴다.
    화이트리스트는 마케팅 `scene_templates.toml`에만 있다(여기에 enum으로 복사하지 않는다)."""

    season: str | None = Field(default=None, max_length=30)  # spring·summer·autumn·winter
    color_theme: str | None = Field(default=None, max_length=30)  # 예: warm cream
    category: str | None = Field(default=None, max_length=30)  # 책 분류 짧은 단어


def _clamp(value: Any, low: float, high: float) -> Any:
    """숫자면 [low, high]로 자른다. 숫자가 아니면 그대로(검증이 거른다)."""
    if isinstance(value, bool) or not isinstance(value, int | float):
        return value
    return min(max(value, low), high)


class KeyPoseHead(AxModel):
    """마네킹 머리 방향(도). yaw: 몸 기준 좌우(몸 yaw에 더함), pitch: 위아래(+ = 아래)."""

    yaw: float = 0.0
    pitch: float = 0.0

    @model_validator(mode="before")
    @classmethod
    def _clamp(cls, data: Any) -> Any:
        if isinstance(data, dict):
            data = {**data}
            data["yaw"] = _clamp(data.get("yaw", 0.0), -80, 80)
            data["pitch"] = _clamp(data.get("pitch", 0.0), -40, 40)
        return data


class KeyPoseArm(AxModel):
    """마네킹 팔 한쪽(도). raise: 어깨에서 앞으로 든 각도(0 = 내림), bend: 팔꿈치 굽힘.
    `raise`는 파이썬 예약어라 별칭으로 받고 JSON에도 `raise`로 쓴다."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True, serialize_by_alias=True)

    raise_: float = Field(default=0.0, alias="raise")
    bend: float = 0.0

    @model_validator(mode="before")
    @classmethod
    def _clamp(cls, data: Any) -> Any:
        if isinstance(data, dict):
            data = {**data}
            if "raise_" in data and "raise" not in data:
                data["raise"] = data.pop("raise_")
            data["raise"] = _clamp(data.get("raise", 0.0), -30, 170)
            data["bend"] = _clamp(data.get("bend", 0.0), 0, 150)
        return data


class KeyPose(AxModel):
    """3D 마네킹 콘티 한 칸의 포즈 (11/25). 기획 LLM은 이 숫자만 내고 그림은 기획 코드가 그린다
    (`pose_render`, 원근 투영·바닥 격자·그림자). 범위 밖 숫자는 잘라서(clamp) 받는다.

    shot: 카메라 샷 크기, x: 좌우 위치(m, + = 화면 오른쪽), z: 카메라에서 먼 쪽(m),
    yaw: 몸 방향(0 = 카메라를 봄, 90 = 화면 왼쪽을 봄), step: 걸음 폭(0 = 섬), prop: 소품,
    label_ko: 칸 위 한 줄."""

    shot: Literal["close", "medium", "full"] = "medium"
    x: float = 0.0
    z: float = 0.0
    yaw: float = 0.0
    head: KeyPoseHead = Field(default_factory=KeyPoseHead)
    arm_l: KeyPoseArm = Field(default_factory=KeyPoseArm)
    arm_r: KeyPoseArm = Field(default_factory=KeyPoseArm)
    step: float = 0.0
    prop: Literal["book", "desk", "phone", "shelf", "cup"] | None = None
    label_ko: str = Field(default="", max_length=30)

    @model_validator(mode="before")
    @classmethod
    def _clamp(cls, data: Any) -> Any:
        if isinstance(data, dict):
            data = {**data}
            for key, low, high in (("x", -1.5, 1.5), ("z", 0, 3), ("yaw", -180, 180),
                                   ("step", 0, 0.6)):  # fmt: skip
                if key in data:
                    data[key] = _clamp(data[key], low, high)
            label = data.get("label_ko")
            if isinstance(label, str):
                data["label_ko"] = " ".join(label.split())[:30]
        return data


class VideoScene(AxModel):
    """영상 제작안의 장면 하나 (11/19 홍보영상 솔루션 ③ 장면별 스토리보드).

    `caption`은 자막(편집 때 오버레이로 넣는다)이고 영상 모델에 그리지 않는다. `prompt`는 그
    장면을 만들 영상 생성 프롬프트(영문)이며 글자·실존 인물·실제 브랜드를 요청하지 않는다."""

    n: int = Field(ge=1, le=8)
    start_s: int = Field(ge=0, le=120)
    end_s: int = Field(ge=1, le=120)
    visual: str = Field(min_length=1, max_length=200)  # 화면 구성 (사람이 읽는 한국어)
    caption: str = Field(default="", max_length=60)  # 자막 (오버레이, 비면 자막 없음)
    prompt: str = Field(min_length=1, max_length=800)  # 영상 생성 프롬프트 (영문)
    # 아래는 예전 제작안은 없다 (빈 문자열). 키프레임으로 쓸 만큼 자세한 "무엇을 보여 줄지"
    # (11/24 멘토링 "첫·끝 화면을 주고 사이 동작을 자세히"). 기획은 모델을 모르고 글만 쓴다.
    # 그림·영상 프롬프트는 마케팅이 만든다. `prompt`와 같이 글자·실존 인물·실제 브랜드를
    # 담지 않는다.
    # 장면 i의 end_frame과 장면 i+1의 start_frame은 같은 순간이다
    start_frame: str = Field(default="", max_length=200)  # 장면 첫 화면 (한국어)
    end_frame: str = Field(default="", max_length=200)  # 장면 끝 화면 (한국어)
    action_en: str = Field(default="", max_length=400)  # 첫→끝 사이 동작 (동사 중심 영문)
    camera_en: str = Field(default="", max_length=120)  # 샷 크기·카메라 움직임 (영문)
    # 아래는 예전 제작안은 없다 (빈 문자열). 기획이 그린 SVG 콘티(11/25): 장면 첫·끝 화면의 구도를
    # 단순 도형으로 그린 SVG 원문(viewBox 0 0 90 160, 9:16). 기획이 허용 요소·속성만 남겨 정리한
    # 값이다. 화면은 <img src="data:image/svg+xml;base64,…">로만 보여 준다. 마케팅은 스토리보드
    # 칸의 SVG로 그대로 쓸 수 있다. 11/25 3D 마네킹 콘티(그라데이션 구 음영·바닥 격자)를 넣으려고
    # 상한을 8KB → 16KB로 늘렸다(허용 범위 확대)
    start_svg: str = Field(default="", max_length=16384)
    end_svg: str = Field(default="", max_length=16384)
    # 아래는 예전 제작안은 없다 (None). 3D 마네킹 콘티의 첫·끝 포즈(11/25). 기획 LLM이 숫자만 내고
    # 기획 코드가 SVG로 그려 start_svg·end_svg에 넣는다
    start_pose: KeyPose | None = None
    end_pose: KeyPose | None = None

    @model_validator(mode="after")
    def _ends_after_start(self) -> VideoScene:
        if self.end_s <= self.start_s:
            raise ValueError("end_s는 start_s보다 커야 한다")
        return self


class RequestRule(AxModel):
    """의뢰문에 적힌 연출 지시 하나와 제작안이 지켰는지.

    (11/26 wr_012c98d9d9 "마지막 대사는 정확히 …")

    kind: quote = 대사·자막 문장 그대로, ending = 마지막 장면, setting = 배경, prop = 소품,
    action = 동작, look = 외형. kept·where는 기획 코드 검사 결과(LLM이 정하지 않는다)."""

    kind: Literal["quote", "ending", "setting", "prop", "action", "look"]
    text_ko: str = Field(min_length=1, max_length=120)
    kept: bool = False
    where: str = Field(default="", max_length=60)  # 지킨 곳 (예: "장면 4 자막", "장면 1~3")


class VideoPlan(AxModel):
    """기획 → 마케팅 영상 제작안: 콘셉트·대본·장면별 스토리보드·영상 생성 프롬프트.

    `style`은 모든 장면 프롬프트에 공통으로 붙일 화풍, `negative`는 공통 금지(글자·로고·실존
    인물·실제 브랜드). 장면 시간은 0초부터 빈틈없이 이어지고 마지막 장면이 `duration_s`에 끝난다."""

    concept: str = Field(min_length=1, max_length=200)  # 홍보 콘셉트 한 줄
    duration_s: int = Field(ge=5, le=120)
    aspect: Literal["9:16", "1:1", "16:9"] = "9:16"
    script: str = Field(min_length=1, max_length=800)  # 전체 흐름 대본 (한국어)
    scenes: list[VideoScene] = Field(min_length=1, max_length=8)
    style: str = Field(min_length=1, max_length=300)  # 공통 화풍 (영문)
    negative: str = Field(min_length=1, max_length=300)  # 공통 금지 (영문)
    written_by: Literal["llm", "rule", "human"] = "rule"
    media_hints: VideoMediaHints | None = None  # 마케팅 장면 템플릿 힌트 (11/20, 선택)
    # 아래는 예전 제작안은 없다. 광고 목적 (11/24 멘토링 "광고는 광고답게"):
    # solution_ad = 우리 솔루션·서비스 소개(문제 → 반전 → 해결 장면 → 행동 문구),
    # client_offer = 고객 혜택(할인·특가·이벤트·후기,
    #   혜택 먼저 → 쓰는 장면 → 후기 한 줄 → 행동 문구),
    # channel_content = 채널 콘텐츠(지금 흐름). None = 정하지 않음(규칙 제작안·예전 제작안)
    purpose: Literal["solution_ad", "client_offer", "channel_content"] | None = None
    offer_ko: str = Field(default="", max_length=60)  # 혜택 한 줄 (한국어, 비면 없음)
    cta_ko: str = Field(default="", max_length=40)  # 마지막 행동 문구 (한국어, 비면 없음)
    # 규칙 제작안으로 쓴 이유 (11/25 PROP-37 "규칙 초안"이 왜인지 화면에 보이게). LLM 제작안·사람·
    # 예전 제작안은 "". no_llm = 회사 AI 연결 없음, timeout = 제한 시간 초과, bad_format = 답 형식
    # 불일치, rejected = 금지 규칙에 걸림, error = 그 밖의 호출 실패
    fallback_reason: Literal["", "no_llm", "timeout", "error", "bad_format", "rejected"] = ""
    # 3D 마네킹 콘티(장면 SVG)를 못 그린 이유 (11/25 PROP-38 "장면 그림 자리"만 남은 것이 왜인지
    # 화면에 보이게). 다 그렸거나 일부라도 그렸거나 예전 제작안은 "". no_llm = 회사 AI 연결 없음,
    # timeout = 포즈 호출 제한 시간 초과, bad_format = 포즈 답 형식 불일치, rejected = 답은 왔지만
    # 쓸 포즈가 하나도 없음(정리 규칙 탈락 포함), error = 그 밖의 호출 실패
    svg_reason: Literal["", "no_llm", "timeout", "bad_format", "rejected", "error"] = ""
    # 아래는 예전 제작안은 없다. 주인공을 어디서 정했나
    # (11/26 wr_012c98d9d9: 실사 가상 인물을 골랐는데 고양이 귀 일러스트로 나왔다).
    # company_character = 의뢰에서 고른 회사 캐릭터(마케팅 제작 기록),
    # brand = 회사 브랜드 기본 캐릭터(tenant [brand], 지금 동작)
    character_source: Literal["company_character", "brand"] = "brand"
    # 화면 "주인공" 줄: 회사 캐릭터 외형 요약(한국어, 시트 summary_ko)과 그림체(photo = 실사 그대로,
    # illustration = 그림체 그대로). brand면 비어 있다
    character_ko: str = Field(default="", max_length=300)
    character_look: Literal["", "photo", "illustration"] = ""
    # 아래는 예전 제작안은 없다. 의뢰문의 연출 지시와 지켰는지 (11/26).
    # 지시가 없는 의뢰·규칙 초안은 []
    request_rules: list[RequestRule] = Field(default_factory=list, max_length=6)

    @model_validator(mode="after")
    def _scenes_cover_duration(self) -> VideoPlan:
        at = 0
        for i, sc in enumerate(self.scenes, start=1):
            if sc.n != i or sc.start_s != at:
                raise ValueError("장면은 1번부터 차례로, 앞 장면이 끝난 시각에 시작해야 한다")
            at = sc.end_s
        if at != self.duration_s:
            raise ValueError("마지막 장면이 duration_s에 끝나야 한다")
        return self


ReceiveInsightOutput.model_rebuild()  # video_plan이 아래에 정의된 VideoPlan을 가리킨다


class CampaignBriefInput(AxModel):
    """기획 → 마케팅: 사람이 확정한 제안 (marketing `receive_campaign_brief`)."""

    schema_version: SchemaVersion = PROPOSAL_SCHEMA_VERSION
    proposal_id: str
    insight_id: str | None = None
    title: str
    idea: str  # 무엇을 할지
    why: str  # 왜 해야 하는지 (facts 인용)
    facts: list[InsightFact] = Field(default_factory=list)
    target: ProposalTarget | None = None
    products: list[str] = Field(default_factory=list)
    format: str | None = None
    approved_by: str  # 확정한 사람
    note: str | None = None
    body_md: str | None = None
    # 후속 기획이면 이어 가는 지난 캠페인 id (같은 캠페인을 계속하는 방향, 11/6 예진 결정)
    continue_campaign: str | None = None
    # 아래는 예전 요청은 없다. products와 같은 순서의 회사 상품 ID (인사이트에서 받은 것)
    product_ids: list[str] = Field(default_factory=list, max_length=10)
    # 아래는 예전 요청은 없다. 인사이트에서 받은 콘텐츠 카테고리·형식 (재무·마케팅 content_category,
    # format과 같은 값). `format`은 기획 초안의 자유 문구(숏폼 15초 등)라 이름을 따로 둔다
    content_category: str | None = Field(default=None, min_length=1, max_length=40)
    content_format: ContentFormat | None = None
    # 아래는 예전 요청은 없다. 사람이 고른 기획 방향과 올라탈 유행 후보 이름
    # (11/18 기획 방향 선택지)
    idea_angle: Literal["ride_trend", "own_trend", "with_product"] | None = None
    trend_refs: list[str] = Field(default_factory=list, max_length=3)
    # 아래는 예전 요청은 없다. 영상 제작안(콘셉트·대본·장면별 스토리보드·영상 생성 프롬프트).
    # 사람이 기획안에서 확인한 뒤 넘긴다 (11/19 홍보영상 솔루션 ③→⑤)
    video_plan: VideoPlan | None = None


class CampaignBriefOutput(AxModel):
    schema_version: SchemaVersion = PROPOSAL_SCHEMA_VERSION
    outcome: Literal["accepted", "duplicate", "rejected"]
    campaign_id: str | None = None  # 마케팅이 붙인 캠페인 id (accepted일 때)
    reason_code: str | None = None  # rejected일 때


class ReceiveCampaignUpdateInput(AxModel):
    """게시(marketing)·성과(data)를 기획에 기록한다. 성과 숫자는 data가 낸 `facts` 그대로."""

    schema_version: SchemaVersion = PROPOSAL_SCHEMA_VERSION
    proposal_id: str
    campaign_id: str
    stage: Literal["published", "result"]
    direction: Literal["늘었음", "같음", "줄었음", "확인 불가"] | None = None  # result일 때
    summary: str | None = Field(default=None, max_length=200)  # result일 때
    facts: list[InsightFact] = Field(default_factory=list, max_length=12)  # result 숫자 (data)
    # 아래는 published 때 마케팅이 채운다 (선택, 저장만)
    insight_id: str | None = Field(default=None, max_length=80)  # 받은 brief의 insight_id
    channel: str | None = Field(default=None, max_length=40)  # 게시 채널 (banner·sns 등)
    published_at: UtcDatetime | None = None
    post_ref: str | None = Field(default=None, max_length=120)  # 게시물 id

    @model_validator(mode="after")
    def _result_has_direction(self) -> ReceiveCampaignUpdateInput:
        if self.stage == "result" and (self.direction is None or not self.summary):
            raise ValueError("result면 direction·summary가 있어야 한다")
        return self


class ReceiveCampaignUpdateOutput(AxModel):
    schema_version: SchemaVersion = PROPOSAL_SCHEMA_VERSION
    outcome: Literal["applied", "duplicate", "unknown_proposal"]


# ---------------------------------------------------------------- 기획 AI 직접 대화 (11/5)
# dashboard-api `/api/dashboard/planning-chat` → planning `planning_chat_*`.
# 마케팅·고객지원 대화와 같은 모양. `who`는 dashboard-api가 사람 토큰에서만 채운다.
# 대화(thread)는 id·title·status(talking·answering·closed)·who·summary·messages[]
# ·task_id·restricted·message_count·created_at·updated_at.
CHAT_MESSAGE_MAX = 2000


class PlanningChatSendInput(AxModel):
    who: str = Field(min_length=1, max_length=80)
    thread_id: str | None = Field(default=None, min_length=1, max_length=64)
    message: str = Field(min_length=1, max_length=CHAT_MESSAGE_MAX)


class PlanningChatListInput(AxModel):
    who: str = Field(min_length=1, max_length=80)
    limit: int = Field(default=50, ge=1, le=100)


class PlanningChatRef(AxModel):
    who: str = Field(min_length=1, max_length=80)
    thread_id: str = Field(min_length=1, max_length=64)


class PlanningChatThreadOutput(AxModel):
    thread: dict[str, Any]


class PlanningChatListOutput(AxModel):
    threads: list[dict[str, Any]]


# ---------------------------------------------------------------- 개발 AI 직접 대화 (11/5)
# dashboard-api `/api/dashboard/dev-chat` → dev `dev_chat_*`. 기획 AI 대화와 같은 모양에
# 개발만의 `connection`(올릴 레포 연결)·`mode`(agent 서버 AI · subscription 본인 Claude)가 더 있다.
# 대화(thread)는 id·title·status(talking·answering·started·blocked·waiting·closed)·who·messages[]
# ·task_id·connection·mode·summary·restricted·message_count·created_at·updated_at.
DEV_CHAT_MESSAGE_MAX = 4000


class DevChatSendInput(AxModel):
    who: str = Field(min_length=1, max_length=80)
    thread_id: str | None = Field(default=None, min_length=1, max_length=64)
    message: str = Field(min_length=1, max_length=DEV_CHAT_MESSAGE_MAX)
    connection: str | None = Field(default=None, min_length=1, max_length=120)
    mode: Literal["agent", "subscription"] | None = None


class DevChatListInput(AxModel):
    who: str = Field(min_length=1, max_length=80)
    limit: int = Field(default=50, ge=1, le=100)


class DevChatRef(AxModel):
    who: str = Field(min_length=1, max_length=80)
    thread_id: str = Field(min_length=1, max_length=64)


class DevChatThreadOutput(AxModel):
    thread: dict[str, Any]


class DevChatListOutput(AxModel):
    threads: list[dict[str, Any]]
