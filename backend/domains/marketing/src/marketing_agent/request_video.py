"""의뢰(`wr_*`) 홍보 영상: 생성 직후 AI 라벨·초안 연결, 의뢰 영상 목록.

skill은 `list_request_videos`(읽기)다.

문제: 총괄 PROMO_VIDEO 흐름은 `generate_media`에서 끝난다. 영상 파일(`asset.mp4`)과
`meta.json`만 남고 AI 라벨(`labeled.mp4`)도, 초안(`prepare_video_draft_from_plan`이 만든
릴스 초안)과의 연결도, 웹 미리보기도 없었다.
라벨·연결은 `prepare_sns_post_from_media`(화면 경로)만 했다.

여기서 하는 일(새 판단 규칙 없음, 기존 라벨·점검을 그대로 쓴다):
- `attach_label_after_generate`: `generate_media` 완료 직후(동기·비동기 모두 같은
  실행기를 거친다),
  입력 `post_draft_id`가 이 서버가 만든 릴스 초안이고 결과가 영상이면 `sns_prepare`의 변환·라벨
  (`_convert_and_label`, 같은 원본·설정이면 재사용)으로 `labeled.mp4`를 만들고 초안에
  `asset_ref`(미디어 폴더 기준 상대 경로)·`asset_sha256`·
  `media_flow.prepared`를 기록한 뒤 `check_sns_post`로 점검한다.
  초안 id는 그대로다(내용 주소 id를 새로 만들지 않는다).
- 라벨이 실패하면 영상 생성 결과는 그대로 두고
  `output.label="failed"`(+`label_reason`)만 덧붙인다.
  라벨 없는 상태는 초안에 연결·`prepared`로 기록하지 않는다(라벨 없는 원본은 게시 점검이 막는다).
- 초안이 없거나 릴스·brief 초안이 아니면(다른 경로가 `generate_media_flow`를 직접 부르는 경우 포함)
  아무것도 하지 않는다. 다른 경로의 라벨링과 겹치지 않고, 이미 라벨이 있으면 다시 만들지 않는다.
- `list_request_videos`(read): 의뢰 영상 목록. 초안 저장소와 `meta.json`을 읽기만 한다.

게시(`publish_sns`, C등급 사람 승인)·R2 업로드는 건드리지 않는다. 비용·생성은 여기서 하지 않는다.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import Field, ValidationError

from ax_contracts.schemas import AgentRequest, AgentResult, TaskStatus
from ax_contracts.schemas.common import AxModel
from marketing_agent import sns, sns_media
from marketing_agent.media.audio_mix import Runner, subprocess_runner
from marketing_agent.sns import SnsPostDraft, SnsStore
from marketing_agent.sns_prepare import (
    PrepareError,
    _convert_and_label,
    _is_inside,
    preflight_tools,
)

LIST_ACTION = "list_request_videos"
REQUEST_CONTEXT_PREFIX = "ctx_wr_"  # 의뢰 context_id = "ctx_" + 의뢰 id(`wr_*`)
LABELED_VIDEO = "labeled.mp4"
PREVIEW_PATH = "/dashboard-api/api/dashboard/marketing/sns-drafts/{draft_id}/media/preview"
REQUEST_ID = r"^wr_[A-Za-z0-9_-]{1,60}$"
DRAFT_ID = r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$"
Process = Callable[..., Any]
Check = Callable[[AgentRequest, SnsStore], Awaitable[AgentResult]]


# --- 생성 직후 라벨·초안 연결 ---------------------------------------------------


def _label_failed(reason: str) -> dict[str, Any]:
    return {"label": "failed", "label_reason": reason}


def _prepared(draft: SnsPostDraft, verdict: dict[str, Any], label_text: str) -> dict[str, Any]:
    ok = verdict.get("verdict") == "approved" and verdict.get("publishable") is True
    return {
        "post_draft_id": draft.post_draft_id,
        "stage": "draft_ready" if ok else "check_rejected",
        "media_kind": draft.media_kind,
        "asset_ref": draft.asset_ref,
        "asset_sha256": draft.asset_sha256,
        "label_text": label_text,
        "check": {
            "verdict": verdict.get("verdict"),
            "publishable": verdict.get("publishable"),
            "problems": list(verdict.get("problems") or [])[:20],
        },
        "prepared_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


def _already_attached(draft: SnsPostDraft, labeled: Path) -> bool:
    """이 미디어 폴더의 `labeled.mp4`가 이미 같은 해시로 연결돼 있고 `prepared`도 있다."""
    if not (labeled.is_file() and draft.asset_ref and draft.asset_sha256):
        return False
    prepared = (draft.media_flow or {}).get("prepared")
    if not isinstance(prepared, dict):
        return False
    sha = sns_media.sha256_file(labeled)
    return (
        draft.asset_ref == sns_media.asset_ref_for(labeled)
        and draft.asset_sha256 == sha
        and prepared.get("asset_sha256") == sha
        and sns._overlay_recorded(labeled, sha)  # noqa: SLF001 - 게시 점검과 같은 기준
    )


async def _attach(
    request: AgentRequest,
    draft: SnsPostDraft,
    gen: dict[str, Any],
    *,
    store: SnsStore,
    runner: Runner,
    process: Process,
    preflight: Callable[[], str | None],
    check: Check,
) -> dict[str, Any]:
    root = sns_media.media_root()
    if root is None:
        return _label_failed("media_dir_not_configured")
    asset = Path(str(gen.get("asset_ref", "")))
    if not _is_inside(asset, root) or not asset.is_file():
        return _label_failed("media_dir_mismatch")
    labeled_path = asset.parent / LABELED_VIDEO
    if _already_attached(draft, labeled_path):  # 멱등: 다시 돌려도 라벨·점검을 되풀이하지 않는다
        return {
            "label": "reused",
            "labeled_ref": draft.asset_ref,
            "labeled_sha256": draft.asset_sha256,
            "draft_stage": ((draft.media_flow or {}).get("prepared") or {}).get("stage"),
        }
    missing = preflight()
    if missing is not None:
        return _label_failed("post_tools_unavailable")
    try:
        origin_sha = sns_media.sha256_file(asset)
        _, label, reused = await asyncio.to_thread(
            process, asset, asset.parent, "video", str(gen.get("model_id", "")), origin_sha, runner
        )
    except PrepareError as error:
        return _label_failed(error.code)
    except (OSError, ValueError) as error:
        return _label_failed(type(error).__name__)

    labeled_ref = sns_media.asset_ref_for(label.labeled_path)
    latest = store.get(draft.post_draft_id) or draft
    attached = latest.model_copy(
        update={
            "asset_ref": labeled_ref,
            "asset_sha256": label.labeled_sha256,
            "media_check": None,
            "media_flow": None,
        }
    )
    try:
        store.update_meta([attached])  # 초안 id는 그대로(내용 주소 id를 새로 만들지 않는다)
    except KeyError:
        return _label_failed("draft_update_failed")
    verdict: dict[str, Any] = {}
    try:
        checked = await check(
            request.model_copy(
                update={
                    "skill": "check_sns_post",
                    "input": {
                        "post_draft_id": attached.post_draft_id,
                        "caption": attached.caption,
                        "is_ai_generated": True,
                    },
                }
            ),
            store,
        )
        verdict = checked.output or {}
    except Exception:  # noqa: BLE001 - 점검을 못 돌려도 라벨·연결은 유지하고 점검 거절로 둔다
        verdict = {"verdict": None, "publishable": False, "problems": ["check_unavailable"]}
    prepared = _prepared(attached, verdict, label.text)
    try:
        store.update_meta(
            [
                attached.model_copy(
                    update={
                        "media_check": {
                            "verdict": verdict.get("verdict"),
                            "publishable": verdict.get("publishable"),
                            "problems": list(verdict.get("problems") or [])[:20],
                            "checked_at": prepared["prepared_at"],
                        },
                        "media_flow": {"prepared": prepared},
                    }
                )
            ]
        )
    except KeyError:
        return _label_failed("draft_update_failed")
    return {
        "label": "reused" if reused else "ok",
        "labeled_ref": labeled_ref,
        "labeled_sha256": label.labeled_sha256,
        "draft_stage": prepared["stage"],
    }


async def attach_label_after_generate(
    request: AgentRequest,
    result: AgentResult,
    *,
    store: SnsStore,
    runner: Runner = subprocess_runner,
    process: Process = _convert_and_label,
    preflight: Callable[[], str | None] = preflight_tools,
    check: Check = sns.check_sns_post,
) -> AgentResult:
    """`generate_media` 결과에 필요하면 라벨·초안 연결 사실을 덧붙인다(생성 결과는 그대로)."""
    if (
        result.outcome != "generated"
        or result.output is None
        or not isinstance(request.input, dict)
    ):
        return result
    draft_id = request.input.get("post_draft_id")
    if request.input.get("media_kind") != "video" or not isinstance(draft_id, str):
        return result
    draft = store.get(draft_id)
    if draft is None or draft.media_kind != "reel" or draft.source_kind != "brief":
        return result  # 이 흐름의 초안이 아니다(다른 경로가 라벨링한다)
    try:
        extra = await _attach(
            request,
            draft,
            result.output,
            store=store,
            runner=runner,
            process=process,
            preflight=preflight,
            check=check,
        )
    except Exception as error:  # noqa: BLE001 - 생성은 끝났다. 라벨 실패 사실만 알린다
        extra = _label_failed(type(error).__name__)
    return result.model_copy(update={"output": result.output | extra})


# --- 의뢰 영상 목록 --------------------------------------------------------------


class ListRequestVideosInput(AxModel):
    request_id: str | None = Field(default=None, pattern=REQUEST_ID)
    post_draft_id: str | None = Field(default=None, pattern=DRAFT_ID)  # 한 건만 조회(미리보기)
    limit: int = Field(default=20, ge=1, le=100)


class RequestVideoList(AxModel):
    items: list[dict[str, Any]] = Field(default_factory=list)


def _read_json(path: Path) -> dict[str, Any]:
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _find_meta(root: Path, draft: SnsPostDraft) -> dict[str, Any]:
    """초안에 연결된 폴더의 `meta.json`, 없으면 같은 context에서 이 초안으로 만든 최신 영상."""
    ref = draft.asset_ref
    if ref:
        path = (root / ref).resolve().parent / "meta.json"
        if path.is_relative_to(root):
            meta = _read_json(path)
            if meta.get("post_draft_id") == draft.post_draft_id:
                return meta
    found: list[dict[str, Any]] = []
    for path in root.glob(f"*/{draft.context_id}/med_*/meta.json"):
        meta = _read_json(path)
        if meta.get("post_draft_id") == draft.post_draft_id and meta.get("media_kind") == "video":
            found.append(meta)
    found.sort(key=lambda m: str(m.get("generated_at", "")), reverse=True)
    return found[0] if found else {}


def _labeled_prepared(root: Path, draft: SnsPostDraft) -> dict[str, str] | None:
    """초안이 연결한 파일이 `labeled.mp4`·기록된 해시와 같고 라벨 입힘이 기록돼 있으면 그 참조."""
    ref, sha = draft.asset_ref, draft.asset_sha256
    if not (ref and sha):
        return None
    path = (root / ref).resolve()
    if path.name != LABELED_VIDEO or not path.is_relative_to(root) or not path.is_file():
        return None
    if sns_media.sha256_file(path) != sha or not sns._overlay_recorded(path, sha):  # noqa: SLF001
        return None
    return {"asset_ref": ref, "asset_sha256": sha}


def _item(root: Path, draft: SnsPostDraft, created_at: datetime) -> dict[str, Any] | None:
    meta = _find_meta(root, draft)
    if not meta:
        return None  # 아직 영상이 만들어지지 않은 초안은 목록에 넣지 않는다
    prepared = _labeled_prepared(root, draft)
    check = draft.media_check or {}
    if prepared is None:
        status = "label_missing"
    elif check.get("verdict") == "approved" and check.get("publishable") is True:
        status = "draft_ready"
    else:
        status = "check_rejected"
    return {
        "post_draft_id": draft.post_draft_id,
        "request_id": draft.context_id.removeprefix("ctx_"),
        "context_id": draft.context_id,
        "media_id": meta.get("media_id"),
        "media_kind": "video",
        "duration_s": meta.get("duration_s"),
        "caption_ko": draft.caption,
        "created_at": str(meta.get("generated_at") or created_at.isoformat()),
        "labeled": prepared is not None,
        "status": status,
        # 파일 확인용(dashboard-api가 읽고 응답에서 뺀다). 라벨 없는 파일은 넣지 않는다
        "prepared": prepared,
    }


async def list_request_videos(request: AgentRequest, store: SnsStore) -> AgentResult:
    try:
        data = ListRequestVideosInput.model_validate(request.input)
    except ValidationError as error:
        return AgentResult(
            meta=request.meta,
            status=TaskStatus.FAILED,
            outcome="invalid_input",
            output={
                "errors": [f"{'.'.join(map(str, e['loc']))}: {e['type']}" for e in error.errors()]
            },
        )
    root = sns_media.media_root()
    items: list[dict[str, Any]] = []
    if root is not None:
        rows, _ = store.list_by_source("brief", None, include_merged=False)
        for draft, created_at in rows:
            if draft.media_kind != "reel" or not draft.context_id.startswith(
                REQUEST_CONTEXT_PREFIX
            ):
                continue
            if data.post_draft_id not in (None, draft.post_draft_id):
                continue
            if data.request_id is not None and draft.context_id != f"ctx_{data.request_id}":
                continue
            item = _item(root, draft, created_at)
            if item is not None:
                items.append(item)
    items.sort(key=lambda i: i["created_at"], reverse=True)
    items = items[: data.limit]
    return AgentResult(
        meta=request.meta,
        status=TaskStatus.COMPLETED,
        output=RequestVideoList(items=items).model_dump(mode="json"),
        evidence=sns._evidence(  # noqa: SLF001
            [LIST_ACTION],
            f"의뢰 영상 {len(items)}건 조회",
            [i["post_draft_id"] for i in items],
        ),
    )


__all__ = [
    "LIST_ACTION",
    "PREVIEW_PATH",
    "ListRequestVideosInput",
    "RequestVideoList",
    "attach_label_after_generate",
    "list_request_videos",
]
