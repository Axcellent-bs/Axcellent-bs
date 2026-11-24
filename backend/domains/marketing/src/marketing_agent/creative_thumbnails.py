"""제작 기록 영상의 썸네일 후보 3장을 Higgsfield로 만들고 사람이 고른다 (2026-11-23).

의뢰 영상 썸네일(`request_video_cover.py`, #1624)과 같은 규칙
(고르기·기본값·승인 해시·잠김·AI 표시)을 제작 기록(CR-…)에 붙였다.
다른 점은 후보를 영상 프레임이 아니라 Higgsfield 그림으로 만든다는 것이다.
상태는 기록의 `render.thumbnails` 칸에 둔다(새 저장소 없음).

- 언제: 기록에 영상(완성 영상 `final_video` 우선, 없으면 Higgsfield 영상 `video`)이 생기면
  기록을 읽을 때(`creative_record_get`) 후보 만들기 요청이 저절로 한 번 걸린다(`auto=True`). 화면의
  [썸네일 3장 만들기·다시 만들기]도 같은 함수를 부른다
- 길(`route`): 회사 Higgsfield 계정(MCP, 구독 크레딧)이 연결돼 있으면 그 계정, 없으면 기존 키 길,
  둘 다 없으면 `not_connected`로 만들지 않는다. 모델은 `MARKETING_HIGGSFIELD_MCP_IMAGE_MODEL`
  (키 길은 `MARKETING_HIGGSFIELD_IMAGE_MODEL`) 설정값만 쓴다
- 비용·안전: 우리 키 월 예산(키 길만, MCP는 걸지 않는다 #1629) + 장부 예약 → `execute()`
  `generate_creative_thumbnails`(C, 사람 결정) → 승인 뒤 같은 키로 다시 물어 executed일 때만 만든다.
  자동 요청이어도 크레딧은 사람이 승인한 뒤에만 나간다(SNS·미디어 › 사람 결정 대기)
- 프롬프트(`prompts`): 시트의 캐릭터 고정 문장 + 스토리보드 그림체·장면(+ 있으면 대본 핵심 장면)으로
  구도가 다른 3장(캐릭터 클로즈업+제목 자리 / 핵심 장면 / 액션·감정 순간). 비율은 영상에 맞춘다(세로
  9:16, 가로 16:9). 글자는 넣지 않는다. 영상 첫 프레임(없으면 시트 그림)을 참조 이미지로 넘긴다
- AI 표시: 장마다 JPEG XMP(`ai_generated=true`·모델·시각)를 붙여 읽어 확인하고, 폴더의 `meta.json`과
  후보 칸(`is_ai_generated`)에 기록한다.
  자리는 회사 폴더 아래 그 기록 폴더 `creative/<기록>/thumbs/`
- 고르기(`select`): 후보 안의 값만 받는다. 고르지 않으면 첫 후보가 기본값(`ensure_cover`). 고르면
  `cover_rev`(+1)가 올라 승인 해시가 바뀐다(유튜브 대기 건은 초안으로 되돌린다). 올린 뒤엔 잠긴다
"""

from __future__ import annotations

import json
import logging
import os
import re
import subprocess
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx

from ax_agent_template.execute import execute
from ax_contracts.schemas import Evidence, ExecuteDecision, RequestMeta
from marketing_agent import creative_mcp as _mcp
from marketing_agent import creative_records as _creative
from marketing_agent import creative_render as _render
from marketing_agent import higgsfield_mcp_auth as _mcp_auth
from marketing_agent import sns_label, sns_media
from marketing_agent.higgsfield_mcp import McpError
from marketing_agent.higgsfield_mcp_auth import McpAuthError
from marketing_agent.media import usage as usage_mod
from marketing_agent.media.config import MediaSettings, ffmpeg_bin
from marketing_agent.media.ledger import LedgerError, MediaLedger

logger = logging.getLogger("marketing.creative_thumbnails")

