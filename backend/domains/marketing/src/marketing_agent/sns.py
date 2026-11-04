"""Instagram 게시 초안과 점검 (skill `draft_sns_post`, `check_sns_post`). SNS 설계 S2 단계.

- 둘 다 읽기 전용이다. 상태를 바꾸는 `publish_sns`·`generate_video`는 이 단계에 없다.
- 초안 문안은 고정 템플릿이다. 이미 게시된 공지(`NoticeStore`)의 공개 문구 또는 공개 쿠폰 사실
  (제약 사항 포함)만 쓰고, 원인·영향 숫자는 적지 않는다. 외부 입력(`untrusted`)은 읽지 않는다.
- 점검은 코드 규칙이다. 필수 항목이 하나라도 빠지면 `rejected`이고, 개수는 safety 기준표
  (`ai_label_missing_count`·`text_mismatch_count`·`constraint_missing_count`)의 정의와 같게 센다.
- 필수 문구(AI 라벨·시연용·약관)는 팀·변호사 승인 전의 자리표시 문구다(설계 5절, 열린 결정 4).
"""

from __future__ import annotations

import hashlib
import re
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import Field, StrictBool, ValidationError, model_validator

from ax_contracts.schemas import AgentRequest, AgentResult, Evidence, TaskStatus
from ax_contracts.schemas.common import AxModel
from marketing_agent.notice import NoticeStore

# 필수 문구. 승인 전 자리표시이며 코드 한 곳에서만 바꾼다
AI_LABEL_TEXT = "이 글과 이미지는 AI로 만들었습니다."
DEMO_TEXT = "캣북스는 시연용 가상 서비스이며 실제 거래·배송이 없습니다."
TERMS_TEXT = "주문·결제·환불 등은 캣북스 이용약관·구매자 이용약관에 따릅니다."
HASHTAGS = ("#캣북스",)
# 참여 조건부 게시 금지(체크리스트 1절): 조건을 거는 말이 캡션에 있으면 막는다
FORBIDDEN_PHRASES = ("좋아요", "팔로우", "공유", "댓글")
CAPTION_MAX = 2200  # Instagram 캡션 길이 상한 [확인 필요: 공식 문서]

ConstraintCode = Literal[
    "use_period",
    "target",
    "min_order_and_cap",
    "per_person_limit",
    "ends_on_exhaustion",
    "stacking",
    "refund_handling",
]
# 쿠폰 게시물 필수 제약 사항 (instagram-policy-checklist 2절. 제안이라 이 상수 한 곳에서 관리)
CONSTRAINT_LABELS: dict[str, str] = {
    "use_period": "사용 기간",
    "target": "대상",
    "min_order_and_cap": "최소 주문 금액과 할인 상한",
    "per_person_limit": "1인당 사용 횟수",
    "ends_on_exhaustion": "예산·수량 소진 시 종료",
    "stacking": "다른 쿠폰과 중복 사용 여부",
    "refund_handling": "환불 시 쿠폰 처리",
}


class CouponConstraint(AxModel):
    code: ConstraintCode
    text: str = Field(min_length=1)


class SnsCouponFacts(AxModel):
    """공개 쿠폰 사실. 참여 조건이 없는 공개 쿠폰만 받는다."""

    campaign_id: str = Field(min_length=1)
    unit_value: int = Field(gt=0)  # 정수 원
    constraints: list[CouponConstraint] = Field(default_factory=list)


class DraftSnsPostInput(AxModel):
    """`notice_id`(게시된 공지) 또는 `coupon`(공개 쿠폰 사실) 중 정확히 하나."""

    notice_id: str | None = None
    coupon: SnsCouponFacts | None = None
    platform: Literal["instagram"] = "instagram"
    media_kind: Literal["image"] = "image"  # 영상(reel)은 S3 이후

    @model_validator(mode="after")
    def _exactly_one_source(self) -> DraftSnsPostInput:
        if (self.notice_id is None) == (self.coupon is None):
            raise ValueError("notice_id와 coupon 중 하나만 준다")
        return self


