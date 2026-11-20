"""총괄이 부르는 홍보 영상 초안 준비 (skill `prepare_video_draft_from_plan`). 프레임워크 무관.

문제: 총괄의 '홍보 영상' 흐름 마지막 단계 `generate_media`는 `post_draft_id`와
`scene_template_id`가 필요한데, 기획 `receive_insight`가 돌려주는 `video_plan`에는 둘 다 없다.
초안을 만드는 `handoff_brief_to_sns`·`prepare_brief_media`는 총괄이 못 부른다(dashboard-api 전용).

이 skill이 하는 일(얇은 연결, 새 판단 규칙 없음):
- 입력은 `receive_insight` 응답의 `proposal_id`와 `video_plan` 그대로다(총괄은 이 둘만 넘긴다).
- SNS 글 초안(릴스, 캡션만)을 만들어 보관하고 `post_draft_id`를 돌려준다. 문안은 기존
  `draft_sns_post`의 기획안 템플릿과 같은 방식(고정 템플릿, LLM 없음)이고 `video_plan`의
  concept·script 첫 문장만 옮긴다. 숫자를 만들지 않는다. 금칙 문구·과장·개인정보는 거절한다.
- `scene_template_id`는 기획이 정하지 않는다. 마케팅이 `media_kind=video`로 기존 매핑
  (`brief_handoff.SCENE_TEMPLATES["video"]`)에서 고른다. 매핑 템플릿이 없고 영상 템플릿이
  여럿이면 지어내지 않고 `scene_template_ambiguous`와 후보 목록을 돌려준다.
- 템플릿 필드(season·color_theme·category) 값의 우선순위: 호출자가 준 `fields`(`admin`) >
  `video_plan.media_hints`(`media_hints`, 있고 `check_field_value`를 통과한 값만) > 기획안 추천
  (`recommended`, `media_recommend.recommend_fields`가 concept·script에서 규칙으로 찾는다).
  힌트가 검사에 걸리면 값은 옮기지 않고 걸린 필드 이름만 `rejected_hints`에 남긴다.
  끝내 못 정한 필드가 있으면 지어내지 않고 `template_fields_missing`(+ 고를 값)으로 거절한다.
- 길이: 템플릿 값이 우선이다. `video_plan.duration_s`가 다르면 조용히 바꾸지 않고
  `duration_adjusted: {requested, used}`로 알린다. 비율: 템플릿에 비율 개념이 없고 세로 영상
  (9:16) 고정 문장이라 거절하지 않고, 다르면 `aspect_adjusted: {requested, used}`로 알린다.

여기서 하지 않는 일: 미디어 생성(`generate_media`, B등급·비용)과 게시(`publish_sns`, C등급
사람 승인). 그래서 `execute()`가 없고 등급은 `draft_sns_post`와 같은 read다(쓰는 것은 SNS 초안
저장소의 초안 한 건, 내용 주소 id라 같은 입력이면 같은 id).
응답·로그에는 입력 값(개념·대본·힌트 값)을 되돌리지 않는다. 이유 코드와 필드 이름만 담는다.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel, ConfigDict, Field, StrictInt, ValidationError

from ax_contracts.schemas import AgentRequest, AgentResult, Evidence, TaskStatus
from ax_contracts.schemas.common import AxModel
from ax_contracts.schemas.planning import CampaignBriefInput
from marketing_agent import sns
from marketing_agent.brief_handoff import SCENE_TEMPLATES
from marketing_agent.media.prompts import (
    PromptRejected,
    SceneTemplate,
    check_field_value,
    contains_pii,
    field_choices,
    list_templates,
)
from marketing_agent.media_recommend import recommend_fields

ACTION = "prepare_video_draft_from_plan"
MEDIA_KIND = "video"
DRAFT_MEDIA_KIND = "reel"
FIELDS_MAX = 8
HINT_KEYS = ("season", "color_theme", "category")
TEMPLATE_ASPECT = "9:16"  # 영상 템플릿 문장이 세로 클립이다(비율 필드는 없다)
PLAN_TEMPLATE = "video_plan_caption_v1"
SCRIPT_LINE_MAX = 120
HOOK_MAX = 50


class VideoPlanIn(BaseModel):
    """총괄이 넘기는 `video_plan`. 계약(`VideoPlan`)이 바뀌어도 받도록 알 수 없는 키를 허용한다.

    쓰는 값은 concept·script·duration_s·aspect·media_hints뿐이다. 나머지는 읽지 않는다."""

    model_config = ConfigDict(extra="allow")

    concept: str = Field(min_length=1, max_length=200)
    script: str = Field(min_length=1, max_length=800)
    duration_s: StrictInt = Field(ge=1, le=120)
    aspect: str | None = Field(default=None, max_length=10)
    media_hints: dict[str, Any] | None = None  # 선택. season·color_theme·category(짧은 문자열)


class PrepareVideoDraftInput(AxModel):
    proposal_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
    video_plan: VideoPlanIn
    # 사람이 정한 템플릿 필드 값(가장 우선, 화이트리스트 검사). 보통 비운다
    fields: dict[str, str] | None = Field(default=None, max_length=FIELDS_MAX)


class DurationAdjusted(AxModel):
    requested: int
    used: int


class AspectAdjusted(AxModel):
    requested: str
    used: str


class PreparedVideoDraft(AxModel):
    """`generate_media` 입력 재료. `generate_media_input`을 그대로 넘기면 된다."""

    post_draft_id: str
    proposal_id: str
    media_kind: str = MEDIA_KIND
    scene_template_id: str
    fields: dict[str, str]
    # 필드마다 출처: admin | media_hints | recommended (추천 안의 기본값도 recommended)
    field_sources: dict[str, str]
    rejected_hints: list[str] = Field(default_factory=list)  # 검사에 걸려 안 쓴 힌트 필드 이름
    resolution: str
    duration_s: int  # 템플릿 값(우선)
    duration_adjusted: DurationAdjusted | None = None
    aspect_adjusted: AspectAdjusted | None = None
    generate_media_input: dict[str, Any]
    draft_state: str = "caption_only"
    next_steps: list[str] = Field(default_factory=lambda: ["generate_media", "check_sns_post"])
    ai_generated: bool = True


def _now() -> datetime:
    return datetime.now(UTC)


def _fail(request: AgentRequest, outcome: str, **output: Any) -> AgentResult:
    return AgentResult(meta=request.meta, status=TaskStatus.FAILED, outcome=outcome, output=output)


def resolve_template(
    templates_path: Path | None = None, mapping: dict[str, str] | None = None
) -> SceneTemplate | tuple[str, list[str]]:
    """영상 장면 템플릿 하나. 못 정하면 (이유 코드, 후보 id 목록)."""
    videos = [t for t in list_templates(templates_path) if t.kind == MEDIA_KIND]
    mapped = (mapping or SCENE_TEMPLATES)[MEDIA_KIND]
    by_id = {t.template_id: t for t in videos}
    if mapped in by_id:
        return by_id[mapped]
    if len(videos) == 1:
        return videos[0]
    candidates = [t.template_id for t in videos]
    return ("scene_template_ambiguous" if candidates else "unknown_scene_template", candidates)


def _plan_text(plan: VideoPlanIn) -> str:
    return f"{plan.concept}\n{plan.script}"


def _caption_body(plan: VideoPlanIn, proposal_id: str) -> tuple[str, dict[str, Any]]:
    hook = sns._first_sentence(plan.concept, HOOK_MAX)  # noqa: SLF001
    line = sns._first_sentence(plan.script, SCRIPT_LINE_MAX)  # noqa: SLF001
    blocks = [hook]
    used = [sns._field_meta("concept", plan.concept)]  # noqa: SLF001
    if line:
        blocks.append(line)
        used.append(sns._field_meta("script", plan.script))  # noqa: SLF001
    blocks.append("기획팀 제안 기반")
    meta = {
        "kind": "video_plan",
        "proposal_id": proposal_id,
        "template": PLAN_TEMPLATE,
        "reconstructed": True,
        "fields": used,
    }
    return "\n\n".join(blocks), meta


def _resolve_fields(
    plan: VideoPlanIn,
    proposal_id: str,
    admin: dict[str, str] | None,
    template: SceneTemplate,
    templates_path: Path | None,
    now: datetime,
) -> tuple[dict[str, str], dict[str, str], list[str]] | tuple[str, str, str]:
    """(필드, 출처, 걸린 힌트 이름) 또는 ('invalid_field', 필드, 코드)."""
    values: dict[str, str] = {}
    sources: dict[str, str] = {}
    for name, value in (admin or {}).items():
        if name not in template.fields:
            return ("invalid_field", name, "field_not_allowed")
        try:
            values[name] = check_field_value(name, value, templates_path)
        except PromptRejected as error:
            return ("invalid_field", name, error.code)
        sources[name] = "admin"
    hints = plan.media_hints or {}
    rejected: list[str] = []
    for name in HINT_KEYS:
        if name in values or name not in template.fields or name not in hints:
            continue
        raw = hints[name]
        if not isinstance(raw, str) or not raw.strip():
            continue  # 비면 힌트가 없는 것으로 본다(걸린 것이 아니다)
        try:
            values[name] = check_field_value(name, raw, templates_path)
            sources[name] = "media_hints"
        except PromptRejected:
            rejected.append(name)  # 값은 옮기지 않는다
    brief = CampaignBriefInput(
        proposal_id=proposal_id,
        title=plan.concept,
        idea=plan.concept,
        why=plan.script,
        approved_by="video_plan",
    )
    for note in recommend_fields(brief, now, template.fields):
        if note.field not in values and note.value is not None:
            try:
                values[note.field] = check_field_value(note.field, note.value, templates_path)
            except PromptRejected:
                continue
            sources[note.field] = "recommended"
    return values, sources, rejected


def prepare_video_draft(
    request: AgentRequest,
    store: sns.SnsStore,
    *,
    templates_path: Path | None = None,
    scene_mapping: dict[str, str] | None = None,
) -> AgentResult:
    try:
        data = PrepareVideoDraftInput.model_validate(request.input)
    except ValidationError as error:
        return _fail(
            request,
            "invalid_input",
            errors=[f"{'.'.join(map(str, e['loc']))}: {e['type']}" for e in error.errors()],
        )
    plan = data.video_plan
    text = _plan_text(plan)
    if contains_pii(text, templates_path):
        return _fail(request, "pii_in_plan")
    if any(phrase in text for phrase in sns.FORBIDDEN_PHRASES):
        return _fail(request, "forbidden_phrase")
    if sns.has_forbidden_claim(text):
        return _fail(request, "forbidden_claim")

    chosen = resolve_template(templates_path, scene_mapping)
    if isinstance(chosen, tuple):
        return _fail(request, chosen[0], candidates=chosen[1])
    template = chosen

    resolved = _resolve_fields(
        plan, data.proposal_id, data.fields, template, templates_path, _now()
    )
    if resolved[0] == "invalid_field":
        return _fail(request, "invalid_field", field=resolved[1], field_code=resolved[2])
    fields, sources, rejected = resolved  # type: ignore[misc]
    missing = [n for n in template.fields if n not in fields]
    if missing:
        options: dict[str, list[str] | None] = {}
        for name in missing:
            choices, _ = field_choices(name, templates_path)
            options[name] = list(choices) if choices is not None else None
        return _fail(
            request,
            "template_fields_missing",
            scene_template_id=template.template_id,
            missing_fields=missing,
            field_options=options,
            rejected_hints=rejected,
        )

    body, meta = _caption_body(plan, data.proposal_id)
    caption = sns._compose(body, DRAFT_MEDIA_KIND)  # noqa: SLF001
    if len(caption) > sns.CAPTION_MAX:
        return _fail(request, "caption_too_long")
    options_meta = {"share_to_feed": True}
    caption_sha = sns._sha(caption)  # noqa: SLF001
    draft = sns.SnsPostDraft(
        post_draft_id=sns.content_id(
            caption_sha, None, DRAFT_MEDIA_KIND, "instagram", options_meta
        ),
        source_kind="brief",
        source_ref=data.proposal_id,
        platform="instagram",
        media_kind=DRAFT_MEDIA_KIND,
        caption=caption,
        hashtags=list(sns.HASHTAGS),
        caption_sha256=caption_sha,
        required_text=[body, sns.DEMO_TEXT, sns.TERMS_TEXT],
        context_id=request.meta.context_id,
        platform_options=options_meta,
        caption_source=meta,
    )
    store.save(draft)

    used_s = template.duration_s or plan.duration_s
    generate_input: dict[str, Any] = {
        "post_draft_id": draft.post_draft_id,
        "media_kind": MEDIA_KIND,
        "scene_template_id": template.template_id,
        "fields": dict(fields),
        "resolution": template.resolution,
        "duration_s": used_s,
    }
    aspect = plan.aspect
    out = PreparedVideoDraft(
        post_draft_id=draft.post_draft_id,
        proposal_id=data.proposal_id,
        scene_template_id=template.template_id,
        fields=fields,
        field_sources=sources,
        rejected_hints=rejected,
        resolution=template.resolution,
        duration_s=used_s,
        duration_adjusted=(
            DurationAdjusted(requested=plan.duration_s, used=used_s)
            if plan.duration_s != used_s
            else None
        ),
        aspect_adjusted=(
            AspectAdjusted(requested=aspect, used=TEMPLATE_ASPECT)
            if aspect and aspect != TEMPLATE_ASPECT
            else None
        ),
        generate_media_input=generate_input,
    )
    return AgentResult(
        meta=request.meta,
        status=TaskStatus.COMPLETED,
        output=out.model_dump(mode="json"),
        evidence=Evidence(
            queries=[ACTION],
            data_summary=f"영상 제작안으로 초안·장면 템플릿 {template.template_id} 준비",
            collected_at=_now(),
            ref_ids=[draft.post_draft_id, data.proposal_id],
        ),
        fallback=True,  # LLM 없이 고정 템플릿·규칙
    )


__all__ = [
    "ACTION",
    "PrepareVideoDraftInput",
    "PreparedVideoDraft",
    "VideoPlanIn",
    "prepare_video_draft",
    "resolve_template",
]