ACTION = "generate_creative_thumbnails"  # 사람 결정(C)
PART = "thumbnails"  # render 안 칸 이름
COUNT = 3
THUMB_DIR = "thumbs"
THUMB_ID = re.compile(r"^thm_[0-9a-f]{12}$")
SOURCES = ("final_video", "video")  # 완성 영상이 있으면 그것에 맞춘다
KINDS = ("closeup", "scene", "moment")
PENDING = ("requesting", "awaiting_approval", "running")
WIDE, TALL = "16:9", "9:16"
MAX_SIDE = 1280  # 유튜브 권장 1280×720. 긴 변을 이만큼으로 줄인다(유튜브 썸네일 2MB 제한)
FFMPEG_TIMEOUT_S = 60
AI_TEXT = "AI로 만든 썸네일입니다 (Higgsfield)"
NO_TEXT = "No text, no letters, no captions, no logos, no watermark."
NEGATIVE = "text, letters, words, captions, watermark, logo, blurry, low quality"
# 이미 올라간 영상이면 썸네일을 바꾸지 않는다(유튜브 올리는 중·올림, 인스타 게시 중·게시됨)
LOCKED = {
    "youtube": ("uploading", "done"),
    "instagram": ("posting", "publishing", "published", "done"),
}
_ERRORS = (McpError, McpAuthError, _render.RenderError, httpx.HTTPError, OSError, ValueError)


