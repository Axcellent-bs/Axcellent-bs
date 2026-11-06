"""기획·개발 화면·서비스가 쓰는 상태 모델 (프로토타입 `data.ts`의 타입을 옮긴 것).

- 필드는 snake_case다. 프로토타입의 `from`은 파이썬 예약어라 `from_` 필드에 별칭 `from`을 붙였다
  (JSON에는 `from`으로 나간다). 직렬화는 항상 `by_alias=True`로 한다.
- 시각은 UTC aware datetime으로 저장한다. KST 표시는 화면이 한다.
- 상태 값(요청 상태·작업 상태 등)은 프로토타입과 같은 한국어 값을 쓴다 (화면이 그대로 쓴다).
- 이 모델은 화면용 내부 모델이다. 부서 간 계약은 `contracts/`의 모델만 쓴다.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from ax_contracts.schemas import TENANT_ID_DEFAULT, UtcDatetime

Dept = Literal[
    "planning",
    "hr",
    "support",
    "marketing",
    "data",
    "finance",
    "infra",
    "orchestrator",
    "safety",
    "shop",
    "memory",
    "human",
    "dev",  # 같은 부서의 개발·배포 서비스 (planning 서비스와 따로 뜰 때)
]

DEPT_NAME: dict[str, str] = {
    "planning": "기획",
    "hr": "인사",
    "support": "고객지원",
    "marketing": "마케팅",
    "data": "데이터분석",
    "finance": "재무/정산",
    "infra": "인프라(DejaFix)",
    "orchestrator": "통합 에이전트",
    "safety": "안전 레이어",
    "shop": "판매 사이트",
    "memory": "회사 기억",
    "human": "사람",
    "dev": "개발·배포",
}

ReqState = Literal["접수", "기획 중", "작업 중", "완료", "중복 연결", "기각"]
TaskState = Literal["진행 중", "PR 검수", "머지 대기", "완료", "C 보류", "중단"]
Review = Literal["통과", "불일치"]
ActivityKind = Literal["A2A", "이벤트", "execute", "API", "사람"]
Grade = Literal["A", "B", "C", "read"]
BlockKind = Literal["h2", "p", "todo", "ref"]
AutoWait = Literal["답 대기", "교차검증 대기", "사람 확인"]


class DomainModel(BaseModel):
    """도메인 모델 기반. 정의하지 않은 필드는 거부하고, 별칭·필드 이름 모두로 만들 수 있다."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)


class AutoCheck(DomainModel):
    """간단 요청 판정 결과. 사유는 항상 4줄(민감 경로·표시 전용·기각 이력·크기)."""

    ok: bool
    reasons: list[str]


class RelatedItem(DomainModel):
    """비슷한·충돌하는 다른 요청 (related.py). 제안이고 묶기·보류는 사람이 정한다.

    - relation: same(같은 일) · conflict(둘 다 진행 중인데 양립 불가) · update(앞 요청을 다시
      바꿈) · related(비슷함, 관계는 정하지 않음 포함)
    - by: llm(LLM이 후보 안에서 판단) · embed(임베딩 검색만) · lexical(글자 검색만) ·
      path(열린 PR과 같은 파일을 고침, 코드 규칙) · memory(회사 기억 요청 색인)
    """

    id: str
    title: str
    score: float
    relation: Literal["same", "conflict", "update", "related"]
    reason: str
    by: Literal["llm", "embed", "lexical", "path", "memory"]
    # 다른 서비스(기획↔개발)에만 있는 요청이면 찾을 때 읽은 그 요청의 상태 (이 서버에 있으면 None)
    remote_state: str | None = None


