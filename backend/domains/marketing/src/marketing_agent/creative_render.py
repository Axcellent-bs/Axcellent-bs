"""이미지 시트·스토리보드·영상을 Higgsfield로 실제로 만든다 (2026-11-20).

제작 기록(CR-…)마다 세 단계를 기록의 `render` 칸에 남긴다.
- `sheet_image`: 이미지 시트 프롬프트 + 올린 캐릭터(참조)로 이미지 시트 그림
- `board_image`: 스토리보드 시트 프롬프트 + 이미지 시트 그림(참조)으로 스토리보드 그림
- `video`: 스토리보드 그림을 시작 이미지로 영상. **사람 승인(C, `generate_creative_video`) 뒤에만**

공통 순서(돈·크레딧이 나가기 전): 모델 경로·키 확인 → 월 사용량 예산 + 장부 예약(`MediaLedger`,
#1490과 같은 장부) → `execute()` 안전 관문 → `executed`일 때만 Higgsfield 호출 → 파일 저장·정산.
그림은 기존 이미지 생성과 같은 안전 작업 `generate_media`(B, media_kind=image)를, 영상은 새 작업
`generate_creative_video`(C)를 쓴다. 영상은 보류(held)되면 기록에 `review_id`를 남기고,
기록을 다시 읽을 때(`check_video`) 같은 멱등 키로 관문을 다시 물어 승인됐으면 그때 만든다.

Higgsfield(공식 문서 docs.higgsfield.ai, 2026-11-20 확인):
- 인증 `Authorization: Key <ID>:<SECRET>`(키 `MARKETING_HIGGSFIELD_API_KEY`,
  값은 로그·결과에 내지 않는다)
- 생성 `POST https://api.higgsfield.ai/<모델 경로>` → `request_id`, 상태 `GET /requests/<id>/status`
  (queued·in_progress·completed·failed·nsfw·canceled), 완료 `images[].url` / `video.url`.
  결과는 7일 보관이라 받아서 저장한다
- 참조 이미지는 `POST /files/generate-upload-url`로 받은 `upload_url`에 PUT(인증 헤더 없이)하고
  `public_url`을 넘긴다
- 미검증: 나노바나나 등 이미지 모델 경로와 요청 필드 이름은 공개 문서에 없다. 경로는 설정
  (`MARKETING_HIGGSFIELD_IMAGE_MODEL`·`MARKETING_HIGGSFIELD_VIDEO_MODEL`)로만 받고, 필드 이름은 아래
  `IMAGE_FIELDS`·`VIDEO_FIELDS` 한 곳에 모았다. 실제 키로 한 번 불러 본 뒤 맞춘다.
"""

from __future__ import annotations

import asyncio
import hashlib
import logging
import os
import re
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from ax_agent_template.execute import execute
from ax_contracts.schemas import Evidence, ExecuteDecision, RequestMeta
from ax_secret_store import get_secret
from marketing_agent import creative_records as _creative
from marketing_agent.media import usage as usage_mod
from marketing_agent.media.config import (
    HIGGSFIELD_API_KEY_ENV,
    HIGGSFIELD_IMAGE_MODEL_ENV,
    HIGGSFIELD_VIDEO_MODEL_ENV,
    MediaSettings,
)
from marketing_agent.media.ledger import LedgerError, MediaLedger

logger = logging.getLogger("marketing.creative_render")

API_HOST = "https://api.higgsfield.ai"
PROVIDER = "higgsfield"
IMAGE_ACTION = "generate_media"  # 기존 이미지 생성과 같은 안전 작업(B)
VIDEO_ACTION = "generate_creative_video"  # 사람 승인(C)
VIDEO_DURATION_ENV = "MARKETING_HIGGSFIELD_VIDEO_DURATION_S"
DEFAULT_VIDEO_S = 5
MAX_VIDEO_S = 15
POLL_S = 4.0
JOB_TIMEOUT_S = 600.0
HTTP_TIMEOUT_S = 60.0
MAX_DOWNLOAD = 200 * 1024 * 1024
STALE_S = 1800  # 이만큼 넘게 'running'이면 서버가 다시 켜져 끊긴 것으로 본다

