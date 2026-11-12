"""Instagram 게시 초안과 점검 (skill `draft_sns_post`, `check_sns_post`). SNS 설계 S2 단계.

- 둘 다 읽기 전용이다(초안 저장만 한다). 상태를 바꾸는 `publish_sns`·`generate_media`는
  이 단계에 없다.
- 초안 문안은 고정 템플릿이다. 이미 게시된 공지(`NoticeStore`)의 공개 문구 또는 공개 쿠폰 사실
  (제약 사항 포함)만 쓰고, 원인·영향 숫자는 적지 않는다. 외부 입력(`untrusted`)은 읽지 않는다.
- 점검은 코드 규칙이다. 필수 항목이 하나라도 빠지면 `rejected`이고, 개수는 safety 기준표
  (`ai_label_missing_count`·`text_mismatch_count`·`constraint_missing_count`)의 정의와 같게 센다.
- 초안 id는 내용 주소다: 캡션·자산 sha256·media_kind·플랫폼 옵션의 해시. 같은 내용이면 같은 id다.
  초안은 SQLite(`MARKETING_SNS_DRAFT_DB`, 비우면 메모리)에 둔다.
- 미디어(JPEG 이미지·릴스)는 `MARKETING_MEDIA_DIR` 안의 자산 경로로 받고
  규격은 `sns_media`가 검사한다.
  AI 라벨 오버레이 결과(`sns_label`의 `labeled.*`·`meta.json`)가 맞는지도 점검한다.
- 필수 문구(AI 라벨·시연용·약관)는 팀·변호사 승인 전의 자리표시 문구다(설계 5절, 열린 결정 4).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sqlite3
import threading
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, StrictBool, ValidationError, model_validator

from ax_contracts.schemas import AgentRequest, AgentResult, Evidence, TaskStatus
from ax_contracts.schemas.common import AxModel
from marketing_agent import sns_media
from marketing_agent.briefs import BriefStorageError, BriefStore
from marketing_agent.notice import NoticeStore
from marketing_agent.schemas import NoticeKind

DRAFT_DB_ENV = "MARKETING_SNS_DRAFT_DB"

# 필수 문구. 승인 전 자리표시이며 코드 한 곳에서만 바꾼다
AI_LABEL_TEXT = "이 글과 이미지는 AI로 만들었습니다."
AI_LABEL_TEXT_REEL = "이 글과 영상은 AI로 만들었습니다."
DEMO_TEXT = "캣북스는 시연용 가상 서비스이며 실제 거래·배송이 없습니다."
TERMS_TEXT = "주문·결제·환불 등은 캣북스 이용약관·구매자 이용약관에 따릅니다."
HASHTAGS = ("#캣북스",)
# 참여 조건부 게시 금지(체크리스트 1절): 조건을 거는 말이 캡션에 있으면 막는다
FORBIDDEN_PHRASES = ("좋아요", "팔로우", "공유", "댓글")
CAPTION_MAX = sns_media.CAPTION_MAX  # Instagram 캡션 길이 상한 [확인 필요: 공식 문서]
HASHTAG_MAX = sns_media.HASHTAG_MAX
MENTION_MAX = sns_media.MENTION_MAX


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


class DraftSnsPostInput(AxModel):
    """`notice_id`(게시된 공지), `coupon`(공개 쿠폰 사실), `proposal_id`(보관한 기획안) 중 하나.


    기획안은 호출자가 글을 넘기지 않고 번호만 준다. 글은 마케팅이 보관한 기획안에서 읽는다.
    """

    notice_id: str | None = None
    coupon: SnsCouponFacts | None = None
    proposal_id: str | None = None
    platform: Literal["instagram"] = "instagram"
    media_kind: Literal["image", "reel", "carousel"] = "image"
    # `MARKETING_MEDIA_DIR` 안의 라벨 입힌 결과 파일(예: .../labeled.jpg).
    # 없으면 캡션만 있는 초안이다
    asset_path: str | None = None
    # 캐러셀(`media_kind="carousel"`): 라벨 입힌 이미지 2~10장. 순서가 게시 순서다
    asset_paths: list[str] | None = None
    platform_options: PlatformOptions = Field(default_factory=PlatformOptions)

    @model_validator(mode="after")
    def _exactly_one_source(self) -> DraftSnsPostInput:
        given = [x for x in (self.notice_id, self.coupon, self.proposal_id) if x is not None]
        if len(given) != 1:
            raise ValueError("notice_id, coupon, proposal_id 중 하나만 준다")
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
    source_kind: Literal["notice", "coupon", "brief"]
    source_ref: str  # notice_id, campaign_id 또는 proposal_id
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
    payload       TEXT NOT NULL
);
"""