class Request(DomainModel):
    id: str
    context_id: str
    tenant_id: str = TENANT_ID_DEFAULT
    from_: Dept = Field(alias="from")
    via: str  # 들어온 연결 지점
    title: str
    evidence: str
    state: ReqState
    spec: str | None = None
    duplicate_of: str | None = None
    auto: AutoCheck | None = None  # 간단 요청 판정 결과 (create_ticket으로 들어온 것만)
    # 요청 부서 쪽 참조 ID (예: 고객지원 문의 inq_12). 저장만 하고 판정(중복·간단 요청)에 안 쓴다
    source_ref: str | None = None
    evidence_ids: list[str] = Field(default_factory=list)  # 요청 부서가 보낸 근거 ID
    flow: bool = False  # 들어온 뒤 사람 클릭 없이 다음 단계로 이어 간다 (일반 요청·개선 초안)
    related: list[RelatedItem] = Field(default_factory=list)  # 비슷한·충돌하는 요청 (제안)
    # 요청을 보낸 서비스 id (예: inquiry·coupon). `from_`은 부서, 되묻기·회신은 이 서비스로 보낸다
    from_service: str | None = None
    # 사람이 중단함: 기획서 초안·자동 확정·개발 전달·배포 지시를 재개 전까지 멈춘다
    paused: bool = False


class ParaWarn(DomainModel):
    card: str
    text: str


class Para(DomainModel):
    id: str  # R1, C1 …
    kind: Literal["req", "done"]
    text: str
    warn: ParaWarn | None = None  # 인사 ② check_plan 결과
    incident: str | None = None  # 장애 표시 (link_incident)
    paths: list[str] = Field(default_factory=list)  # 바꿀 파일 경로 (비면 역추적 규칙으로 정함)


class AutoReview(DomainModel):
    """자동 확정·자동 머지를 하지 않고 멈춘 이유 (대기열 항목).

    - `state`: 답 대기(질문 남음) · 교차검증 대기(#171 규칙에 걸림, safety 보류 포함) ·
      사람 확인(되돌릴 수 없는 영역)
    - `rules`: 걸린 #171 규칙 키. `params`: 판정에 쓴 execute params (코드 계산 값)
    - `gate_status`·`review_id`: safety가 보류·거절했을 때 그 결과 (planning 사전 판정은 통과)
    판단은 기존 사람 버튼(`confirmSpec`·`mergePr`)으로 한다. 실제 교차검증자 연결은 safety 협의 뒤.
    """

    state: AutoWait
    rules: list[str] = Field(default_factory=list)
    params: dict[str, Any] = Field(default_factory=dict)
    at: UtcDatetime
    gate_status: str | None = None
    review_id: str | None = None
    # 교차검증 근거 (cross_check.EvidenceLine): 어느 부서에 무엇을 물어 어떤 답을 받았나
    evidence: list[dict[str, Any]] = Field(default_factory=list)


class Spec(DomainModel):
    id: str
    title: str
    request: str
    status: Literal["초안", "확정"]
    problem: str
    goal: str
    paras: list[Para]
    question: str | None = None
    basis: list[str] = Field(default_factory=list)
    human_sentences: int = 0
    auto: bool = False  # 간단 요청 규칙·자동 확정 규칙으로 사람 확정 없이 처리됨
    auto_review: AutoReview | None = None  # 자동 확정을 멈춘 이유 (확정되면 비움)
    # planning 서비스가 dev 서비스로 넘긴 기획서 (start_dev). 합친 모드에서는 늘 False
    handed_to_dev: bool = False
    # 기획서를 쓴 곳. "dev_team" = 개발팀원이 개발 화면에서 직접 쓴 기획 (코딩 에이전트)
    origin: str | None = None
    # 인사 ② check_plan 실제 결과 (ok·warn·unknown). None = 묻지 않음 (목 모드·예전 기획서)
    hr_check: Literal["ok", "warn", "unknown"] | None = None
    # 요청 서비스에 되물은 질문 (answer_planning_question). 답을 받으면 `question`과 같이 비운다
    question_id: str | None = None
    question_to: str | None = None  # 질문을 보낸 서비스 id
    # 사람이 자유롭게 쓰는 칸 (마크다운). 유사·충돌 판단(related)·인사 확인에 쓰고,
    # 화면의 자동 채우기가 이 글을 AI로 읽어 필수 칸 초안을 만든다 (spec_fill.py, 저장은 사람이)
    body_md: str | None = None
    # 양식에만 있는 칸 (planning spec_template.py): 범위·타깃·핵심 메시지·일정·예산·고려사항
    extra: dict[str, str] = Field(default_factory=dict)
    # 개발이 "코드 변경이 아니다"로 돌려보낸 뒤 사람이 "개발에 다시 맡기기"로 확정함.
    # 이 기획서의 작업은 다시 돌려보내지 않는다 (개발↔기획 핑퐁 방지, coder `not_code`)
    keep_dev: bool = False


