"""마케팅 캠페인 skill 호출 (A2A). 화면의 캠페인 시작·결과·취소가 쓴다.

재무 판단과 안전 관문은 마케팅 skill 안에서 일어난다. 보류·거절은 오류가 아니라 판단 결과라
화면이 이유를 보여 줄 수 있게 그대로 돌려준다. 닿지 못한 경우만 API 오류로 바꾼다.
"""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from ax_agent_template import config
from ax_agent_template.client import call_agent
from ax_contracts.schemas import AgentRequest, AgentResult, RequestMeta, TaskStatus
from ax_dashboard_api.envelope import ApiError

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


async def brief_media_call(proposal_id: str) -> dict[str, Any]:
    """넘긴 기획안의 요청 초안으로 미디어 초안을 만든다(`prepare_brief_media`). 게시하지 않는다.

    종류·필드는 보내지 않는다(마케팅이 보관본에서 읽는다). 같은 기획안은 키가 같고, 마케팅이
    생성 장부로 중복 생성을 막는다. 영상 생성이 길어 마감은 영상 준비와 같다.
    """
    out = await call(
        "prepare_brief_media",
        {"proposal_id": proposal_id},
        campaign_id=brief_media_context(proposal_id),
        key=f"ui:brief-media:{proposal_id}",
        deadline_s=video_timeout_s(),
    )
    if out["status"] == TaskStatus.FAILED.value and out["outcome"] in BRIEF_MEDIA_FAILURES:
        status, code = BRIEF_MEDIA_FAILURES[out["outcome"]]
        raise ApiError(status, code, f"미디어를 만들 수 없다 ({out['outcome']})")
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


async def chat_call(skill: str, payload: dict[str, Any]) -> dict[str, Any]:
    """마케팅 AI 대화 skill 호출. 거절(outcome)은 오류 모양(`{error:{code,message}}`)으로 바꾼다."""
    config.set_agent_name(AGENT_NAME)
    request = AgentRequest(
        meta=RequestMeta(
            context_id=f"ctx_chat_{uuid.uuid4().hex[:12]}",
            token=config.agent_token(AGENT_NAME),
            deadline=datetime.now(UTC) + timedelta(seconds=CHAT_DEADLINE_SECONDS),
            idempotency_key=f"ui:chat:{uuid.uuid4().hex}",
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