class ThumbError(Exception):
    """거절 코드: not_found·video_not_ready·not_connected·model_not_configured·quota_*·storage_error
    ·in_progress·credits_insufficient·higgsfield_*·cover_not_candidate·cover_locked."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _now() -> datetime:
    return datetime.now(UTC)


# ---------------------------------------------------------------- 테스트가 바꾸는 ffmpeg 자리
def _ffmpeg(args: list[str], out: Path) -> bool:
    try:
        done = subprocess.run(  # noqa: S603 - 인자 목록으로만 부른다
            [ffmpeg_bin(), "-y", "-hide_banner", "-loglevel", "error", *args, str(out)],
            capture_output=True,
            timeout=FFMPEG_TIMEOUT_S,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return done.returncode == 0 and out.is_file()


def ffmpeg_first_frame(video: Path, out: Path) -> bool:
    """영상 첫 프레임(0.5초, 전환 직후 흐린 프레임을 피한다)을 JPEG로."""
    return _ffmpeg(["-ss", "0.5", "-i", str(video), "-frames:v", "1", "-q:v", "3"], out)


def ffmpeg_normalize(raw: Path, out: Path) -> bool:
    """받은 그림(JPEG·PNG·WebP)을 긴 변 1280 이하 JPEG로."""
    scale = (
        f"scale=w='min({MAX_SIDE},iw)':h='min({MAX_SIDE},ih)':force_original_aspect_ratio=decrease"
    )
    return _ffmpeg(["-i", str(raw), "-vf", scale, "-frames:v", "1", "-q:v", "3"], out)


def probe_size(video: Path) -> tuple[int, int] | None:
    for s in (sns_media.run_ffprobe(video) or {}).get("streams") or []:
        if s.get("codec_type") == "video" and s.get("width") and s.get("height"):
            return int(s["width"]), int(s["height"])
    return None


FIRST_FRAME: Callable[[Path, Path], bool] = ffmpeg_first_frame
NORMALIZE: Callable[[Path, Path], bool] = ffmpeg_normalize
PROBE: Callable[[Path], tuple[int, int] | None] = probe_size


# ---------------------------------------------------------------- 상태
def get(record_id: str) -> dict[str, Any]:
    return dict(_creative.store().get_render(record_id).get(PART) or {})


def _set(record_id: str, **values: Any) -> dict[str, Any]:
    return _render._set(record_id, PART, **values)  # noqa: SLF001 - 같은 render 칸 규칙


def _folder(record_id: str) -> Path:
    return _render.asset_path(record_id, "video").parent / THUMB_DIR


def source_video(rec: dict[str, Any]) -> tuple[str, dict[str, Any]] | None:
    """후보를 맞출 영상 칸: 내보낼 영상(`creative_render.publish_video_part`, 약속 B 한 곳).

    그 칸에 파일 정보가 모자라면 예전처럼 완성 영상 → 영상 순. 없으면 None."""
    render = rec.get("render") or {}
    for part in (_render.publish_video_part(rec), *SOURCES):
        if part is None:
            continue
        info = render.get(part) or {}
        if info.get("status") == "done" and info.get("sha256") and info.get("asset"):
            return part, info
    return None


def _video_path(record_id: str, info: dict[str, Any]) -> Path | None:
    name = str(info.get("asset") or "")
    if not name or "/" in name or "\\" in name or name.startswith("."):
        return None
    return _render.asset_path(record_id, "video").parent / name


def aspect_for(record_id: str, part: str, info: dict[str, Any]) -> str:
    """Higgsfield 영상은 세로(9:16)다. 직접 올린 완성 영상은 파일 크기로 정하고, 모르면 가로."""
    if part == "video" and info.get("source") != "upload":
        return _render.VIDEO_ASPECT
    path = _video_path(record_id, info)
    size = PROBE(path) if path is not None and path.is_file() else None
    if size is None:  # 자막을 얹은 영상은 원본 칸(source_part)을 따른다
        origin = info.get("source_part") if part == _render.OVERLAY_PART else part
        return WIDE if origin == "final_video" else _render.VIDEO_ASPECT
    return TALL if size[1] > size[0] else WIDE


def _locked(rec: dict[str, Any]) -> bool:
    render = rec.get("render") or {}
    return any((render.get(k) or {}).get("status") in v for k, v in LOCKED.items())


# ---------------------------------------------------------------- 프롬프트
def _script(rec: dict[str, Any]) -> dict[str, Any]:
    """대본(다른 단계가 붙이면 기록의 `script` 칸). 없으면 빈 dict."""
    raw = rec.get("script")
    return raw if isinstance(raw, dict) else {}


def _shot_prompt(shot: Any) -> str:
    return str(shot.get("prompt_en") or "") if isinstance(shot, dict) else ""


def prompts(rec: dict[str, Any], aspect: str) -> list[dict[str, str]]:
    """구도가 다른 3장 프롬프트(영어). 시트·스토리보드·대본에 있는 문장만 쓴다(지어내지 않는다)."""
    sheet = rec.get("sheet") or {}
    board = rec.get("storyboard") or {}
    script = _script(rec)
    character = str(sheet.get("consistency_en") or sheet.get("appearance_en") or "").strip()
    style = str(board.get("style_en") or "").strip()
    shots = [s for s in board.get("shots") or [] if _shot_prompt(s)]
    cast = [s for s in shots if s.get("has_character")] or shots
    key = str(script.get("key_scene_en") or "").strip() or (
        _shot_prompt(shots[len(shots) // 2]) if shots else ""
    )
    peak = _shot_prompt(cast[-1]) if cast else key
    frame = "vertical 9:16" if aspect == TALL else "horizontal 16:9"
    room = "the upper third" if aspect == TALL else "the left third"
    tail = " ".join(
        p
        for p in (
            f"Same character in every detail: {character}." if character else "",
            f"Art style: {style}." if style else "",
            "Eye-catching, high contrast, vivid colors, sharp focus.",
            NO_TEXT,
        )
        if p
    )
    head = f"Video thumbnail, {frame} frame."
    texts = {
        "closeup": f"{head} Expressive close-up of the main character facing the viewer with a "
        f"strong emotion, simple uncluttered background, clean empty space in {room} reserved "
        f"for a short title added later. {tail}",
        "scene": f"{head} The key scene of the video"
        + (f": {key}" if key else "")
        + f". Cinematic composition with one clear subject. {tail}",
        "moment": f"{head} The action or emotional peak moment"
        + (f": {peak}" if peak else "")
        + f". Dynamic pose, motion, dramatic lighting. {tail}",
    }
    return [{"kind": k, "prompt": texts[k][:8000]} for k in KINDS]


def _negative(rec: dict[str, Any]) -> str:
    board = rec.get("storyboard") or {}
    extra = str(board.get("negative_prompt_en") or "").strip()
    return f"{NEGATIVE}, {extra}"[:1000] if extra else NEGATIVE


# ---------------------------------------------------------------- 만들기 요청 (사람 결정)
def _evidence(record_id: str) -> Evidence:
    return Evidence(
        queries=["creative_thumbnails"],
        data_summary=f"제작 기록 {record_id} 영상 썸네일 후보 {COUNT}장",
        collected_at=_now(),
        ref_ids=[record_id],
    )


def _why_not(via: str) -> str | None:
    if via == _render.API and not _render._key():  # noqa: SLF001
        return "not_connected"  # 회사 계정도 키도 없다
    return _render.configured("image", via)


async def _preflight(cid: str | None, params: dict[str, Any]) -> float | None:
    """3장 예상 크레딧(모르면 None). 모자라거나 연결이 없으면 예외."""
    if not cid:
        raise McpAuthError("not_connected")
    async with _render.CLIENT() as client:
        hf = _mcp.Higgsfield(client, cid)
        one = await _mcp.preflight(hf, "image", params)  # 무제한 불가면 image_unlim_unavailable
        if one is None:
            return None
        if not _mcp.uses_unlim(params):  # 11/24: 무제한이면 크레딧을 쓰지 않아 잔액으로 막지 않는다
            credits, _ = await _mcp.balance(hf)
            if credits is not None and credits < one * COUNT:
                raise McpError("credits_insufficient")
        return one * COUNT


def _reserve(ledger: MediaLedger, key: str, via: str) -> int:
    settings = MediaSettings.from_env()
    applies = _render._quota_applies(settings, via)  # noqa: SLF001 - MCP는 우리 키 예산을 걸지 않는다
    cap = settings.quota("image") if applies else None
    if applies and cap is None:
        raise ThumbError("quota_not_configured")
    now = _now()
    try:
        res = ledger.reserve(
            key=key,
            month=now.strftime("%Y-%m"),
            media_kind="image",
            provider=_render._provider(via),  # noqa: SLF001
            model_id=_render._model("image", via),  # noqa: SLF001
            usage=usage_mod.UsageAmount("images", COUNT),
            monthly_cap=cap,
            now=now,
        )
    except LedgerError as error:
        raise ThumbError("storage_error") from error
    if res.outcome == "over_budget":
        raise ThumbError("quota_exceeded")
    if res.row is None or res.outcome != "reserved":
        raise ThumbError("in_progress")
    return res.row.seq


async def request(
    ledger: MediaLedger,
    meta: RequestMeta,
    record_id: str,
    who: str | None,
    *,
    regenerate: bool = False,
    auto: bool = False,
) -> dict[str, Any]:
    """후보 3장 만들기를 사람 결정에 올린다. 돌려주는 값은 render.thumbnails.

    `auto`(기록을 읽을 때)는 이 칸이 비어 있을 때 한 번만 걸고, 막히면 이유만 남긴다(예외 없음)."""
    rec = _creative.store().get(record_id)
    if rec is None:
        raise ThumbError("not_found")
    cur = get(record_id)
    status = cur.get("status")
    if status in PENDING or (auto and status) or (cur.get("candidates") and not regenerate):
        return cur
    source = source_video(rec)
    if source is None:
        if auto:
            return cur
        raise ThumbError("video_not_ready")
    if _locked(rec):
        if auto:
            return cur
        raise ThumbError("cover_locked")
    _set(record_id, status="requesting", error=None)  # 첫 await 전에 표시(겹쳐 읽어도 한 번만)
    try:
        return await _request(ledger, meta, record_id, who, rec, source, cur, auto)
    except ThumbError as error:
        _set(record_id, status="unavailable" if auto else "blocked", error=error.code)
        if auto:
            return get(record_id)
        raise
    except Exception:
        _set(record_id, status="blocked", error="internal_error")  # '요청 중'으로 멈추지 않게
        raise


async def _request(
    ledger: MediaLedger,
    meta: RequestMeta,
    record_id: str,
    who: str | None,
    rec: dict[str, Any],
    source: tuple[str, dict[str, Any]],
    cur: dict[str, Any],
    auto: bool,
) -> dict[str, Any]:
    part, info = source
    via = _render.route()
    why = _why_not(via)
    if why:
        raise ThumbError(why)
    aspect = aspect_for(record_id, part, info)
    planned = prompts(rec, aspect)
    cid = _mcp_auth.company() if via == _render.MCP else None
    credits: float | None = None
    if via == _render.MCP:
        # 11/24: 썸네일도 시트·스토리보드 그림과 같이 creative_mcp.image_unlim()을 따른다
        params = _mcp.image_params(_render.mcp_image_model(), planned[0]["prompt"], "", aspect)
        try:
            credits = await _preflight(cid, params)
        except _ERRORS as error:
            raise ThumbError(_render._code(error)) from None  # noqa: SLF001
    round_no = int(cur.get("round") or 0) + 1
    key = f"{meta.context_id}:{ACTION}:{record_id}:{round_no}"
    seq = _reserve(ledger, key, via)
    title = (rec.get("storyboard") or {}).get("title_ko") or record_id
    billing = _render.image_billing(via)  # 11/24: 무제한이면 unlim("Higgsfield 무제한")
    params = {
        "record_id": record_id,
        "provider": _render._provider(via),  # noqa: SLF001
        "media_kind": "image",
        "count": COUNT,
        "aspect_ratio": aspect,
        "source_part": part,
        "source_sha256": info["sha256"],
        "usage_unit": "images",
        "usage_amount": COUNT,
        "ai_generated": True,
        "auto_requested": auto,
        "requested_by": (who or "")[:80],
        "summary": f"영상 썸네일 후보 {COUNT}장 만들기({aspect}): {title} "
        f"({_render._billing_ko(via, billing)}, AI 생성)",  # noqa: SLF001
    }
    if billing:
        params["billing"] = billing
        if credits is not None:
            params["credits_estimate"] = credits
    decision = await execute(
        ACTION, params, meta.model_copy(update={"idempotency_key": key}), _evidence(record_id)
    )
    base = {
        "key": key,
        "seq": seq,
        "round": round_no,
        "context_id": meta.context_id,
        "requested_by": who,
        "params": params,
        "route": via,
        "company_id": cid,
        "aspect": aspect,
        "source_part": part,
        "prompts": planned,
    }
    return _apply(ledger, record_id, seq, decision, base)


def _apply(
    ledger: MediaLedger,
    record_id: str,
    seq: int,
    decision: ExecuteDecision,
    base: dict[str, Any],
) -> dict[str, Any]:
    if decision.status == "held":
        return _set(
            record_id, status="awaiting_approval", review_id=decision.review_id, error=None, **base
        )
    if decision.status == "executed":
        _set(record_id, status="running", error=None, **base)
        _render._spawn(_make(ledger, record_id, seq, decision))  # noqa: SLF001
        return get(record_id)
    if decision.status == "verifying":
        return _set(record_id, status="awaiting_approval", **base)
    _render._safe(lambda: ledger.release(seq))  # noqa: SLF001
    return _set(record_id, status="blocked", error=f"gate_{decision.status}", **base)


async def check(ledger: MediaLedger, meta: RequestMeta, record_id: str) -> None:
    """사람 결정 대기면 같은 키·같은 내용으로 관문을 다시 묻는다(멱등). 승인됐으면 만든다."""
    cur = get(record_id)
    if cur.get("status") != "awaiting_approval" or not cur.get("key"):
        return
    stored = meta.model_copy(
        update={
            "idempotency_key": cur["key"],
            "context_id": cur.get("context_id") or meta.context_id,
        }
    )
    decision = await execute(ACTION, dict(cur.get("params") or {}), stored, _evidence(record_id))
    seq = int(cur.get("seq") or 0)
    if decision.status in ("held", "verifying"):
        return
    if decision.status == "executed":
        _set(record_id, status="running", error=None)
        _render._spawn(_make(ledger, record_id, seq, decision))  # noqa: SLF001
        return
    _render._safe(lambda: ledger.release(seq))  # noqa: SLF001
    _set(record_id, status="rejected", error=f"gate_{decision.status}")


def expire_stale(record_id: str) -> None:
    """서버가 다시 켜져 끊긴 'running'·'requesting'을 실패로 바꾼다(기록을 읽을 때)."""
    cur = get(record_id)
    if cur.get("status") not in ("running", "requesting") or not cur.get("at"):
        return
    try:
        age = (_now() - datetime.fromisoformat(cur["at"])).total_seconds()
    except ValueError:
        return
    if age > _render.STALE_S and not _render._tasks:  # noqa: SLF001
        _set(record_id, status="failed", error="interrupted")


# ---------------------------------------------------------------- 만들기 (승인 뒤)
def _reference(record_id: str, rec: dict[str, Any], folder: Path) -> bytes | None:
    """참조 그림: 영상 첫 프레임, 못 뽑으면 이미지 시트 그림. 둘 다 없으면 None."""
    source = source_video(rec)
    video = _video_path(record_id, source[1]) if source else None
    tmp = folder / ".ref.jpg"
    try:
        if video is not None and video.is_file() and FIRST_FRAME(video, tmp):
            return tmp.read_bytes()
        if ((rec.get("render") or {}).get("sheet_image") or {}).get("status") == "done":
            return _render.asset_path(record_id, "sheet_image").read_bytes()
    except OSError:
        return None
    finally:
        tmp.unlink(missing_ok=True)
    return None


async def _one_api(
    client: httpx.AsyncClient, prompt: str, negative: str, aspect: str, ref_url: str | None
) -> str:
    payload: dict[str, Any] = {
        _render.IMAGE_FIELDS["prompt"]: prompt,
        _render.IMAGE_FIELDS["aspect"]: aspect,
        _render.IMAGE_FIELDS["negative"]: negative,
    }
    if ref_url:
        payload[_render.IMAGE_FIELDS["refs"]] = [ref_url]
    job = await _render._submit(client, _render.image_model(), payload)  # noqa: SLF001
    return await _render._wait(client, job, "image")  # noqa: SLF001


async def _one_mcp(
    hf: _mcp.Higgsfield, prompt: str, negative: str, aspect: str, media: str | None
) -> str:
    params = _mcp.image_params(_render.mcp_image_model(), prompt, negative, aspect)
    if media:
        params = _mcp.with_media(params, media, _mcp.IMAGE_REF_ROLE)
    try:
        job = await _mcp.submit(hf, "image", params)
        return await _mcp.wait(hf, job, _render.SLEEP)
    except McpError as error:
        # 11/24: 무제한이 안 되면 크레딧으로 다시 시도하지 않고 멈춘다 (#1697과 같은 규칙)
        if _mcp.uses_unlim(params) and (no := _mcp.unlim_unavailable(error)) is not None:
            raise no from error
        raise


def _finish(raw: Path, folder: Path, model: str) -> dict[str, Any]:
    """받은 그림 → JPEG(긴 변 1280) → AI 표시(XMP) 확인 → `thm_<sha12>.jpg`."""
    jpg = raw.with_suffix(".norm.jpg")
    try:
        if not NORMALIZE(raw, jpg):
            if raw.read_bytes()[:2] != b"\xff\xd8":
                raise OSError("not_jpeg")
            jpg.write_bytes(raw.read_bytes())
        made_at = _now().strftime("%Y-%m-%dT%H:%M:%SZ")
        mark = {"ai_generated": "true", "ai_generator_model": model, "ai_generated_at": made_at}
        sns_label.inject_xmp(jpg, mark, AI_TEXT)
        if 'catbooks:ai_generated="true"' not in (sns_media.jpeg_xmp(jpg) or ""):
            raise sns_label.LabelError("metadata_not_written")
        sha = sns_media.sha256_file(jpg)
        media_id = f"thm_{sha[:12]}"
        os.replace(jpg, folder / f"{media_id}.jpg")
    finally:
        raw.unlink(missing_ok=True)
        jpg.unlink(missing_ok=True)
    return {"media_id": media_id, "sha256": sha, "ai_generated_at": made_at, "mark": mark}


async def _render_all(
    record_id: str, rec: dict[str, Any], cur: dict[str, Any], folder: Path
) -> tuple[list[dict[str, Any]], list[str]]:
    via = _render.MCP if cur.get("route") == _render.MCP else _render.API  # 승인받은 길 그대로
    aspect = str(cur.get("aspect") or _render.VIDEO_ASPECT)
    negative = _negative(rec)
    model = _render._model("image", via)  # noqa: SLF001
    ref = _reference(record_id, rec, folder)
    made: list[dict[str, Any]] = []
    errors: list[str] = []
    async with _render.CLIENT() as client:
        hf: _mcp.Higgsfield | None = None
        ref_id: str | None = None
        if via == _render.MCP:
            cid = cur.get("company_id")
            if not cid or not _mcp_auth.connected(cid):
                raise McpAuthError("not_connected")  # 키 길로 바꾸지 않는다
            hf = _mcp.Higgsfield(client, cid)
            if ref:
                ref_id = await _mcp.upload(hf, ref, f"{record_id}-thumb-ref.jpg", "image/jpeg")
        elif ref:
            ref_id = await _render._upload(client, ref)  # noqa: SLF001
        for n, item in enumerate(cur.get("prompts") or []):
            try:
                prompt = str(item.get("prompt") or "")
                if hf is not None:
                    url = await _one_mcp(hf, prompt, negative, aspect, ref_id)
                else:
                    url = await _one_api(client, prompt, negative, aspect, ref_id)
                raw = folder / f".raw_{n}"
                await _render._download(client, url, raw)  # noqa: SLF001
                done = _finish(raw, folder, model)
            except (*_ERRORS, sns_label.LabelError) as error:
                errors.append(_render._code(error))  # noqa: SLF001
                stop = ("credits_insufficient", _mcp.UNLIM_UNAVAILABLE)
                if isinstance(error, McpError) and error.code in stop:
                    break  # 남은 장도 못 만든다(무제한 불가면 크레딧으로 다시 시도하지 않는다)
                continue
            if any(m["media_id"] == done["media_id"] for m in made):
                continue  # 같은 그림은 한 번만
            made.append(done | {"kind": str(item.get("kind") or "")})
    return made, errors


def _write_meta(folder: Path, made: list[dict[str, Any]], model: str) -> None:
    records = {
        f"{m['media_id']}.jpg": {
            "machine_mark": True,
            "ai_generated": True,
            "ai_generator": "higgsfield",
            "ai_generator_model": model,
            "ai_generated_at": m["ai_generated_at"],
            "sha256": m["sha256"],
            "kind": m["kind"],
            "text": AI_TEXT,
        }
        for m in made
    }
    (folder / "meta.json").write_text(
        json.dumps({"ai_label": records, "made_at": _now().isoformat()}, ensure_ascii=False),
        encoding="utf-8",
    )


async def _make(ledger: MediaLedger, record_id: str, seq: int, decision: ExecuteDecision) -> None:
    rec = _creative.store().get(record_id) or {}
    cur = get(record_id)
    folder = _folder(record_id)
    via = _render.MCP if cur.get("route") == _render.MCP else _render.API
    try:
        folder.mkdir(parents=True, exist_ok=True)
        if via == _render.MCP:  # 승인 사이에 크레딧이 줄었을 수 있다
            first = str(((cur.get("prompts") or [{}])[0]).get("prompt") or "thumbnail")
            aspect = str(cur.get("aspect") or WIDE)
            params = _mcp.image_params(_render.mcp_image_model(), first, "", aspect)
            await _preflight(cur.get("company_id"), params)
        made, errors = await _render_all(record_id, rec, cur, folder)
    except _ERRORS as error:
        made, errors = [], [_render._code(error)]  # noqa: SLF001
    model = _render._model("image", via)  # noqa: SLF001
    if not made:
        logger.warning("썸네일 후보를 못 만듦: %s (%s)", errors, record_id)
        _render._safe(lambda: ledger.mark_failed(seq, _now()))  # noqa: SLF001
        first_error = errors[0] if errors else "no_result"
        # 11/24: 무제한이 안 되면 실패가 아니라 멈춤(크레딧으로 다시 시도하지 않는다, #1697과 같게)
        stopped = first_error == _mcp.UNLIM_UNAVAILABLE
        _set(record_id, status="blocked" if stopped else "failed", error=first_error)
        return
    _render._safe(lambda: _write_meta(folder, made, model))  # noqa: SLF001
    _render._safe(  # noqa: SLF001
        lambda: ledger.settle(
            seq,
            model_id=model,
            usage=usage_mod.UsageAmount("images", len(made)),
            usage_source="estimate",
            result_json=f'{{"provider":"{_render._provider(via)}"}}',  # noqa: SLF001
            now=_now(),
        )
    )
    candidates = [
        {
            "media_id": m["media_id"],
            "kind": m["kind"],
            "sha256": m["sha256"],
            "is_ai_generated": True,
            "ai_label_text": AI_TEXT,
            "ai_generator_model": model,
        }
        for m in made
    ]
    _set(
        record_id,
        status="done",
        error=None,
        errors=errors,
        candidates=candidates,
        chosen=None,
        cover_rev=int(cur.get("cover_rev") or 0) + 1,
        execution_id=decision.execution_id,
    )
    _reset_approvals(record_id)  # 후보가 바뀌면 지금 썸네일도 바뀐다


# ---------------------------------------------------------------- 지금 썸네일 · 고르기
def _candidate_ok(record_id: str, c: dict[str, Any]) -> tuple[bool, Path]:
    media_id = str(c.get("media_id") or "")
    path = _folder(record_id) / f"{media_id}.jpg"
    ok = (
        THUMB_ID.match(media_id) is not None
        and path.is_file()
        and sns_media.sha256_file(path) == c.get("sha256")
    )
    return ok, path


def chosen_id(state: dict[str, Any]) -> str | None:
    ids = [str(c.get("media_id")) for c in state.get("candidates") or []]
    if not ids:
        return None
    return state.get("chosen") if state.get("chosen") in ids else ids[0]


def current_cover(record_id: str) -> dict[str, Any] | None:
    """지금 썸네일: {media_id, path, sha256(지금 파일), ok, token}. 후보가 없으면 None.

    유튜브(`youtube.py`)와 인스타 올리기가 승인 해시·게시 직전 확인에 같이 쓴다."""
    state = get(record_id)
    media_id = chosen_id(state)
    if media_id is None:
        return None
    c = next(x for x in state["candidates"] if x.get("media_id") == media_id)
    ok, path = _candidate_ok(record_id, c)
    sha = sns_media.sha256_file(path) if ok else "missing"
    rev = int(state.get("cover_rev") or 0)
    return {
        "media_id": media_id,
        "path": path,
        "sha256": sha,
        "ok": ok,
        "token": f"{media_id}:{sha}:{rev}",
    }


def cover_token(record_id: str) -> str | None:
    cover = current_cover(record_id)
    return cover["token"] if cover is not None else None


def ensure_cover(record_id: str) -> None:
    """승인 요청 전: 고르지 않았으면 첫 후보를 적는다(`cover_rev`는 그대로)."""
    state = get(record_id)
    media_id = chosen_id(state)
    if media_id is not None and state.get("chosen") != media_id:
        _set(record_id, chosen=media_id)


def _reset_approvals(record_id: str) -> list[str]:
    """썸네일이 바뀌면 승인 대기 중인 유튜브·인스타 올리기를 초안으로 되돌린다(다시 승인받는다)."""
    from marketing_agent import instagram_post as _ig
    from marketing_agent import youtube as _yt

    reset = ["youtube"] if _yt.reset_for_cover(record_id) else []
    if _ig.reset_for_cover(_ig.Target("record", record_id)):
        reset.append("instagram")
    return reset


def select(record_id: str, media_id: str, who: str | None) -> dict[str, Any]:
    """사람이 고른 후보를 적는다. 승인 대기면 그 승인을 무효로 한다. 올린 뒤에는 cover_locked."""
    rec = _creative.store().get(record_id)
    if rec is None:
        raise ThumbError("not_found")  # 다른 회사 기록도 없는 것과 같다
    state = get(record_id)
    if media_id not in {str(c.get("media_id")) for c in state.get("candidates") or []}:
        raise ThumbError("cover_not_candidate")
    rev = int(state.get("cover_rev") or 0)
    if state.get("chosen") == media_id:
        return {"record_id": record_id, "cover_media_id": media_id, "changed": False,
                "cover_rev": rev, "approvals_reset": []}  # fmt: skip
    if _locked(rec):
        raise ThumbError("cover_locked")
    _set(record_id, chosen=media_id, cover_rev=rev + 1, chosen_by=(who or "")[:80] or None)
    return {
        "record_id": record_id,
        "cover_media_id": media_id,
        "changed": True,
        "cover_rev": rev + 1,
        "approvals_reset": _reset_approvals(record_id),
    }


def cover_file(record_id: str, media_id: str) -> Path | None:
    """미리보기용 후보 파일. 후보가 아니거나 파일이 기록과 다르면 None."""
    for c in get(record_id).get("candidates") or []:
        if c.get("media_id") == media_id:
            ok, path = _candidate_ok(record_id, c)
            return path if ok else None
    return None


__all__ = [
    "ACTION",
    "ThumbError",
    "check",
    "cover_file",
    "cover_token",
    "current_cover",
    "ensure_cover",
    "expire_stale",
    "get",
    "prompts",
    "request",
    "select",
]