# 요청 필드 이름(미검증, 한 곳에 모은다)
IMAGE_FIELDS = {
    "prompt": "prompt",
    "negative": "negative_prompt",
    "refs": "image_urls",
    "aspect": "aspect_ratio",
}
VIDEO_FIELDS = {
    "prompt": "prompt",
    "refs": "image_urls",
    "aspect": "aspect_ratio",
    "duration": "duration",
}
SHEET_ASPECT, BOARD_ASPECT, VIDEO_ASPECT = "16:9", "3:4", "9:16"

PARTS = ("sheet_image", "board_image", "video")
FILES = {"sheet_image": "sheet.jpg", "board_image": "board.jpg", "video": "video.mp4"}
ATTACH_ACTION = "attach_creative_file"  # 사람이 올린 파일을 기록에 붙임(A)
# 사람이 올릴 수 있는 칸과 저장 이름(그림은 JPEG·PNG, 영상은 MP4)
UPLOAD_FILES = {
    "sheet_image": ("sheet.jpg", "sheet.png"),
    "board_image": ("board.jpg", "board.png"),
    "video": ("video.mp4",),
    "final_video": ("final.mp4",),
}
_ID = re.compile(r"[A-Za-z0-9-]{8,64}")
PENDING = {"queued", "in_progress"}
FAILED = {"failed", "nsfw", "canceled"}


def _client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=HTTP_TIMEOUT_S)


# 테스트가 바꾼다: Higgsfield HTTP 클라이언트, 기다리기
CLIENT: Callable[[], httpx.AsyncClient] = _client
SLEEP: Callable[[float], Awaitable[None]] = asyncio.sleep
_tasks: set[asyncio.Task[Any]] = set()


class RenderError(Exception):
    """사유 코드만 담는다(키·응답 본문을 넣지 않는다)."""


def _now() -> datetime:
    return datetime.now(UTC)


def _key() -> str:
    return get_secret(HIGGSFIELD_API_KEY_ENV).strip()


def image_model() -> str:
    return os.getenv(HIGGSFIELD_IMAGE_MODEL_ENV, "").strip()


def video_model() -> str:
    return os.getenv(HIGGSFIELD_VIDEO_MODEL_ENV, "").strip()


def video_seconds() -> int:
    try:
        n = int(os.getenv(VIDEO_DURATION_ENV, "") or DEFAULT_VIDEO_S)
    except ValueError:
        n = DEFAULT_VIDEO_S
    return max(1, min(n, MAX_VIDEO_S))


def configured(kind: str) -> str | None:
    """만들 수 없는 이유 코드. 되면 None."""
    if not _key():
        return "higgsfield_key_missing"
    if not (image_model() if kind == "image" else video_model()):
        return "model_not_configured"
    return None


def _dir(record_id: str) -> Path:
    return MediaSettings.from_env().media_dir / "creative" / record_id


def asset_path(record_id: str, part: str) -> Path:
    return _dir(record_id) / FILES[part]


def _set(record_id: str, part: str, **values: Any) -> dict[str, Any]:
    """기록의 render.<part>를 바꾼다(없던 칸은 만든다)."""
    store = _creative.store()
    render = store.get_render(record_id)
    cur = dict(render.get(part) or {})
    cur.update(values)
    cur["at"] = _now().isoformat()
    render[part] = cur
    store.set_render(record_id, render)
    return cur


def _spawn(coro: Awaitable[Any]) -> None:
    task = asyncio.get_running_loop().create_task(coro)  # type: ignore[arg-type]
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


async def wait_idle() -> None:
    """테스트용: 돌고 있는 렌더가 끝나기를 기다린다."""
    while _tasks:
        await asyncio.gather(*list(_tasks), return_exceptions=True)


def _safe(fn: Callable[[], Any]) -> None:
    try:
        fn()
    except Exception:  # noqa: BLE001 - 장부 정리 실패는 결과를 막지 않는다
        logger.exception("creative_render: 장부 정리 실패")


# ---------------------------------------------------------------- Higgsfield HTTP
def _auth() -> dict[str, str]:
    return {"Authorization": f"Key {_key()}"}


async def _upload(client: httpx.AsyncClient, data: bytes, content_type: str = "image/jpeg") -> str:
    r = await client.post(
        f"{API_HOST}/files/generate-upload-url",
        json={"content_type": content_type},
        headers=_auth(),
    )
    if r.status_code not in (200, 201):
        raise RenderError(f"upload_url_http_{r.status_code}")
    body = r.json()
    public, put_url = body.get("public_url"), body.get("upload_url")
    if not (isinstance(public, str) and isinstance(put_url, str)):
        raise RenderError("upload_url_missing")
    headers = {str(k): str(v) for k, v in (body.get("upload_headers") or {}).items()}
    put = await client.put(put_url, content=data, headers=headers)  # 인증 헤더는 보내지 않는다
    if put.status_code not in (200, 201, 204):
        raise RenderError(f"upload_http_{put.status_code}")
    return public