CodingStatus = Literal[
    "queued",
    "claimed",  # 개발자 본인 Claude가 가져감 (구독형, work_queue.py)
    "reading",
    "writing",
    "reviewing",
    "opened",
    "merged",
    "waiting_human",
    "failed",
    "canceled",  # 사람이 중단 (열린 PR은 닫고 브랜치를 지움)
    # 앞 작업 PR이 이미 반영해 바뀐 내용이 없음 → PR 없이 완료 (`covered_by`가 반영한 PR)
    "covered",
    # 코드 변경이 아니라 다른 부서 일이라 기획에 돌려보냄 (`returned_to`·`returned_reason`).
    # 기획이 받은 요청 사람 확인에 올리고, 사람이 다른 부서로 보내거나 개발에 다시 맡긴다
    "returned",
]


class CodingFile(DomainModel):
    """코딩 에이전트가 바꾼 파일 하나. 줄 수는 기준 브랜치 글과 비교해 코드로 센다."""

    path: str
    additions: int = 0
    deletions: int = 0
    reason: str = ""


class CodingReview(DomainModel):
    verdict: Review
    notes: list[str] = Field(default_factory=list)


class CodingRun(DomainModel):
    """코딩 에이전트 진행 상태 (작업 하나). 단계마다 바뀌어 SSE로 나간다.

    `attempts`는 파일을 쓴 횟수 (첫 작성 1, 리뷰 불일치 뒤 수정 2).
    `connection`은 올릴 연결 id, `pr`은 만든 PR 표시 id (없으면 None)."""

    status: CodingStatus = "queued"
    model: str | None = None
    files: list[CodingFile] = Field(default_factory=list)
    summary: str | None = None
    review: CodingReview | None = None
    error: str | None = None
    attempts: int = 0
    updated_at: UtcDatetime
    connection: str | None = None
    pr: str | None = None
    instruction: str | None = None  # 사람이 대기 중인 PR에 준 수정 지시 (마지막 것)
    # 작업 대기열: 같은 파일을 먼저 고치는 작업이 있으면 끝날 때까지 기다린다 (coder._wait_turn).
    # skip_queue는 사람이 '지금 진행'으로 기다리지 않게 한 작업. 머지는 그래도 차례로 간다
    skip_queue: bool = False
    waiting_for: list[str] = Field(default_factory=list)  # 기다리는 앞 작업 id
    # 머지가 충돌로 실패함: 다시 시도하면 PR을 닫고 최신 코드로 다시 쓴다
    merge_conflict: bool = False
    # 이 작업이 지금까지 쓴 LLM 호출·토큰 (다시 시도해도 이어서 센다). 실행 한 번 상한은 coder
    llm_calls: int = 0
    llm_tokens: int = 0
    # 누가 코딩하나: agent = 서버 코딩 에이전트(API 키), subscription = 개발자 본인 Claude가
    # MCP로 가져가 코딩한다 (work_queue.py). 구독형은 서버 에이전트를 띄우지 않는다
    mode: Literal["agent", "subscription"] = "agent"
    claimed_by: str | None = None  # 가져간 사람 (사람 토큰 이름)
    # 내 Claude 작업을 맡긴 사람 (사람 토큰 이름). 있으면 그 사람의 Claude만 가져간다
    assignee: str | None = None
    claimed_at: UtcDatetime | None = None
    lease_until: UtcDatetime | None = None  # 이때까지 진행 보고가 없으면 대기열로 돌린다
    progress: str | None = None  # 개발자 Claude가 마지막으로 보고한 진행 글
    # 작업 글(제목·기획·완료 기준)에 에이전트를 노린 지시로 보이는 것 (work_queue.warnings_of).
    # 있으면 사람이 화면에서 글을 확인(text_ok_by)하기 전까지 개발자 Claude가 가져가지 못한다
    text_warnings: list[str] = Field(default_factory=list)
    text_ok_by: str | None = None
    # 실행 관문이 generate_pr를 보류(held)했을 때: 그 멱등 키·보류 건·경로. 사람이 보류 건을
    # 승인하고 "승인하고 PR 열기"(hold_ok)로 다시 돌리면 같은 키로 보내 승인된 결정을 받는다
    hold_key: str | None = None
    hold_review: str | None = None
    hold_paths: list[str] = Field(default_factory=list)
    hold_ok: bool = False
    # status covered: 이 작업 내용을 이미 반영한 PR 표시 id (배포 전 대조에서 그 PR이 맡는다)
    covered_by: list[str] = Field(default_factory=list)
    # CI 실패·시간 초과로 자동 머지 대신 사람 확인에 둠. PR 맞추기(sync)가 CI를 다시 보고
    # 나중에 통과하면 자동 머지 판단을 다시 한다 (dev `recheck_ci`)
    ci_hold: bool = False
    # status returned: 맞을 것 같은 부서(marketing·support·finance·hr·data·other)와 이유 한 줄
    returned_to: str | None = None
    returned_reason: str | None = None


