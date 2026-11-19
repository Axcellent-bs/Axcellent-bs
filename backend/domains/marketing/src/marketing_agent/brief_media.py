"""skill `prepare_brief_media`: 넘긴 기획안의 미디어 요청 초안으로 미디어 초안을 만든다.

화면의 "미디어 만들기" 버튼이 부른다. 입력은 기획안 번호뿐이다. 종류·장면 템플릿·필드·규격은
호출자가 보내지 않고 **보관된 요청 초안**(`handoff_brief_to_sns`가 만든 것)에서 읽는다.

하는 일은 기존 `prepare_sns_post_from_media`를 그대로 부르는 것까지다:
생성(`generate_media`: B등급 안전 관문·건당/월 상한·단가표 fail-closed) → 규격 변환 → AI 라벨 →
`draft_sns_post` → `check_sns_post`. 상한·단가·안전 규칙은 여기서 바꾸지 않는다.

하지 않는 일:
- 게시(`publish_sns`, C등급 사람 승인)는 부르지 않는다. 승인으로 이어지는 경로도 만들지 않는다.
- 캐러셀은 만들지 않는다. 요청 초안에는 장면이 하나뿐이고 장마다 무엇을 그릴지는 사람이 정해야
  한다(지어내지 않는다). `carousel_items_undecided`로 거절한다.
- 미정 필드가 남은 요청 초안은 만들지 않는다(`fields_missing`).
- 배경음 모드(`MARKETING_AUDIO_MODE=bgm_overlay`)의 영상은 배경음 템플릿·필드가 요청 초안에 없어
  만들지 않는다(`bgm_fields_required`). 모두 비용이 나기 전에 막힌다.

멱등: 생성 묶음 id(`media_ref`)와 `context_id`를 기획안에서만 정하므로(`ctx_brief_<번호>`) 누가
몇 번을 불러도 같은 요청 초안은 같은 장부 키다. 끝난 생성은 장부에서 재생되어 비용이 다시 나지
않고, 만드는 중에 또 부르면 `in_progress`다. 이미 준비된 초안이 있으면 `already_prepared`.

결과는 넘김 기록의 `prepared`에 남긴다(어느 요청 초안으로 만들었는지 `request_sha` 포함). 그동안
요청 초안이 바뀌었으면 조회에는 보이지 않는다(`brief_handoff.prepared_of`).

기획 데이터 반영: 보관된 기획안의 구조화 필드(`plan_fields_for`)를 `plan_*` 선택 필드로 같이 넘긴다.
프롬프트 만들기(`media.prompts`)가 정리·검사해서 영상 템플릿의 `{plan_clause}` 자리에만 끼운다.
요청 초안(`media_request`)과 그 해시는 바뀌지 않는다(기획안 본문은 보관본에서 그때그때 읽는다).
쓴 필드 이름·길이·해시는 생성 결과(`meta.json`·장부)와 `prepared.plan_context`에 남는다.

예상 비용(`estimate`)은 조회 때 `plan_cost`(=`generate_media`와 같은 단가표·환율·건당 상한 규칙)로
계산한다. 값을 저장하거나 지어내지 않는다. 계산할 수 없으면 이유 코드만 돌려준다.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from pydantic import Field, ValidationError

from ax_contracts.schemas import AgentRequest, AgentResult, Evidence, TaskStatus
from ax_contracts.schemas.common import AxModel
from ax_contracts.schemas.planning import CampaignBriefInput
from marketing_agent.brief_handoff import request_sha, state_of
from marketing_agent.briefs import BriefStorageError, BriefStore, SnsHandoffRecord
from marketing_agent.media.config import MOCK, MediaSettings
from marketing_agent.media.ledger import LedgerError, MediaLedger
from marketing_agent.media.prompts import PromptRejected, get_template
from marketing_agent.media.skill import FromDecision, plan_cost
from marketing_agent.media.video_limits import ABSOLUTE_MAX_DURATION_S
from marketing_agent.notice import NoticeStore
from marketing_agent.sns import SnsStore
from marketing_agent.sns_prepare import prepare_sns_post_from_media

ACTION = "prepare_brief_media"
READY_STAGES = ("draft_ready", "check_rejected")
PrepareFn = Callable[..., Awaitable[AgentResult]]


class PrepareBriefMediaInput(AxModel):
    proposal_id: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")
    # 영상 길이(초). 주면 보관된 요청 초안의 길이를 바꾼다.
    # 설정 상한(`MARKETING_VIDEO_MAX_DURATION_S`)은 아래에서 따로 본다(넘으면 `duration_over_limit`)
    duration_s: int | None = Field(default=None, ge=1, le=ABSOLUTE_MAX_DURATION_S, strict=True)
    # 화면의 긴 영상 경고창에서 사람이 '계속'을 눌렀다는 주장. 감사 기록용일 뿐이다.
    # 서버는 이 값을 믿지 않는다: 경고창은 UX이고 안전 관문이 아니다(상한·비용 가드는 이 값과 무관).
    confirmed_long: bool | None = Field(default=None, strict=True)


def context_id_for(proposal_id: str) -> str:
    """생성 장부 키·미디어 폴더에 쓰는 사건 id. 기획안에서만 정해져 호출자가 달라도 같다."""
    return f"ctx_brief_{proposal_id}"


def blocked_reason(media: dict, settings: MediaSettings) -> str | None:
    """이 요청 초안으로 만들 수 없는 이유 코드(비용 전). 만들 수 있으면 None."""
    kind = media.get("media_kind")
    if kind == "carousel":
        return "carousel_items_undecided"
    if kind not in ("image", "video"):
        return "unknown_media_kind"
    if media.get("missing_fields"):
        return "fields_missing"
    if kind == "video" and settings.audio_mode == "bgm_overlay":
        return "bgm_fields_required"
    if kind == "video" and settings.video_max_duration_problem is not None:
        return settings.video_max_duration_problem
    return None


PLAN_FACTS_MAX = 2
PLAN_PRODUCTS_MAX = 2


def plan_fields_for(brief: CampaignBriefInput | None) -> dict[str, str]:
    """기획안의 구조화 필드 -> 영상 프롬프트용 `plan_*` 원재료. 비어 있는 칸은 넣지 않는다.

    쓰는 칸: idea(핵심 메시지)·why(이유)·facts[].label(근거 주제, 숫자 값 `value`는 쓰지 않는다)·
    target.label·products·content_category·idea_angle(방향 -> 고정 분위기 문구)·trend_refs.
    제목(title)·본문(body_md·note)·승인자·링크는 쓰지 않는다. 길이 상한·특수문자·금칙어 정리는
    프롬프트 만들 때(`media.prompts`) 한 번 더 거치므로 여기서는 고르기만 한다.
    """
    if brief is None:
        return {}
    raw: dict[str, str | None] = {
        "plan_idea": brief.idea,
        "plan_why": brief.why,
        "plan_evidence": " ".join(f.label for f in brief.facts[:PLAN_FACTS_MAX]),
        "plan_target": brief.target.label if brief.target else None,
        "plan_products": " ".join(brief.products[:PLAN_PRODUCTS_MAX]),
        "plan_category": brief.content_category,
        "plan_tone": brief.idea_angle,
        "plan_trends": " ".join(brief.trend_refs) if brief.idea_angle == "ride_trend" else None,
    }
    return {k: v for k, v in raw.items() if v and v.strip()}


def _supports_plan(media: dict) -> bool:
    try:
        return get_template(str(media.get("scene_template_id"))).plan_clause
    except PromptRejected:
        return False


def _generation_fields(media: dict, plan_fields: dict[str, str] | None = None) -> dict[str, Any]:
    """요청 초안 → 생성 입력의 공통 칸. 없는 값은 넣지 않는다(템플릿 기본값을 쓴다).

    `plan_fields`는 기획안에서 고른 `plan_*` 원재료다. 문장 자리(`{plan_clause}`)가 있는
    템플릿(영상)에만 붙고, 나머지는 전과 똑같다.
    """
    kind = media["media_kind"]
    out: dict[str, Any] = {
        "media_kind": kind,
        "scene_template_id": media.get("scene_template_id"),
        "fields": dict(media.get("fields") or {}),
    }
    if plan_fields and _supports_plan(media):
        out["fields"] |= plan_fields
    if media.get("resolution"):
        out["resolution"] = media["resolution"]
    if kind == "image" and media.get("quality"):
        out["quality"] = media["quality"]
    if kind == "video" and media.get("duration_s"):
        out["duration_s"] = media["duration_s"]
    return out


def prepare_input(
    proposal_id: str, media: dict, plan_fields: dict[str, str] | None = None
) -> dict[str, Any]:
    """`prepare_sns_post_from_media` 입력. 자유 프롬프트는 없다(템플릿 id + 화이트리스트 필드)."""
    return {"proposal_id": proposal_id, "media_ref": media.get("media_ref")} | _generation_fields(
        media, plan_fields
    )


def _inner(
    request: AgentRequest, proposal_id: str, skill: str, data: dict, context_id: str | None = None
) -> AgentRequest:
    meta = request.meta.model_copy(update={"context_id": context_id or context_id_for(proposal_id)})
    return request.model_copy(update={"skill": skill, "input": data, "meta": meta})


def estimate(
    request: AgentRequest,
    proposal_id: str,
    media: dict,
    *,
    ledger: MediaLedger,
    settings: MediaSettings | None = None,
    context_id: str | None = None,
    plan_fields: dict[str, str] | None = None,
) -> dict[str, Any]:
    """지금 요청 초안으로 만들 때의 예상 비용과 상한. 외부 호출 없이 단가표·장부만 읽는다.

    `status`: `ok`(만들 수 있음) | `blocked`(`reason_code`). `settled`면 이미 끝난 생성이라 다시
    눌러도 비용이 나지 않는다. 모르는 값은 None으로 둔다. `context_id`는 장부 키를 만드는 사건 id를
    기획안 말고 다른 출처(장애 초안)가 정할 때만 준다.
    """
    settings = settings or MediaSettings.from_env()
    kind = media.get("media_kind")
    out: dict[str, Any] = {
        "status": "blocked",
        "reason_code": None,
        "media_kind": kind,
        "cost_krw": None,
        "settled": False,
        "basis": None,
        "per_item_limit_krw": settings.max_cost_krw_per_item,
        "monthly_budget_krw": settings.monthly_budget_krw,
        "month_total_krw": None,
        "video": None,
    }
    try:
        out["month_total_krw"] = ledger.month_total(datetime.now(UTC).strftime("%Y-%m"))
    except LedgerError:
        return out | {"reason_code": "storage_error"}
    reason = blocked_reason(media, settings)
    if reason is not None:
        return out | {"reason_code": reason}
    gen_input = {"post_draft_id": media.get("media_ref")} | _generation_fields(media, plan_fields)
    if kind == "video" and (gen_input.get("duration_s") or 0) > settings.video_max_duration_s:
        # 상한을 낮춘 뒤라면 상한 길이로 계산해 길이 표를 준다(고르는 값이 아니라 조회용)
        gen_input["duration_s"] = settings.video_max_duration_s
    plan = plan_cost(
        _inner(request, proposal_id, "generate_media", gen_input, context_id),
        ledger=ledger,
        settings=settings,
        allow_video=True,
        with_table=True,
    )
    if kind == "video":
        current = gen_input.get("duration_s")
        if not current:  # 길이를 안 정한 요청 초안은 템플릿 길이(설정 상한 안으로)가 기본이다
            template_s = get_template(str(media.get("scene_template_id"))).duration_s
            current = min(template_s, settings.video_max_duration_s) if template_s else None
        out["video"] = _video_info(current, settings, plan)
    if isinstance(plan, AgentResult):
        cost = (plan.output or {}).get("cost_krw")
        return out | {"reason_code": plan.outcome, "cost_krw": cost}
    out["cost_krw"] = plan.krw_est
    out["settled"] = plan.settled
    provider = settings.provider(kind)
    out["basis"] = "ledger" if plan.settled else ("mock" if provider == MOCK else "price_table")
    budget = settings.monthly_budget_krw
    if not plan.settled and budget is not None and out["month_total_krw"] + plan.krw_est > budget:
        return out | {"reason_code": "budget_exceeded"}
    return out | {"status": "ok"}


def _video_info(current: int | None, settings: MediaSettings, plan: Any) -> dict[str, Any]:
    """화면의 길이 입력용. 상한은 운영자가 정한 설정값이고 `absolute_max_s`는 코드 절대 상한이다."""
    if isinstance(plan, AgentResult):  # 건당 상한 초과로 막혀도 다른 길이를 고를 수 있게 표는 준다
        table = (plan.output or {}).get("by_duration")
        clips = (plan.output or {}).get("clips_by_duration")
    else:
        table = plan.by_duration
        clips = plan.clips_by_duration
    return {
        "min_s": 1,
        "max_s": settings.video_max_duration_s,
        "absolute_max_s": ABSOLUTE_MAX_DURATION_S,
        # 화면 경고창 기준(UX). 이 길이를 넘으면 화면이 시작 전에 확인을 받는다. 안전 관문이 아니다
        "warn_duration_s": settings.video_warn_duration_s,
        "current_s": current,
        "cost_by_duration": {str(k): v for k, v in (table or {}).items()},
        "clips_by_duration": {str(k): v for k, v in (clips or {}).items()},
    }


def _failed(request: AgentRequest, outcome: str, **output: Any) -> AgentResult:
    return AgentResult(
        meta=request.meta,
        status=TaskStatus.FAILED,
        outcome=outcome,
        output={"stage": "preflight"} | output,
    )


def _prepared_record(
    sha: str, output: dict[str, Any], now: datetime, confirmed_long: bool | None = None
) -> dict[str, Any]:
    check = output.get("check") or {}
    generation = output.get("generation") or {}
    label = output.get("label") or {}
    return {
        "request_sha": sha,
        "post_draft_id": output.get("post_draft_id"),
        "stage": output.get("stage"),
        "media_kind": output.get("media_kind"),
        "asset_ref": output.get("asset_ref"),
        "asset_sha256": output.get("asset_sha256"),
        "cost_krw": generation.get("total_cost_krw", generation.get("cost_krw")),
        # 기획 데이터를 반영했으면 쓴 필드 이름·길이·해시(값 전체 아님). 옛 기록에는 없다
        "plan_context": generation.get("plan_context"),
        "label_text": label.get("text"),
        "check": {
            "verdict": check.get("verdict"),
            "publishable": check.get("publishable"),
            "problems": list(check.get("problems") or []),
        },
        "prepared_at": now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        # 사람이 긴 영상 경고창에서 '계속'을 눌렀다고 화면이 알린 값(감사용, 판정에 쓰지 않는다)
        "confirmed_long": confirmed_long,
    }


def _state(record: SnsHandoffRecord, store: SnsStore) -> dict[str, Any]:
    draft = store.get(record.draft_id or "")
    if draft is None:
        draft_state = "missing"
    else:
        draft_state = "media_attached" if (draft.asset_ref or draft.assets) else "caption_only"
    return state_of(record, draft_state).model_dump(mode="json")


async def prepare_brief_media(
    request: AgentRequest,
    *,
    briefs: BriefStore,
    notices: NoticeStore,
    store: SnsStore,
    ledger: Any,
    from_decision: FromDecision,
    settings: MediaSettings | None = None,
    prepare: PrepareFn = prepare_sns_post_from_media,
) -> AgentResult:
    try:
        data = PrepareBriefMediaInput.model_validate(request.input)
    except ValidationError as error:
        return _failed(
            request,
            "invalid_input",
            errors=[f"{'.'.join(map(str, e['loc']))}: {e['type']}" for e in error.errors()],
        )
    pid = data.proposal_id
    try:
        if briefs.existing(pid) is None:
            return _failed(request, "brief_not_found")
        record = briefs.handoff(pid)
    except BriefStorageError:
        return _failed(request, "storage_error")
    media = record.media_request if record is not None else None
    if record is None or record.status != "handed" or not media:
        return _failed(request, "not_handed")
    settings = settings or MediaSettings.from_env()
    reason = blocked_reason(media, settings)
    if reason is not None:
        return _failed(request, reason, sns_handoff=_state(record, store))
    if data.duration_s is not None:
        if media.get("media_kind") != "video":
            return _failed(request, "invalid_input", errors=["duration_s: video_only"])
        if data.duration_s > settings.video_max_duration_s:
            return _failed(
                request,
                "duration_over_limit",
                duration_s=data.duration_s,
                limit_s=settings.video_max_duration_s,
            )
        if data.duration_s != media.get("duration_s"):
            # 사람이 고른 길이를 요청 초안에 남긴다(해시·비용 장부 키가 같이 바뀐다)
            media = media | {"duration_s": data.duration_s}
            record = record.model_copy(update={"media_request": media})
            try:
                briefs.save_handoff(record)
            except BriefStorageError:
                return _failed(request, "storage_error")

    sha = request_sha(media)
    done = record.prepared if isinstance(record.prepared, dict) else None
    if (
        done is not None
        and done.get("request_sha") == sha
        and done.get("stage") == "draft_ready"
        and store.get(str(done.get("post_draft_id") or "")) is not None
    ):  # 이미 준비된 초안: 생성·변환을 다시 돌리지 않는다
        return AgentResult(
            meta=request.meta,
            status=TaskStatus.COMPLETED,
            outcome="already_prepared",
            output={
                "stage": "draft_ready",
                "post_draft_id": done.get("post_draft_id"),
                "sns_handoff": _state(record, store),
            },
            evidence=Evidence(
                queries=[ACTION],
                data_summary=f"기획안 {pid} 미디어 초안이 이미 준비돼 있음",
                collected_at=datetime.now(UTC),
                ref_ids=[pid, str(done.get("post_draft_id"))],
            ),
        )

    stored = briefs.existing(pid)
    plan_fields = plan_fields_for(stored.brief if stored else None)
    result = await prepare(
        _inner(request, pid, "prepare_sns_post_from_media", prepare_input(pid, media, plan_fields)),
        notices=notices,
        store=store,
        ledger=ledger,
        from_decision=from_decision,
        settings=settings,
        briefs=briefs,
    )
    output = dict(result.output or {})
    latest = record
    if result.status == TaskStatus.COMPLETED and output.get("stage") in READY_STAGES:
        try:
            # 만드는 동안 요청 초안이 바뀌었을 수 있다. 최신 기록에 `prepared`만 덧붙인다
            latest = briefs.handoff(pid) or record
            latest = latest.model_copy(
                update={
                    "prepared": _prepared_record(
                        sha, output, datetime.now(UTC), data.confirmed_long
                    )
                }
            )
            briefs.save_handoff(latest)
        except BriefStorageError:
            return _failed(
                request,
                "storage_error",
                stage="record",
                post_draft_id=output.get("post_draft_id"),
                note="미디어 초안은 만들어졌다. 다시 부르면 생성은 장부에서 재생된다",
            )
    output["sns_handoff"] = _state(latest, store)
    return result.model_copy(update={"meta": request.meta, "output": output})
