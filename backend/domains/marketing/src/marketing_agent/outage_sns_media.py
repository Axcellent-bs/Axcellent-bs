"""장애 사과·보상 SNS 글 초안에 이미지를 붙여 게시 요청까지 잇는 skill 2개.

- `prepare_outage_sns_media`: 이미지 생성 → 규격 변환 → AI 라벨 → 초안에 붙임 → 초안 검사
- `request_outage_sns_publish`: 붙인 초안으로 `publish_sns`(C등급 승인 대기 건) 요청

화면 카드의 "이 초안으로 진행"을 사람이 한 번 누르면 화면이 두 skill을 차례로 부른다.
그 뒤 같은 카드에
승인 패널이 뜨고, 사람이 이미지·최종 캡션을 보고 승인해야(두 번째 사람 결정) 게시된다.
첫 클릭은 게시 권한이 아니라 "이미지 생성 진행 확인"이다. 게시 자동 승인·승인 우회·만료 연장은 없다.

입력은 초안 id(`post_draft_id`)뿐이다. 장면 템플릿(`cat_apology_image`)·필드·규격은 코드 고정값이고
호출자가 정하지 못한다(자유 프롬프트 없음). 초안 캡션은 건드리지 않는다: 이미지를 붙인 **사본**을 새
초안으로 저장하고(`media_from`이 원본 id), 게시 대상은 그 사본이다. 캡션이 같아서 AI 표시·금지어
점검
결과는 그대로 유효하고, 사본 점검(`check_sns_post`)은 미디어까지 다시 본다.

생성은 기존 `generate_media_flow`를 그대로 거친다(B등급 안전 관문·건당 상한·월 상한·단가표
fail-closed·
장부 예약). 멱등: `context_id`·생성 묶음 id를 초안 id에서만 정하므로(`ctx_outage_<초안 id>`)
누가 몇 번
불러도 같은 장부 키다. 끝난 생성은 장부에서 재생되어 비용이 다시 나지 않고, 만드는 중이면
`in_progress`다.

진행할 수 없는 초안(비용 전에 막힌다): 이미 병합된 초안(`already_merged`), 게시 요청 이력이 있는
초안
(`publish_history`), 검사를 통과하지 못한 초안(`check_failed`), 영상 초안(`video_not_supported`,
이번 범위는
이미지만), 장애 초안이 아닌 것(`not_outage_draft`).

게시 요청의 상태 조회·만료 뒤 사람 재요청은 `brief_publish`의 상태·재시도 로직을 그대로 쓴다
(`state_from_chain`·`submit_publish`). 게시할 값(초안·파일·해시·캡션)은 서버 보관본에서 읽고
브라우저가
정하지 못한다.
"""

from __future__ import annotations

import asyncio
import hashlib
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import Field, ValidationError

from ax_contracts.schemas import AgentRequest, AgentResult, Evidence, TaskStatus
from ax_contracts.schemas.common import AxModel
from marketing_agent import brief_media, sns, sns_media
from marketing_agent.brief_publish import state_from_chain, submit_publish
from marketing_agent.media.audio_mix import Runner, subprocess_runner
from marketing_agent.media.bgm_flow import generate_media_flow
from marketing_agent.media.config import MediaSettings
from marketing_agent.media.skill import FromDecision
from marketing_agent.outage_sns import SOURCE_KIND, passes_checks
from marketing_agent.sns import SnsPostDraft, SnsStore
from marketing_agent.sns_prepare import (
    PrepareError,
    _convert_and_label,
    _is_inside,
    preflight_tools,
)
from marketing_agent.sns_publish import pending as pend
from marketing_agent.sns_publish import publisher

MEDIA_ACTION = "prepare_outage_sns_media"
PUBLISH_ACTION = "request_outage_sns_publish"
# 장면은 코드 고정값이다. 템플릿 문장은 `media/scene_templates.toml`에서 사람이 검토해 바꾼다
TEMPLATE_ID = "cat_apology_image"
TEMPLATE_FIELDS: dict[str, str] = {"color_theme": "soft pastel"}
DRAFT_ID = r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$"
PREVIEW_PATH = "/api/dashboard/marketing/outage-sns-drafts/{draft_id}/media/preview"
PendingGet = Callable[[str], pend.PendingPost | None]
Process = Callable[..., Any]
Check = Callable[..., Any]


class PrepareOutageSnsMediaInput(AxModel):
    post_draft_id: str = Field(pattern=DRAFT_ID)