class Task(DomainModel):
    id: str
    spec: str
    para: str
    title: str
    owner: str
    state: TaskState
    coding: CodingRun | None = None  # 코딩 에이전트 작업만 (개발팀 기획)
    # 사람이 폐기함 (중단·실패·돌려보낸 작업만, 열린 PR 없음).
    # 다시 돌리지 않고, 화면 목록에서 숨길 수 있다 (dev `discard_dev_task`)
    discarded: bool = False
    discarded_by: str | None = None
    discarded_at: UtcDatetime | None = None


class PrGate(DomainModel):
    """안전 레이어 코드 규칙 결과 (pr_changed_lines·files·allowed_paths)."""

    result: Literal["통과", "C 보류"]
    lines: int
    files: int
    paths: str


class RepoCandidate(DomainModel):
    connection_id: str
    repo: str
    score: int


class RepoTarget(DomainModel):
    """PR을 올릴 레포(연결). AI가 제안하고 사람이 승인한다 (연결이 하나면 자동 승인).

    `candidates`는 규칙 점수 순, `reasons`는 짧은 근거 줄 (LLM 근거는 앞에 "AI: ")."""

    connection_id: str
    repo: str
    status: Literal["proposed", "approved", "rejected"]
    confidence: Literal["high", "mid", "low"]
    reasons: list[str] = Field(default_factory=list)
    candidates: list[RepoCandidate] = Field(default_factory=list)
    decided_by: str | None = None
    decided_at: UtcDatetime | None = None


class PR(DomainModel):
    id: str  # 표시용 "PR #28"
    number: int  # 28 (계약 RelatedPr.pr_number)
    task: str
    title: str
    branch: str
    reviews: list[Review]
    gate: PrGate
    merged: bool
    merged_at: UtcDatetime | None = None
    changed_paths: list[str] = Field(default_factory=list)
    # 이 머지로 나간 배포 "서비스 버전" (데모: 머지 = dev 배포). 대응을 모르면 None
    deployed_version: str | None = None
    auto: bool = False  # 간단 요청 규칙·자동 머지 규칙으로 자동 머지됨
    auto_review: AutoReview | None = None  # 자동 머지를 멈춘 이유 (머지되면 비움)
    # 형상관리 연결 계층으로 실제로 올린 변경 번호·주소 (목 설정이면 None)
    vcs_number: int | None = None
    url: str | None = None
    # 올릴 레포 제안·승인 (형상관리 연결이 있을 때만. 목 설정이면 None)
    target: RepoTarget | None = None
    closed: bool = False  # 작업 중단으로 닫음 (병합 안 함)
    # 마지막 교차검증에서 다른 부서(총괄·데이터분석)에 받은 근거 줄 (dev cross_check.EvidenceLine).
    # auto_review와 달리 통과·머지·사람 결정 뒤에도 남겨 PR 화면에서 늘 보인다
    cross_evidence: list[dict[str, Any]] = Field(default_factory=list)
    cross_checked_at: UtcDatetime | None = None