class SnsPostDraft(AxModel):
    post_draft_id: str
    source_kind: Literal["notice", "coupon"]
    source_ref: str  # notice_id 또는 campaign_id
    platform: Literal["instagram"]
    media_kind: Literal["image"]
    caption: str
    hashtags: list[str]
    caption_sha256: str
    required_text: list[str]  # 캡션에 그대로 있어야 하는 문구(공지 문구·시연용·약관)
    constraints: list[CouponConstraint] = Field(default_factory=list)  # 쿠폰이면 7개 전부
    is_ai_generated: Literal[True] = True
    context_id: str


class CheckSnsPostInput(AxModel):
    post_draft_id: str
    caption: str  # 게시하려는 캡션
    is_ai_generated: StrictBool | None = None  # 게시 API로 보낼 값. 없거나 false면 라벨 누락


class SnsPostCheck(AxModel):
    """점검 결과. 앞의 세 개수는 safety `publish_sns` 기준표 필드와 뜻이 같다(0이 정상)."""

    post_draft_id: str
    verdict: Literal["approved", "rejected"]
    ai_label_missing_count: int
    text_mismatch_count: int
    constraint_missing_count: int
    forbidden_phrase_count: int
    unverified_number_count: int
    caption_too_long: bool
    caption_sha256: str
    problems: list[str]


def _now() -> datetime:
    return datetime.now(UTC)


def _evidence(queries: list[str], summary: str, ref_ids: list[str] | None = None) -> Evidence:
    return Evidence(
        queries=queries, data_summary=summary, collected_at=_now(), ref_ids=ref_ids or []
    )


def _result(request: AgentRequest, status: TaskStatus, **kwargs: Any) -> AgentResult:
    return AgentResult(meta=request.meta, status=status, **kwargs)


def _sha(text: str) -> str:
    return hashlib.sha256(text.encode()).hexdigest()


class SnsStore:
    """초안을 메모리에 둔다(목 모드). 같은 입력이면 같은 `post_draft_id`다."""

    def __init__(self) -> None:
        self._drafts: dict[str, SnsPostDraft] = {}

    def save(self, draft: SnsPostDraft) -> None:
        self._drafts[draft.post_draft_id] = draft

    def get(self, post_draft_id: str) -> SnsPostDraft | None:
        return self._drafts.get(post_draft_id)


def _compose(body: str) -> str:
    return f"{body}\n\n{AI_LABEL_TEXT}\n{DEMO_TEXT}\n{TERMS_TEXT}\n\n{' '.join(HASHTAGS)}"


def _coupon_body(facts: SnsCouponFacts) -> str:
    lines = [f"캣북스 공개 쿠폰 안내: {facts.unit_value:,}원 쿠폰", "", "제약 사항"]
    lines += [f"- {CONSTRAINT_LABELS[c.code]}: {c.text}" for c in facts.constraints]
    return "\n".join(lines)


async def draft_sns_post(
    request: AgentRequest, notices: NoticeStore, store: SnsStore
) -> AgentResult:
    try:
        data = DraftSnsPostInput.model_validate(request.input)
    except ValidationError as error:
        return _result(
            request,
            TaskStatus.FAILED,
            outcome="invalid_input",
            output={
                "errors": [f"{'.'.join(map(str, e['loc']))}: {e['type']}" for e in error.errors()]
            },
        )

    constraints: list[CouponConstraint] = []
    if data.notice_id is not None:
        view = notices.view(data.notice_id)
        if not view.found or view.public_text is None:
            return _result(request, TaskStatus.FAILED, outcome="notice_not_found")
        if view.status != "published":  # 끝난 공지를 다시 올리지 않는다
            return _result(request, TaskStatus.FAILED, outcome="notice_not_active")
        source_kind: Literal["notice", "coupon"] = "notice"
        source_ref = data.notice_id
        body = view.public_text
        required = [body, DEMO_TEXT, TERMS_TEXT]
    else:
        assert data.coupon is not None
        facts = data.coupon
        have = {c.code for c in facts.constraints}
        missing = [code for code in CONSTRAINT_LABELS if code not in have]
        if missing:  # 제약 사항이 없는 쿠폰 계획은 만들지 않는다(체크리스트 1절)
            return _result(
                request,
                TaskStatus.COMPLETED,
                outcome="rejected_constraint_missing",
                output={"missing_constraints": missing},
                evidence=_evidence(["draft_sns_post"], "쿠폰 제약 사항 누락", [facts.campaign_id]),
            )
        source_kind = "coupon"
        source_ref = facts.campaign_id
        body = _coupon_body(facts)
        required = [f"{facts.unit_value:,}원 쿠폰", DEMO_TEXT, TERMS_TEXT]
        constraints = facts.constraints

    caption = _compose(body)
    if len(caption) > CAPTION_MAX:
        return _result(request, TaskStatus.FAILED, outcome="caption_too_long")
    digest = _sha(f"{request.meta.context_id}:{source_kind}:{source_ref}:{caption}")[:10]
    draft = SnsPostDraft(
        post_draft_id=f"spd_{digest}",
        source_kind=source_kind,
        source_ref=source_ref,
        platform=data.platform,
        media_kind=data.media_kind,
        caption=caption,
        hashtags=list(HASHTAGS),
        caption_sha256=_sha(caption),
        required_text=required,
        constraints=constraints,
        context_id=request.meta.context_id,
    )
    store.save(draft)
    return _result(
        request,
        TaskStatus.COMPLETED,
        output=draft.model_dump(mode="json"),
        evidence=_evidence(
            ["draft_sns_post"],
            f"{source_kind} 사실 필드로 고정 템플릿 캡션 생성",
            [draft.post_draft_id, source_ref],
        ),
        fallback=True,  # LLM 문안은 연결하지 않았다. 고정 템플릿
    )