class RequestOutageSnsPublishInput(AxModel):
    post_draft_id: str = Field(pattern=DRAFT_ID)
    # 만료로 닫힌 건을 사람이 다시 요청한다. 만료가 아니면 retry_* 코드로 거부한다
    retry: bool = False


def context_id_for(draft_id: str) -> str:
    """생성 장부 키·미디어 폴더·게시 멱등 키에 쓰는 사건 id. 초안 id에서만 정한다."""
    return f"ctx_outage_{draft_id}"


def media_input(draft_id: str) -> dict[str, Any]:
    """`generate_media` 입력. 템플릿·필드·묶음 id가 모두 코드/초안 id에서 정해진다."""
    return {
        "media_kind": "image",
        "scene_template_id": TEMPLATE_ID,
        "fields": dict(TEMPLATE_FIELDS),
        "post_draft_id": draft_id,
    }


def blocked_reason(draft: SnsPostDraft | None) -> str | None:
    """이 초안으로 진행할 수 없는 이유 코드(비용 전, 저장된 값만 본다). 진행할 수 있으면 None."""
    if draft is None:
        return "draft_not_found"
    if draft.source_kind != SOURCE_KIND or draft.outage_trigger is None or draft.media_from:
        return "not_outage_draft"
    if draft.status == "merged":
        return "already_merged"
    if draft.asset_ref or draft.assets or draft.asset_sha256:
        return "media_attached"
    if draft.media_kind != "image":
        return "video_not_supported"
    if not passes_checks(draft):
        return "check_failed"
    return None


def _has_publish_history(*drafts: SnsPostDraft | None) -> bool:
    """게시 요청(승인 대기·게시 중·게시됨·거절·만료 포함) 기록이 있는가. 읽지 못하면 예외."""
    return any(bool(publisher.store().with_draft(d.post_draft_id)) for d in drafts if d is not None)


def _check_ok(draft: SnsPostDraft) -> bool:
    check = draft.media_check or {}
    return check.get("verdict") == "approved" and check.get("publishable") is True


def _caption_intact(draft: SnsPostDraft) -> bool:
    return hashlib.sha256(draft.caption.encode()).hexdigest() == draft.caption_sha256


def publish_input_for(
    origin: SnsPostDraft, derived: SnsPostDraft | None
) -> tuple[dict | None, str]:
    """보관본(이미지를 붙인 사본)에서 `publish_sns` 입력을 만든다.

    (입력, "") 또는 (None, 이유 코드).
    """
    if derived is None:
        return None, "not_prepared"
    if not _check_ok(derived):
        return None, "check_not_passed"
    ref, sha = derived.asset_ref, derived.asset_sha256
    if (
        derived.media_kind != "image"
        or not (ref and sha)
        or derived.media_from != origin.post_draft_id
        or derived.caption != origin.caption  # 캡션은 원본 그대로여야 한다
        or derived.caption_sha256 != origin.caption_sha256
        or not _caption_intact(derived)
    ):
        return None, "draft_changed"
    head = derived.caption.strip().replace("\n", " ")[:80]
    summary = (
        f"장애 사과·보상 안내 이미지 Instagram 게시 요청 (AI 표시 포함, 사람 승인 필요): {head}"
    )[:200]
    return {
        "post_draft_id": derived.post_draft_id,
        "asset_ref": ref,
        "asset_sha256": sha,
        "caption": derived.caption,
        "media_kind": "image",
        "share_to_feed": True,
        "summary": summary,
    }, ""


def publish_state(
    origin: SnsPostDraft, derived: SnsPostDraft | None, get: PendingGet | None = None
) -> dict[str, Any] | None:
    """조회용 게시 요청 상태(`brief_publish.publish_state`와 같은 모양). 미디어 초안이 없으면 None.

    `proposal_id`는 화면 승인 패널이 카드를 구분하는 값이라 장애 초안에서는 원본 초안 id를 넣는다.
    """
    if derived is None:
        return None
    data, reason = publish_input_for(origin, derived)
    base: dict[str, Any] = {
        "status": "not_requested",
        "can_request": False,
        "reason_code": reason or None,
        "review_id": None,
        "failure_code": None,
        "permalink": None,
        "caption": None,
        "proposal_id": origin.post_draft_id,
        "expires_at": None,
        "preview_url": None,
        "preview_kind": None,
        "ai_label": None,
        "rejected_cause": None,
        "attempt": 0,
        "can_retry": False,
        "retry_reason_code": None,
    }
    if data is None:
        return base
    base["caption"] = data["caption"]  # 게시될 글. 화면은 고칠 수 없다
    base["preview_url"] = PREVIEW_PATH.format(draft_id=origin.post_draft_id)
    base["preview_kind"] = "image"
    base["ai_label"] = sns.ai_label_text("image")
    return state_from_chain(base, data, context_id_for(origin.post_draft_id), get)