class Message(DomainModel):
    who: str
    dept: Dept
    text: str
    decision: str | None = None
    at: UtcDatetime | None = None


class Thread(DomainModel):
    """소통 채널. spec = 문서에 붙는 스레드, team = 기획팀 채널, dept = 다른 부서 채널."""

    id: str
    on: str
    title: str
    messages: list[Message] = Field(default_factory=list)
    kind: Literal["spec", "team", "dept"] | None = None
    peer: Dept | None = None


class DocBlock(DomainModel):
    id: str
    kind: BlockKind
    text: str
    done: bool | None = None


class Doc(DomainModel):
    """정리 문서: 요청·기획서·작업·PR·대화·결정을 한 장으로 모으고 사람이 고친다."""

    id: str
    title: str
    about: str | None = None
    updated_at: UtcDatetime
    created_at: UtcDatetime | None = None  # 만든 시각 (예전 문서는 없음 → 고친 시각으로 대신)
    blocks: list[DocBlock] = Field(default_factory=list)
    by: Literal["ai", "human"] | None = None


class Decision(DomainModel):
    id: str
    spec: str
    text: str
    reason: str
    alternatives: str
    from_: str = Field(alias="from")
    sent_to_hr: bool
    # 인사 record_case 결과: ok 기록됨 · failed 실패 · held 인사 보류 · None 부르지 않음(목 모드)
    hr_record: Literal["ok", "failed", "held"] | None = None


class IncidentCandidate(DomainModel):
    pr: str
    spec: str
    para: str
    merged_at: UtcDatetime


class RollbackCheck(DomainModel):
    target: str
    result: Literal["승인", "거절"]
    why: str


class Incident(DomainModel):
    context_id: str
    tenant_id: str = TENANT_ID_DEFAULT
    title: str
    opened_at: UtcDatetime
    candidates: list[IncidentCandidate] = Field(default_factory=list)
    rollback_check: RollbackCheck | None = None
    linked: bool = False
    follow_up: str | None = None


class Activity(DomainModel):
    seq: int | None = None  # 시뮬레이터로 들어온 건의 순번 (새로 받은 것 표시용)
    at: UtcDatetime
    context_id: str
    tenant_id: str = TENANT_ID_DEFAULT
    from_: Dept = Field(alias="from")
    to: Dept
    point: str  # 연결 지점
    kind: ActivityKind
    grade: Grade | None = None
    text: str


class OutMsg(DomainModel):
    """기획·개발 → 다른 부서로 나간 메시지 (다른 부서 화면의 "기획에서 받은 것")."""

    id: int
    at: UtcDatetime
    context_id: str
    tenant_id: str = TENANT_ID_DEFAULT
    to: Dept
    point: str
    text: str
    read: bool = False


class AutoLevel(DomainModel):
    level: int
    name: str
    lines: int
    files: int


class AutoRun(DomainModel):
    pr: str
    req: str
    at: UtcDatetime
    lines: int
    files: int
    result: Literal["정상", "장애 연결"]


class AutoProposal(DomainModel):
    to: int
    at: UtcDatetime
    why: str


class AutoLog(DomainModel):
    at: UtcDatetime
    text: str
    by: Literal["사람", "자동"]


class AutoPolicy(DomainModel):
    """자동 처리 범위. 넓히기는 사람 승인, 줄이기는 자동."""

    level: int
    paths: str
    streak: int = 0  # 마지막 단계 변경 뒤 연속 정상 건수
    history: list[AutoRun] = Field(default_factory=list)
    proposal: AutoProposal | None = None
    log: list[AutoLog] = Field(default_factory=list)