async def check_sns_post(request: AgentRequest, store: SnsStore) -> AgentResult:
    try:
        data = CheckSnsPostInput.model_validate(request.input)
    except ValidationError as error:
        return _result(
            request,
            TaskStatus.FAILED,
            outcome="invalid_input",
            output={
                "errors": [f"{'.'.join(map(str, e['loc']))}: {e['type']}" for e in error.errors()]
            },
        )
    draft = store.get(data.post_draft_id)
    if draft is None:
        return _result(request, TaskStatus.FAILED, outcome="draft_not_found")

    caption = data.caption
    problems: list[str] = []

    # AI 라벨: 캡션 안내문, 게시 API 값(이미지는 영상 오버레이가 없다)
    ai_missing = 0
    if AI_LABEL_TEXT not in caption:
        ai_missing += 1
        problems.append("ai_label_caption_missing")
    if data.is_ai_generated is not True:
        ai_missing += 1
        problems.append("is_ai_generated_not_true")

    # 승인된 초안과 다른 항목: 캡션 전체, 그리고 공지 문구·시연용·약관
    mismatch = 0
    if _sha(caption) != draft.caption_sha256:
        mismatch += 1
        problems.append("caption_differs_from_draft")
    for fragment in draft.required_text:
        if fragment not in caption:
            mismatch += 1
            problems.append("required_text_missing")

    # 쿠폰 제약 사항(공지 게시물은 0)
    constraint_missing = sum(1 for c in draft.constraints if c.text not in caption)
    if constraint_missing:
        problems.append("constraint_missing")

    forbidden = sum(1 for phrase in FORBIDDEN_PHRASES if phrase in caption)
    if forbidden:
        problems.append("forbidden_phrase")

    # 공지 게시물: 공지 문구 밖에 숫자가 있으면 출처를 알 수 없는 숫자다(원인·영향 숫자 금지)
    unverified = 0
    if draft.source_kind == "notice":
        rest = caption
        for fragment in [*draft.required_text, AI_LABEL_TEXT, *draft.hashtags]:
            rest = rest.replace(fragment, "")
        unverified = len(re.findall(r"\d+", rest))
        if unverified:
            problems.append("unverified_number")

    too_long = len(caption) > CAPTION_MAX
    if too_long:
        problems.append("caption_too_long")

    approved = not problems
    check = SnsPostCheck(
        post_draft_id=draft.post_draft_id,
        verdict="approved" if approved else "rejected",
        ai_label_missing_count=ai_missing,
        text_mismatch_count=mismatch,
        constraint_missing_count=constraint_missing,
        forbidden_phrase_count=forbidden,
        unverified_number_count=unverified,
        caption_too_long=too_long,
        caption_sha256=_sha(caption),
        problems=problems,
    )
    summary = "필수 항목 모두 확인" if approved else f"필수 항목 누락·위반 {len(problems)}건"
    return _result(
        request,
        TaskStatus.COMPLETED,
        outcome=None if approved else "rejected_by_check",
        output=check.model_dump(mode="json"),
        evidence=_evidence(["check_sns_post"], summary, [draft.post_draft_id]),
    )
