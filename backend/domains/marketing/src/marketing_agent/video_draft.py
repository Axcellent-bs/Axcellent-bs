"""총괄이 부르는 홍보 영상 초안 준비 (skill `prepare_video_draft_from_plan`). 프레임워크 무관.

문제: 총괄의 '홍보 영상' 흐름 마지막 단계 `generate_media`는 `post_draft_id`와
`scene_template_id`가 필요한데, 기획 `receive_insight`가 돌려주는 `video_plan`에는 둘 다 없다.
초안을 만드는 `handoff_brief_to_sns`·`prepare_brief_media`는 총괄이 못 부른다(dashboard-api 전용).

이 skill이 하는 일(얇은 연결, 새 판단 규칙 없음):
- 입력은 `receive_insight` 응답의 `proposal_id`와 `video_plan` 그대로다(총괄은 이 둘만 넘긴다).
- SNS 글 초안(릴스, 캡션만)을 만들어 보관하고 `post_draft_id`를 돌려준다. 문안은 기존
  `draft_sns_post`의 기획안 템플릿과 같은 방식(고정 템플릿, LLM 없음)이고 `video_plan`의
  concept·script 첫 문장만 옮긴다. 숫자를 만들지 않는다. 금칙 문구·과장·개인정보는 거절한다.
- 11/22: `video_plan.scenes[].prompt`(최대 4)가 있으면 `plan_story_video` 템플릿에 plan_* 필드
  (회사 [brand] 주인공·배경, concept, 장면 1~4)로 넣는다. 정리·금칙에 걸린 필드는 이름만
  `rejected_plan_fields`에 남기고, 장면이 하나도 안 남으면 아래 예전 템플릿으로 돌아가
  `template_fallback: "plan_fields_rejected"`를 남긴다. 사람이 `fields`를 주면 예전 템플릿이다.
  초안 id에는 의뢰 `context_id`를 넣는다(같은 제안을 다시 쓴 의뢰도 자기 초안·영상)
- 장면이 없을 때 `scene_template_id`는 기획이 정하지 않는다. 마케팅이 `media_kind=video`로 기존 매핑
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

from ax_agent_template import tenant
from ax_contracts.schemas import AgentRequest, AgentResult, Evidence, TaskStatus
from ax_contracts.schemas.common import AxModel
from ax_contracts.schemas.planning import CampaignBriefInput
from marketing_agent import sns
from marketing_agent.brief_handoff import PLAN_VIDEO_TEMPLATE, SCENE_TEMPLATES
from marketing_agent.media.prompts import (
    PLAN_SCENE_FIELDS,
    PromptRejected,
    SceneTemplate,
    check_field_value,
    contains_pii,
    field_choices,
    get_template,
    list_templates,
    render_prompt_detailed,
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


class SceneIn(BaseModel):
    """`video_plan.scenes[]` 한 칸. 쓰는 값은 prompt(영문 장면 프롬프트)뿐이다."""

    model_config = ConfigDict(extra="allow")

    prompt: str = Field(default="", max_length=800)


class VideoPlanIn(BaseModel):
    """총괄이 넘기는 `video_plan`. 계약(`VideoPlan`)이 바뀌어도 받도록 알 수 없는 키를 허용한다.

    쓰는 값은 concept·script·duration_s·aspect·media_hints·scenes[].prompt다(11/22 scenes·style을
    받는다, 계약 VideoPlan에 이미 있는 칸). style은 받기만 하고 프롬프트에는 회사 [brand]를 쓴다."""

    model_config = ConfigDict(extra="allow")

    concept: str = Field(min_length=1, max_length=200)
    script: str = Field(min_length=1, max_length=800)
    duration_s: StrictInt = Field(ge=1, le=120)
    aspect: str | None = Field(default=None, max_length=10)
    media_hints: dict[str, Any] | None = None  # 선택. season·color_theme·category(짧은 문자열)
    scenes: list[SceneIn] = Field(default_factory=list, max_length=8)
    style: str | None = Field(default=None, max_length=300)


class PrepareVideoDraftInput(AxModel):
    proposal_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]*$")
    video_plan: VideoPlanIn
    # 사람이 정한 템플릿 필드 값(가장 우선, 화이트리스트 검사). 보통 비운다
    fields: dict[str, str] | None = Field(default=None, max_length=FIELDS_MAX)
    # 11/23 의뢰자가 고른 회사 대표 캐릭터(제작 기록 CR-…). 총괄이 의뢰 기록 값을 그대로 넘긴다.
    # 있으면 의뢰용 기록을 만들고 기획 장면으로 스토리보드를 붙여 영상은 캐릭터 경로(C)로 만든다
    brand_character_record_id: str | None = Field(default=None, pattern=r"^CR-[0-9a-f]{12}$")


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
    # 11/22 기획 장면 경로: 정리·금칙·길이 상한으로 빠진 plan_* 필드 이름(값은 남기지 않는다)
    rejected_plan_fields: list[str] = Field(default_factory=list)
    # 장면이 있었지만 모두 빠져 예전 템플릿으로 돌아갔으면 "plan_fields_rejected"
    template_fallback: str | None = None
    resolution: str
    duration_s: int  # 템플릿 값(우선)
    duration_adjusted: DurationAdjusted | None = None
    aspect_adjusted: AspectAdjusted | None = None
    generate_media_input: dict[str, Any]
    draft_state: str = "caption_only"
    next_steps: list[str] = Field(default_factory=lambda: ["generate_media", "check_sns_post"])
    ai_generated: bool = True
    # 11/23 캐릭터 경로: 이 의뢰의 제작 기록(이미지 시트·스토리보드). 총괄이 단계 결과 번호로 읽는다
    creative_record_id: str | None = None
    # 대표 캐릭터를 못 쓴 이유(record_not_found · no_scenes). 쓰면 None
    brand_character_skipped: str | None = None


def _now() -> datetime:
    return datetime.now(UTC)


def _fail(request: AgentRequest, outcome: str, **output: Any) -> AgentResult:
    return AgentResult(meta=request.meta, status=TaskStatus.FAILED, outcome=outcome, output=output)


def resolve_template(
    templates_path: Path | None = None, mapping: dict[str, str] | None = None
) -> SceneTemplate | tuple[str, list[str]]:
    """영상 장면 템플릿 하나. 못 정하면 (이유 코드, 후보 id 목록)."""
    videos = [
        t
        for t in list_templates(templates_path)
        if t.kind == MEDIA_KIND and t.template_id != PLAN_VIDEO_TEMPLATE  # 장면 경로 전용
    ]
    mapped = (mapping or SCENE_TEMPLATES)[MEDIA_KIND]
    by_id = {t.template_id: t for t in videos}
    if mapped in by_id:
        return by_id[mapped]
    if len(videos) == 1:
        return videos[0]
    candidates = [t.template_id for t in videos]
    return ("scene_template_ambiguous" if candidates else "unknown_scene_template", candidates)


def plan_story(
    plan: VideoPlanIn, templates_path: Path | None = None
) -> tuple[SceneTemplate | None, dict[str, str], list[str]]:
    """기획 장면 경로: (plan_story_video, 넘길 plan_* 필드, 빠진 필드 이름).

    장면 프롬프트(최대 4) · concept · 회사 [brand] 주인공·배경을 plan_* 필드로 만들고, 마케팅
    `render_prompt_detailed`와 같은 정리·검사·길이 상한으로 남는 것만 넘긴다(값은 그대로, 넘긴 뒤
    generate_media가 같은 규칙으로 다시 만든다). 장면이 없거나 모두 빠지면 템플릿 None."""
    prompts = [s.prompt for s in plan.scenes if s.prompt.strip()][: len(PLAN_SCENE_FIELDS)]
    if not prompts:
        return None, {}, []
    try:
        template = get_template(PLAN_VIDEO_TEMPLATE, templates_path)
    except PromptRejected:
        return None, {}, []
    b = tenant.brand()
    raw = {
        "plan_character": b["character_en"],
        "plan_setting": b["setting_en"],
        "plan_concept": plan.concept,
    }
    raw |= dict(zip(PLAN_SCENE_FIELDS, prompts, strict=False))
    rendered = render_prompt_detailed(template.template_id, raw, templates_path)
    used = [u["field"] for u in rendered.plan_used]
    rejected = [s["field"] for s in rendered.plan_skipped]
    if not any(name in PLAN_SCENE_FIELDS for name in used):
        return None, {}, rejected  # 기획 장면이 하나도 안 남았다: 지어내지 않는다
    return template, {name: raw[name] for name in used}, rejected


def _plan_text(plan: VideoPlanIn) -> str:
    return f"{plan.concept}\n{plan.script}"


def scene_starts(plan: VideoPlanIn) -> list[float]:
    """기획 장면의 시작 시각(초, 계약 `VideoScene.start_s`). 하나라도 없거나 이상하면 빈 목록.

    썸네일 후보 시점을 고르는 데만 쓴다(`request_video_cover.pick_times`)."""
    out: list[float] = []
    for scene in plan.scenes:
        raw = (scene.model_extra or {}).get("start_s")
        if isinstance(raw, bool) or not isinstance(raw, int | float) or raw < 0:
            return []
        out.append(float(raw))
    return out


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
    if sns.has_engagement_bait(text):
        return _fail(request, "forbidden_phrase")
    if sns.has_forbidden_claim(text):
        return _fail(request, "forbidden_claim")

    # 11/22: 장면 프롬프트가 있으면 plan_story_video.
    # 사람이 템플릿 필드를 정했으면(admin) 예전 템플릿
    story, plan_fields, rejected_plan = (
        plan_story(plan, templates_path) if not data.fields else (None, {}, [])
    )
    fallback = "plan_fields_rejected" if story is None and rejected_plan else None
    rejected: list[str] = []
    if story is not None:
        template = story
        fields = dict(plan_fields)
        sources = {
            n: "brand" if n in ("plan_character", "plan_setting") else "video_plan" for n in fields
        }
    else:
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
            rejected_plan_fields=rejected_plan,
            template_fallback=fallback,
        )

    body, meta = _caption_body(plan, data.proposal_id)
    caption = sns._compose(body, DRAFT_MEDIA_KIND)  # noqa: SLF001
    if len(caption) > sns.CAPTION_MAX:
        return _fail(request, "caption_too_long")
    options_meta = {"share_to_feed": True}
    caption_sha = sns._sha(caption)  # noqa: SLF001
    # 11/22: 초안 id에 의뢰 context_id를 넣는다.
    # 같은 제안을 다시 쓴 의뢰도 자기 초안·자기 영상이 된다
    # (예전에는 내용만으로 정해져 영상이 예전 의뢰 밑에 붙었다).
    # 게시 옵션(platform_options)은 그대로
    id_options = {**options_meta, "context_id": request.meta.context_id}
    draft = sns.SnsPostDraft(
        post_draft_id=sns.content_id(
            caption_sha, None, DRAFT_MEDIA_KIND, "instagram", id_options
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
        scene_starts_s=scene_starts(plan) if story is not None else [],
        plan_duration_s=float(plan.duration_s),
    )
    existing = store.get(draft.post_draft_id)
    if existing is None or not existing.asset_ref:  # 영상이 이미 연결된 초안은 덮어쓰지 않는다
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
    creative_id: str | None = None
    skipped: str | None = None
    if data.brand_character_record_id:  # 11/23 캐릭터 경로 (없으면 지금 그대로)
        from marketing_agent import request_creative

        rid, skipped = request_creative.prepare_storyboard(
            data.brand_character_record_id, request.meta.context_id, plan
        )
        if rid is not None and skipped is None:
            creative_id = rid
            generate_input[request_creative.INPUT_KEY] = rid
    aspect = plan.aspect
    out = PreparedVideoDraft(
        post_draft_id=draft.post_draft_id,
        proposal_id=data.proposal_id,
        scene_template_id=template.template_id,
        fields=fields,
        field_sources=sources,
        rejected_hints=rejected,
        rejected_plan_fields=rejected_plan,
        template_fallback=fallback,
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
        creative_record_id=creative_id,
        brand_character_skipped=skipped,
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
