"""support skill의 입력·출력 모델과 LLM 응답 스키마.

필드 이름은 dashboard-web 화면이 받는 형태(`types.ts`)와 맞춘다. 고객 문의 원문(제목·본문)은
`AgentRequest.untrusted`로만 들어오므로 입력 모델에는 없다.
"""

from __future__ import annotations

from typing import Any, Literal

from pydantic import Field, field_validator

from ax_contracts.schemas.common import TENANT_ID_DEFAULT, AxModel
from support_agent.categories import Category, normalize_category
from support_agent.claim_schemas import EntityId

Urgency = Literal["low", "medium", "high"]
Sentiment = Literal["calm", "frustrated", "angry"]
InquiryStatus = Literal["open", "resolved", "escalated"]
DraftStyle = Literal["empathetic", "concise", "step_by_step"]
STYLES: tuple[DraftStyle, ...] = ("empathetic", "concise", "step_by_step")


class Finding(AxModel):
    code: str
    level: Literal["error", "warning"]
    message: str


class Validation(AxModel):
    """답변 가드 검사 결과. `error`가 하나라도 있으면 통과하지 못한다."""

    passed: bool
    findings: list[Finding] = Field(default_factory=list)


class Classification(AxModel):
    intent_ko: str
    category: str
    sentiment: Sentiment
    urgency: Urgency
    search_query: str
    pii_masked: list[str] = Field(default_factory=list)
    language: str = "ko"  # 고객 메시지의 언어 (`languages.detect`, 코드가 판별: ko·en·ja·zh·other)
    keywords: list[str] = Field(default_factory=list)  # 핵심 단어 최대 5개 (개인정보 제거)


class Source(AxModel):
    id: str
    doc_id: str
    category: str
    title: str
    section: str
    text: str
    score: float
    from_decision: bool | None = None


class Draft(AxModel):
    style: DraftStyle
    reply: str
    source_ids: list[str] = Field(default_factory=list)
    validation: Validation
    language: str = "ko"  # `reply`를 쓴 언어 (고객 언어, 가드가 검사하지 못하는 언어는 en)
    reply_ko: str | None = None  # 한국어가 아닐 때 상담원이 읽을 한국어 번역


class Analysis(AxModel):
    classification: Classification | None = None
    sources: list[Source] | None = None
    drafts: list[Draft] | None = None
    timings_ms: dict[str, int] | None = None
    validation: Validation | None = None


class Message(AxModel):
    id: int
    role: Literal["customer", "agent"]
    text: str  # 접수된 원문 (증거라 고치지 않는다)
    display_text: str | None = None  # 욕설·모욕·위협 표현을 가린 글. 가릴 것이 없으면 None
    abuse_level: Literal["none", "profanity", "insult", "threat"] = "none"
    abuse_kinds: list[str] = Field(default_factory=list)  # profanity·insult·threat, llm=모델이 찾음
    softened_text: str | None = (
        None  # LLM이 요청만 남겨 정중하게 옮긴 글 (2단계, 코드가 검사한 것만)
    )
    analysis: Analysis | None = None
    created_at: str


class Summary(AxModel):
    summary_ko: str
    issue_ko: str
    actions_ko: list[str] = Field(default_factory=list)
    next_action_ko: str
    category: str
    resolved: bool
    escalate: bool
    escalate_reason_ko: str
    tags: list[str] = Field(default_factory=list)


class ConversationListItem(AxModel):
    id: int
    customer_name: str
    subject: str
    subject_display: str | None = None  # 제목에 욕설·모욕·위협이 있으면 가린 제목
    category: str | None = None
    sentiment: str | None = None
    urgency: Urgency | None = None
    status: InquiryStatus
    order_id: EntityId | None = None
    updated_at: str
    preview: str
    awaiting_reply: bool