def _prepared(derived: SnsPostDraft) -> dict[str, Any]:
    check = derived.media_check or {}
    return {
        "post_draft_id": derived.post_draft_id,
        "stage": "draft_ready" if _check_ok(derived) else "check_rejected",
        "media_kind": derived.media_kind,
        "asset_ref": derived.asset_ref,
        "asset_sha256": derived.asset_sha256,
        "label_text": sns.ai_label_text("image"),
        "check": {
            "verdict": check.get("verdict"),
            "publishable": check.get("publishable"),
            "problems": list(check.get("problems") or []),
        },
        "prepared_at": check.get("checked_at"),
    }


def flow_state(
    request: AgentRequest,
    origin: SnsPostDraft,
    store: SnsStore,
    *,
    ledger: Any | None = None,
    settings: MediaSettings | None = None,
    get: PendingGet | None = None,
) -> dict[str, Any]:
    """카드 한 장의 진행 상태. 저장값·단가표·장부·승인 대기 저장소에서 읽기만 한다.

    `stage`: not_started | draft_ready(이미지 준비됨) | check_rejected. `can_proceed`가 참일 때만
    화면이 "이 초안으로 진행" 버튼을 둔다. `estimate`는 조회 때 계산한 예상 비용·상한(저장 안 함).
    """
    derived = store.find_derived(origin.post_draft_id)
    out: dict[str, Any] = {
        "draft_id": origin.post_draft_id,
        "stage": "not_started",
        "can_proceed": False,
        "reason_code": None,
        "estimate": None,
        "prepared": None,
        "publish": None,
    }
    if derived is not None:
        prepared = _prepared(derived)
        out["stage"] = prepared["stage"]
        out["prepared"] = prepared
        try:
            out["publish"] = publish_state(origin, derived, get)
        except Exception:  # noqa: BLE001 - 조회는 돌려준다. 화면은 게시 요청 버튼을 두지 않는다
            out["publish"] = None
        return out
    reason = blocked_reason(origin)
    if reason is None:
        try:
            if _has_publish_history(origin):
                reason = "publish_history"
        except Exception:  # noqa: BLE001 - 읽지 못하면 진행하지 않는다(fail-closed)
            reason = "storage_error"
    out["reason_code"] = reason
    if reason is not None or ledger is None:
        out["can_proceed"] = reason is None and ledger is not None
        return out
    try:
        estimate = brief_media.estimate(
            request,
            origin.post_draft_id,
            media_input(origin.post_draft_id) | {"media_ref": origin.post_draft_id},
            ledger=ledger,
            settings=settings,
            context_id=context_id_for(origin.post_draft_id),
        )
    except Exception:  # noqa: BLE001 - 비용을 모르면 진행하지 않는다
        out["reason_code"] = "estimate_unavailable"
        return out
    out["estimate"] = estimate
    if estimate["status"] == "ok":
        out["can_proceed"] = True
    else:
        out["reason_code"] = estimate["reason_code"]
    return out


def _failed(request: AgentRequest, outcome: str, **output: Any) -> AgentResult:
    return AgentResult(
        meta=request.meta,
        status=TaskStatus.FAILED,
        outcome=outcome,
        output={"stage": "preflight"} | output,
    )


def _invalid(request: AgentRequest, error: ValidationError) -> AgentResult:
    return _failed(
        request,
        "invalid_input",
        errors=[f"{'.'.join(map(str, e['loc']))}: {e['type']}" for e in error.errors()],
    )


def _with_ctx(request: AgentRequest, draft_id: str, skill: str, data: dict) -> AgentRequest:
    meta = request.meta.model_copy(update={"context_id": context_id_for(draft_id)})
    return request.model_copy(update={"skill": skill, "input": data, "meta": meta})


