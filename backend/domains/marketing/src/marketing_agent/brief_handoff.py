"""기획안을 SNS·미디어 작업으로 넘긴다 (skill `handoff_brief_to_sns`). 프레임워크 무관.

넘기는 일 = 아래 둘을 *만들어 보관*하는 것까지다.
- (a) 미디어 요청 초안: 장면 템플릿 id·해상도·품질과 아직 빈 필드. 프롬프트 문장은 만들지 않는다
  (기존 규칙: 템플릿 고정 문장 + 화이트리스트 필드뿐이라 기획안 글은 프롬프트에 안 들어간다).
- (b) SNS 글 초안: 기존 `draft_sns_post`(출처 = 보관된 기획안)가 고정 템플릿으로 만든다.
  title·idea·why·facts를 받은 그대로 옮기고 숫자를 새로 만들거나 계산하지 않는다.

여기서 하지 않는 일(사람이 다음 단계에서 한다):
- 미디어 생성(`generate_media`, B등급·비용): 부르지 않는다.
- 게시(`publish_sns`, C등급·사람 승인): 부르지 않는다.
그래서 돈이 나가는 호출도 외부 게시도 없고, 쓰는 것은 마케팅 전용 저장소(기획안 DB의 넘김
기록, SNS 초안 DB의 초안)뿐이다. 새 안전 액션이 필요 없다(`draft_sns_post`처럼 `execute()` 없음).

가격표는 fail-closed다: 실제 공급자가 설정돼 있는데 단가표(`prices.toml`)에 그 조합이 없거나
모델이 비어 있으면 넘기지 않고 `failed`(이유 코드)로 기록한다. 목 모드(`mock`)는 단가가 필요 없다.

멱등: 이미 `handed`면 보관본을 그대로 돌려준다(초안이 저장소에서 사라졌으면 같은 내용으로 다시
저장 - 초안 id는 내용 주소라 같다). `failed`는 다시 넘기면 처음부터 다시 시도한다.

자동 추천(`media_recommend.py`, 결정적 규칙·LLM 없음):
- `media_kind="auto"`면 기획안 글에서 종류(image·video·carousel)를 고르고,
  `autofill=true`(또는 auto)면 템플릿 필드를 기획안 근거·안전한 기본값으로 채운다.
  근거가 없는 필드는 미정(`missing_fields`)으로 남는다.
- 관리자가 고른 값이 우선한다: 종류를 직접 주면 그 종류(`kind_source="admin"`), `fields`로 준 값은
  추천 값을 덮어쓴다(`field_sources[이름]="admin"`). 값은 장면 템플릿 화이트리스트 검사를 거친다.
- 자동으로 고른 종류를 서버가 만들 수 없으면(단가표·모델 없음) 넘김을 막지 않고 이미지로 넘긴 뒤
  `kind_source="auto_fallback"`과 `recommendation_blocked`(이유 코드)를 남긴다. 관리자가 직접 고른
  종류는 그대로 fail-closed다.
- 이미 넘긴 기획안은 `revise=true`일 때만 요청 초안을 다시 만든다(종류·필드 바꾸기). 막히면
  보관본을 바꾸지 않고 `revise_rejected`(이유 코드)를 돌려준다. `revise`가 없으면 전처럼 멱등이다.
- 옛 호출(종류만 주고 `autofill` 없음)은 전과 같이 필드를 채우지 않는다.

이유 코드(`reason_code`): `price_not_found`·`model_not_configured`·`price_table_error`(가격표),
`unknown_scene_template`, 초안 단계는 `draft_sns_post`의 outcome 그대로
(`caption_too_long`·`forbidden_phrase` 등). 관리자 필드 값이 걸리면 `invalid_field`
(+ `field`, `field_code`).
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Literal

from pydantic import Field

from ax_contracts.schemas.common import AxModel
from marketing_agent.briefs import (
    DraftState,
    SnsHandoffRecord,
    SnsHandoffState,
    StoredBrief,
    StoredBriefView,
)
from marketing_agent.media import pricing
from marketing_agent.media.config import MOCK, MediaSettings
from marketing_agent.media.pricing import PriceNotFound, PriceTableError
from marketing_agent.media.prompts import (
    PromptRejected,
    check_field_value,
    field_choices,
    get_template,
)
from marketing_agent.media_recommend import MediaRecommendation, recommend, recommend_fields

# 미디어 종류 → 기존 장면 템플릿. 템플릿은 `scene_templates.toml`에 있는 것만 쓴다
# 캐러셀은 이미지 여러 장이라 이미지 템플릿을 쓴다(장수는 생성 단계에서 사람이 정한다)
SCENE_TEMPLATES = {
    "image": "cat_bookstore_image",
    "video": "cat_bookstore_video",
    "carousel": "cat_bookstore_image",
}
# 초안(draft_sns_post)의 media_kind. 캐러셀 초안은 이미지가 2장 이상 있어야 만들 수 있어
# 넘길 때는 캡션만 있는 이미지 초안으로 둔다
DRAFT_MEDIA_KIND = {"image": "image", "video": "reel", "carousel": "image"}
AUTO = "auto"
FIELDS_MAX = 8
KindSource = Literal["auto", "auto_fallback", "admin"]
NEXT_STEPS = ("generate_media", "check_sns_post", "publish_sns")  # 모두 사람이 이어서 한다


class HandoffBriefToSnsInput(AxModel):
    proposal_id: str | None = Field(default=None, min_length=1)
    campaign_id: str | None = Field(default=None, min_length=1)
    # auto = 기획안에서 종류를 고른다. 종류를 직접 주면 관리자 선택으로 본다
    media_kind: str = Field(default="image", pattern=r"^(image|video|carousel|auto)$")
    # 관리자가 정한 필드 값(추천을 덮어쓴다). 주면 `autofill`로 본다
    fields: dict[str, str] | None = Field(default=None, max_length=FIELDS_MAX)
    autofill: bool = False  # 나머지 필드를 추천 값으로 채운다
    revise: bool = False  # 이미 넘긴 기획안의 요청 초안을 다시 만든다

    @property
    def fills(self) -> bool:
        return self.autofill or self.media_kind == AUTO or self.fields is not None


class SnsMediaRequestDraft(AxModel):
    """미디어 요청 초안. `generate_media` 입력의 밑그림이며 자동으로 부르지 않는다."""

    media_kind: str
    scene_template_id: str
    resolution: str
    quality: str | None = None
    duration_s: int | None = None
    media_ref: str  # generate_media의 묶음 id(post_draft_id 칸). 기획안·종류에서만 정해진다
    fields: dict[str, str] = Field(
        default_factory=dict
    )  # 기획안에서 안전하게 정할 값이 없어 비어 있다
    missing_fields: list[str]  # 사람이 화이트리스트 안에서 정해야 하는 템플릿 필드
    next_steps: list[str] = Field(default_factory=lambda: list(NEXT_STEPS))
    auto_generate: bool = False  # 항상 false: 생성은 사람이 시작한다
    # --- 자동 추천·관리자 선택 기록(옛 호출은 비어 있다) ---
    kind_source: KindSource | None = None
    recommended_media_kind: str | None = None
    recommendation_rule: str | None = None
    recommendation_reason: str | None = None  # 근거 한 줄
    recommendation_confidence: str | None = None
    recommendation_blocked: str | None = None  # 추천 종류를 서버가 만들 수 없던 이유 코드
    field_sources: dict[str, str] = Field(default_factory=dict)  # brief | default | admin
    field_reasons: dict[str, str] = Field(default_factory=dict)  # 필드마다 근거·미정 이유
    field_options: dict[str, list[str] | None] = Field(default_factory=dict)  # None = 짧은 단어
    field_max_len: dict[str, int] = Field(default_factory=dict)


class HandoffBriefToSnsOutput(AxModel):
    outcome: str  # handed | already_handed | failed | revised | revise_rejected
    sns_handoff: SnsHandoffState
    campaign_id: str | None = None
    reason_code: str | None = None  # revise_rejected일 때
    field: str | None = None  # invalid_field일 때 걸린 필드 이름(값은 담지 않는다)
    field_code: str | None = None


class FieldRejected(Exception):
    """관리자가 준 필드 값을 쓸 수 없다. 값은 담지 않는다."""

    def __init__(self, field: str, code: str) -> None:
        super().__init__(code)
        self.field = field
        self.code = code


def media_ref_for(proposal_id: str, media_kind: str) -> str:
    """생성 장부·폴더의 묶음 id. 기획안·종류에서만 정해져 다시 넘겨도 같다."""
    digest = hashlib.sha256(f"{proposal_id}:{media_kind}".encode()).hexdigest()[:16]
    return f"brf_{digest}"


def media_request_for(proposal_id: str, media_kind: str) -> SnsMediaRequestDraft:
    """장면 템플릿에서 요청 초안을 만든다. 템플릿이 없으면 `PromptRejected`."""
    template = get_template(SCENE_TEMPLATES[media_kind])
    return SnsMediaRequestDraft(
        media_kind=media_kind,
        scene_template_id=template.template_id,
        resolution=template.resolution,
        quality=template.quality,
        duration_s=template.duration_s,
        media_ref=media_ref_for(proposal_id, media_kind),
        missing_fields=list(template.fields),
    )


def template_fields_by_kind() -> dict[str, tuple[str, ...]]:
    """종류마다 템플릿 필드. 템플릿이 없는 종류는 뺀다."""
    found: dict[str, tuple[str, ...]] = {}
    for kind, template_id in SCENE_TEMPLATES.items():
        try:
            found[kind] = get_template(template_id).fields
        except PromptRejected:
            continue
    return found


def recommendation_for(item: StoredBrief) -> MediaRecommendation:
    """보관된 기획안에서 추천을 계산한다(순수 계산, 저장 안 함)."""
    return recommend(item.brief, item.received_at, template_fields_by_kind())


def build_media_request(
    item: StoredBrief,
    requested_kind: str,
    admin_fields: dict[str, str] | None = None,
    *,
    kind_source: KindSource | None = None,
    blocked: str | None = None,
) -> SnsMediaRequestDraft:
    """추천으로 채우고 관리자 값으로 덮어쓴 요청 초안.

    `requested_kind`가 `auto`면 추천 종류를 쓴다. 관리자 필드가 템플릿에 없거나 검사에 걸리면
    `FieldRejected`, 템플릿이 없으면 `PromptRejected`.
    """
    rec = recommendation_for(item)
    kind = rec.media_kind if requested_kind == AUTO else requested_kind
    draft = media_request_for(item.proposal_id, kind)
    template = get_template(draft.scene_template_id)
    notes = recommend_fields(item.brief, item.received_at, template.fields)
    fields: dict[str, str] = {}
    sources: dict[str, str] = {}
    reasons: dict[str, str] = {}
    for note in notes:
        reasons[note.field] = note.reason
        if note.value is not None:
            fields[note.field] = note.value
            sources[note.field] = note.source
    for name, value in (admin_fields or {}).items():
        if name not in template.fields:
            raise FieldRejected(name, "field_not_allowed")
        try:
            fields[name] = check_field_value(name, value)
        except PromptRejected as error:
            raise FieldRejected(name, error.code) from error
        sources[name] = "admin"
        reasons[name] = "관리자가 정함"
    options: dict[str, list[str] | None] = {}
    max_len: dict[str, int] = {}
    for name in template.fields:
        values, limit = field_choices(name)
        options[name] = list(values) if values is not None else None
        if values is None:
            max_len[name] = limit
    return draft.model_copy(
        update={
            "fields": fields,
            "missing_fields": [n for n in template.fields if n not in fields],
            "kind_source": kind_source or ("auto" if requested_kind == AUTO else "admin"),
            "recommended_media_kind": rec.media_kind,
            "recommendation_rule": rec.rule,
            "recommendation_reason": rec.reason,
            "recommendation_confidence": rec.confidence,
            "recommendation_blocked": blocked,
            "field_sources": sources,
            "field_reasons": reasons,
            "field_options": options,
            "field_max_len": max_len,
        }
    )


def price_problem(
    request: SnsMediaRequestDraft, settings: MediaSettings, prices_path: Path | None = None
) -> str | None:
    """단가표로 이 요청을 만들 수 있는지. 없으면 이유 코드(fail-closed), 되면 None."""
    kind = "video" if request.media_kind == "video" else "image"  # 캐러셀은 이미지 단가
    provider = settings.provider(kind)
    if provider == MOCK:
        return None
    model = settings.model(kind)
    if not model:
        return "model_not_configured"
    try:
        pricing.find_entry(
            provider=provider,
            model=model,
            kind=kind,
            resolution=request.resolution,
            quality=request.quality if kind == "image" else None,
            path=prices_path,
        )
    except PriceNotFound:
        return "price_not_found"
    except PriceTableError:
        return "price_table_error"
    return None


def request_sha(media_request: dict) -> str:
    """요청 초안에서 생성 결과를 정하는 값(종류·템플릿·규격·필드)의 해시. 바뀌면 다른 미디어다."""
    names = ("media_kind", "scene_template_id", "resolution", "quality", "duration_s", "media_ref")
    parts = [[name, media_request.get(name)] for name in names]
    parts.append(["fields", media_request.get("fields") or {}])
    raw = json.dumps(parts, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def prepared_of(record: SnsHandoffRecord) -> dict | None:
    """지금 요청 초안으로 만든 미디어 초안 기록. 요청 초안이 바뀌었으면 None이다."""
    prepared, media = record.prepared, record.media_request
    if record.status != "handed" or not isinstance(prepared, dict) or not media:
        return None
    if prepared.get("request_sha") != request_sha(media):
        return None
    return {k: v for k, v in prepared.items() if k != "request_sha"}


def state_of(
    record: SnsHandoffRecord | None,
    draft_state: DraftState | None,
    estimate: dict | None = None,
) -> SnsHandoffState:
    if record is None:
        return SnsHandoffState()
    handed = record.status == "handed"
    return SnsHandoffState(
        status=record.status,
        draft_id=record.draft_id,
        media_request=record.media_request,
        reason_code=record.reason_code,
        handed_at=record.handed_at,
        draft_state=draft_state if handed else None,
        media_estimate=estimate if handed else None,
        prepared=prepared_of(record),
    )


def view_of(
    item: StoredBrief,
    record: SnsHandoffRecord | None,
    draft_state: DraftState | None,
    estimate: dict | None = None,
) -> StoredBriefView:
    try:
        recommendation: dict | None = recommendation_for(item).model_dump(mode="json")
    except Exception:  # noqa: BLE001 - 추천은 참고 정보다. 못 만들어도 조회는 돌려준다
        recommendation = None
    return StoredBriefView(
        **item.model_dump(),
        sns_handoff=state_of(record, draft_state, estimate),
        media_recommendation=recommendation,
    )


__all__ = [
    "AUTO",
    "DRAFT_MEDIA_KIND",
    "FieldRejected",
    "HandoffBriefToSnsInput",
    "HandoffBriefToSnsOutput",
    "PromptRejected",
    "SnsMediaRequestDraft",
    "build_media_request",
    "media_request_for",
    "prepared_of",
    "price_problem",
    "recommendation_for",
    "request_sha",
    "state_of",
    "view_of",
]