class ActivityEntry(AxModel):
    """문의 한 건의 AI 처리 내력 한 줄. 누가(actor) 무엇을(kind) 했는지를 시간순으로 남긴다.

    `title`·`detail`은 코드가 만든 사실 문장이고, 코드 값(`data`)은 화면이 이름표를 붙일 때 쓴다.
    고객 원문·답변 본문은 담지 않는다.
    """

    at: str
    actor: Literal["ai", "agent", "customer", "system"]
    kind: str
    title: str
    detail: str = ""
    ref: str | None = None  # 이어진 대상 (예: claim:3)
    data: dict[str, Any] = Field(default_factory=dict)


FeedbackStatus = Literal["visible", "flagged", "hidden"]


class FeedbackFlag(AxModel):
    """후기에 코드가 붙인 표시. `info`이면 공개는 그대로 두고 선제 연락 같은 후속 조치만 알린다."""

    code: str
    message: str
    info: bool = False


class Feedback(AxModel):
    """고객이 해결된 문의에 남긴 평가(후기). 글은 개인정보를 가린 채 저장한다."""

    rating: int = Field(ge=1, le=5)
    resolved: bool  # 고객이 "문제가 해결됐다"고 답했는지
    comment: str = ""
    status: FeedbackStatus = "visible"
    flags: list[FeedbackFlag] = Field(default_factory=list)
    source: Literal["customer", "console"] = "customer"
    created_at: str
    decided_by: str | None = None
    decided_at: str | None = None


class ResolutionNote(AxModel):
    """해결된 문의에 상담원이 남긴 해결 메모(원인·조치·결과). 글은 개인정보를 가려 저장한다.

    작성자는 호출한 화면이 사람 토큰에서 채워 보낸 이름이다. 세 칸 중 비운 칸은 `None`이다.
    """

    id: int
    author: str
    root_cause: str | None = None
    action_taken: str | None = None
    result: str | None = None
    created_at: str


class Conversation(ConversationListItem):
    tenant_id: str = TENANT_ID_DEFAULT
    context_id: str
    user_id: int | None = None
    messages: list[Message] = Field(default_factory=list)
    summary: Summary | None = None
    order_no: str | None = None
    claims: list[dict[str, Any]] = Field(default_factory=list)  # 읽을 때 이어진 클레임으로 채운다
    activity: list[ActivityEntry] = Field(default_factory=list)  # AI 처리 내력 (오래된 것부터)
    feedback: Feedback | None = None  # 해결 결과에 대한 고객 평가 (문의당 한 번)
    resolution_notes: list[ResolutionNote] = Field(default_factory=list)  # 해결 메모 (최신 먼저)


# ---------------------------------------------------------------- LLM 응답 스키마
class LLMClassification(AxModel):
    intent_ko: str
    category: Category  # 코드 집합으로 고정한다 (`categories.py`). 모델이 쓴 이름은 코드로 바꾼다
    sentiment: Sentiment
    urgency: Urgency
    search_query: str
    keywords: list[str] = Field(default_factory=list)

    @field_validator("category", mode="before")
    @classmethod
    def _to_code(cls, value: Any) -> str:
        return normalize_category(value)


class StyleReply(AxModel):
    style: DraftStyle
    reply: str
    reply_ko: str | None = None


class LLMDrafts(AxModel):
    replies: list[StyleReply]


# ---------------------------------------------------------------- skill 입력
class ListInquiriesInput(AxModel):
    status: InquiryStatus | None = None
    user_id: int | None = None  # 이 고객의 문의만 (고객 화면을 대신 부르는 shop-api는 필수)


class GetInquiryInput(AxModel):
    inquiry_id: int
    user_id: int | None = None  # 이 고객의 문의일 때만 (shop-api는 필수, 남의 문의는 not_found)


class CreateInquiryInput(AxModel):
    """제목·본문은 `untrusted`의 `subject`·`message`로 받는다."""

    user_id: int
    customer_name: str
    order_id: EntityId | None = None
    order_no: str | None = None
    notice_id: str | None = None  # 문의가 가리키는 공지. 있으면 마케팅 get_notice로 사실을 읽는다
    # 공지 번호를 모를 때 찾을 구성요소. 이게 있거나 사건(ctx_inc_) 요청이면 게시 중인 공지를 찾는다
    notice_component: Literal["api", "db", "payment"] | None = None