async def prepare_outage_sns_media(
    request: AgentRequest,
    *,
    store: SnsStore,
    ledger: Any,
    from_decision: FromDecision,
    settings: MediaSettings | None = None,
    runner: Runner = subprocess_runner,
    process: Process = _convert_and_label,
    preflight: Callable[[], str | None] = preflight_tools,
    check: Check = sns.check_sns_post,
) -> AgentResult:
    try:
        data = PrepareOutageSnsMediaInput.model_validate(request.input)
    except ValidationError as error:
        return _invalid(request, error)
    did = data.post_draft_id
    origin = store.get(did)
    if origin is None:
        return _failed(request, "draft_not_found")
    if origin.source_kind != SOURCE_KIND or origin.outage_trigger is None or origin.media_from:
        return _failed(request, "not_outage_draft")
    settings = settings or MediaSettings.from_env()

    derived = store.find_derived(did)
    if derived is not None and _check_ok(derived):  # 이미 준비됨: 생성·변환을 다시 돌리지 않는다
        return AgentResult(
            meta=request.meta,
            status=TaskStatus.COMPLETED,
            outcome="already_prepared",
            output={
                "stage": "draft_ready",
                "post_draft_id": did,
                "media_flow": flow_state(request, origin, store, ledger=ledger, settings=settings),
            },
            evidence=Evidence(
                queries=[MEDIA_ACTION],
                data_summary=f"장애 초안 {did} 이미지가 이미 준비돼 있음",
                collected_at=datetime.now(UTC),
                ref_ids=[did, derived.post_draft_id],
            ),
        )

    reason = blocked_reason(origin)
    if reason is not None:
        return _failed(request, reason)
    try:
        if _has_publish_history(origin):
            return _failed(request, "publish_history")
    except Exception:  # noqa: BLE001 - 게시 상태를 읽지 못하면 진행하지 않는다
        return _failed(request, "storage_error")

    # 생성 전 확인. 여기서 막히면 비용이 나지 않는다
    root = sns_media.media_root()
    if root is None:
        return _failed(request, "media_dir_not_configured")
    if not _is_inside(settings.media_dir, root):
        return _failed(request, "media_dir_mismatch")
    missing_tool = preflight()
    if missing_tool is not None:
        return _failed(request, "post_tools_unavailable", reason=missing_tool)

    # 1) 생성. 안전 관문(B)·건당/월 상한·단가표는 generate_media_flow가 그대로 적용한다
    generated = await generate_media_flow(
        _with_ctx(request, did, "generate_media", media_input(did)),
        ledger=ledger,
        from_decision=from_decision,
        settings=settings,
    )
    if generated.outcome != "generated" or generated.output is None:
        # 보류·거절·상한·공급자 실패를 그대로 돌려준다. 같은 입력으로 다시 부르면 생성은 재생된다
        return generated.model_copy(
            update={
                "meta": request.meta,
                "output": (generated.output or {}) | {"stage": "generate_media"},
            }
        )
    gen = generated.output

    # 2~3) 규격 변환·AI 라벨 (파일 작업이라 스레드에서)
    asset = Path(gen["asset_ref"])
    if not _is_inside(asset, root):
        return _failed(request, "media_dir_mismatch", stage="generate_media")
    try:
        origin_sha = sns_media.sha256_file(asset)
        _, label, _ = await asyncio.to_thread(
            process, asset, asset.parent, "image", gen["model_id"], origin_sha, runner
        )
    except PrepareError as error:
        return _failed(
            request,
            f"{error.stage}_failed",
            stage=error.stage,
            reason=error.code,
            media_id=gen["media_id"],
            cost_krw=gen.get("total_cost_krw", gen["cost_krw"]),
            note="생성 비용은 이미 장부에 남았다. 같은 입력으로 다시 부르면 생성은 재생된다",
        )

    # 4) 초안에 이미지 붙이기(캡션은 그대로, 사본 저장) → 점검
    labeled_ref = sns_media.asset_ref_for(label.labeled_path)
    asset_sha = sns_media.sha256_file(label.labeled_path)
    media_draft = origin.model_copy(
        update={
            "post_draft_id": sns.content_id(
                origin.caption_sha256, asset_sha, origin.media_kind, origin.platform, {}
            ),
            "asset_ref": labeled_ref,
            "asset_sha256": asset_sha,
            "media_from": did,
            "media_check": None,
            "status": "draft",
            "merged_into": None,
            "merged_from": [],
            "merged_at": None,
        }
    )
    store.save(media_draft)
    checked = await check(
        request.model_copy(
            update={
                "skill": "check_sns_post",
                "input": {
                    "post_draft_id": media_draft.post_draft_id,
                    "caption": media_draft.caption,
                    "is_ai_generated": True,
                },
            }
        ),
        store,
    )
    verdict = checked.output or {}
    ready = verdict.get("verdict") == "approved" and verdict.get("publishable") is True
    store.update_meta(
        [
            media_draft.model_copy(
                update={
                    "media_check": {
                        "verdict": verdict.get("verdict"),
                        "publishable": verdict.get("publishable"),
                        "problems": list(verdict.get("problems") or [])[:20],
                        "checked_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
                    }
                }
            )
        ]
    )
    output = {
        "stage": "draft_ready" if ready else "check_rejected",
        "post_draft_id": did,
        "media_draft_id": media_draft.post_draft_id,
        "media_kind": "image",
        "generation": {
            "media_id": gen["media_id"],
            "provider": gen["provider"],
            "model_id": gen["model_id"],
            "resolution": gen["resolution"],
            "cost_krw": gen["cost_krw"],
            "total_cost_krw": gen.get("total_cost_krw", gen["cost_krw"]),
            "replayed": bool(gen.get("replayed")),
        },
        "label": {"text": label.text},
        "check": {
            "verdict": verdict.get("verdict"),
            "publishable": verdict.get("publishable"),
            "problems": list(verdict.get("problems") or [])[:20],
        },
        "next_step": "publish_sns는 C등급이라 사람 승인이 필요하다. 이 skill은 게시하지 않는다.",
    }
    latest = store.get(did) or origin
    output["media_flow"] = flow_state(request, latest, store, ledger=ledger, settings=settings)
    return AgentResult(
        meta=request.meta,
        status=TaskStatus.COMPLETED,
        outcome=None if ready else "rejected_by_check",
        output=output,
        evidence=Evidence(
            queries=[MEDIA_ACTION],
            data_summary=f"장애 초안 이미지 생성·변환·라벨·점검 ({'통과' if ready else '거절'})",
            collected_at=datetime.now(UTC),
            ref_ids=[did, media_draft.post_draft_id, gen["media_id"], gen["execution_id"]],
        ),
        fallback=True,  # 캡션은 LLM이 아니라 고정 문장이다(`draft_outage_sns_post`)
    )


