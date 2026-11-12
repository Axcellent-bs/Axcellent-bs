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

이유 코드(`reason_code`): `price_not_found`·`model_not_configured`·`price_table_error`(가격표),
`unknown_scene_template`, 초안 단계는 `draft_sns_post`의 outcome 그대로
(`caption_too_long`·`forbidden_phrase` 등).
"""

from __future__ import annotations

import hashlib
from pathlib import Path

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
from marketing_agent.media.prompts import PromptRejected, get_template

# 미디어 종류 → 기존 장면 템플릿. 템플릿은 `scene_templates.toml`에 있는 것만 쓴다
SCENE_TEMPLATES = {"image": "cat_bookstore_image", "video": "cat_bookstore_video"}
DRAFT_MEDIA_KIND = {"image": "image", "video": "reel"}  # 초안(draft_sns_post)의 media_kind
NEXT_STEPS = ("generate_media", "check_sns_post", "publish_sns")  # 모두 사람이 이어서 한다


class HandoffBriefToSnsInput(AxModel):
    proposal_id: str | None = Field(default=None, min_length=1)
    campaign_id: str | None = Field(default=None, min_length=1)
    media_kind: str = Field(default="image", pattern=r"^(image|video)$")


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


class HandoffBriefToSnsOutput(AxModel):
    outcome: str  # handed | already_handed | failed
    sns_handoff: SnsHandoffState
    campaign_id: str | None = None


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


def price_problem(
    request: SnsMediaRequestDraft, settings: MediaSettings, prices_path: Path | None = None
) -> str | None:
    """단가표로 이 요청을 만들 수 있는지. 없으면 이유 코드(fail-closed), 되면 None."""
    kind = "video" if request.media_kind == "video" else "image"
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


def state_of(record: SnsHandoffRecord | None, draft_state: DraftState | None) -> SnsHandoffState:
    if record is None:
        return SnsHandoffState()
    return SnsHandoffState(
        status=record.status,
        draft_id=record.draft_id,
        media_request=record.media_request,
        reason_code=record.reason_code,
        handed_at=record.handed_at,
        draft_state=draft_state if record.status == "handed" else None,
    )


def view_of(
    item: StoredBrief, record: SnsHandoffRecord | None, draft_state: DraftState | None
) -> StoredBriefView:
    return StoredBriefView(**item.model_dump(), sns_handoff=state_of(record, draft_state))


__all__ = [
    "DRAFT_MEDIA_KIND",
    "HandoffBriefToSnsInput",
    "HandoffBriefToSnsOutput",
    "PromptRejected",
    "SnsMediaRequestDraft",
    "media_request_for",
    "price_problem",
    "state_of",
    "view_of",
]
