"""마케팅 캠페인 skill 호출 (A2A). 화면의 캠페인 시작·결과·취소가 쓴다.

재무 판단과 안전 관문은 마케팅 skill 안에서 일어난다. 보류·거절은 오류가 아니라 판단 결과라
화면이 이유를 보여 줄 수 있게 그대로 돌려준다. 닿지 못한 경우만 API 오류로 바꾼다.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from ax_agent_template import config
from ax_agent_template.client import call_agent
from ax_contracts.schemas import AgentRequest, AgentResult, RequestMeta, TaskStatus
from ax_dashboard_api.envelope import ApiError, tenant_meta

AGENT_NAME = "dashboard-api"
TARGET = "marketing"
DEADLINE_SECONDS = 60
VIDEO_TIMEOUT_ENV = "DASHBOARD_VIDEO_TIMEOUT_S"
DEFAULT_VIDEO_TIMEOUT_S = 360  # 영상 생성은 동기로 길어질 수 있다

SendFn = Callable[[str, AgentRequest], Awaitable[AgentResult]]
SEND: SendFn = call_agent  # 테스트에서 바꿀 수 있다


async def call(
    skill: str,
    payload: dict[str, Any],
    *,
    campaign_id: str,
    key: str,
    deadline_s: float = DEADLINE_SECONDS,
) -> dict[str, Any]:
    config.set_agent_name(AGENT_NAME)
    request = AgentRequest(
        meta=RequestMeta(
            context_id=f"ctx_cmp_{campaign_id}",
            token=config.agent_token(AGENT_NAME),
            deadline=datetime.now(UTC) + timedelta(seconds=deadline_s),
            idempotency_key=key,
            **tenant_meta(),
        ),
        skill=skill,
        input=payload,
    )
    result = await SEND(TARGET, request)
    if result.status == TaskStatus.REJECTED:
        raise ApiError(403, "UPSTREAM_REJECTED", f"마케팅이 요청을 거부했다 ({result.outcome})")
    if result.status == TaskStatus.CANCELED:
        raise ApiError(504, "UPSTREAM_TIMEOUT", "마케팅 에이전트가 제때 응답하지 않았다")
    return {"status": result.status.value, "outcome": result.outcome, "output": result.output}


# 기획안 넘기기 skill이 실패로 돌려주는 outcome → (HTTP 상태, 오류 코드).
# `handed`·`already_handed`·`failed`(이유 코드는 output에)는 판단 결과라 오류가 아니다
HANDOFF_FAILURES = {
    "brief_not_found": (404, "NOT_FOUND"),
    "invalid_input": (400, "INVALID_INPUT"),
    "storage_error": (503, "STORAGE_ERROR"),
}


async def handoff_call(
    proposal_id: str, media_kind: str, key: str, extra: dict[str, Any] | None = None
) -> dict[str, Any]:
    """받은 기획안을 SNS·미디어로 넘긴다. 미디어 생성·게시는 하지 않는다(마케팅 skill 쪽 규칙).

    `extra`는 관리자 선택(`fields`·`autofill`·`revise`)이다. 준 것만 그대로 넘긴다.
    """
    out = await call(
        "handoff_brief_to_sns",
        {"proposal_id": proposal_id, "media_kind": media_kind, **(extra or {})},
        campaign_id=f"cmp_{proposal_id}",
        key=key,
    )
    if out["status"] == TaskStatus.FAILED.value:
        status, code = HANDOFF_FAILURES.get(out["outcome"] or "", (502, "UPSTREAM_FAILED"))
        raise ApiError(status, code, f"기획안을 넘기지 못했다 ({out['outcome']})")
    return out


def video_timeout_s() -> float:
    try:
        value = float((os.getenv(VIDEO_TIMEOUT_ENV) or "").strip() or DEFAULT_VIDEO_TIMEOUT_S)
    except ValueError:
        return float(DEFAULT_VIDEO_TIMEOUT_S)
    return value if value > 0 else float(DEFAULT_VIDEO_TIMEOUT_S)


def _digest(payload: dict[str, Any]) -> str:
    raw = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


async def video_prepare_call(payload: dict[str, Any]) -> dict[str, Any]:
    """영상 게시물 준비(`prepare_sns_post_from_media`). 생성 비용·승인은 마케팅 skill이 판단한다.

    판단 결과(보류·실패 이유 등)는 오류가 아니라 그대로 돌려준다. 입력 거절만 400으로 바꾼다.
    """
    digest = _digest(payload)
    out = await call(
        "prepare_sns_post_from_media",
        payload,
        campaign_id=f"vid_{digest}",
        key=f"ui:video-prepare:{digest}",
        deadline_s=video_timeout_s(),
    )
    if out["status"] == TaskStatus.FAILED.value and out["outcome"] == "invalid_input":
        raise ApiError(400, "INVALID_INPUT", "영상 준비 입력이 맞지 않는다 (invalid_input)")
    return out


async def video_publish_call(publish_input: dict[str, Any]) -> dict[str, Any]:
    """영상 게시(`publish_sns`). C등급 승인 대기(`input-required`+review_id)를 그대로 돌려준다."""
    digest = _digest(publish_input)
    out = await call(
        "publish_sns",
        publish_input,
        campaign_id=f"vid_{digest}",
        key=f"ui:video-publish:{digest}",
        deadline_s=video_timeout_s(),
    )
    if out["status"] == TaskStatus.FAILED.value and out["outcome"] == "invalid_input":
        raise ApiError(400, "INVALID_INPUT", "게시 입력이 맞지 않는다 (invalid_input)")
    return out


# 기획안 미디어 만들기에서 화면이 설명할 수 없는(요청 자체가 틀린) 실패만 HTTP 오류로 바꾼다.
# 그 밖의 실패·보류(상한 초과·안전 장치 보류·미정 필드 등)는 판단 결과라 그대로 돌려준다
BRIEF_MEDIA_FAILURES = {
    "brief_not_found": (404, "NOT_FOUND"),
    "invalid_input": (400, "INVALID_INPUT"),
}
MEDIA_DIR_ENV = "MARKETING_MEDIA_DIR"  # 마케팅과 같은 폴더를 가리켜야 미리보기를 읽을 수 있다
PREVIEW_FILES = {"labeled.jpg": "image/jpeg", "labeled.mp4": "video/mp4"}  # AI 라벨을 입힌 결과만


def brief_media_context(proposal_id: str) -> str:
    return f"brfm_{proposal_id}"


async def brief_media_call(
    proposal_id: str, duration_s: int | None = None, *, confirmed_long: bool = False
) -> dict[str, Any]:
    """넘긴 기획안의 요청 초안으로 미디어 초안을 만든다(`prepare_brief_media`). 게시하지 않는다.

    종류·필드는 보내지 않는다(마케팅이 보관본에서 읽는다). 같은 기획안은 키가 같고, 마케팅이
    생성 장부로 중복 생성을 막는다. 영상 생성이 길어 마감은 영상 준비와 같다.
    `duration_s`: 사람이 고른 영상 길이(설정 상한 안인지는 마케팅이 판정). 길이마다 키가 다르다.
    `confirmed_long`: 화면의 긴 영상 경고창에서 '계속'을 눌렀다는 주장. 감사 기록용으로만 넘기고
    멱등 키에는 넣지 않는다(경고창은 UX일 뿐 안전 관문이 아니다: 상한·비용 가드는 마케팅이 본다).
    """
    payload: dict[str, Any] = {"proposal_id": proposal_id}
    key = f"ui:brief-media:{proposal_id}"
    if duration_s is not None:
        payload["duration_s"] = duration_s
        key += f":{duration_s}"
    if confirmed_long:
        payload["confirmed_long"] = True
    out = await call(
        "prepare_brief_media",
        payload,
        campaign_id=brief_media_context(proposal_id),
        key=key,
        deadline_s=video_timeout_s(),
    )
    if out["status"] == TaskStatus.FAILED.value and out["outcome"] in BRIEF_MEDIA_FAILURES:
        status, code = BRIEF_MEDIA_FAILURES[out["outcome"]]
        raise ApiError(status, code, f"미디어를 만들 수 없다 ({out['outcome']})")
    return out


async def brief_audio_call(
    proposal_id: str,
    choice: str,
    *,
    confirmed_listened: bool = False,
    bgm_mood: str | None = None,
    bgm_genre: str | None = None,
) -> dict[str, Any]:
    """만든 영상의 소리를 정한다(`choose_brief_audio`). 게시하지 않는다.

    `choice`: `keep`(이 영상 그대로 사용) | `bgm`(배경음악 따로 만들기). BGM 생성은 마케팅이
    안전 관문(B등급)·상한을 거쳐 처리하고, 보류·상한 초과는 판단 결과로 그대로 돌려준다.
    `confirmed_listened`는 화면의 "소리까지 들어 봤다"는 주장이며 감사 기록용으로만 넘긴다.
    같은 기획안·같은 선택은 키가 같아 승인된 보류 건이 이어진다(영상은 장부에서 재생).
    """
    payload: dict[str, Any] = {"proposal_id": proposal_id, "choice": choice}
    if confirmed_listened:
        payload["confirmed_listened"] = True
    if bgm_mood:
        payload["bgm_mood"] = bgm_mood
    if bgm_genre:
        payload["bgm_genre"] = bgm_genre
    out = await call(
        "choose_brief_audio",
        payload,
        campaign_id=brief_media_context(proposal_id),
        key=f"ui:brief-audio:{proposal_id}:{choice}",
        deadline_s=video_timeout_s(),
    )
    if out["status"] == TaskStatus.FAILED.value and out["outcome"] in BRIEF_MEDIA_FAILURES:
        status, code = BRIEF_MEDIA_FAILURES[out["outcome"]]
        raise ApiError(status, code, f"영상 소리를 정할 수 없다 ({out['outcome']})")
    return out


async def brief_publish_request_call(proposal_id: str, retry: bool = False) -> dict[str, Any]:
    """준비된 미디어 초안의 게시 요청(`request_brief_publish`). 승인 대기 건만 만든다.

    본문이 없다: 초안·파일·해시·캡션은 마케팅이 보관본에서 읽는다. 게시는 사람이 승인 화면에서
    진행을 눌러야만 이어진다(여기서 승인하지 않는다). 막힘·상한·거절은 판단 결과라 그대로 돌려준다.
    `retry`는 사람이 누른 "다시 요청"이다(만료로 닫힌 건만, 마케팅이 판정한다). 호출마다 키가 달라
    마케팅이 새 승인 건을 만들 수 있고, 같은 내용의 중복은 마케팅 저장소가 막는다.
    """
    payload: dict[str, Any] = {"proposal_id": proposal_id}
    key = f"ui:brief-publish:{proposal_id}"
    if retry:
        payload["retry"] = True
        key += f":retry:{uuid.uuid4().hex[:12]}"
    out = await call(
        "request_brief_publish",
        payload,
        campaign_id=brief_media_context(proposal_id),
        key=key,
    )
    if out["status"] == TaskStatus.FAILED.value and out["outcome"] in BRIEF_MEDIA_FAILURES:
        status, code = BRIEF_MEDIA_FAILURES[out["outcome"]]
        raise ApiError(status, code, f"게시를 요청할 수 없다 ({out['outcome']})")
    return out


PUBLISH_CONTROL_CONTEXT = "publish_control"


async def publish_control_get() -> dict[str, Any]:
    """Instagram 게시 일시 중지 스위치의 현재 상태(`get_publish_control`, 읽기)."""
    out = await call(
        "get_publish_control",
        {},
        campaign_id=PUBLISH_CONTROL_CONTEXT,
        key=f"ui:publish-control-get:{uuid.uuid4().hex[:12]}",
    )
    if out["status"] == TaskStatus.FAILED.value and out["outcome"] == "invalid_input":
        raise ApiError(400, "INVALID_INPUT", "게시 상태 요청이 맞지 않는다 (invalid_input)")
    return out


async def publish_control_set(paused: bool, reason: str, human: str) -> dict[str, Any]:
    """게시를 멈추거나 푼다(`set_publish_control`). 안전 관문: 중지는 A, 재개는 C(사람 승인).

    `human`은 사람 토큰에서 채운 이름이다(본문에서 받지 않는다).
    재개가 보류(`held`)로 오면 판단 결과라 그대로 돌려준다.
    승인한 뒤 같은 요청을 다시 보내면 마케팅이 적용한다(키는 마케팅이 정한다).
    """
    out = await call(
        "set_publish_control",
        {"channel": "instagram", "paused": paused, "reason": reason, "updated_by": human},
        campaign_id=PUBLISH_CONTROL_CONTEXT,
        key="ui:publish-control-set",
    )
    if out["status"] == TaskStatus.FAILED.value and out["outcome"] == "invalid_input":
        raise ApiError(400, "INVALID_INPUT", "게시 상태 입력이 맞지 않는다 (invalid_input)")
    return out


VIDEO_PROVIDERS_CONTEXT = "video_providers"
VIDEO_PROVIDER_FAILURES = {
    "invalid_input": (400, "INVALID_INPUT"),
    "invalid_spec": (400, "INVALID_SPEC"),
    "unknown_provider": (404, "NOT_FOUND"),
    "not_found": (404, "NOT_FOUND"),
    "provider_disabled": (409, "PROVIDER_DISABLED"),
    "model_not_priced": (409, "MODEL_NOT_PRICED"),
    "too_many_custom_providers": (409, "TOO_MANY"),
}


async def _video_provider_call(skill: str, payload: dict[str, Any], key: str) -> dict[str, Any]:
    out = await call(skill, payload, campaign_id=VIDEO_PROVIDERS_CONTEXT, key=key)
    if out["status"] == TaskStatus.FAILED.value and out["outcome"] in VIDEO_PROVIDER_FAILURES:
        status, code = VIDEO_PROVIDER_FAILURES[out["outcome"]]
        reason = (
            (out.get("output") or {}).get("reason") if out["outcome"] == "invalid_spec" else None
        )
        detail = f": {reason}" if reason else ""
        raise ApiError(
            status, code, f"영상 공급자 요청을 처리할 수 없다 ({out['outcome']}){detail}"
        )
    return out


async def video_providers_get() -> dict[str, Any]:
    """선택 가능한 영상 공급자 목록·현재 선택(`list_video_providers`, 읽기). 키 값은 없다."""
    return await _video_provider_call(
        "list_video_providers", {}, f"ui:video-providers-get:{uuid.uuid4().hex[:12]}"
    )


async def video_provider_select(provider: str, model: str, human: str) -> dict[str, Any]:
    """영상 공급자·모델 선택(`select_video_provider`, A). `human`은 토큰에서 채운 이름이다."""
    return await _video_provider_call(
        "select_video_provider",
        {"provider": provider, "model": model, "updated_by": human},
        "ui:video-provider-select",
    )


async def video_provider_save_custom(spec: dict[str, Any], human: str) -> dict[str, Any]:
    """커스텀 영상 공급자 저장(`save_custom_video_provider`, C 사람 승인). 보류는 그대로."""
    return await _video_provider_call(
        "save_custom_video_provider",
        {"spec": spec, "updated_by": human},
        "ui:video-provider-save",
    )


async def video_provider_delete_custom(provider_id: str, human: str) -> dict[str, Any]:
    """커스텀 영상 공급자 삭제(`delete_custom_video_provider`, A)."""
    return await _video_provider_call(
        "delete_custom_video_provider",
        {"id": provider_id, "updated_by": human},
        "ui:video-provider-delete",
    )


def manual_publish_context(post_draft_id: str) -> str:
    return f"manual_publish_{post_draft_id}"[:128]


async def manual_publish_package_get(post_draft_id: str) -> dict[str, Any]:
    """수동 게시 안내용 복사 패키지(`get_manual_publish_package`, 읽기). 대기 건이 없으면 404."""
    out = await call(
        "get_manual_publish_package",
        {"post_draft_id": post_draft_id},
        campaign_id=manual_publish_context(post_draft_id),
        key=f"ui:manual-package:{uuid.uuid4().hex[:12]}",
    )
    if out["status"] == TaskStatus.FAILED.value:
        if out["outcome"] == "not_manual_pending":
            raise ApiError(404, "NOT_FOUND", "수동 게시 대기 건이 없다")
        if out["outcome"] == "invalid_input":
            raise ApiError(400, "INVALID_INPUT", "수동 게시 입력이 맞지 않는다 (invalid_input)")
    return out


# 완료 기록에서 요청이 틀렸거나 상태가 맞지 않아 거절된 결과. 화면이 코드별 문구로 바꾼다
MANUAL_PUBLISH_REFUSALS = {
    "invalid_permalink": (400, "INVALID_PERMALINK"),
    "ai_label_not_confirmed": (400, "AI_LABEL_NOT_CONFIRMED"),
    "published_at_in_future": (400, "INVALID_INPUT"),
    "invalid_input": (400, "INVALID_INPUT"),
    "not_found": (404, "NOT_FOUND"),
    "not_manual_pending": (409, "NOT_MANUAL_PENDING"),
    "already_published": (409, "ALREADY_PUBLISHED"),
    "permalink_already_used": (409, "PERMALINK_ALREADY_USED"),
}


async def manual_publish_complete(
    post_draft_id: str,
    permalink: str,
    ai_label_confirmed: bool,
    published_at: str | None,
    human: str,
) -> dict[str, Any]:
    """사람이 직접 올린 건을 게시 완료로 기록한다(`complete_manual_publish`, 안전 관문 A등급).

    `human`은 사람 토큰에서 채운 이름이다(본문에서 받지 않는다). 링크 형식·중복·대상 상태·AI 표시
    확인은 마케팅이 검사해 거절하고, 여기서는 그 거절을 HTTP 오류로 바꾼다.
    링크 진위는 확인하지 않는다.
    같은 건은 키가 같아 다시 보내도 한 번만 기록된다(마케팅이 대기 건 키로 정한다).
    """
    payload: dict[str, Any] = {
        "post_draft_id": post_draft_id,
        "permalink": permalink,
        "ai_label_confirmed": ai_label_confirmed,
        "posted_by": human,
    }
    if published_at:
        payload["published_at"] = published_at
    out = await call(
        "complete_manual_publish",
        payload,
        campaign_id=manual_publish_context(post_draft_id),
        key=f"ui:manual-complete:{post_draft_id}",
    )
    if out["status"] == TaskStatus.FAILED.value and out["outcome"] in MANUAL_PUBLISH_REFUSALS:
        status, code = MANUAL_PUBLISH_REFUSALS[out["outcome"]]
        raise ApiError(status, code, f"게시 완료를 기록할 수 없다 ({out['outcome']})")
    return out


# 장애 초안 이미지·게시 요청에서 요청 자체가 틀린 실패만 HTTP 오류로 바꾼다. 막힘·상한·보류는
# 판단 결과
OUTAGE_MEDIA_FAILURES = {
    "draft_not_found": (404, "NOT_FOUND"),
    "invalid_input": (400, "INVALID_INPUT"),
}


def outage_media_context(draft_id: str) -> str:
    return f"outg_{draft_id}"


async def outage_media_call(draft_id: str) -> dict[str, Any]:
    """장애 초안에 이미지를 붙인다(`prepare_outage_sns_media`). 게시하지 않는다.

    장면·필드는 보내지 않는다(마케팅이 코드 고정값을 쓴다). 같은 초안은 키가 같고 마케팅이 생성
    장부로
    중복 생성을 막는다. 보류·상한 초과·진행 불가는 판단 결과라 그대로 돌려준다.
    """
    out = await call(
        "prepare_outage_sns_media",
        {"post_draft_id": draft_id},
        campaign_id=outage_media_context(draft_id),
        key=f"ui:outage-media:{draft_id}",
        deadline_s=video_timeout_s(),
    )
    if out["status"] == TaskStatus.FAILED.value and out["outcome"] in OUTAGE_MEDIA_FAILURES:
        status, code = OUTAGE_MEDIA_FAILURES[out["outcome"]]
        raise ApiError(status, code, f"이미지를 만들 수 없다 ({out['outcome']})")
    return out


async def outage_publish_request_call(draft_id: str, retry: bool = False) -> dict[str, Any]:
    """이미지를 붙인 장애 초안의 게시 요청(`request_outage_sns_publish`). 승인 대기 건만 만든다.

    본문이 없다: 사본 초안·파일·해시·캡션은 마케팅이 보관본에서 읽는다. 여기서 승인하지 않는다.
    `retry`는 사람이 누른 "다시 요청"이다(만료로 닫힌 건만, 마케팅이 판정한다).
    """
    payload: dict[str, Any] = {"post_draft_id": draft_id}
    key = f"ui:outage-publish:{draft_id}"
    if retry:
        payload["retry"] = True
        key += f":retry:{uuid.uuid4().hex[:12]}"
    out = await call(
        "request_outage_sns_publish",
        payload,
        campaign_id=outage_media_context(draft_id),
        key=key,
    )
    if out["status"] == TaskStatus.FAILED.value and out["outcome"] in OUTAGE_MEDIA_FAILURES:
        status, code = OUTAGE_MEDIA_FAILURES[out["outcome"]]
        raise ApiError(status, code, f"게시를 요청할 수 없다 ({out['outcome']})")
    return out


def _preview_file(ref: Any, sha: Any, missing: ApiError) -> tuple[Path, str]:
    """기록된 파일만 내준다: 미디어 폴더 안쪽·이름이 `labeled.jpg|mp4`·기록된 sha256과 같은 파일."""
    raw_root = (os.getenv(MEDIA_DIR_ENV) or "").strip()
    if not (isinstance(ref, str) and isinstance(sha, str) and ref and raw_root):
        raise missing
    root = Path(raw_root).resolve()
    path = (root / ref).resolve()
    media_type = PREVIEW_FILES.get(path.name)
    if media_type is None or not path.is_relative_to(root) or not path.is_file():
        raise missing
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    if digest.hexdigest() != sha:  # 점검한 뒤 바뀐 파일은 보여 주지 않는다
        raise missing
    return path, media_type


async def outage_media_preview(draft_id: str) -> tuple[Path, str]:
    """이미지를 붙인 장애 초안의 AI 라벨 입힌 파일(경로, Content-Type). 없으면 404."""
    missing = ApiError(404, "NOT_FOUND", "준비된 이미지가 없다")
    out = await call(
        "list_outage_sns_drafts",
        {"post_draft_id": draft_id, "include_merged": True, "limit": 1},
        campaign_id=outage_media_context(draft_id),
        key=f"ui:outage-media-preview:{draft_id}",
    )
    items = (out.get("output") or {}).get("items") or []
    prepared = ((items[0].get("media_flow") or {}).get("prepared") or {}) if items else {}
    return _preview_file(prepared.get("asset_ref"), prepared.get("asset_sha256"), missing)


REQUEST_VIDEO_PREVIEW = "/dashboard-api/api/dashboard/marketing/sns-drafts/{draft_id}/media/preview"


async def briefs_list_call(limit: int) -> dict[str, Any]:
    """받은 기획안 목록(`list_campaign_briefs`, 읽기). 마케팅 skill 출력을 그대로 돌려준다."""
    out = await call(
        "list_campaign_briefs",
        {"limit": limit},
        campaign_id="briefs_read",
        key=f"ui:briefs-list:{limit}",
    )
    if out["status"] == TaskStatus.FAILED.value:
        raise ApiError(503, "STORAGE_ERROR", f"받은 기획안을 읽지 못했다 ({out['outcome']})")
    return out["output"] or {"items": [], "total": 0, "unknown_reason": "받은 기획안이 없음"}


async def brief_get_call(proposal_id: str) -> dict[str, Any]:
    """받은 기획안 하나(`get_campaign_brief`, 읽기). 없으면 404."""
    out = await call(
        "get_campaign_brief",
        {"proposal_id": proposal_id},
        campaign_id=f"cmp_{proposal_id}",
        key=f"ui:brief-get:{proposal_id}",
    )
    if out["status"] == TaskStatus.FAILED.value:
        status, code = BRIEF_MEDIA_FAILURES.get(out["outcome"] or "", (503, "STORAGE_ERROR"))
        raise ApiError(status, code, f"받은 기획안을 읽지 못했다 ({out['outcome']})")
    output = out["output"] or {}
    if not output.get("found") or not isinstance(output.get("item"), dict):
        raise ApiError(404, "NOT_FOUND", "받은 기획안을 찾을 수 없다")
    return output["item"]


async def outage_sns_drafts_list_call(limit: int, include_merged: bool) -> dict[str, Any]:
    """장애 사과·보상 SNS 초안 목록(`list_outage_sns_drafts`, 읽기). 게시하지 않는다."""
    out = await call(
        "list_outage_sns_drafts",
        {"limit": limit, "include_merged": include_merged},
        campaign_id="outage_sns_read",
        key=f"ui:outage-sns-list:{limit}:{int(include_merged)}",
    )
    if out["status"] == TaskStatus.FAILED.value:
        raise ApiError(400, "INVALID_INPUT", f"장애 SNS 초안을 읽지 못했다 ({out['outcome']})")
    empty = {"items": [], "total": 0, "unknown_reason": "장애 사과·보상 SNS 초안이 없음"}
    return out["output"] or empty


async def request_videos_call(request_id: str | None, limit: int) -> dict[str, Any]:
    """의뢰 홍보 영상 목록(`list_request_videos`, 읽기). 파일 확인용 `prepared`는 응답에서 뺀다.

    `labeled`가 거짓이면 `preview_path`는 None이다(라벨 없는 원본은 보여 주지 않는다).
    """
    payload: dict[str, Any] = {"limit": limit}
    if request_id:
        payload["request_id"] = request_id
    out = await call(
        "list_request_videos",
        payload,
        campaign_id="request_videos",
        key=f"ui:request-videos:{request_id or 'all'}:{limit}",
    )
    if out["status"] == TaskStatus.FAILED.value:
        raise ApiError(400, "INVALID_INPUT", f"영상 목록을 만들 수 없다 ({out['outcome']})")
    items: list[dict[str, Any]] = []
    for raw in (out.get("output") or {}).get("items") or []:
        item = {k: v for k, v in raw.items() if k not in ("prepared", "cover_files")}
        labeled = bool(raw.get("labeled")) and isinstance(raw.get("prepared"), dict)
        item["labeled"] = labeled
        item["preview_path"] = (
            REQUEST_VIDEO_PREVIEW.format(draft_id=raw.get("post_draft_id")) if labeled else None
        )
        items.append(item)
    return {"items": items}


async def sns_draft_media_preview(draft_id: str) -> tuple[Path, str]:
    """의뢰 영상 초안의 AI 라벨 입힌 파일(경로, Content-Type). 없으면 404(`_preview_file` 규칙)."""
    missing = ApiError(404, "NOT_FOUND", "준비된 영상이 없다")
    out = await call(
        "list_request_videos",
        {"post_draft_id": draft_id, "limit": 1},
        campaign_id="request_videos",
        key=f"ui:sns-draft-preview:{draft_id}",
    )
    items = (out.get("output") or {}).get("items") or []
    prepared = (items[0].get("prepared") or {}) if items else {}
    if not isinstance(prepared, dict):
        raise missing
    return _preview_file(prepared.get("asset_ref"), prepared.get("asset_sha256"), missing)


COVER_ID = re.compile(r"^cov_[0-9a-f]{12}$")


async def request_video_cover_file(draft_id: str, media_id: str) -> Path:
    """의뢰 영상 썸네일 후보 그림(AI 표시 포함 JPEG).

    후보 기록과 이름·해시가 같은 파일만 내준다. 없으면 404."""
    missing = ApiError(404, "NOT_FOUND", "썸네일 후보가 없다")
    if not COVER_ID.match(media_id):
        raise missing
    out = await call(
        "list_request_videos",
        {"post_draft_id": draft_id, "limit": 1},
        campaign_id="request_videos",
        key=f"ui:request-video-cover:{draft_id}:{media_id}",
    )
    items = (out.get("output") or {}).get("items") or []
    files = (items[0].get("cover_files") or {}) if items else {}
    found = files.get(media_id) if isinstance(files, dict) else None
    ref = found.get("asset_ref") if isinstance(found, dict) else None
    sha = found.get("asset_sha256") if isinstance(found, dict) else None
    raw_root = (os.getenv(MEDIA_DIR_ENV) or "").strip()
    if not (isinstance(ref, str) and isinstance(sha, str) and ref and raw_root):
        raise missing
    root = Path(raw_root).resolve()
    path = (root / ref).resolve()
    if path.name != f"{media_id}.jpg" or not path.is_relative_to(root) or not path.is_file():
        raise missing
    if hashlib.sha256(path.read_bytes()).hexdigest() != sha:  # 기록 뒤 바뀐 파일은 내주지 않는다
        raise missing
    return path


async def request_video_cover_call(post_draft_id: str, media_id: str, who: str) -> dict[str, Any]:
    """의뢰 영상 썸네일 고르기(마케팅 `request_video_cover_select`). 후보 밖 값은 422."""
    return await _request_video_youtube(
        "request_video_cover_select",
        {"post_draft_id": post_draft_id, "media_id": media_id, "who": who},
    )


async def outage_sns_merge_call(draft_ids: list[str] | None, dry_run: bool) -> dict[str, Any]:
    """장애 사과·보상 SNS 초안의 유사 초안 병합(`merge_outage_sns_drafts`).

    마케팅 초안 저장소의 메타데이터(병합 표시)만 바꾼다. 캡션·게시·비용은 없다(read 등급).
    `dry_run`이면 저장 없이 묶일 그룹만 돌려준다. 같은 병합을 다시 해도 결과가 같다.
    """
    payload: dict[str, Any] = {"dry_run": dry_run}
    if draft_ids is not None:
        payload["draft_ids"] = draft_ids
    out = await call(
        "merge_outage_sns_drafts",
        payload,
        campaign_id="outage_sns_merge",
        key=f"ui:outage-sns-merge:{_digest(payload)}",
    )
    if out["status"] == TaskStatus.FAILED.value:
        if out["outcome"] == "invalid_input":
            raise ApiError(400, "INVALID_INPUT", "병합 입력이 맞지 않는다 (invalid_input)")
        raise ApiError(502, "UPSTREAM_FAILED", f"초안을 병합하지 못했다 ({out['outcome']})")
    return out


async def brief_media_preview(proposal_id: str) -> tuple[Path, str]:
    """준비된 미디어 초안의 AI 라벨 입힌 파일(경로, Content-Type). 없으면 404.

    마케팅 조회(`get_campaign_brief`)의 `sns_handoff.prepared`가 가리키는 파일만 내준다:
    미디어 폴더 안쪽·이름이 `labeled.jpg|mp4`·기록된 sha256과 같은 파일(점검을 거친 그 파일).
    """
    missing = ApiError(404, "NOT_FOUND", "준비된 미디어 초안이 없다")
    out = await call(
        "get_campaign_brief",
        {"proposal_id": proposal_id},
        campaign_id=brief_media_context(proposal_id),
        key=f"ui:brief-media-preview:{proposal_id}",
    )
    item = ((out.get("output") or {}).get("item")) or {}
    prepared = (item.get("sns_handoff") or {}).get("prepared") or {}
    ref, sha = prepared.get("asset_ref"), prepared.get("asset_sha256")
    raw_root = (os.getenv(MEDIA_DIR_ENV) or "").strip()
    if not (isinstance(ref, str) and isinstance(sha, str) and ref and raw_root):
        raise missing
    root = Path(raw_root).resolve()
    path = (root / ref).resolve()
    media_type = PREVIEW_FILES.get(path.name)
    if media_type is None or not path.is_relative_to(root) or not path.is_file():
        raise missing
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    if digest.hexdigest() != sha:  # 점검한 뒤 바뀐 파일은 보여 주지 않는다
        raise missing
    return path, media_type


CHAT_DEADLINE_SECONDS = 30
# 마케팅 대화 skill이 거절할 때 돌려주는 outcome → (HTTP 상태, 오류 코드)
CHAT_REFUSALS = {
    "llm_not_configured": (409, "LLM_NOT_CONFIGURED"),
    "forbidden": (403, "FORBIDDEN"),
    "not_found": (404, "NOT_FOUND"),
    "thread_closed": (409, "THREAD_CLOSED"),
    "thread_busy": (409, "THREAD_BUSY"),
    "invalid_input": (400, "INVALID_INPUT"),
    "storage_error": (503, "STORAGE_ERROR"),
}


async def chat_call(
    skill: str, payload: dict[str, Any], *, tenant_id: str | None = None
) -> dict[str, Any]:
    """마케팅 AI 대화 skill 호출. 거절(outcome)은 오류 모양(`{error:{code,message}}`)으로 바꾼다."""
    config.set_agent_name(AGENT_NAME)
    request = AgentRequest(
        meta=RequestMeta(
            context_id=f"ctx_chat_{uuid.uuid4().hex[:12]}",
            token=config.agent_token(AGENT_NAME),
            deadline=datetime.now(UTC) + timedelta(seconds=CHAT_DEADLINE_SECONDS),
            idempotency_key=f"ui:chat:{uuid.uuid4().hex}",
            **tenant_meta(tenant_id),  # 명시 값 먼저, 없으면 세션 회사
        ),
        skill=skill,
        input=payload,
    )
    result = await SEND(TARGET, request)
    if result.status == TaskStatus.COMPLETED:
        return result.output or {}
    if result.status == TaskStatus.CANCELED:
        raise ApiError(504, "UPSTREAM_TIMEOUT", "마케팅 에이전트가 제때 응답하지 않았다")
    status, code = CHAT_REFUSALS.get(result.outcome or "", (502, "UPSTREAM_FAILED"))
    message = (result.output or {}).get(
        "message"
    ) or f"마케팅이 요청을 처리하지 못했다 ({result.outcome})"
    raise ApiError(status, code, str(message))


# 캐릭터 시트 프롬프트(read) 거절 outcome → (HTTP 상태, 오류 코드)
CHARACTER_SHEET_REFUSALS = {
    "no_image": (422, "NO_IMAGE"),
    "invalid_image": (422, "INVALID_IMAGE"),
    "image_too_large": (413, "IMAGE_TOO_LARGE"),
    "no_character": (422, "NO_CHARACTER"),
    "llm_not_configured": (409, "LLM_NOT_CONFIGURED"),
    "llm_failed": (502, "LLM_FAILED"),
    "invalid_input": (400, "INVALID_INPUT"),
}
CHARACTER_SHEET_DEADLINE_SECONDS = 120  # 이미지를 읽는 LLM 호출이라 대화보다 길게 둔다


async def character_sheet_call(
    image: dict[str, Any], note: str | None, thumbnail: str | None = None
) -> dict[str, Any]:
    """캐릭터 시트 프롬프트 skill.

    이미지·메모는 사람이 올린 것이라 untrusted로만 넘긴다(저장 안 함).
    """
    config.set_agent_name(AGENT_NAME)
    untrusted: dict[str, Any] = {"image": image}
    if note:
        untrusted["note"] = note
    if thumbnail:  # 제작 기록에 남길 작은 미리보기(원본은 저장하지 않는다)
        untrusted["thumbnail"] = thumbnail
    request = AgentRequest(
        meta=RequestMeta(
            context_id=f"ctx_charsheet_{uuid.uuid4().hex[:12]}",
            token=config.agent_token(AGENT_NAME),
            deadline=datetime.now(UTC) + timedelta(seconds=CHARACTER_SHEET_DEADLINE_SECONDS),
            idempotency_key=f"ui:charsheet:{uuid.uuid4().hex}",
            **tenant_meta(),
        ),
        skill="character_sheet_prompt",
        input={},
        untrusted=untrusted,
    )
    result = await SEND(TARGET, request)
    if result.status == TaskStatus.COMPLETED:
        return result.output or {}
    if result.status == TaskStatus.CANCELED:
        raise ApiError(504, "UPSTREAM_TIMEOUT", "마케팅 에이전트가 제때 응답하지 않았다")
    status, code = CHARACTER_SHEET_REFUSALS.get(result.outcome or "", (502, "UPSTREAM_FAILED"))
    message = (result.output or {}).get("message") or (
        f"마케팅이 요청을 처리하지 못했다 ({result.outcome})"
    )
    raise ApiError(status, code, str(message))


# 스토리보드 프롬프트(read) 거절 outcome → (HTTP 상태, 오류 코드)
STORYBOARD_REFUSALS = {
    "no_story": (422, "NO_STORY"),
    "invalid_input": (400, "INVALID_INPUT"),
    "llm_not_configured": (409, "LLM_NOT_CONFIGURED"),
    "llm_failed": (502, "LLM_FAILED"),
}
STORYBOARD_DEADLINE_SECONDS = 120  # 칸 12개를 쓰는 LLM 호출이라 대화보다 길게 둔다


async def storyboard_call(
    story: str,
    character_en: str | None,
    style_en: str | None,
    shots: int,
    record_id: str | None = None,
) -> dict[str, Any]:
    """스토리보드 프롬프트 skill.

    줄거리·캐릭터 문장·그림체는 사람이 넣은 것이라 untrusted로만 넘긴다.
    """
    config.set_agent_name(AGENT_NAME)
    untrusted: dict[str, Any] = {"story": story, "shots": shots}
    if character_en:
        untrusted["character_en"] = character_en
    if style_en:
        untrusted["style_en"] = style_en
    if record_id:  # 이 제작 기록에 스토리보드를 붙인다
        untrusted["record_id"] = record_id
    request = AgentRequest(
        meta=RequestMeta(
            context_id=f"ctx_storyboard_{uuid.uuid4().hex[:12]}",
            token=config.agent_token(AGENT_NAME),
            deadline=datetime.now(UTC) + timedelta(seconds=STORYBOARD_DEADLINE_SECONDS),
            idempotency_key=f"ui:storyboard:{uuid.uuid4().hex}",
            **tenant_meta(),
        ),
        skill="storyboard_prompt",
        input={},
        untrusted=untrusted,
    )
    result = await SEND(TARGET, request)
    if result.status == TaskStatus.COMPLETED:
        return result.output or {}
    if result.status == TaskStatus.CANCELED:
        raise ApiError(504, "UPSTREAM_TIMEOUT", "마케팅 에이전트가 제때 응답하지 않았다")
    status, code = STORYBOARD_REFUSALS.get(result.outcome or "", (502, "UPSTREAM_FAILED"))
    message = (result.output or {}).get("message") or (
        f"마케팅이 요청을 처리하지 못했다 ({result.outcome})"
    )
    raise ApiError(status, code, str(message))


async def higgsfield_status_call() -> dict[str, Any]:
    """Higgsfield 연결 상태 skill(read). 키 값은 돌아오지 않는다."""
    config.set_agent_name(AGENT_NAME)
    request = AgentRequest(
        meta=RequestMeta(
            context_id=f"ctx_hfstatus_{uuid.uuid4().hex[:12]}",
            token=config.agent_token(AGENT_NAME),
            deadline=datetime.now(UTC) + timedelta(seconds=20),
            idempotency_key=f"ui:hfstatus:{uuid.uuid4().hex}",
            **tenant_meta(),
        ),
        skill="higgsfield_status",
        input={},
    )
    result = await SEND(TARGET, request)
    if result.status == TaskStatus.COMPLETED:
        return result.output or {}
    if result.status == TaskStatus.CANCELED:
        raise ApiError(504, "UPSTREAM_TIMEOUT", "마케팅 에이전트가 제때 응답하지 않았다")
    raise ApiError(502, "UPSTREAM_FAILED", f"마케팅이 요청을 처리하지 못했다 ({result.outcome})")


async def _creative_read(skill: str, payload: dict[str, Any]) -> dict[str, Any]:
    config.set_agent_name(AGENT_NAME)
    request = AgentRequest(
        meta=RequestMeta(
            context_id=f"ctx_creative_{uuid.uuid4().hex[:12]}",
            token=config.agent_token(AGENT_NAME),
            deadline=datetime.now(UTC) + timedelta(seconds=20),
            idempotency_key=f"ui:creative:{uuid.uuid4().hex}",
            **tenant_meta(),
        ),
        skill=skill,
        input=payload,
    )
    result = await SEND(TARGET, request)
    if result.status == TaskStatus.COMPLETED:
        return result.output or {}
    if result.status == TaskStatus.CANCELED:
        raise ApiError(504, "UPSTREAM_TIMEOUT", "마케팅 에이전트가 제때 응답하지 않았다")
    if result.outcome == "not_found":
        raise ApiError(404, "NOT_FOUND", "제작 기록이 없다")
    if result.outcome in VIDEO_REFUSALS:
        status, code = VIDEO_REFUSALS[result.outcome]
        message = str((result.output or {}).get("message") or result.outcome)
        raise ApiError(status, code, message)
    raise ApiError(502, "UPSTREAM_FAILED", f"마케팅이 요청을 처리하지 못했다 ({result.outcome})")


async def creative_records_call(limit: int) -> dict[str, Any]:
    """제작 기록 목록(read)."""
    return await _creative_read("creative_records_list", {"limit": limit})


async def creative_record_call(record_id: str) -> dict[str, Any]:
    """제작 기록 하나(read)."""
    return await _creative_read("creative_record_get", {"record_id": record_id})


# 영상 요청 거절 outcome → (HTTP 상태, 오류 코드)
VIDEO_REFUSALS = {
    "board_image_not_ready": (409, "BOARD_IMAGE_NOT_READY"),
    "model_not_configured": (409, "MODEL_NOT_CONFIGURED"),
    "higgsfield_key_missing": (409, "HIGGSFIELD_KEY_MISSING"),
    # 회사 Higgsfield 계정 연결(MCP)로 만들 때
    "credits_insufficient": (409, "HIGGSFIELD_CREDITS_INSUFFICIENT"),
    "higgsfield_reconnect_needed": (409, "HIGGSFIELD_RECONNECT_NEEDED"),
    "higgsfield_not_connected": (409, "HIGGSFIELD_NOT_CONNECTED"),
    "higgsfield_app_missing": (409, "HIGGSFIELD_APP_MISSING"),
    "quota_exceeded": (409, "QUOTA_EXCEEDED"),
    "quota_not_configured": (409, "QUOTA_NOT_CONFIGURED"),
    "in_progress": (409, "IN_PROGRESS"),
    "storage_error": (500, "STORAGE_ERROR"),
    "draft_missing": (409, "DRAFT_MISSING"),
    "video_not_ready": (409, "VIDEO_NOT_READY"),
    "youtube_not_connected": (409, "YOUTUBE_NOT_CONNECTED"),
    "already_requested": (409, "ALREADY_REQUESTED"),
    # 의뢰 영상 썸네일: 후보 밖 값 · 이미 올라간 영상 · 후보 파일이 기록과 다름
    "cover_not_candidate": (422, "COVER_NOT_CANDIDATE"),
    "cover_locked": (409, "COVER_LOCKED"),
    "cover_missing": (409, "COVER_MISSING"),
    "bad_asset": (422, "BAD_ASSET"),
    "file_missing": (409, "FILE_MISSING"),
    "gate_held": (409, "GATE_HELD"),
    "gate_rejected": (409, "GATE_REJECTED"),
    "gate_blocked": (409, "GATE_BLOCKED"),
    "unknown_campaign": (422, "UNKNOWN_CAMPAIGN"),
    "rights_not_confirmed": (422, "RIGHTS_NOT_CONFIRMED"),
}
CREATIVE_FILES = {
    "sheet_image": ("sheet.jpg", "image/jpeg"),
    "board_image": ("board.jpg", "image/jpeg"),
    "video": ("video.mp4", "video/mp4"),
    "final_video": ("final.mp4", "video/mp4"),
}
# 사람이 올린 PNG 그림은 PNG 그대로 둔다
CREATIVE_ALT_FILES = {
    "sheet_image": ("sheet.png", "image/png"),
    "board_image": ("board.png", "image/png"),
}
UPLOAD_MAX_BYTES = {"image": 20 * 1024 * 1024, "video": 200 * 1024 * 1024}
UPLOAD_BASE = {
    "sheet_image": "sheet",
    "board_image": "board",
    "video": "video",
    "final_video": "final",
}


async def creative_video_call(record_id: str, who: str | None) -> dict[str, Any]:
    """스토리보드 영상 만들기를 사람 승인에 올린다(마케팅 `creative_video_request`)."""
    payload: dict[str, Any] = {"record_id": record_id}
    if who:
        payload["who"] = who
    return await _creative_read("creative_video_request", payload)


def creative_root(root: Path) -> Path:
    """미디어 폴더 안 요청 회사의 제작 기록 폴더(11/23, 마케팅 `creative_render.company_part`).

    캣북스(세션 없음 포함)는 지금 자리 `creative/`,
    다른 회사 세션은 `companies/<회사>/creative/`."""
    company = tenant_meta().get("tenant_id")
    return root / "companies" / company / "creative" if company else root / "creative"


async def brand_characters_call() -> dict[str, Any]:
    """요청 회사의 대표 캐릭터 목록(마케팅 `brand_characters_list`, read)."""
    return await _creative_read("brand_characters_list", {})


async def brand_character_mark_call(
    record_id: str, rights_confirmed: bool, who: str
) -> dict[str, Any]:
    """제작 기록을 대표 캐릭터로 표시(마케팅 `brand_character_mark`).

    권리 확인한 사람을 같이 보낸다."""
    payload = {"record_id": record_id, "rights_confirmed": rights_confirmed, "who": who}
    return await _creative_read("brand_character_mark", payload)


async def brand_character_unmark_call(record_id: str, who: str) -> dict[str, Any]:
    """대표 캐릭터 표시만 뗀다(마케팅 `brand_character_unmark`)."""
    return await _creative_read("brand_character_unmark", {"record_id": record_id, "who": who})


async def creative_asset(record_id: str, part: str) -> tuple[Path, str]:
    """제작 기록의 Higgsfield 결과 파일(경로, Content-Type). 기록된 sha256과 같은 파일만 내준다."""
    missing = ApiError(404, "NOT_FOUND", "결과 파일이 없다")
    rec = await creative_record_call(record_id)
    item = ((rec.get("render") or {}).get(part)) or {}
    name, sha = item.get("asset"), item.get("sha256")
    raw_root = (os.getenv(MEDIA_DIR_ENV) or "").strip()
    allowed = [f for f in (CREATIVE_FILES.get(part), CREATIVE_ALT_FILES.get(part)) if f]
    expected = next((f for f in allowed if f[0] == name), None)
    if not (expected and isinstance(sha, str) and raw_root):
        raise missing
    root = Path(raw_root).resolve()
    path = (creative_root(root) / record_id / name).resolve()
    if not path.is_relative_to(root) or not path.is_file():
        raise missing
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    if digest.hexdigest() != sha:
        raise missing
    return path, expected[1]


def _sniff(head: bytes) -> str | None:
    """앞 바이트로 형식을 본다(확장자·Content-Type은 믿지 않는다)."""
    if head.startswith(b"\xff\xd8\xff"):
        return "jpg"
    if head.startswith(b"\x89PNG\r\n\x1a\n"):
        return "png"
    if len(head) >= 12 and head[4:8] == b"ftyp":
        return "mp4"
    return None


async def creative_upload(
    record_id: str,
    part: str,
    chunks: Any,
    who: str | None,
    label: str | None,
) -> dict[str, Any]:
    """사람이 올린 그림·영상을 미디어 폴더에 저장하고 마케팅에 기록을 붙여 달라고 한다.

    `chunks`는 요청 본문 바이트 조각(async iterator). 크기는 칸 종류별 상한까지만 받는다.
    """
    raw_root = (os.getenv(MEDIA_DIR_ENV) or "").strip()
    if not raw_root:
        raise ApiError(503, "STORAGE_NOT_CONFIGURED", "미디어 폴더가 설정되지 않았다")
    await creative_record_call(record_id)  # 없는 기록이면 404
    kind = "video" if part in ("video", "final_video") else "image"
    limit = UPLOAD_MAX_BYTES[kind]
    root = Path(raw_root).resolve()
    folder = (creative_root(root) / record_id).resolve()
    if not folder.is_relative_to(root):
        raise ApiError(422, "BAD_RECORD", "기록 번호가 올바르지 않다")
    folder.mkdir(parents=True, exist_ok=True)
    tmp = folder / f".upload-{uuid.uuid4().hex}"
    digest = hashlib.sha256()
    size = 0
    head = b""
    try:
        with tmp.open("wb") as handle:
            async for chunk in chunks:
                if not chunk:
                    continue
                size += len(chunk)
                if size > limit:
                    mb = limit // (1024 * 1024)
                    raise ApiError(413, "TOO_LARGE", f"파일이 너무 크다 (최대 {mb}MB)")
                if len(head) < 16:
                    head += chunk[: 16 - len(head)]
                digest.update(chunk)
                handle.write(chunk)
        if size == 0:
            raise ApiError(422, "EMPTY", "빈 파일이다")
        ext = _sniff(head)
        ok_ext = ("mp4",) if kind == "video" else ("jpg", "png")
        if ext not in ok_ext:
            want = "MP4 영상" if kind == "video" else "JPG·PNG 그림"
            raise ApiError(415, "UNSUPPORTED_FILE", f"{want}만 올릴 수 있다")
        asset = f"{UPLOAD_BASE[part]}.{ext}"
        os.replace(tmp, folder / asset)
    finally:
        tmp.unlink(missing_ok=True)
    payload: dict[str, Any] = {
        "record_id": record_id,
        "part": part,
        "asset": asset,
        "sha256": digest.hexdigest(),
        "size_bytes": size,
    }
    if label:
        payload["label"] = label[:120]
    if who:
        payload["who"] = who
    return await _creative_read("creative_record_attach", payload)


# ---- 캠페인별 광고 숫자·추적 링크 (docs/marketing-ad-metrics-design.md C, 2026-11-23)
AD_FAILURES = {
    "invalid_input": (400, "INVALID_INPUT"),
    "storage_error": (503, "STORAGE_ERROR"),
    "held": (409, "HELD"),
    "execute_rejected": (409, "GATE_REJECTED"),
    "execute_failed": (502, "GATE_FAILED"),
    "TIMEOUT": (504, "UPSTREAM_TIMEOUT"),
    "rejected_by_review": (409, "GATE_REJECTED"),
}


def _ad_result(result: dict[str, Any]) -> dict[str, Any]:
    """판단 결과(recorded·rejected 등)는 그대로 돌려주고, 실패만 오류로 바꾼다."""
    if result["status"] == TaskStatus.COMPLETED.value:
        return result["output"] or {}
    status, code = AD_FAILURES.get(result["outcome"] or "", (502, "UPSTREAM_FAILED"))
    detail = result["output"] or {}
    errors = detail.get("errors") if isinstance(detail, dict) else None
    message = (
        "; ".join(map(str, errors)) if errors else f"마케팅이 처리하지 못했다 ({result['outcome']})"
    )
    raise ApiError(status, code, message)


async def ad_metrics_call(
    campaign_id: str, payload: dict[str, Any] | None = None
) -> dict[str, Any]:
    """`payload`가 없으면 조회(get_campaign_ad_metrics), 있으면 한 줄 적기(record_…)."""
    if payload is None:
        key = f"ui:ad-metrics:get:{campaign_id}:{uuid.uuid4().hex[:8]}"
        result = await call(
            "get_campaign_ad_metrics",
            {"campaign_id": campaign_id},
            campaign_id=campaign_id,
            key=key,
        )
        return _ad_result(result)
    body = {"campaign_id": campaign_id, **payload}
    raw = json.dumps(body, sort_keys=True, ensure_ascii=False)
    key = f"ui:ad-metrics:put:{hashlib.sha256(raw.encode()).hexdigest()[:24]}"
    result = await call("record_campaign_ad_metrics", body, campaign_id=campaign_id, key=key)
    return _ad_result(result)


async def campaign_link_call(campaign_id: str, payload: dict[str, Any]) -> dict[str, Any]:
    """꼬리표 붙인 추적 링크(build_campaign_link, read). 어긋난 입력은 이유를 그대로 돌려준다."""
    key = f"ui:campaign-link:{campaign_id}:{uuid.uuid4().hex[:8]}"
    result = await call(
        "build_campaign_link",
        {"campaign_id": campaign_id, **payload},
        campaign_id=campaign_id,
        key=key,
    )
    return _ad_result(result)


async def youtube_draft_call(record_id: str, body: dict[str, Any]) -> dict[str, Any]:
    """유튜브 제목·설명 초안(마케팅 `creative_youtube_draft`). 올리지는 않는다."""
    return await _creative_read("creative_youtube_draft", {"record_id": record_id, **body})


async def youtube_request_call(record_id: str, who: str | None) -> dict[str, Any]:
    """유튜브 올리기를 사람 승인에 올린다(마케팅 `creative_youtube_request`)."""
    payload: dict[str, Any] = {"record_id": record_id}
    if who:
        payload["who"] = who
    return await _creative_read("creative_youtube_request", payload)


async def _request_video_youtube(skill: str, payload: dict[str, Any]) -> dict[str, Any]:
    try:
        return await _creative_read(skill, payload)
    except ApiError as error:
        if error.status_code == 404:
            raise ApiError(404, "NOT_FOUND", "의뢰로 만든 영상이 없다") from error
        raise


async def request_video_youtube_draft_call(
    post_draft_id: str, body: dict[str, Any]
) -> dict[str, Any]:
    """의뢰로 만든 영상의 유튜브 제목·설명 초안(마케팅 `request_video_youtube_draft`)."""
    return await _request_video_youtube(
        "request_video_youtube_draft", {"post_draft_id": post_draft_id, **body}
    )


async def request_video_youtube_request_call(post_draft_id: str, who: str | None) -> dict[str, Any]:
    """의뢰로 만든 영상(AI 라벨 입힌 파일)의 유튜브 올리기를 사람 승인에 올린다."""
    payload: dict[str, Any] = {"post_draft_id": post_draft_id}
    if who:
        payload["who"] = who
    return await _request_video_youtube("request_video_youtube_request", payload)


async def campaigns_list_call(limit: int) -> dict[str, Any]:
    """마케팅이 시작한 캠페인 대장(`list_marketing_campaigns`, read)."""
    return await _creative_read("list_marketing_campaigns", {"limit": limit})


async def youtube_status_call() -> dict[str, Any]:
    return await _creative_read("youtube_status", {})


async def instagram_status_call() -> dict[str, Any]:
    return await _creative_read("instagram_status", {})