async def _submit(client: httpx.AsyncClient, model: str, payload: dict[str, Any]) -> str:
    r = await client.post(f"{API_HOST}/{model}", json=payload, headers=_auth())
    if r.status_code not in (200, 201, 202):
        raise RenderError(f"submit_http_{r.status_code}")
    job = r.json().get("request_id")
    if not isinstance(job, str) or not _ID.fullmatch(job):
        raise RenderError("no_request_id")
    return job


def _result_url(body: dict[str, Any], kind: str) -> str:
    if kind == "video":
        url = (body.get("video") or {}).get("url")
    else:
        images = body.get("images") or []
        url = images[0].get("url") if images and isinstance(images[0], dict) else None
    if not isinstance(url, str) or not url.startswith("https://"):
        raise RenderError("no_result_url")
    return url


async def _wait(client: httpx.AsyncClient, job: str, kind: str) -> str:
    """완료되면 결과 URL. 실패·시간 초과면 RenderError."""
    waited = 0.0
    while waited <= JOB_TIMEOUT_S:
        r = await client.get(f"{API_HOST}/requests/{job}/status", headers=_auth())
        if r.status_code != 200:
            raise RenderError(f"status_http_{r.status_code}")
        body = r.json()
        status = str(body.get("status") or "")
        if status == "completed":
            return _result_url(body, kind)
        if status in FAILED:
            raise RenderError(f"job_{status}")
        if status not in PENDING:
            raise RenderError("job_unknown_status")
        await SLEEP(POLL_S)
        waited += POLL_S
    raise RenderError("job_timeout")


async def _download(client: httpx.AsyncClient, url: str, path: Path) -> str:
    r = await client.get(url)
    if r.status_code != 200:
        raise RenderError(f"download_http_{r.status_code}")
    if len(r.content) > MAX_DOWNLOAD:
        raise RenderError("download_too_large")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_bytes(r.content)
    return hashlib.sha256(r.content).hexdigest()


# ---------------------------------------------------------------- 관문 + 예산
def _evidence(record_id: str, what: str) -> Evidence:
    return Evidence(
        queries=["creative_render"],
        data_summary=f"제작 기록 {record_id} {what}",
        collected_at=_now(),
        ref_ids=[record_id],
    )


async def _gate_image(
    ledger: MediaLedger, meta: RequestMeta, record_id: str, part: str
) -> tuple[ExecuteDecision | None, int | None, str | None]:
    """(결정, 장부 seq, 막힌 이유). 막히면 결정·seq는 None."""
    settings = MediaSettings.from_env()
    expected = usage_mod.planned("image", None)
    cap = settings.quota("image") if settings.quota_applies else None
    if settings.quota_applies and cap is None:
        return None, None, "quota_not_configured"
    key = f"{meta.context_id}:creative_render:{record_id}:{part}"
    now = _now()
    try:
        res = ledger.reserve(
            key=key,
            month=now.strftime("%Y-%m"),
            media_kind="image",
            provider=PROVIDER,
            model_id=image_model(),
            usage=expected,
            monthly_cap=cap,
            now=now,
        )
    except LedgerError:
        return None, None, "storage_error"
    if res.outcome == "over_budget":
        return None, None, "quota_exceeded"
    if res.row is None or res.outcome != "reserved":
        return None, None, "in_progress"
    label = "이미지 시트" if part == "sheet_image" else "스토리보드"
    params = {
        "media_kind": "image",
        "resolution": "1024x1024",
        "provider": PROVIDER,
        "scene_template_id": f"creative_{part}",
        "post_draft_id": record_id,
        "request_sha256": hashlib.sha256(key.encode()).hexdigest(),
        "ai_generated": True,
        "usage_unit": expected.unit,
        "usage_amount": expected.amount,
        "summary": f"{label} 그림 생성 1장 (Higgsfield, AI 생성)",
    }
    decision = await execute(
        IMAGE_ACTION,
        params,
        meta.model_copy(update={"idempotency_key": key}),
        _evidence(record_id, f"{label} 그림"),
    )
    if decision.status != "executed":
        seq = res.row.seq
        _safe(lambda: ledger.release(seq))
        return None, None, f"gate_{decision.status}"
    return decision, res.row.seq, None