class AddCustomerMessageInput(AxModel):
    """본문은 `untrusted`의 `message`로 받는다."""

    inquiry_id: int
    notice_id: str | None = None
    notice_component: Literal["api", "db", "payment"] | None = None  # CreateInquiryInput과 같다
    user_id: int | None = None  # shop-api는 필수: 이 고객의 문의에만 메시지를 덧붙일 수 있다


class CheckReplyInput(AxModel):
    """검사할 답변은 `untrusted`의 `text`로 받는다."""

    inquiry_id: int | None = None


class SendReplyInput(AxModel):
    """답변은 `untrusted`의 `text`로 받는다. 초안을 썼다면 `draft_style`·`draft_text`도 적는다."""

    inquiry_id: int
    draft_style: DraftStyle | None = None
    draft_text: str | None = None


RejectReason = Literal["wrong_info", "missing_policy", "tone", "too_long", "other"]


class RejectDraftInput(AxModel):
    inquiry_id: int
    draft_style: DraftStyle
    # 거절한 이유. 문서가 없어서(missing_policy)인지 말투 때문인지 가르는 지식 공백 신호다 (선택)
    reason_code: RejectReason | None = None


class SummarizeInquiryInput(AxModel):
    inquiry_id: int


class SetInquiryStatusInput(AxModel):
    inquiry_id: int
    status: InquiryStatus


ClaimActivityKind = Literal[
    "claim_created", "claim_decided", "claim_return_received", "claim_inspected", "claim_paid"
]


class RecordClaimActivityInput(AxModel):
    """클레임 서비스가 문의 서비스에 알리는 클레임 처리 결과. 문장은 문의 쪽이 코드로 만든다.

    고객이 쓴 글·상담원 메모는 받지 않는다 (코드 값과 금액만).
    """

    inquiry_id: int
    kind: ClaimActivityKind
    claim_id: int
    claim_type: str | None = None
    reason: str | None = None
    status: str | None = None
    outcome: str | None = None
    decision: Literal["approve", "reject"] | None = None
    inspection: str | None = None
    amount: int | None = None


class SubmitFeedbackInput(AxModel):
    """해결된 문의의 평가. 후기 글은 `untrusted.comment`로만 받는다."""

    inquiry_id: int
    rating: int = Field(ge=1, le=5)
    resolved: bool
    user_id: int | None = None  # 고객 화면이 대신 부를 때 본인 문의인지 확인한다
    source: Literal["customer", "console"] = "customer"


class ListFeedbackInput(AxModel):
    status: FeedbackStatus | None = None
    max_rating: int | None = Field(default=None, ge=1, le=5)
    limit: int = Field(default=100, ge=1, le=500)


class AddResolutionNoteInput(AxModel):
    """해결 메모 남기기. 메모 글(`root_cause`·`action_taken`·`result`)은 `untrusted`로만 받는다.

    `author`는 화면(dashboard-api)이 사람 토큰에서 채운 이름이다. 화면 본문의 값은 쓰지 않는다.
    """

    inquiry_id: int
    author: str = Field(min_length=1, max_length=50)


class SetFeedbackStatusInput(AxModel):
    inquiry_id: int
    status: Literal["visible", "hidden"]


class RequestCompensationInput(AxModel):
    """보상 요청. 사유 코드(`late_delivery`, `damaged` 등)로 단가를 코드가 판정한다.

    `buyer_id`는 보상 쿠폰을 받을 구매자다. 문의 원문은 받지 않는다.
    """

    inquiry_id: int
    buyer_id: str
    reason_code: str
    count: int = 1


class RequestCompensationOutput(AxModel):
    """`request_compensation` 결과. 마케팅 회신은 상태·outcome·출력을 그대로 담는다."""

    eligible: bool
    reason: str | None = None  # 대상이 아닐 때 이유
    sequence: int | None = None
    count: int | None = None
    unit_value: int | None = None
    total_value: int | None = None
    marketing_status: str | None = None
    marketing_outcome: str | None = None
    marketing_output: dict[str, Any] = Field(default_factory=dict)