class ChatPlan(DomainModel):
    """개발 AI가 대화로 정리한 구현 계획 (코딩 작업 입력)."""

    title: str
    plan: str
    done: list[str] = Field(default_factory=list)
    paths: list[str] = Field(default_factory=list)


ChatKind = Literal["text", "question", "plan", "started", "waiting", "error"]


class ChatMessage(DomainModel):
    id: str  # m1, m2 … (대화 안에서)
    role: Literal["user", "assistant"]
    text: str
    at: UtcDatetime
    kind: ChatKind = "text"
    plan: ChatPlan | None = None
    task_id: str | None = None


class ChatThread(DomainModel):
    """개발 화면에서 개발팀원이 개발 AI와 나눈 대화 하나. 계획이 서면 코딩 작업을 띄운다.

    status: talking(대화 중) · started(작업 띄움, `task_id`) · blocked(연결이 없어 못 띄움) ·
    waiting(충돌하는 요청 `waiting_for`가 끝나면 마지막 계획으로 작업을 띄움) ·
    answering(사람 말을 적고 답을 만드는 중) · closed(끝낸 대화, 30분 말이 없어도 끝남)."""

    id: str  # chat-1 …
    title: str
    status: Literal["talking", "started", "blocked", "waiting", "answering", "closed"] = "talking"
    messages: list[ChatMessage] = Field(default_factory=list)
    task_id: str | None = None
    connection: str | None = None
    waiting_for: list[str] = Field(default_factory=list)  # 끝나기를 기다리는 요청 ID
    # 작업을 누가 코딩하나: agent(서버 AI, API 키) · subscription(개발자 본인 Claude, work_queue.py)
    mode: Literal["agent", "subscription"] = "agent"
    # 대화를 연 사람 (사람 토큰 이름). 내 Claude 작업은 이 사람에게 맡긴다
    who: str | None = None
    created_at: UtcDatetime
    updated_at: UtcDatetime


# ---- 기획 제안: 데이터분석 인사이트(근거 수치) → 기획 판단(할 일·이유·목표) → 사람 확정·폐기
#      → 마케팅 또는 개발(요청) → 데이터분석 성과 → 기획에 기록 (계약 ax_contracts planning.py)
ProposalStatus = Literal[
    "확인 대기", "기획안", "마케팅 전달", "진행 중", "성과 확인", "기획서로", "폐기"
]


class ProposalFact(DomainModel):
    """데이터 근거 한 줄. 숫자는 데이터분석 코드가 낸 값 그대로 (기획이 만들지 않는다)."""

    label: str
    value: str
    source: str  # 그 값을 낸 skill
    link: str | None = None  # 출처 링크


class ProposalTarget(DomainModel):
    """기획이 정한 목표치 (기획이 넣는 유일한 숫자)."""

    label: str
    value: str
    measure: str | None = None  # 잴 skill (campaign_effect 등)


class ProposalHandoff(DomainModel):
    """마케팅에 넘긴 결과. 실패면 이유 (마케팅 창구 없음·응답 없음)."""

    ok: bool
    reason: str | None = None
    campaign: str | None = None  # 마케팅이 돌려준 캠페인 id
    at: UtcDatetime | None = None
    channel: str | None = None  # 게시 채널 (published 때 마케팅이 알려 줌)
    published_at: UtcDatetime | None = None
    post_ref: str | None = None  # 게시물 id


class ProposalResult(DomainModel):
    direction: Literal["늘었음", "같음", "줄었음", "확인 불가"]
    summary: str
    facts: list[ProposalFact] = Field(default_factory=list)  # 데이터분석이 낸 성과 숫자
    at: UtcDatetime