# ---------------------------------------------------------------- 이미지 시트·스토리보드 그림
def start_sheet(
    ledger: MediaLedger,
    meta: RequestMeta,
    record_id: str,
    sheet: dict[str, Any],
    ref: bytes | None,
) -> None:
    """이미지 시트 프롬프트가 나온 직후 부른다. 설정이 없으면 기록에 이유만 남긴다."""
    why = configured("image")
    if why:
        _set(record_id, "sheet_image", status="unavailable", error=why)
        return
    _set(record_id, "sheet_image", status="running", error=None)
    prompt = str(sheet.get("prompt_en") or "")
    negative = str(sheet.get("negative_prompt_en") or "")
    _spawn(
        _render_image(ledger, meta, record_id, "sheet_image", prompt, negative, ref, SHEET_ASPECT)
    )


def _story_sha(rec: dict[str, Any]) -> str:
    return hashlib.sha256(str(rec.get("storyboard_at") or "").encode()).hexdigest()[:12]


def maybe_start_board(ledger: MediaLedger, meta: RequestMeta, record_id: str) -> None:
    """스토리보드가 붙었고 이미지 시트 그림이 있으면 스토리보드 그림을 만든다."""
    rec = _creative.store().get(record_id)
    if rec is None or not rec.get("storyboard"):
        return
    render = rec.get("render") or {}
    sheet = render.get("sheet_image") or {}
    board = render.get("board_image") or {}
    if board.get("status") in ("running", "done") and board.get("story_sha") == _story_sha(rec):
        return
    if sheet.get("status") != "done":
        if sheet.get("status") in ("unavailable", "failed", "blocked"):
            _set(record_id, "board_image", status="unavailable", error="sheet_image_missing")
        return  # 시트 그림이 끝나면 다시 부른다
    why = configured("image")
    if why:
        _set(record_id, "board_image", status="unavailable", error=why)
        return
    try:
        ref = asset_path(record_id, "sheet_image").read_bytes()
    except OSError:
        _set(record_id, "board_image", status="failed", error="sheet_file_missing")
        return
    board_out = rec["storyboard"]
    _set(record_id, "board_image", status="running", error=None, story_sha=_story_sha(rec))
    _spawn(
        _render_image(
            ledger,
            meta,
            record_id,
            "board_image",
            str(board_out.get("sheet_prompt_en") or ""),
            str(board_out.get("negative_prompt_en") or ""),
            ref,
            BOARD_ASPECT,
        )
    )


async def _render_image(
    ledger: MediaLedger,
    meta: RequestMeta,
    record_id: str,
    part: str,
    prompt: str,
    negative: str,
    ref: bytes | None,
    aspect: str,
) -> None:
    decision, seq, blocked = await _gate_image(ledger, meta, record_id, part)
    if blocked or decision is None or seq is None:
        _set(record_id, part, status="blocked", error=blocked)
        if part == "sheet_image":
            maybe_start_board(ledger, meta, record_id)
        return
    try:
        async with CLIENT() as client:
            payload: dict[str, Any] = {
                IMAGE_FIELDS["prompt"]: prompt[:8000],
                IMAGE_FIELDS["aspect"]: aspect,
            }
            if negative:
                payload[IMAGE_FIELDS["negative"]] = negative[:1000]
            if ref:
                payload[IMAGE_FIELDS["refs"]] = [await _upload(client, ref)]
            job = await _submit(client, image_model(), payload)
            _set(record_id, part, request_id=job)
            url = await _wait(client, job, "image")
            sha = await _download(client, url, asset_path(record_id, part))
    except (RenderError, httpx.HTTPError, OSError, ValueError) as error:
        code = str(error) if isinstance(error, RenderError) else type(error).__name__
        logger.warning("Higgsfield %s 실패: %s (%s)", part, code, record_id)
        _safe(lambda: ledger.mark_failed(seq, _now()))
        _set(record_id, part, status="failed", error=code)
        if part == "sheet_image":
            maybe_start_board(ledger, meta, record_id)
        return
    usage = usage_mod.planned("image", None)
    _safe(
        lambda: ledger.settle(
            seq,
            model_id=image_model(),
            usage=usage,
            usage_source="estimate",
            result_json='{"provider":"higgsfield"}',
            now=_now(),
        )
    )
    _set(
        record_id,
        part,
        status="done",
        error=None,
        asset=FILES[part],
        sha256=sha,
        execution_id=decision.execution_id,
    )
    if part == "sheet_image":
        maybe_start_board(ledger, meta, record_id)