class SnsStore:
    """초안 저장소. 경로가 없으면 `MARKETING_SNS_DRAFT_DB`, 그것도 없으면 메모리(`:memory:`).

    id가 내용 주소라 같은 초안을 다시 저장해도 같은 줄이다.
    """

    def __init__(self, path: str | None = None) -> None:
        self.path = (
            path if path is not None else (os.getenv(DRAFT_DB_ENV, "").strip() or ":memory:")
        )
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False, timeout=5.0)
        with self._lock, self._conn:
            self._conn.execute(_SCHEMA)

    def close(self) -> None:
        self._conn.close()

    def save(self, draft: SnsPostDraft) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT OR REPLACE INTO sns_drafts (post_draft_id, created_at, payload)"
                " VALUES (?, ?, ?)",
                (draft.post_draft_id, _now().isoformat(), draft.model_dump_json()),
            )

    def get(self, post_draft_id: str) -> SnsPostDraft | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT payload FROM sns_drafts WHERE post_draft_id = ?", (post_draft_id,)
            ).fetchone()
        if row is None:
            return None
        try:
            return SnsPostDraft.model_validate_json(row[0])
        except ValidationError:  # 계약이 바뀌어 읽을 수 없는 줄
            return None


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


def _compose(body: str, media_kind: str) -> str:
    return (
        f"{body}\n\n{ai_label_text(media_kind)}\n{DEMO_TEXT}\n{TERMS_TEXT}\n\n{' '.join(HASHTAGS)}"
    )


PRODUCT_NOTICE_KINDS: frozenset[str] = frozenset({"product_created"})


def is_product_promotion(draft: SnsPostDraft) -> bool:
    """상품 홍보 초안인가. 쿠폰 사실, 공지 종류가 `product_created`, 종류를 모르는 옛 공지 초안이다.

    장애 안내(`incident_opened`)·정상화 안내(`incident_resolved`)는 상품 홍보가 아니다.
    """
    if draft.source_kind == "coupon":
        return True
    return draft.notice_kind is None or draft.notice_kind in PRODUCT_NOTICE_KINDS


def _coupon_body(facts: SnsCouponFacts) -> str:
    lines = [f"캣북스 공개 쿠폰 안내: {facts.unit_value:,}원 쿠폰", "", "제약 사항"]
    lines += [f"- {CONSTRAINT_LABELS[c.code]}: {c.text}" for c in facts.constraints]
    return "\n".join(lines)


@dataclass(frozen=True)
class SourceParts:
    """초안 본문의 출처(공지 또는 쿠폰 사실)에서 뽑은 값."""

    kind: Literal["notice", "coupon", "brief"]
    ref: str
    body: str
    required: list[str]
    constraints: list[CouponConstraint]
    notice_kind: NoticeKind | None = None  # 공지 출처일 때 그 공지의 종류


def _brief_body(stored: Any) -> str:
    """보관된 기획안의 title·idea·why·facts를 받은 그대로 옮긴다. 숫자는 만들지 않는다."""
    b = stored.brief
    lines = [b.title.strip(), "", b.idea.strip(), "", b.why.strip()]
    if b.facts:
        lines += ["", "근거"] + [f"- {f.label.strip()}: {f.value.strip()}" for f in b.facts]
    return "\n".join(lines)


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
        return SourceParts(
            "notice", data.notice_id, body, [body, DEMO_TEXT, TERMS_TEXT], [], view.kind
        )
    if data.proposal_id is not None:
        if briefs is None:
            return _result(request, TaskStatus.FAILED, outcome="brief_not_found")
        try:
            stored = briefs.existing(data.proposal_id)
        except BriefStorageError:
            return _result(request, TaskStatus.FAILED, outcome="storage_error")
        if stored is None:
            return _result(request, TaskStatus.FAILED, outcome="brief_not_found")
        body = _brief_body(stored)
        if any(phrase in body for phrase in FORBIDDEN_PHRASES):  # 참여 조건부 게시 금지
            return _result(request, TaskStatus.FAILED, outcome="forbidden_phrase")
        return SourceParts("brief", data.proposal_id, body, [body, DEMO_TEXT, TERMS_TEXT], [])
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
    return SourceParts(
        "coupon",
        facts.campaign_id,
        _coupon_body(facts),
        [f"{facts.unit_value:,}원 쿠폰", DEMO_TEXT, TERMS_TEXT],
        facts.constraints,
    )


def preflight_draft(
    request: AgentRequest, data: DraftSnsPostInput, notices: NoticeStore
) -> AgentResult | None:
    """초안을 만들 수 있는지 미리 본다(출처·캡션 길이). 만들 수 있으면 None."""
    parts = source_parts(request, data, notices)
    if isinstance(parts, AgentResult):
        return parts
    if len(_compose(parts.body, data.media_kind)) > CAPTION_MAX:
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

    caption = _compose(body, data.media_kind)
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
        hashtags=list(HASHTAGS),
        caption_sha256=caption_sha,
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

    forbidden = sum(1 for phrase in FORBIDDEN_PHRASES if phrase in caption)
    if forbidden:
        problems.append("forbidden_phrase")

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
        warnings=media.warnings,
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