async def request_outage_sns_publish(
    request: AgentRequest,
    *,
    store: SnsStore,
    publish: Callable[..., Any] = publisher.publish_sns,
    check: Check = sns.check_sns_post,
) -> AgentResult:
    try:
        data = RequestOutageSnsPublishInput.model_validate(request.input)
    except ValidationError as error:
        return _invalid(request, error)
    did = data.post_draft_id

    def fail(outcome: str, **output: Any) -> AgentResult:
        return AgentResult(
            meta=request.meta, status=TaskStatus.FAILED, outcome=outcome, output=output
        )

    origin = store.get(did)
    if origin is None:
        return fail("draft_not_found")
    if origin.source_kind != SOURCE_KIND or origin.outage_trigger is None or origin.media_from:
        return fail("not_outage_draft")
    derived = store.find_derived(did)
    publish_input, reason = publish_input_for(origin, derived)
    if publish_input is None:
        return fail(reason)
    # 요청 직전에 점검을 다시 돌린다. 통과한 값만 승인을 받으러 간다(우회·완화 없음)
    checked = await check(
        request.model_copy(
            update={
                "skill": "check_sns_post",
                "input": {
                    "post_draft_id": publish_input["post_draft_id"],
                    "caption": publish_input["caption"],
                    "is_ai_generated": True,
                },
            }
        ),
        store,
    )
    verdict = checked.output or {}
    if verdict.get("verdict") != "approved" or verdict.get("publishable") is not True:
        return fail("check_not_passed", problems=list(verdict.get("problems") or [])[:20])

    return await submit_publish(
        request,
        context_id=context_id_for(did),
        ref_id=did,
        label=f"장애 초안 {did}",
        publish_input=publish_input,
        retry=data.retry,
        publish=publish,
        state_of=lambda: publish_state(origin, derived),
        store=store,
    )


__all__ = [
    "MEDIA_ACTION",
    "PUBLISH_ACTION",
    "PrepareOutageSnsMediaInput",
    "RequestOutageSnsPublishInput",
    "flow_state",
    "prepare_outage_sns_media",
    "request_outage_sns_publish",
]