# ---------------------------------------------------------------- 영상 (사람 승인)
async def request_video(
    ledger: MediaLedger, meta: RequestMeta, record_id: str, who: str | None
) -> dict[str, Any]:
    """영상 만들기를 사람 승인에 올린다. 돌려주는 값은 기록의 render.video."""
    rec = _creative.store().get(record_id)
    if rec is None:
        raise RenderError("not_found")
    render = rec.get("render") or {}
    video = render.get("video") or {}
    if video.get("status") in ("awaiting_approval", "running", "done"):
        return video
    if (render.get("board_image") or {}).get("status") != "done":
        raise RenderError("board_image_not_ready")
    why = configured("video")
    if why:
        raise RenderError(why)
    settings = MediaSettings.from_env()
    seconds = video_seconds()
    expected = usage_mod.UsageAmount("seconds", seconds)
    cap = settings.quota("video") if settings.quota_applies else None
    if settings.quota_applies and cap is None:
        raise RenderError("quota_not_configured")
    key = f"{meta.context_id}:{VIDEO_ACTION}:{record_id}"
    now = _now()
    try:
        res = ledger.reserve(
            key=key,
            month=now.strftime("%Y-%m"),
            media_kind="video",
            provider=PROVIDER,
            model_id=video_model(),
            usage=expected,
            monthly_cap=cap,
            now=now,
        )
    except LedgerError as error:
        raise RenderError("storage_error") from error
    if res.outcome == "over_budget":
        raise RenderError("quota_exceeded")
    if res.row is None or res.outcome != "reserved":
        raise RenderError("in_progress")
    seq = res.row.seq
    title = (rec.get("storyboard") or {}).get("title_ko") or record_id
    params = {
        "record_id": record_id,
        "provider": PROVIDER,
        "duration_s": seconds,
        "usage_unit": "seconds",
        "usage_amount": seconds,
        "ai_generated": True,
        "requested_by": (who or "")[:80],
        "summary": f"스토리보드 영상 {seconds}초 만들기: {title} (Higgsfield, AI 생성)",
    }
    decision = await execute(
        VIDEO_ACTION,
        params,
        meta.model_copy(update={"idempotency_key": key}),
        _evidence(record_id, "스토리보드 영상"),
    )
    base = {
        "key": key,
        "seq": seq,
        "seconds": seconds,
        "context_id": meta.context_id,
        "requested_by": who,
        "params": params,
    }
    if decision.status == "held":
        return _set(
            record_id,
            "video",
            status="awaiting_approval",
            review_id=decision.review_id,
            error=None,
            **base,
        )
    if decision.status == "executed":
        _set(record_id, "video", status="running", error=None, **base)
        _spawn(_render_video(ledger, record_id, seq, decision))
        return _creative.store().get_render(record_id).get("video") or {}
    _safe(lambda: ledger.release(seq))
    return _set(record_id, "video", status="blocked", error=f"gate_{decision.status}", **base)


async def check_video(ledger: MediaLedger, meta: RequestMeta, record_id: str) -> None:
    """승인 대기 영상이면 같은 키·같은 내용으로 관문을 다시 묻는다(멱등). 승인됐으면 만든다."""
    video = (_creative.store().get_render(record_id).get("video")) or {}
    if video.get("status") != "awaiting_approval" or not video.get("key"):
        return
    stored = meta.model_copy(
        update={
            "idempotency_key": video["key"],
            "context_id": video.get("context_id") or meta.context_id,
        }
    )
    decision = await execute(
        VIDEO_ACTION,
        dict(video.get("params") or {"record_id": record_id}),
        stored,
        _evidence(record_id, "스토리보드 영상"),
    )
    seq = int(video.get("seq") or 0)
    if decision.status in ("held", "verifying"):
        return
    if decision.status == "executed":
        _set(record_id, "video", status="running", error=None)
        _spawn(_render_video(ledger, record_id, seq, decision))
        return
    _safe(lambda: ledger.release(seq))
    _set(record_id, "video", status="rejected", error=f"gate_{decision.status}")


