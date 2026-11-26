"""사람이 고른 마케팅 영상을 AXcellent 홍보 사이트에 올리기 (11/26).

홍보 사이트는 로그인 없이 누구나 보는 정적 사이트다. 사이트가 백엔드를 부르지 않도록, 승인된
영상만 **공개 폴더**(`AX_PROMO_FEED_DIR`)에 복사하고 목록 `videos.json`을 쓴다.

- 대상: 제작 기록 영상(`CR-…`)과 총괄 의뢰 영상(초안 번호). 올리는 파일은 늘 **AI 표시를 입힌
  영상**이고, 제작 기록은 이미지 시트·스토리보드 그림이 있으면 같이 올린다.
- 올리기(`publish`) → `execute()` publish_promo_video(C, 사람 승인). 승인 전에는 폴더에 아무것도
  두지 않는다. 승인 대기는 `check`가 같은 멱등 키로 다시 묻는다.
- 내리기(`remove`) → `execute()` unpublish_promo_video(A). 파일을 지우고 목록을 다시 쓴다.
- **최대 3개**(승인 대기 포함). 넷째는 하나를 내려야 올린다.
- 사이트는 하나라 개수와 목록은 회사 전체에서 센다. 올릴 수 있는 회사는
  `AX_PROMO_FEED_COMPANIES`(쉼표, 기본 캣북스)뿐이다.

상태는 제작 기록 DB의 `marketing_promo_site` 표에 남긴다. 올린 뒤에는 폴더의 사본을 보여 주므로
원본 기록이 바뀌거나 지워져도 승인한 파일 그대로다.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import os
import shutil
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from pydantic import Field

from ax_agent_template.execute import execute
from ax_contracts.schemas import Evidence, ExecuteDecision, RequestMeta
from ax_contracts.schemas.common import TENANT_ID_CATBOOKS, AxModel
from marketing_agent import creative_records as _creative
from marketing_agent import creative_render as _render
from marketing_agent import creative_thumbnails as _thumbs
from marketing_agent import instagram_post as _instagram
from marketing_agent import request_video as _request_video
from marketing_agent import sns_media

logger = logging.getLogger("marketing.promo_site")

PUBLISH_ACTION = "publish_promo_video"  # 외부 공개 → 늘 사람 승인(C)
REMOVE_ACTION = "unpublish_promo_video"  # 내리기(안전한 방향, A)
FEED_DIR_ENV = "AX_PROMO_FEED_DIR"
COMPANIES_ENV = "AX_PROMO_FEED_COMPANIES"
FEED_NAME = "videos.json"
FILE_PREFIX = "pm-"  # 폴더에서 이 접두의 파일만 지운다
MAX_ITEMS = 3
MAX_VIDEO_BYTES = 80 * 1024 * 1024
PENDING = "awaiting_approval"
APPROVED = "approved"  # 승인됨, 폴더에 쓰는 데 아직 성공하지 못함(다음 확인 때 다시)
PUBLISHED = "published"
HOLDING = (PENDING, APPROVED, PUBLISHED)  # 3개 자리를 차지하는 상태
IMAGE_PARTS = (("sheet", "sheet_image"), ("board", "board_image"))
IMAGE_SUFFIXES = (".jpg", ".jpeg", ".png", ".webp")
ITEM_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$"

# 테스트가 바꾸는 자리: (제작 기록 | 의뢰 영상) → AI 표시 영상 (미디어 폴더 기준 참조, sha256)
RECORD_VIDEO: Callable[[str, str], Awaitable[tuple[str, str]]] | None = None
REQUEST_VIDEO: Callable[[Any, str], tuple[str, str]] | None = None


class PromoSiteStatusInput(AxModel):
    pass


class PromoSitePublishInput(AxModel):
    item_id: str = Field(pattern=ITEM_PATTERN)  # 제작 기록 번호 또는 의뢰 영상 초안 번호
    title: str = Field(min_length=1, max_length=40)  # 사이트에 보일 제목
    customer: str = Field(min_length=1, max_length=20)  # 사이트에 보일 고객사 이름
    # 사람이 "공개해도 되는 영상이에요(인물·음원·상표 권리)"를 체크했는가. true가 아니면 거절
    rights_confirmed: bool = False
    who: str | None = Field(default=None, max_length=80)


class PromoSiteRemoveInput(AxModel):
    item_id: str = Field(pattern=ITEM_PATTERN)
    who: str | None = Field(default=None, max_length=80)


class PromoError(Exception):
    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _now() -> datetime:
    return datetime.now(UTC)


def _clean(text: str) -> str:
    return " ".join(str(text or "").split())


# ---------------------------------------------------------------- 설정·상태
def feed_dir() -> Path | None:
    raw = os.getenv(FEED_DIR_ENV, "").strip()
    return Path(raw) if raw else None


def allowed() -> bool:
    """지금 요청의 회사가 AXcellent 홍보 사이트에 올릴 수 있나."""
    raw = os.getenv(COMPANIES_ENV, "").strip() or TENANT_ID_CATBOOKS
    return _creative.store().company_id in {c.strip() for c in raw.split(",") if c.strip()}


def _mine(item_id: str) -> dict[str, Any]:
    """이 회사의 항목 상태. 없거나 다른 회사 것이면 빈 값."""
    row = _creative.store().promo_get(item_id)
    return row[1] if row and row[0] == _creative.store().company_id else {}


def _put(item_id: str, state: dict[str, Any]) -> dict[str, Any]:
    _creative.store().promo_put(item_id, state)
    return state


def _public(item_id: str, state: dict[str, Any]) -> dict[str, Any]:
    keys = ("status", "title", "customer", "review_id", "error", "video_sha256", "published_at")
    return {"item_id": item_id} | {k: state.get(k) for k in keys}


def snapshot() -> dict[str, Any]:
    """화면이 읽는 값: 이 회사 항목, 쓰고 있는 자리 수, 올릴 수 있는지."""
    store = _creative.store()
    rows = store.promo_rows()
    return {
        "available": allowed(),
        "feed_ready": feed_dir() is not None,
        "limit": MAX_ITEMS,
        "used": sum(1 for _, _, s in rows if s.get("status") in HOLDING),
        "items": [_public(i, s) for i, company, s in rows if company == store.company_id],
    }


# ---------------------------------------------------------------- 올릴 파일
def _media(ref: str) -> Path:
    root = sns_media.media_root()
    if root is None:
        raise PromoError("media_dir_not_configured")
    path = (root / ref).resolve()
    if not path.is_relative_to(root):
        raise PromoError("media_dir_mismatch")
    return path


def _seconds(value: Any) -> float | None:
    return float(value) if isinstance(value, int | float) and value > 0 else None


def _record_images(record_id: str, rec: dict[str, Any]) -> dict[str, dict[str, str]]:
    """제작 기록의 이미지 시트·스토리보드 그림(끝났고 파일이 기록과 같은 것)."""
    root = sns_media.media_root()
    folder = _render.asset_path(record_id, "video").parent
    found: dict[str, dict[str, str]] = {}
    for name, part in IMAGE_PARTS:
        info = (rec.get("render") or {}).get(part) or {}
        asset = str(info.get("asset") or "")
        if info.get("status") != "done" or not asset or Path(asset).name != asset:
            continue
        path = (folder / asset).resolve()
        if root is None or not path.is_relative_to(root) or not path.is_file():
            continue
        if path.suffix.lower() not in IMAGE_SUFFIXES:
            continue
        sha = sns_media.sha256_file(path)
        if info.get("sha256") in (None, sha):
            found[name] = {"ref": path.relative_to(root).as_posix(), "sha256": sha}
    return found


async def _source(sns: Any, item_id: str) -> dict[str, Any]:
    """올릴 파일: AI 표시 영상, 재생 길이, 제작 과정 그림."""
    store = _creative.store()
    try:
        if item_id.startswith("CR-"):
            rec = store.get(item_id)
            if rec is None:
                raise PromoError("not_found")
            part = _render.publish_video_part(rec)
            if part is None:
                raise PromoError("video_not_ready")
            labeler = RECORD_VIDEO or _instagram._record_video  # noqa: SLF001 - 같은 라벨 파일
            ref, sha = await labeler(item_id, part)
            listed = next((r for r in store.list(200) if r["record_id"] == item_id), {})
            return {
                "video_ref": ref,
                "video_sha256": sha,
                "seconds": _seconds(listed.get("video_seconds")),
                "images": _record_images(item_id, rec),
            }
        if REQUEST_VIDEO is not None:
            ref, sha = REQUEST_VIDEO(sns, item_id)
        else:
            ref, sha, _ = _instagram._request_video(sns, item_id)  # noqa: SLF001
        return {
            "video_ref": ref,
            "video_sha256": sha,
            "seconds": _request_seconds(sns, item_id),
            "images": {},
        }
    except _instagram.InstagramError as error:
        raise PromoError(str(error)) from None


def _request_seconds(sns: Any, post_draft_id: str) -> float | None:
    root = sns_media.media_root()
    if root is None or sns is None:
        return None
    rows, _ = sns.list_by_source("brief", None, include_merged=False)
    for draft, _at in rows:
        if draft.post_draft_id == post_draft_id:
            return _seconds(_request_video._find_meta(root, draft).get("duration_s"))  # noqa: SLF001
    return None


# ---------------------------------------------------------------- 공개 폴더
def _hashed(item_id: str) -> str:
    """사이트에 보이는 번호(기록·초안 번호를 드러내지 않는다)."""
    return hashlib.sha256(item_id.encode()).hexdigest()[:12]


def _copy(source: Path, target: Path) -> None:
    """읽을 수 있는 권한으로, 다 쓴 뒤에 이름을 바꿔 넣는다(반쯤 쓴 파일이 보이지 않게)."""
    part = target.with_name(target.name + ".part")
    shutil.copyfile(source, part)
    os.chmod(part, 0o644)
    os.replace(part, target)


def _export(item_id: str, state: dict[str, Any]) -> dict[str, str]:
    """승인한 파일을 공개 폴더에 복사하고 파일 이름을 돌려준다."""
    feed = feed_dir()
    if feed is None:
        raise PromoError("feed_not_configured")
    video = _media(str(state.get("video_ref") or ""))
    if not video.is_file() or sns_media.sha256_file(video) != state.get("video_sha256"):
        raise PromoError("source_changed")
    if video.stat().st_size > MAX_VIDEO_BYTES:
        raise PromoError("video_too_large")
    feed.mkdir(parents=True, exist_ok=True)
    base = f"{FILE_PREFIX}{_hashed(item_id)}-{state['video_sha256'][:10]}"
    files = {"video": f"{base}.mp4"}
    _copy(video, feed / files["video"])
    poster = feed / f"{base}.jpg"
    frame = poster.with_name(f"{base}.frame.jpg")
    if _thumbs.FIRST_FRAME(video, frame):  # 대표 그림이 없어도 영상은 보인다
        _copy(frame, poster)
        files["poster"] = poster.name
    frame.unlink(missing_ok=True)
    for name, image in (state.get("images") or {}).items():
        source = _media(str(image.get("ref") or ""))
        if source.is_file() and sns_media.sha256_file(source) == image.get("sha256"):
            files[name] = f"{base}-{name}{source.suffix.lower()}"
            _copy(source, feed / files[name])
    return files


def write_feed() -> None:
    """올라가 있는 항목으로 목록을 다시 쓰고, 어느 항목에도 없는 파일을 지운다."""
    feed = feed_dir()
    if feed is None:
        return
    published = [
        (item_id, s)
        for item_id, _, s in _creative.store().promo_rows()
        if s.get("status") == PUBLISHED and (s.get("files") or {}).get("video")
    ]
    published.sort(key=lambda row: str(row[1].get("published_at") or ""), reverse=True)
    items = [
        {
            "id": _hashed(item_id),
            "title": s.get("title"),
            "customer": s.get("customer"),
            "seconds": s.get("seconds"),
            "published_at": s.get("published_at"),
            "video": s["files"]["video"],
            "poster": s["files"].get("poster"),
            "sheet": s["files"].get("sheet"),
            "board": s["files"].get("board"),
        }
        for item_id, s in published[:MAX_ITEMS]
    ]
    feed.mkdir(parents=True, exist_ok=True)
    part = feed / f"{FEED_NAME}.part"
    body = {"updated_at": _now().isoformat(), "items": items}
    part.write_text(json.dumps(body, ensure_ascii=False), encoding="utf-8")
    os.chmod(part, 0o644)
    os.replace(part, feed / FEED_NAME)
    keep = {name for _, s in published for name in (s.get("files") or {}).values()}
    for path in feed.iterdir():
        if path.name.startswith(FILE_PREFIX) and path.name not in keep:
            path.unlink(missing_ok=True)


async def _finish(item_id: str) -> dict[str, Any]:
    """승인된 항목을 폴더에 올린다. 폴더 쓰기에 실패하면 approved로 남겨 다음에 다시 한다."""
    state = _mine(item_id)
    if state.get("status") != APPROVED:
        return state
    try:
        files = await asyncio.to_thread(_export, item_id, state)
    except PromoError as error:
        if error.code in ("source_changed", "video_too_large"):  # 다시 해도 안 된다
            return _put(item_id, state | {"status": "failed", "error": error.code})
        return _put(item_id, state | {"error": error.code})
    except OSError as error:
        logger.warning("홍보 사이트 폴더 쓰기 실패 %s: %s", item_id, type(error).__name__)
        return _put(item_id, state | {"error": "feed_write_failed"})
    now = _mine(item_id)
    if now.get("status") != APPROVED:
        return now  # 쓰는 사이에 사람이 내렸다
    state = _put(
        item_id,
        now | {"status": PUBLISHED, "files": files, "error": None,
               "published_at": _now().isoformat()},
    )  # fmt: skip
    try:
        await asyncio.to_thread(write_feed)
    except OSError as error:
        logger.warning("홍보 사이트 목록 쓰기 실패: %s", type(error).__name__)
        return _put(item_id, state | {"status": APPROVED, "error": "feed_write_failed"})
    return state


# ---------------------------------------------------------------- 올리기·확인·내리기
def _evidence(item_id: str, what: str) -> Evidence:
    return Evidence(
        queries=["promo_site"],
        data_summary=f"{item_id} {what}",
        collected_at=_now(),
        ref_ids=[item_id],
    )


async def _apply(item_id: str, decision: ExecuteDecision, state: dict[str, Any]) -> dict[str, Any]:
    if decision.status == "held":
        return _put(item_id, state | {"status": PENDING, "review_id": decision.review_id})
    if decision.status == "executed":
        _put(item_id, state | {"status": APPROVED, "execution_id": decision.execution_id})
        return await _finish(item_id)
    if decision.status == "verifying":
        return _mine(item_id)
    status = "rejected" if decision.status == "rejected" else "failed"  # failed는 다시 올릴 수 있다
    return _put(item_id, state | {"status": status, "error": f"gate_{decision.status}"})


async def publish(
    meta: RequestMeta,
    sns: Any,
    item_id: str,
    title: str,
    customer: str,
    who: str | None,
    rights_confirmed: bool,
) -> dict[str, Any]:
    """홍보 사이트 공개를 사람 승인에 올린다."""
    if not allowed():
        raise PromoError("company_not_allowed")
    if not rights_confirmed or not who:
        raise PromoError("rights_not_confirmed")
    title, customer = _clean(title), _clean(customer)
    if not title or not customer:
        raise PromoError("title_required")
    if feed_dir() is None:
        raise PromoError("feed_not_configured")
    row = _creative.store().promo_get(item_id)
    if row and row[0] != _creative.store().company_id:
        raise PromoError("not_found")
    cur = row[1] if row else {}
    if cur.get("status") in HOLDING:
        return _public(item_id, cur)  # 이미 올렸거나 승인 대기. 바꾸려면 내리고 다시
    source = await _source(sns, item_id)
    same = (cur.get("video_sha256"), cur.get("title"), cur.get("customer"))
    if cur.get("status") == "rejected" and same == (source["video_sha256"], title, customer):
        return _public(item_id, cur)  # 사람이 거절한 같은 내용은 다시 묻지 않는다
    others = [s for i, _, s in _creative.store().promo_rows() if i != item_id]
    if sum(1 for s in others if s.get("status") in HOLDING) >= MAX_ITEMS:
        raise PromoError("limit_reached")
    # 내렸다 다시 올려도 옛 승인이 다시 쓰이지 않게 요청마다 새 키
    key = f"{meta.context_id}:{PUBLISH_ACTION}:{item_id}:{uuid.uuid4().hex[:12]}"
    images = {name: image["sha256"] for name, image in source["images"].items()}
    extra = " · 이미지 시트·스토리보드 그림 포함" if images else ""
    params = {
        "item_id": item_id,
        "platform": "promo_site",
        "title": title,
        "customer": customer,
        "video_sha256": source["video_sha256"],
        "image_sha256": images,
        "ai_generated": True,
        "requested_by": who[:80],
        "summary": f"홍보 사이트에 영상 공개(로그인 없이 누구나 봄): {title} ({customer}){extra}",
    }
    # 관문에 묻기 전에 자리를 잡는다(동시에 두 건을 올려 3개를 넘지 않게)
    state = _put(
        item_id,
        source | {"status": PENDING, "title": title, "customer": customer, "key": key,
                  "context_id": meta.context_id, "requested_by": who, "params": params},
    )  # fmt: skip
    try:
        decision = await execute(
            PUBLISH_ACTION,
            params,
            meta.model_copy(update={"idempotency_key": key}),
            _evidence(item_id, "홍보 사이트 공개"),
        )
    except Exception:
        _creative.store().promo_delete(item_id)
        raise
    return _public(item_id, await _apply(item_id, decision, state))


async def check(meta: RequestMeta, item_id: str) -> None:
    """승인 대기면 같은 키·같은 내용으로 관문을 다시 묻고(멱등), 승인됐으면 올린다."""
    cur = _mine(item_id)
    if cur.get("status") == APPROVED:
        await _finish(item_id)
        return
    if cur.get("status") != PENDING or not cur.get("key"):
        return
    stored = meta.model_copy(
        update={
            "idempotency_key": cur["key"],
            "context_id": cur.get("context_id") or meta.context_id,
        }
    )
    decision = await execute(
        PUBLISH_ACTION,
        dict(cur.get("params") or {}),
        stored,
        _evidence(item_id, "홍보 사이트 공개"),
    )
    if decision.status in ("held", "verifying"):
        return
    now = _mine(item_id)
    if now.get("status") != PENDING or now.get("key") != cur["key"]:
        return  # 묻는 사이에 사람이 내렸다
    await _apply(item_id, decision, now)


async def check_all(meta: RequestMeta) -> None:
    store = _creative.store()
    waiting = [
        item_id
        for item_id, company, s in store.promo_rows()
        if company == store.company_id and s.get("status") in (PENDING, APPROVED)
    ]
    for item_id in waiting:
        try:
            await check(meta, item_id)
        except Exception:  # noqa: BLE001 - 승인 확인 실패는 읽기를 막지 않는다
            logger.warning("홍보 사이트 승인 확인 실패: %s", item_id)


async def remove(meta: RequestMeta, item_id: str, who: str | None) -> dict[str, Any]:
    """홍보 사이트에서 내린다(승인 대기 중이면 취소)."""
    cur = _mine(item_id)
    if not cur:
        raise PromoError("not_found")
    if cur.get("status") in HOLDING:
        key = f"{meta.context_id}:{REMOVE_ACTION}:{item_id}:{uuid.uuid4().hex[:12]}"
        decision = await execute(
            REMOVE_ACTION,
            {
                "item_id": item_id,
                "platform": "promo_site",
                "requested_by": (who or "")[:80],
                "summary": f"홍보 사이트에서 영상 내리기: {cur.get('title') or item_id}",
            },
            meta.model_copy(update={"idempotency_key": key}),
            _evidence(item_id, "홍보 사이트에서 내리기"),
        )
        if decision.status != "executed":
            raise PromoError("gate_held" if decision.status == "held" else "gate_blocked")
    _creative.store().promo_delete(item_id)
    try:
        await asyncio.to_thread(write_feed)
    except OSError as error:
        logger.warning("홍보 사이트 목록 쓰기 실패: %s", type(error).__name__)
        raise PromoError("feed_write_failed") from None
    return _public(item_id, {"status": "removed", "title": cur.get("title")})


__all__ = [
    "PromoError",
    "PromoSitePublishInput",
    "PromoSiteRemoveInput",
    "PromoSiteStatusInput",
    "check",
    "check_all",
    "publish",
    "remove",
    "snapshot",
    "write_feed",
]
