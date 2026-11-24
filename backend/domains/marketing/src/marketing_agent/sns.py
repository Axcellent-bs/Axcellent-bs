"""Instagram 게시 초안과 점검 (skill `draft_sns_post`, `check_sns_post`). SNS 설계 S2 단계.

- 둘 다 읽기 전용이다(초안 저장만 한다). 상태를 바꾸는 `publish_sns`·`generate_media`는
  이 단계에 없다.
- 초안 문안은 고정 템플릿이다(기획안은 필드를 골라 SNS 형식으로 다시 짠다).
  이미 게시된 공지(`NoticeStore`)의 공개 문구 또는 공개 쿠폰 사실
  (제약 사항 포함)만 쓰고, 원인·영향 숫자는 적지 않는다. 외부 입력(`untrusted`)은 읽지 않는다.
- 점검은 코드 규칙이다. 필수 항목이 하나라도 빠지면 `rejected`이고, 개수는 safety 기준표
  (`ai_label_missing_count`·`text_mismatch_count`·`constraint_missing_count`)의 정의와 같게 센다.
- 초안 id는 내용 주소다: 캡션·자산 sha256·media_kind·플랫폼 옵션의 해시. 같은 내용이면 같은 id다.
  초안은 SQLite(`MARKETING_SNS_DRAFT_DB`, 비우면 메모리)에 둔다.
- 미디어(JPEG 이미지·릴스)는 `MARKETING_MEDIA_DIR` 안의 자산 경로로 받고
  규격은 `sns_media`가 검사한다.
  AI 라벨 오버레이 결과(`sns_label`의 `labeled.*`·`meta.json`)가 맞는지도 점검한다.
- 주제 문장 초안(`topic`, 근거 자료 없음): 문장은 데이터로만 쓴다. 이 경로에는 LLM이 없고
  (결정적 템플릿) 문장 속 지시를 해석·실행하지 않는다. 확인되지 않은 사실을 지어내지 않으려고
  숫자·링크·태그·멘션이 있는 문장은 거절하고,
  가격·할인·재고·출시 같은 사실성 낱말은 `needs_confirmation`에 남긴다.
- 필수 문구(AI 라벨·시연용·약관)는 팀·변호사 승인 전의 자리표시 문구다(설계 5절, 열린 결정 4).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import threading
import unicodedata
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, StrictBool, ValidationError, field_validator, model_validator

from ax_contracts.schemas import AgentRequest, AgentResult, Evidence, TaskStatus
from ax_contracts.schemas.common import AxModel, UtcDatetime
from marketing_agent import company_scope, sns_hashtags, sns_media
from marketing_agent.briefs import BriefStorageError, BriefStore
from marketing_agent.notice import NoticeStore
from marketing_agent.schemas import NoticeKind

DRAFT_DB_ENV = "MARKETING_SNS_DRAFT_DB"

# 필수 문구. 승인 전 자리표시이며 코드 한 곳에서만 바꾼다
AI_LABEL_TEXT = "이 글과 이미지는 AI로 만들었습니다."
AI_LABEL_TEXT_REEL = "이 글과 영상은 AI로 만들었습니다."
# 시연 안내·약관 안내·브랜드 태그는 요청 회사 설정에서 읽는다(11/24, 캣북스 고정을 없앴다):
# 시연 안내 = tenant.toml `[brand] demo_notice`(없으면 넣지 않음), 약관 = 회사 이름으로,
# 브랜드 태그 = `[brand] hashtags`(없으면 "#" + 회사 이름). `demo_text()`·`terms_text()`·
# `brand_hashtags()`
DEMO_NOTICE_MAX = 200
TERMS_TEMPLATE = "주문·결제·환불 등은 {name} 이용약관·구매자 이용약관에 따릅니다."


def demo_text() -> str | None:
    """요청 회사의 시연 안내 한 줄 (`[brand] demo_notice`). 없거나 쓸 수 없는 값이면 None."""
    from ax_agent_template import tenant

    raw = tenant.brand_section().get("demo_notice")
    if not isinstance(raw, str):
        return None
    text = " ".join(unicodedata.normalize("NFKC", raw).split())
    return text if text and len(text) <= DEMO_NOTICE_MAX else None


def terms_text() -> str:
    """요청 회사 이름으로 쓴 약관 안내 한 줄."""
    return TERMS_TEMPLATE.format(name=company_scope.company_name())


def notice_texts() -> list[str]:
    """캡션에 늘 넣는 안내(시연 안내가 있으면 그것, 그리고 약관). 순서 = 캡션 순서."""
    demo = demo_text()
    return [*([demo] if demo else []), terms_text()]


def brand_hashtags() -> tuple[str, ...]:
    """요청 회사의 브랜드 태그 (`sns_hashtags.brand_tags`, 검사는 `tag_check`)."""
    return sns_hashtags.brand_tags(tag_check)


# 참여 조건부 게시 금지(체크리스트 1절): 조건을 거는 말이 캡션에 있으면 막는다
FORBIDDEN_PHRASES = ("좋아요", "팔로우", "공유", "댓글")
# 팀 마케팅 규칙: 성과 과장·인력 대체·시연 숫자를 실제 성과처럼 쓰는 표현을 캡션에서 막는다.
# 공백을 뺀 글에서 찾는다(띄어쓰기로 피하지 못하게)
FORBIDDEN_CLAIM_PATTERNS = (
    r"인건비(를|가|은|는)?절감",
    r"직원(을|이|은|는)?(모두|전부)?대체",
    r"에이전트(가|는|들이)?(더|훨씬)?빨라",
    r"최고",
    r"무조건",
    r"획기적",
    r"압도적",
    r"혁신적",
)
CAPTION_MAX = sns_media.CAPTION_MAX  # Instagram 캡션 길이 상한 [확인 필요: 공식 문서]
HASHTAG_MAX = sns_media.HASHTAG_MAX
MENTION_MAX = sns_media.MENTION_MAX


def tag_check(tag: str) -> str | None:
    """해시태그 설정 검증: 문자(공백·특수문자 없음)·금칙어·성과 과장 표현. 쓸 수 있으면 None."""
    if not sns_hashtags.TAG_PATTERN.fullmatch(tag):
        return "invalid_chars"
    if any(phrase in tag for phrase in FORBIDDEN_PHRASES):
        return "forbidden_phrase"
    if has_forbidden_claim(tag):
        return "forbidden_claim"
    return None


def ai_label_text(media_kind: str) -> str:
    return AI_LABEL_TEXT_REEL if media_kind == "reel" else AI_LABEL_TEXT


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


class PlatformOptions(AxModel):
    """Instagram 옵션. 릴스의 `share_to_feed`는 기본값이 문서에 없어 항상 명시해 보낸다."""

    share_to_feed: bool | None = None  # 릴스 전용. 비우면 릴스는 True


TOPIC_MAX = 100  # 주제 문장 길이 상한(정리한 뒤 글자 수)
TOPIC_BASIS = "topic_only"
TOPIC_TEMPLATE = "topic_caption_v1"
# 근거 없이 문장만으로 단정하면 안 되는 사실성 낱말. 있으면 승인자가 확인할 항목으로 남긴다
TOPIC_FACT_WORDS: dict[str, str] = {
    "가격": "price",
    "할인": "discount",
    "특가": "discount",
    "무료": "free",
    "재고": "stock",
    "품절": "stock",
    "한정": "limited",
    "출시": "release_date",
    "입고": "release_date",
    "발매": "release_date",
    "배송": "delivery",
    "쿠폰": "coupon",
}
_TOPIC_LINK = re.compile(r"https?:|www\.|[#@]", re.IGNORECASE)


def clean_topic(value: str) -> str:
    """주제 문장 정리: NFC, 공백(줄바꿈·탭 포함) 연속은 한 칸, 앞뒤 공백 제거.

    그 밖의 제어·서식 문자(널·ESC·제로폭·방향 제어 등)가 있으면 ValueError다(지우지 않는다).
    """
    text = unicodedata.normalize("NFC", value)
    for ch in text:
        if not ch.isspace() and unicodedata.category(ch) in ("Cc", "Cf", "Cs", "Co", "Cn"):
            raise ValueError("topic_control_char")
    text = re.sub(r"\s+", " ", text).strip()
    if not text:
        raise ValueError("topic_empty")
    if len(text) > TOPIC_MAX:
        raise ValueError("topic_too_long")
    return text


class DraftSnsPostInput(AxModel):
    """`notice_id`(게시된 공지), `coupon`(공개 쿠폰 사실), `proposal_id`(보관한 기획안) 중 하나,
    또는 근거 없이 `topic`(주제 문장)만.

    기획안은 호출자가 글을 넘기지 않고 번호만 준다. 글은 마케팅이 보관한 기획안에서 읽는다.
    `topic`은 선택 입력이다(최대 `TOPIC_MAX`자, 기본 한국어). 근거 3개 중 하나와 같이 오면
    근거가 우선이고 `topic`은 검증만 하고 캡션에 쓰지 않는다(`topic_ignored`). 근거가 없으면
    주제 문장 초안(`basis="topic_only"`)이다. 문장은 데이터로만 쓰고 안의 지시는 따르지 않는다.
    """

    notice_id: str | None = None
    coupon: SnsCouponFacts | None = None
    proposal_id: str | None = None
    topic: str | None = None
    platform: Literal["instagram"] = "instagram"
    media_kind: Literal["image", "reel", "carousel"] = "image"
    # `MARKETING_MEDIA_DIR` 안의 라벨 입힌 결과 파일(예: .../labeled.jpg).
    # 없으면 캡션만 있는 초안이다
    asset_path: str | None = None
    # 캐러셀(`media_kind="carousel"`): 라벨 입힌 이미지 2~10장. 순서가 게시 순서다
    asset_paths: list[str] | None = None
    platform_options: PlatformOptions = Field(default_factory=PlatformOptions)

    @field_validator("topic", mode="before")
    @classmethod
    def _clean_topic(cls, value: Any) -> Any:
        if value is None:
            return None
        if not isinstance(value, str):
            raise ValueError("topic_not_string")
        return clean_topic(value)

    @model_validator(mode="after")
    def _exactly_one_source(self) -> DraftSnsPostInput:
        given = [x for x in (self.notice_id, self.coupon, self.proposal_id) if x is not None]
        if len(given) > 1 or (not given and self.topic is None):
            raise ValueError("notice_id, coupon, proposal_id 중 하나만 주거나 topic만 준다")
        if self.media_kind == "carousel":
            paths = self.asset_paths or []
            if self.asset_path is not None or not (
                sns_media.CAROUSEL_MIN <= len(paths) <= sns_media.CAROUSEL_MAX
            ):
                raise ValueError("carousel_item_count_out_of_range")
            if len(set(paths)) != len(paths):
                raise ValueError("carousel_duplicate_asset")
        elif self.asset_paths is not None:
            raise ValueError("asset_paths는 carousel만 쓴다")
        return self


class CarouselAsset(AxModel):
    asset_ref: str  # 미디어 폴더 기준 상대 경로
    asset_sha256: str


class SnsPostDraft(AxModel):
    post_draft_id: str
    source_kind: Literal["notice", "coupon", "brief", "outage", "topic"]
    source_ref: str  # notice_id, campaign_id, proposal_id, 장애 이벤트, 주제 문장 해시
    notice_kind: NoticeKind | None = None  # 공지 출처의 종류. 없으면(쿠폰·옛 초안) 상품 홍보로 본다
    platform: Literal["instagram"]
    media_kind: Literal["image", "reel", "carousel"]
    caption: str
    hashtags: list[str]
    caption_sha256: str
    required_text: list[str]  # 캡션에 그대로 있어야 하는 문구(공지 문구·시연용·약관)
    constraints: list[CouponConstraint] = Field(default_factory=list)  # 쿠폰이면 7개 전부
    is_ai_generated: Literal[True] = True
    context_id: str
    asset_ref: str | None = None  # 미디어 폴더 기준 상대 경로
    asset_sha256: str | None = None  # 캐러셀이면 전체 해시(`carousel_sha256`)
    assets: list[CarouselAsset] = Field(default_factory=list)  # 캐러셀의 각 장(게시 순서)
    platform_options: dict[str, Any] = Field(default_factory=dict)
    # 장애 사과·보상 초안(`source_kind="outage"`)이 만들어진 계기: apology_notice | compensation
    outage_trigger: str | None = None
    # 유사 초안 병합(장애 초안만). 캡션은 건드리지 않고 이 메타데이터만 바꾼다.
    # 옛 레코드는 기본값으로 읽는다
    status: Literal["draft", "merged"] = "draft"
    merged_into: str | None = None  # status="merged"인 원본이 가리키는 대표 초안 id
    merged_from: list[str] = Field(default_factory=list)  # 대표 초안이 흡수한 원본 id들
    merged_at: UtcDatetime | None = None
    # 장애 초안에 이미지를 붙여 만든 사본(`prepare_outage_sns_media`). 캡션은 원본 그대로이고 이
    # 초안이
    # 게시 대상이다. 원본(캡션만 있는 초안)의 id를 가리킨다. 옛 레코드는 기본값(None)으로 읽는다
    media_from: str | None = None
    # 사본에 미디어를 붙인 뒤 돌린 `check_sns_post` 결과(verdict·publishable·problems·checked_at)
    media_check: dict[str, Any] | None = None
    # 기획안으로 재구성한 캡션의 출처 메타(승인자용): 기획안 번호,
    # 쓴 필드 이름·글자 수·해시 앞 12자.
    # 값 전문은 넣지 않는다. 옛 레코드·공지·쿠폰 초안은 None이다
    caption_source: dict[str, Any] | None = None
    # 근거 자료 없이 주제 문장만으로 만든 초안이면 "topic_only"(승인자는 사실을 직접 확인한다).
    # 그 밖의 초안은 None이다
    basis: Literal["topic_only"] | None = None
    # 승인자가 게시 전에 확인할 항목 코드(basis_unverified·price·discount·stock·release_date 등)
    needs_confirmation: list[str] = Field(default_factory=list)
    # 근거와 topic이 함께 와서 topic을 캡션에 쓰지 않았다
    topic_ignored: bool = False
    # 의뢰 영상 초안(`request_video`): 생성 직후 라벨을 입혀 연결한 결과 `{"prepared": {...}}`.
    # 옛 레코드는 기본값(None)으로 읽는다
    media_flow: dict[str, Any] | None = None
    # 의뢰 영상 초안의 기획 장면 시작 시각(초)과 기획 길이
    # (`prepare_video_draft_from_plan`이 적는다). 썸네일 후보 시점 계산용.
    # 옛 레코드·장면 없는 초안은 비어 있다
    scene_starts_s: list[float] = Field(default_factory=list)
    plan_duration_s: float | None = None
    # 의뢰 영상 썸네일 후보(`request_video_cover`): [{media_id, at_s, asset_ref, sha256, ...}]
    # 라벨 입힌 영상에서 뽑은 그림이고 AI 표시(XMP)가 들어 있다. 옛 레코드는 빈 목록
    cover_candidates: list[dict[str, Any]] = Field(default_factory=list)
    cover_media_id: str | None = None  # 사람이 고른 후보. 없으면 첫 후보가 기본값
    cover_rev: int = 0  # 사람이 고를 때마다 1씩. 승인 해시에 들어가 바꾼 뒤 옛 승인이 안 맞는다


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
    hashtag_count: int = 0
    mention_count: int = 0
    # 미디어 점검. 캡션만 있는 초안은 media_checked=False이고 publishable=False다(게시할 수 없다)
    media_checked: bool = False
    media_check_unavailable: bool = False  # 검사 도구(ffprobe)가 없어 검사하지 못함
    machine_mark_present: bool | None = None  # 결과 파일에 ai_generated 표시가 있는지
    publishable: bool = False
    media_details: dict[str, Any] = Field(default_factory=dict)
    warnings: list[str] = Field(default_factory=list)
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


_SCHEMA = """
CREATE TABLE IF NOT EXISTS sns_drafts (
    post_draft_id TEXT PRIMARY KEY,
    created_at    TEXT NOT NULL,
    payload       TEXT NOT NULL,
    tenant_id     TEXT NOT NULL DEFAULT 'catbooks'
);
"""


class SnsStore:
    """초안 저장소. 경로가 없으면 `MARKETING_SNS_DRAFT_DB`, 그것도 없으면 메모리(`:memory:`).

    id가 내용 주소라 같은 초안을 다시 저장해도 같은 줄이다.
    """

    def __init__(self, path: str | None = None, company_id: str | None = None) -> None:
        self.path = (
            path if path is not None else (os.getenv(DRAFT_DB_ENV, "").strip() or ":memory:")
        )
        self.company_id = company_scope.scope_id(company_id)  # 회사 칸(여러 회사 1-6)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False, timeout=5.0)
        with self._lock, self._conn:
            self._conn.execute(_SCHEMA)
            company_scope.ensure_tenant_column(self._conn, "sns_drafts")  # 옛 줄은 캣북스

    def close(self) -> None:
        self._conn.close()

    def save(self, draft: SnsPostDraft) -> None:
        with self._lock, self._conn:
            if draft.status == "draft" and not draft.merged_from:
                # 같은 내용의 초안을 다시 저장해도 병합 기록(흡수한 원본·병합 표시)은 지우지 않는다
                row = self._conn.execute(
                    "SELECT payload FROM sns_drafts WHERE post_draft_id = ? AND tenant_id = ?",
                    (draft.post_draft_id, self.company_id),
                ).fetchone()
                if row is not None:
                    try:
                        old = SnsPostDraft.model_validate_json(row[0])
                    except ValidationError:
                        old = None
                    if old is not None and (old.status == "merged" or old.merged_from):
                        draft = draft.model_copy(
                            update={
                                "status": old.status,
                                "merged_into": old.merged_into,
                                "merged_from": old.merged_from,
                                "merged_at": old.merged_at,
                            }
                        )
            self._conn.execute(
                "INSERT OR REPLACE INTO sns_drafts (post_draft_id, created_at, payload, tenant_id)"
                " VALUES (?, ?, ?, ?)",
                (
                    draft.post_draft_id,
                    _now().isoformat(),
                    draft.model_dump_json(),
                    self.company_id,
                ),
            )

    def update_meta(self, drafts: list[SnsPostDraft]) -> None:
        """이미 있는 초안의 저장 내용만 바꾼다(저장 시각은 그대로). 모두 쓰거나 하나도 안 쓴다."""
        with self._lock, self._conn:
            for draft in drafts:
                cursor = self._conn.execute(
                    "UPDATE sns_drafts SET payload = ? WHERE post_draft_id = ? AND tenant_id = ?",
                    (draft.model_dump_json(), draft.post_draft_id, self.company_id),
                )
                if cursor.rowcount != 1:
                    raise KeyError(draft.post_draft_id)  # with 블록이 되돌린다

    def get(self, post_draft_id: str) -> SnsPostDraft | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT payload FROM sns_drafts WHERE post_draft_id = ? AND tenant_id = ?",
                (post_draft_id, self.company_id),
            ).fetchone()
        if row is None:
            return None
        try:
            return SnsPostDraft.model_validate_json(row[0])
        except ValidationError:  # 계약이 바뀌어 읽을 수 없는 줄
            return None

    def list_by_source(
        self, source_kind: str, limit: int | None, include_merged: bool = True
    ) -> tuple[list[tuple[SnsPostDraft, datetime]], int]:
        """출처 종류가 같은 초안을 저장 시각이 늦은 순으로. (목록, 전체 수). 못 읽는 줄은 뺀다.

        `include_merged=False`면 병합된 원본(`status="merged"`)을 목록과 전체 수에서 뺀다.
        `limit=None`이면 자르지 않는다.
        """
        rows = self._rows_by_source(source_kind, include_merged)
        items: list[tuple[SnsPostDraft, datetime]] = []
        for payload, created_at in rows if limit is None else rows[:limit]:
            try:
                items.append(
                    (SnsPostDraft.model_validate_json(payload), datetime.fromisoformat(created_at))
                )
            except ValueError:
                continue
        return items, len(rows)

    def find_derived(self, post_draft_id: str) -> SnsPostDraft | None:
        """원본 초안(`media_from`이 가리키는 id)에서 이미지를 붙여 만든 사본. 없으면 None.

        사본은 원본당 하나다(장애 초안 경로의 장면·필드가 코드 고정이라 같은 원본은 같은 사본이
        된다).
        """
        with self._lock:
            rows = self._conn.execute(
                "SELECT payload FROM sns_drafts WHERE tenant_id = ?"
                " AND json_extract(payload, '$.media_from') = ?"
                " ORDER BY created_at DESC, post_draft_id",
                (self.company_id, post_draft_id),
            ).fetchall()
        for (payload,) in rows:
            try:
                return SnsPostDraft.model_validate_json(payload)
            except ValidationError:
                continue
        return None

    def count_merged_by_source(self, source_kind: str) -> int:
        return len(self._rows_by_source(source_kind, True)) - len(
            self._rows_by_source(source_kind, False)
        )

    def _rows_by_source(self, source_kind: str, include_merged: bool) -> list[tuple[str, str]]:
        where = "json_extract(payload, '$.source_kind') = ?"
        if not include_merged:
            where += " AND COALESCE(json_extract(payload, '$.status'), 'draft') != 'merged'"
        with self._lock:
            return self._conn.execute(
                f"SELECT payload, created_at FROM sns_drafts WHERE tenant_id = ? AND {where}"  # noqa: S608
                " ORDER BY created_at DESC, post_draft_id",
                (self.company_id, source_kind),
            ).fetchall()


def content_id(
    caption_sha256: str,
    asset_sha256: str | None,
    media_kind: str,
    platform: str,
    options: dict[str, Any],
) -> str:
    """내용 주소 id. 캡션·자산·media_kind·플랫폼 옵션이 같으면 같고 하나라도 다르면 다르다."""
    canonical = json.dumps(
        {
            "caption_sha256": caption_sha256,
            "asset_sha256": asset_sha256,
            "media_kind": media_kind,
            "platform": platform,
            "options": options,
        },
        sort_keys=True,
        separators=(",", ":"),
    )
    return f"spd_{_sha(canonical)[:16]}"


def _compose(body: str, media_kind: str, hashtags: tuple[str, ...] | None = None) -> str:
    tags = " ".join(brand_hashtags() if hashtags is None else hashtags)
    notices = "\n".join([ai_label_text(media_kind), *notice_texts()])
    return f"{body}\n\n{notices}" + (f"\n\n{tags}" if tags else "")


PRODUCT_NOTICE_KINDS: frozenset[str] = frozenset({"product_created"})


def is_product_promotion(draft: SnsPostDraft) -> bool:
    """상품 홍보 초안인가. 쿠폰 사실, 공지 종류가 `product_created`, 종류를 모르는 옛 공지 초안이다.

    장애 안내(`incident_opened`)·정상화 안내(`incident_resolved`)는 상품 홍보가 아니다.
    """
    if draft.source_kind == "coupon":
        return True
    if draft.source_kind == "outage":  # 장애 사과·보상 안내는 상품 홍보가 아니다
        return False
    return draft.notice_kind is None or draft.notice_kind in PRODUCT_NOTICE_KINDS


def _coupon_body(facts: SnsCouponFacts) -> str:
    name = company_scope.company_name()
    lines = [f"{name} 공개 쿠폰 안내: {facts.unit_value:,}원 쿠폰", "", "제약 사항"]
    lines += [f"- {CONSTRAINT_LABELS[c.code]}: {c.text}" for c in facts.constraints]
    return "\n".join(lines)


@dataclass(frozen=True)
class SourceParts:
    """초안 본문의 출처(공지 또는 쿠폰 사실)에서 뽑은 값."""

    kind: Literal["notice", "coupon", "brief", "topic"]
    ref: str
    body: str
    required: list[str]
    constraints: list[CouponConstraint]
    notice_kind: NoticeKind | None = None  # 공지 출처일 때 그 공지의 종류
    caption_source: dict[str, Any] | None = None  # 기획안 출처 메타(`draft.caption_source`)
    # 요청 회사의 브랜드 태그. 기획안이면 주제 태그가 더해진다(`sns_hashtags`)
    hashtags: tuple[str, ...] = dataclass_field(default_factory=brand_hashtags)
    needs_confirmation: tuple[str, ...] = ()  # 주제 문장 초안의 확인 필요 항목


def has_forbidden_claim(text: str) -> bool:
    squeezed = re.sub(r"\s+", "", text)
    return any(re.search(pattern, squeezed) for pattern in FORBIDDEN_CLAIM_PATTERNS)


# 참여 조건부 게시(좋아요·공유·댓글·팔로우를 하면 혜택)를 막는다. 참여 단어가 들어 있기만 하면
# 막던 규칙은 "공유할 책 일상 밈" 같은 설명 문장도 막아서,
# 단어 뒤 12자 안에 요구·조건 표현이 있을 때만 본다.
# 혜택 없는 요청("댓글로 알려 주세요")도 지금처럼 막는다("주세요").
ENGAGEMENT_WORDS = (*FORBIDDEN_PHRASES, "리그램", "태그")
ENGAGEMENT_DEMANDS = (
    "누르",
    "눌러",
    "하면",
    "해주",
    "해 주",
    "달아",
    "남겨",
    "부탁",
    "해야",
    "응모",
    "이벤트",
    "추첨",
    "증정",
    "선물",
    "당첨",
    "주세요",
)
ENGAGEMENT_WINDOW = 12
_ENGAGEMENT_WORD = re.compile("|".join(map(re.escape, ENGAGEMENT_WORDS)))
_ENGAGEMENT_DEMAND = re.compile("|".join(re.escape(d.replace(" ", "")) for d in ENGAGEMENT_DEMANDS))


def engagement_bait_count(text: str) -> int:
    """참여 단어 뒤 12자 안에 요구·조건 표현이 붙은 곳의 수.

    공백을 뺀 글에서 센다(띄어쓰기로 피하지 못하게)."""
    squeezed = re.sub(r"\s+", "", text)
    return sum(
        1
        for word in _ENGAGEMENT_WORD.finditer(squeezed)
        if _ENGAGEMENT_DEMAND.search(squeezed[word.end() : word.end() + ENGAGEMENT_WINDOW])
    )


def has_engagement_bait(text: str) -> bool:
    """참여 조건부 게시 표현이 있으면 True.

    해시태그 검사(tag_check)는 단어 포함 규칙을 그대로 쓴다."""
    return engagement_bait_count(text) > 0


BRIEF_HOOK_MAX = 50
BRIEF_LINE_MAX = 120
BRIEF_FACTS_MAX = 2
BRIEF_TEMPLATE = "brief_caption_v1"
_SENTENCE_END = re.compile(r"(?<=[.!?。])\s+|\n+")


def _first_sentence(text: str, limit: int) -> str:
    """첫 문장만, `limit`자를 넘으면 공백 경계에서 자르고 말줄임표를 붙인다."""
    flat = text.strip()
    if not flat:
        return ""
    first = _SENTENCE_END.split(flat, maxsplit=1)[0].strip()
    if len(first) <= limit:
        return first
    cut = first[: limit - 1]
    if " " in cut[limit // 2 :]:
        cut = cut.rsplit(" ", 1)[0]
    return cut.rstrip(" ,.") + "…"


def _field_meta(name: str, value: str) -> dict[str, Any]:
    return {"name": name, "chars": len(value), "sha12": _sha(value)[:12]}


def _brief_body_and_source(stored: Any) -> tuple[str, dict[str, Any]]:
    """기획안 필드에서 SNS 형식 본문을 다시 짠다(결정적 템플릿, LLM 없음).

    첫 줄 훅(title) → 짧은 본문(idea 첫 문장) → 이유 한 줄(why 첫 문장) → 근거 한 줄(facts 앞 2개).
    근거 값은 기획안의 값을 글자 그대로 쓰고 숫자를 만들지 않는다. target(목표치)은 성과가 아니라
    쓰지 않는다. 마지막 줄에 출처 표기를 붙인다. 비어 있는 필드는 건너뛴다.
    """
    b = stored.brief
    used: list[dict[str, Any]] = []
    blocks: list[str] = []
    hook = _first_sentence(b.title, BRIEF_HOOK_MAX)
    if hook:
        blocks.append(hook)
        used.append(_field_meta("title", b.title))
    idea = _first_sentence(b.idea, BRIEF_LINE_MAX)
    if idea:
        blocks.append(idea)
        used.append(_field_meta("idea", b.idea))
    why = _first_sentence(b.why, BRIEF_LINE_MAX)
    if why:
        blocks.append(f"왜 지금? {why}")
        used.append(_field_meta("why", b.why))
    facts = [f for f in b.facts if f.label.strip() and f.value.strip()][:BRIEF_FACTS_MAX]
    if facts:
        joined = " / ".join(f"{f.label.strip()} {f.value.strip()}" for f in facts)
        blocks.append(f"근거(시연 환경 기준): {joined}")
        used.append(_field_meta("facts", joined))
    blocks.append("기획팀 제안 기반")
    meta = {
        "kind": "brief",
        "proposal_id": stored.proposal_id,
        "template": BRIEF_TEMPLATE,
        "reconstructed": True,
        "fields": used,
    }
    return "\n\n".join(blocks), meta


def _brief_all_text(stored: Any) -> str:
    b = stored.brief
    parts = [b.title, b.idea, b.why]
    parts += [f"{f.label} {f.value}" for f in b.facts]
    return "\n".join(parts)


def _topic_parts(request: AgentRequest, data: DraftSnsPostInput) -> SourceParts | AgentResult:
    """근거 자료 없이 주제 문장만으로 본문 재료를 만든다(결정적 템플릿, LLM 없음).

    문장은 글자 그대로 본문이 되고 해석하지 않는다. 숫자·링크·태그·멘션은 거절하고(출처 없는 사실을
    만들지 않는다), 금칙어·성과 과장 표현도 다른 출처와 같은 규칙으로 막는다.
    """
    assert data.topic is not None
    topic = data.topic
    if _TOPIC_LINK.search(topic):
        return _result(request, TaskStatus.FAILED, outcome="topic_link_or_tag")
    if re.search(r"\d", topic):
        return _result(request, TaskStatus.FAILED, outcome="topic_unverified_number")
    if has_engagement_bait(topic):
        return _result(request, TaskStatus.FAILED, outcome="forbidden_phrase")
    if has_forbidden_claim(topic):
        return _result(request, TaskStatus.FAILED, outcome="forbidden_claim")
    confirm = sorted({code for word, code in TOPIC_FACT_WORDS.items() if word in topic})
    meta = {
        "kind": "topic",
        "template": TOPIC_TEMPLATE,
        "basis": TOPIC_BASIS,
        "topic": _field_meta("topic", topic),  # 값 전문은 넣지 않는다
    }
    return SourceParts(
        "topic",
        f"topic_{_sha(topic)[:12]}",
        topic,
        [topic, *notice_texts()],
        [],
        caption_source=meta,
        needs_confirmation=("basis_unverified", *confirm),
    )


def source_parts(
    request: AgentRequest,
    data: DraftSnsPostInput,
    notices: NoticeStore,
    briefs: BriefStore | None = None,
) -> SourceParts | AgentResult:
    """출처를 확인해 본문 재료를 돌려준다. 쓸 수 없는 출처면 결과 Task를 돌려준다(저장하지 않는다).

    `prepare_sns_post_from_media`가 돈이 드는 미디어 생성 전에 같은 검사를 먼저 하려고 쓴다.
    """
    if data.notice_id is not None:
        view = notices.view(data.notice_id)
        if not view.found or view.public_text is None:
            return _result(request, TaskStatus.FAILED, outcome="notice_not_found")
        if view.status != "published":  # 끝난 공지를 다시 올리지 않는다
            return _result(request, TaskStatus.FAILED, outcome="notice_not_active")
        body = view.public_text
        return SourceParts("notice", data.notice_id, body, [body, *notice_texts()], [], view.kind)
    if data.proposal_id is not None:
        if briefs is None:
            return _result(request, TaskStatus.FAILED, outcome="brief_not_found")
        try:
            stored = briefs.existing(data.proposal_id)
        except BriefStorageError:
            return _result(request, TaskStatus.FAILED, outcome="storage_error")
        if stored is None:
            return _result(request, TaskStatus.FAILED, outcome="brief_not_found")
        # 쓰지 않는 문장까지 포함한 기획안 전체를 검사한다(막는 쪽으로 기운다)
        full = _brief_all_text(stored)
        if has_engagement_bait(full):  # 참여 조건부 게시 금지
            return _result(request, TaskStatus.FAILED, outcome="forbidden_phrase")
        if has_forbidden_claim(full):  # 인건비 절감·직원 대체·과장 표현
            return _result(request, TaskStatus.FAILED, outcome="forbidden_claim")
        body, source_meta = _brief_body_and_source(stored)
        tags, tag_meta = sns_hashtags.select_hashtags(
            stored.brief, sns_hashtags.table(tag_check, HASHTAG_MAX), HASHTAG_MAX, brand_hashtags()
        )
        source_meta["hashtags"] = tag_meta  # 어떤 태그를 어떤 필드에서 골랐는지
        return SourceParts(
            "brief",
            data.proposal_id,
            body,
            [body, *notice_texts()],
            [],
            caption_source=source_meta,
            hashtags=tags,
        )
    if data.coupon is None:
        return _topic_parts(request, data)
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
    return SourceParts(
        "coupon",
        facts.campaign_id,
        _coupon_body(facts),
        [f"{facts.unit_value:,}원 쿠폰", *notice_texts()],
        facts.constraints,
    )


def preflight_draft(
    request: AgentRequest,
    data: DraftSnsPostInput,
    notices: NoticeStore,
    briefs: BriefStore | None = None,
) -> AgentResult | None:
    """초안을 만들 수 있는지 미리 본다(출처·캡션 길이). 만들 수 있으면 None."""
    parts = source_parts(request, data, notices, briefs)
    if isinstance(parts, AgentResult):
        return parts
    if len(_compose(parts.body, data.media_kind, parts.hashtags)) > CAPTION_MAX:
        return _result(request, TaskStatus.FAILED, outcome="caption_too_long")
    return None


async def draft_sns_post(
    request: AgentRequest,
    notices: NoticeStore,
    store: SnsStore,
    briefs: BriefStore | None = None,
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

    parts = source_parts(request, data, notices, briefs)
    if isinstance(parts, AgentResult):
        return parts
    source_kind, source_ref, body = parts.kind, parts.ref, parts.body
    required, constraints = parts.required, parts.constraints

    caption = _compose(body, data.media_kind, parts.hashtags)
    if len(caption) > CAPTION_MAX:
        return _result(request, TaskStatus.FAILED, outcome="caption_too_long")

    options: dict[str, Any] = {}
    if data.media_kind == "reel":
        share = data.platform_options.share_to_feed
        options["share_to_feed"] = True if share is None else share
    asset_ref: str | None = None
    asset_sha: str | None = None
    carousel_assets: list[CarouselAsset] = []
    if data.media_kind == "carousel":
        draft_sizes: list[tuple[int, int]] = []
        for path in data.asset_paths or []:
            try:
                asset = sns_media.resolve_asset(path)
            except sns_media.MediaPathError as error:
                return _result(request, TaskStatus.FAILED, outcome=error.code)
            if sns_media.is_jpeg(asset) and (size := sns_media.jpeg_size(asset)) is not None:
                draft_sizes.append(size)
            carousel_assets.append(
                CarouselAsset(
                    asset_ref=sns_media.asset_ref_for(asset),
                    asset_sha256=sns_media.sha256_file(asset),
                )
            )
        asset_sha = sns_media.carousel_sha256([a.asset_sha256 for a in carousel_assets])
        if sns_media.carousel_aspect_mismatch(
            draft_sizes
        ):  # 초안 단계에서 거절(점검·게시도 같은 규칙)
            return _result(request, TaskStatus.FAILED, outcome="carousel_aspect_mismatch")
    elif data.asset_path is not None:
        try:
            asset = sns_media.resolve_asset(data.asset_path)
        except sns_media.MediaPathError as error:
            return _result(request, TaskStatus.FAILED, outcome=error.code)
        asset_ref = sns_media.asset_ref_for(asset)
        asset_sha = sns_media.sha256_file(asset)
    caption_sha = _sha(caption)
    draft = SnsPostDraft(
        post_draft_id=content_id(caption_sha, asset_sha, data.media_kind, data.platform, options),
        asset_ref=asset_ref,
        asset_sha256=asset_sha,
        assets=carousel_assets,
        platform_options=options,
        source_kind=source_kind,
        source_ref=source_ref,
        notice_kind=parts.notice_kind,
        platform=data.platform,
        media_kind=data.media_kind,
        caption=caption,
        hashtags=list(parts.hashtags),
        caption_sha256=caption_sha,
        required_text=required,
        constraints=constraints,
        context_id=request.meta.context_id,
        caption_source=parts.caption_source,
        basis=TOPIC_BASIS if parts.kind == "topic" else None,
        needs_confirmation=list(parts.needs_confirmation),
        topic_ignored=data.topic is not None and parts.kind != "topic",
    )
    store.save(draft)
    return _result(
        request,
        TaskStatus.COMPLETED,
        output=draft.model_dump(mode="json"),
        evidence=_evidence(
            ["draft_sns_post"],
            "주제 문장만으로 고정 템플릿 캡션 생성(근거 자료 없음)"
            if source_kind == "topic"
            else f"{source_kind} 사실 필드로 고정 템플릿 캡션 생성",
            [draft.post_draft_id, source_ref],
        ),
        fallback=True,  # LLM 문안은 연결하지 않았다. 고정 템플릿
    )


class _MediaOutcome:
    def __init__(self) -> None:
        self.problems: list[str] = []
        self.warnings: list[str] = []
        self.details: dict[str, Any] = {}
        self.checked = False
        self.unavailable = False
        self.machine_mark: bool | None = None
        self.overlay_missing = 0
        self.asset_changed = 0


def _overlay_recorded(asset: Path, asset_sha: str) -> bool:
    """`sns_label`이 남긴 `meta.json`이 이 파일을 라벨 입힌 결과로 기록했는지."""
    try:
        meta = json.loads((asset.parent / "meta.json").read_text(encoding="utf-8"))
        label = meta["ai_label"]
    except (OSError, ValueError, KeyError, TypeError):
        return False
    return (
        isinstance(label, dict)
        and label.get("label_overlay") is True
        and label.get("labeled_sha256") == asset_sha
        and label.get("labeled_file") == asset.name
    )


def _check_carousel(draft: SnsPostDraft) -> _MediaOutcome:
    """캐러셀의 모든 장을 하나씩 점검한다. 한 장이라도 라벨·규격이 어긋나면 전체가 거절이다."""
    out = _MediaOutcome()
    count = len(draft.assets)
    items: list[dict[str, Any]] = []
    out.details = {"item_count": count, "items": items}
    if not sns_media.CAROUSEL_MIN <= count <= sns_media.CAROUSEL_MAX:
        out.problems.append("carousel_item_count_out_of_range")
        out.overlay_missing = 1
        return out
    if draft.asset_sha256 != sns_media.carousel_sha256([a.asset_sha256 for a in draft.assets]):
        out.problems.append("asset_changed_after_draft")
        out.asset_changed = 1
    checked = True
    marks: list[bool] = []
    sizes: list[tuple[int, int]] = []
    for index, item in enumerate(draft.assets, 1):
        single = draft.model_copy(
            update={
                "media_kind": "image",
                "asset_ref": item.asset_ref,
                "asset_sha256": item.asset_sha256,
                "assets": [],
            }
        )
        part = _check_asset(single)
        out.problems.extend(f"item_{index}:{p}" for p in part.problems)
        out.warnings.extend(f"item_{index}:{w}" for w in part.warnings)
        out.overlay_missing += part.overlay_missing
        out.asset_changed += part.asset_changed
        out.unavailable = out.unavailable or part.unavailable
        checked = checked and part.checked
        marks.append(part.machine_mark is True)
        width, height = part.details.get("width"), part.details.get("height")
        if isinstance(width, int) and isinstance(height, int) and height > 0:
            sizes.append((width, height))
        items.append(
            {"index": index, "asset_ref": item.asset_ref, "media_checked": part.checked}
            | {k: v for k, v in part.details.items() if k in ("width", "height", "size_bytes")}
        )
    if sns_media.carousel_aspect_mismatch(sizes):
        # 모든 장이 첫 장 비율로 잘린다(Meta 공식 문서). 경고 코드는 유지하고 게시 경로는 거절한다
        out.warnings.append("carousel_aspect_differs")
        out.problems.append("carousel_aspect_mismatch")
    out.checked = checked
    out.machine_mark = all(marks)
    return out


def _check_asset(draft: SnsPostDraft) -> _MediaOutcome:
    if draft.media_kind == "carousel":
        return _check_carousel(draft)
    out = _MediaOutcome()
    if draft.asset_ref is None or draft.asset_sha256 is None:
        out.warnings.append("media_not_attached")  # 캡션만 있는 초안: 게시할 수 없다
        return out
    try:
        asset = sns_media.resolve_asset(draft.asset_ref)
    except sns_media.MediaPathError as error:
        out.problems.append(f"asset_unavailable_{error.code}")
        out.overlay_missing = 1  # 확인할 수 없으니 라벨이 있다고 말하지 않는다
        return out
    asset_sha = sns_media.sha256_file(asset)
    if asset_sha != draft.asset_sha256:
        out.problems.append("asset_changed_after_draft")
        out.asset_changed = 1

    report = sns_media.check_media(asset, draft.media_kind)
    out.details = report.details | {"asset_sha256": asset_sha}
    out.details.pop("tags", None)
    out.warnings.extend(report.warnings)
    out.problems.extend(report.problems)
    out.unavailable = report.unavailable
    out.checked = not report.unavailable and not report.problems

    if not _overlay_recorded(asset, asset_sha):
        out.overlay_missing = 1
        out.problems.append("ai_label_overlay_missing")
    if not report.unavailable:
        mark = sns_media.read_machine_mark(asset, draft.media_kind, report)
        out.machine_mark = mark.get("ai_generated") == "true"
        if not out.machine_mark:
            out.problems.append("machine_mark_missing")
    return out


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

    # AI 라벨 3곳: 캡션 안내문, 미디어 오버레이(아래), 게시 API 값.
    # 프로필 계정 라벨은 사람이 확인한다
    ai_missing = 0
    label_text = ai_label_text(draft.media_kind)
    if label_text not in caption:
        ai_missing += 1
        problems.append("ai_label_caption_missing")
    if data.is_ai_generated is not True:
        ai_missing += 1
        problems.append("is_ai_generated_not_true")

    # 승인된 초안과 다른 항목: 캡션 전체, 그리고 공지 문구·시연용·약관, 자산 파일
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

    forbidden = engagement_bait_count(caption)  # 참여 조건부 게시 표현이 붙은 곳의 수
    if forbidden:
        problems.append("forbidden_phrase")
    if has_forbidden_claim(caption):
        problems.append("forbidden_claim")

    # 상품 홍보(`product_created` 공지)는 공지 문구 밖에 숫자가 있으면 출처를 알 수 없는 숫자다.
    # 장애 안내·정상화 안내 같은 비상품은 이 검사를 하지 않는다. 장애 원인·영향 숫자 금지는
    # 이 검사와 별개 규칙이다: 캡션이 초안과 같아야 하고(caption_differs_from_draft) 초안 문안에
    # 원인·영향을 적지 않는다(notice.py 템플릿)
    unverified = 0
    # 기획안 초안도 같은 규칙이다(본문이 required_text라 받은 그대로의 숫자만 통과한다)
    if draft.source_kind in ("notice", "brief") and is_product_promotion(
        draft
    ):  # 쿠폰은 현행대로 제외
        rest = caption
        for fragment in [*draft.required_text, label_text, *draft.hashtags]:
            rest = rest.replace(fragment, "")
        unverified = len(re.findall(r"\d+", rest))
        if unverified:
            problems.append("unverified_number")

    too_long = len(caption) > CAPTION_MAX
    if too_long:
        problems.append("caption_too_long")
    hashtags = sns_media.count_hashtags(caption)
    mentions = sns_media.count_mentions(caption)
    if hashtags > HASHTAG_MAX:
        problems.append("hashtag_limit_exceeded")
    if mentions > MENTION_MAX:
        problems.append("mention_limit_exceeded")

    media = _check_asset(draft)
    problems.extend(media.problems)
    ai_missing += media.overlay_missing
    mismatch += media.asset_changed

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
        hashtag_count=hashtags,
        mention_count=mentions,
        media_checked=media.checked,
        media_check_unavailable=media.unavailable,
        machine_mark_present=media.machine_mark,
        publishable=approved and media.checked and media.machine_mark is True,
        media_details=media.details,
        warnings=[*media.warnings, *(["topic_only_basis"] if draft.basis == "topic_only" else [])],
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