async def _render_video(
    ledger: MediaLedger, record_id: str, seq: int, decision: ExecuteDecision
) -> None:
    rec = _creative.store().get(record_id) or {}
    board = rec.get("storyboard") or {}
    video = (rec.get("render") or {}).get("video") or {}
    seconds = int(video.get("seconds") or video_seconds())
    prompt = (
        "Animate this storyboard as one continuous short film, panel by panel in order, "
        "keeping the same characters, outfits and art style. "
        f"{board.get('style_en') or ''}"
    ).strip()
    try:
        ref = asset_path(record_id, "board_image").read_bytes()
        async with CLIENT() as client:
            payload = {
                VIDEO_FIELDS["prompt"]: prompt[:2000],
                VIDEO_FIELDS["refs"]: [await _upload(client, ref)],
                VIDEO_FIELDS["aspect"]: VIDEO_ASPECT,
                VIDEO_FIELDS["duration"]: seconds,
            }
            job = await _submit(client, video_model(), payload)
            _set(record_id, "video", request_id=job)
            url = await _wait(client, job, "video")
            sha = await _download(client, url, asset_path(record_id, "video"))
    except (RenderError, httpx.HTTPError, OSError, ValueError) as error:
        code = str(error) if isinstance(error, RenderError) else type(error).__name__
        logger.warning("Higgsfield video 실패: %s (%s)", code, record_id)
        _safe(lambda: ledger.mark_failed(seq, _now()))
        _set(record_id, "video", status="failed", error=code)
        return
    _safe(
        lambda: ledger.settle(
            seq,
            model_id=video_model(),
            usage=usage_mod.UsageAmount("seconds", seconds),
            usage_source="estimate",
            result_json='{"provider":"higgsfield"}',
            now=_now(),
        )
    )
    _set(
        record_id,
        "video",
        status="done",
        error=None,
        asset=FILES["video"],
        sha256=sha,
        execution_id=decision.execution_id,
    )


def expire_stale(record_id: str) -> None:
    """서버가 다시 켜져 끊긴 'running'을 실패로 바꾼다(기록을 읽을 때)."""
    render = _creative.store().get_render(record_id)
    changed = False
    for part in PARTS:
        cur = render.get(part) or {}
        if cur.get("status") != "running" or not cur.get("at"):
            continue
        try:
            age = (_now() - datetime.fromisoformat(cur["at"])).total_seconds()
        except ValueError:
            continue
        if age > STALE_S and not _tasks:
            cur.update(status="failed", error="interrupted")
            render[part] = cur
            changed = True
    if changed:
        _creative.store().set_render(record_id, render)


# ---------------------------------------------------------------- 사람이 올린 파일
def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


async def attach_upload(
    meta: RequestMeta,
    record_id: str,
    part: str,
    asset: str,
    sha256: str,
    size_bytes: int,
    label: str | None,
    who: str | None,
) -> dict[str, Any]:
    """dashboard-api가 저장한 파일을 기록의 render.<part>로 붙인다(관문 A 뒤).

    다른 도구(Higgsfield 화면, 2D 제작 등)로 만든 결과도 제작 기록에 남기려는 길.
    """
    if asset not in UPLOAD_FILES.get(part, ()):
        raise RenderError("bad_asset")
    store = _creative.store()
    if store.get(record_id) is None:
        raise RenderError("not_found")
    path = _dir(record_id) / asset
    if not path.is_file() or _sha256(path) != sha256:
        raise RenderError("file_missing")
    cur = store.get_render(record_id).get(part) or {}
    if cur.get("status") in ("running", "awaiting_approval"):
        raise RenderError("in_progress")
    key = f"{meta.context_id}:creative_attach:{record_id}:{part}:{sha256[:16]}"
    params = {
        "record_id": record_id,
        "part": part,
        "asset": asset,
        "sha256": sha256,
        "size_bytes": size_bytes,
        "summary": f"제작 기록 {record_id}에 {part} 파일 붙이기 (사람이 올림)",
    }
    decision = await execute(
        ATTACH_ACTION,
        params,
        meta.model_copy(update={"idempotency_key": key}),
        _evidence(record_id, f"{part} 파일 올림"),
    )
    if decision.status != "executed":
        raise RenderError(f"gate_{decision.status}")
    return _set(
        record_id,
        part,
        status="done",
        error=None,
        asset=asset,
        sha256=sha256,
        source="upload",
        label=label,
        by=who,
        review_id=None,
    )