class Proposal(DomainModel):
    id: str  # PROP-n
    title: str
    from_: Literal["data", "marketing"] = Field(alias="from")
    skill: str  # 인사이트를 낸 skill
    insight_id: str | None = None  # 보낸 쪽 인사이트 id (id 사슬의 시작)
    insight: str  # 받은 인사이트 (무엇을 발견했나, 보낸 쪽 문장)
    facts: list[ProposalFact] = Field(default_factory=list)  # 데이터 근거 (보낸 쪽 값)
    products: list[str] = Field(default_factory=list)
    audience: str | None = None  # 타깃 연령대 (데이터분석)
    shorts_format: str | None = None  # 잘 맞는 숏츠 형식 (데이터분석)
    body_md: str | None = None  # 보낸 쪽 설명 (외부 문구라 지시로 쓰지 않는다)
    # 아래는 기획 판단 (LLM 초안 또는 규칙, 확정 전 사람이 고친다)
    idea: str  # 무엇을 할지
    why: str  # 왜 해야 하는지
    target: ProposalTarget | None = None
    format: str | None = None
    written_by: Literal["llm", "rule", "human"] = "rule"
    warnings: list[str] = Field(default_factory=list)  # 근거에 없는 숫자 등 (코드 검사)
    status: ProposalStatus = "확인 대기"
    created_at: UtcDatetime
    note: str | None = None  # 폐기 이유 · 확정 메모
    handoff: ProposalHandoff | None = None
    result: ProposalResult | None = None
    request: str | None = None  # 개발로 보냈으면 REQ-n
    spec: str | None = None  # 사람이 확정해 만든 기획안 (기획서 SPEC-n, origin proposal)


class State(DomainModel):
    tenant_id: str = TENANT_ID_DEFAULT
    seq: int = 0  # 들어오고 나간 연동 건마다 1씩 (새로 받은 것 표시용)
    outbox: list[OutMsg] = Field(default_factory=list)
    requests: list[Request] = Field(default_factory=list)
    specs: list[Spec] = Field(default_factory=list)
    tasks: list[Task] = Field(default_factory=list)
    prs: list[PR] = Field(default_factory=list)
    threads: list[Thread] = Field(default_factory=list)
    docs: list[Doc] = Field(default_factory=list)
    decisions: list[Decision] = Field(default_factory=list)
    incidents: list[Incident] = Field(default_factory=list)
    activity: list[Activity] = Field(default_factory=list)
    # 지표 계산용 원자료 (코드로 계산)
    drafts_in: int = 0
    drafts_accepted: int = 0
    warnings: int = 0
    first_pr_minutes: list[int] = Field(default_factory=list)
    auto_policy: AutoPolicy
    chats: list[ChatThread] = Field(default_factory=list)  # 개발 AI 대화 (dev_chat.py)
    # 개발: 넘겨받은 요청 작업을 누가 코딩하나 (회사 설정). auto = 본인 Claude(MCP) 연결한 개발자가
    # 있으면 내 Claude, 없으면 서버 AI(체험 한도 안에서)
    coding_mode: Literal["auto", "agent", "subscription"] = "auto"
    # 서버 AI 코딩 단계별 모델 (plan 파일 고르기 · write 쓰기 · review 리뷰). 비우면 기본 모델
    coding_models: dict[str, str] = Field(default_factory=dict)
    # 서버 AI 코딩 월 예산 (입력+출력 토큰, None = 없음)과 달마다 쓴 양 ("2026-11" → 토큰)
    monthly_budget_tokens: int | None = None
    usage_by_month: dict[str, int] = Field(default_factory=dict)
    # 개발: 본인 Claude(MCP 작업 대기열)로 작업을 본 개발자 → 마지막으로 본 시각 (연결 여부 판단)
    agents_seen: dict[str, UtcDatetime] = Field(default_factory=dict)
    # 기획 제안 (planning proposals.py). 개발은 쓰지 않는다
    proposals: list[Proposal] = Field(default_factory=list)


def dump(model: BaseModel) -> dict:
    """화면·SSE로 내보내는 JSON 형태 (`from` 별칭, ISO8601 UTC)."""
    return model.model_dump(mode="json", by_alias=True)
