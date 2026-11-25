"""회사별 만들기 설정 읽기 + 모델 목록 (11/25).

저장은 dashboard-api가 `ax_agent_template.render_settings`로 한다. 여기서는
- `effective(cid)`: 화면 저장 값 → 서버 설정값(지금 동작) 순으로 합친 값
- `options(cid)`: 화면에 보여 줄 모델 목록. 회사 Higgsfield 연결의 `models_explore`
  (읽기, 크레딧 안 씀)를 하루 기억한다. 실패하면 아는 모델(`VIDEO_MODELS`) + 서버 설정 모델만.
  모르는 값은 빈 값(min_s·max_s는 None, aspects는 빈 목록). 지어내지 않는다.
"""

from __future__ import annotations

import logging
import os
import re
import time
from dataclasses import dataclass
from typing import Any, Literal

import httpx

from ax_agent_template import render_settings as store
from ax_contracts.schemas.marketing_render_settings import (
    DEFAULT_SPEED,
    SPEEDS,
    RenderImageModel,
    RenderModels,
    RenderSettingsOptionsOutput,
    RenderSettingsValues,
    RenderVideoModel,
)
from marketing_agent import creative_mcp as mcp
from marketing_agent.higgsfield_mcp import McpError, log_line
from marketing_agent.higgsfield_mcp_auth import McpAuthError
from marketing_agent.media.config import (
    HIGGSFIELD_MCP_IMAGE_MODEL_ENV,
    HIGGSFIELD_MCP_VIDEO_MODEL_ENV,
)

logger = logging.getLogger("marketing.render_settings")
CACHE_S = 24 * 3600
TIMEOUT_S = 20.0
SAVE_ASPECTS = ("9:16", "1:1", "16:9")
_cache: dict[str, tuple[float, RenderModels]] = {}  # 회사 id → (시각, 모델)
# 테스트가 바꾼다
CLIENT: Any = lambda: httpx.AsyncClient(timeout=TIMEOUT_S)  # noqa: E731
clock: Any = time.time


def reset_cache() -> None:
    _cache.clear()
    mcp.EXPLORED_MAX_S.clear()


@dataclass(frozen=True)
class Effective:
    image_model: str
    video_model: str
    image_credits_ok: bool
    video_seconds: str | int  # "plan" 또는 정수
    aspect: str  # "plan" 또는 비율
    speed: str = DEFAULT_SPEED  # 재생 속도 "1.0"(끔)·"1.2"·"1.5" (11/25)


SPEED_ENV = "MARKETING_VIDEO_SPEED"


def server_speed() -> str:
    """서버 설정 재생 속도. 없거나 모르는 값이면 "1.2"."""
    raw = os.getenv(SPEED_ENV, "").strip()
    return raw if raw in SPEEDS else DEFAULT_SPEED


def server_image_model() -> str:
    return os.getenv(HIGGSFIELD_MCP_IMAGE_MODEL_ENV, "").strip()


def server_video_model() -> str:
    return os.getenv(HIGGSFIELD_MCP_VIDEO_MODEL_ENV, "").strip()


def effective(cid: str | None = None) -> Effective:
    """회사의 만들기 설정. 비어 있는 칸은 서버 설정값(지금 동작)."""
    saved = store.saved_for(cid)
    return Effective(
        image_model=saved.get("image_model") or server_image_model(),
        video_model=saved.get("video_model") or server_video_model(),
        image_credits_ok=saved.get("image_credits_ok", True),
        video_seconds=saved.get("video_seconds", "plan"),
        aspect=saved.get("aspect", "plan"),
        speed=saved.get("speed") or server_speed(),
    )


def defaults() -> RenderSettingsValues:
    return RenderSettingsValues.model_validate(
        {
            "image_model": server_image_model() or None,
            "video_model": server_video_model() or None,
            "image_credits_ok": True,
            "video_seconds": "plan",
            "aspect": "plan",
            "speed": server_speed(),
        }
    )


# ---------------------------------------------------------------- 모델 목록
def _int(value: Any) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return int(value)
    if isinstance(value, str):
        found = re.match(r"\s*(\d+)", value)
        return int(found.group(1)) if found else None
    return None


def _durations(item: dict[str, Any]) -> tuple[int | None, int | None]:
    """(min_s, max_s). 칸 모양이 달라도 읽을 수 있는 것만 읽고 모르면 None."""
    raw = item.get("durations")
    nums: list[int] = []
    if isinstance(raw, list):
        nums = [n for n in (_int(v) for v in raw) if n is not None]
    elif isinstance(raw, dict):
        nums = [n for n in (_int(raw.get("min")), _int(raw.get("max"))) if n is not None]
    if nums:
        return min(nums), max(nums)
    lo = _int(item.get("min_duration") or item.get("min_s"))
    hi = _int(item.get("max_duration") or item.get("max_s"))
    return lo, hi


def _ident(item: dict[str, Any]) -> tuple[str, str] | None:
    mid = item.get("id") or item.get("model_id") or item.get("slug")
    if not isinstance(mid, str) or not mid.strip():
        return None
    name = item.get("name") or item.get("title") or item.get("display_name")
    return mid.strip()[:120], (name.strip() if isinstance(name, str) and name.strip() else mid)[
        :120
    ]


def _aspects(item: dict[str, Any]) -> list[str]:
    raw = item.get("aspect_ratios")
    return [a for a in raw if a in SAVE_ASPECTS] if isinstance(raw, list) else []


def known() -> RenderModels:
    """연결 없이 아는 것: `VIDEO_MODELS` + 서버 설정 모델. 모르는 값은 빈 값."""
    video: dict[str, RenderVideoModel] = {}
    for mid, spec in mcp.VIDEO_MODELS.items():
        video[mid] = RenderVideoModel(id=mid, name=mid, max_s=spec.get("max_s"))
    image: dict[str, RenderImageModel] = {}
    if sid := server_image_model():
        image[sid] = RenderImageModel(id=sid, name=sid)
    if (vid := server_video_model()) and vid not in video:
        video[vid] = RenderVideoModel(id=vid, name=vid, max_s=mcp.video_max_seconds(vid))
    return RenderModels(image=list(image.values()), video=list(video.values()))


def _merge(base: RenderModels, image_rows: list[dict[str, Any]],
           video_rows: list[dict[str, Any]]) -> RenderModels:  # fmt: skip
    image = {m.id: m for m in base.image}
    for row in image_rows:
        if (ident := _ident(row)) is not None:
            image[ident[0]] = RenderImageModel(id=ident[0], name=ident[1])
    video = {m.id: m for m in base.video}
    for row in video_rows:
        if (ident := _ident(row)) is None:
            continue
        lo, hi = _durations(row)
        old = video.get(ident[0])
        hi = hi if hi is not None else (old.max_s if old else None)
        video[ident[0]] = RenderVideoModel(id=ident[0], name=ident[1], min_s=lo, max_s=hi,
                                           aspects=_aspects(row))  # fmt: skip
        if hi:
            mcp.EXPLORED_MAX_S[ident[0]] = hi
    return RenderModels(image=list(image.values()), video=list(video.values()))


async def models(cid: str | None) -> tuple[RenderModels, Literal["higgsfield", "known"]]:
    """(모델 목록, 출처 higgsfield|known). 회사 연결로 하루 한 번만 묻는다."""
    base = known()
    if not cid:
        return base, "known"
    hit = _cache.get(cid)
    if hit is not None and clock() - hit[0] < CACHE_S:
        return hit[1], "higgsfield"
    try:
        async with CLIENT() as client:
            hf = mcp.Higgsfield(client, cid)
            image_rows = await mcp.explore_models(hf, "image")
            video_rows = await mcp.explore_models(hf, "video")
    except (McpError, McpAuthError, httpx.HTTPError, OSError, ValueError) as error:
        logger.warning("%s", log_line("render_settings: models_explore", error, cid))
        return base, "known"
    out = _merge(base, image_rows, video_rows)
    _cache[cid] = (clock(), out)
    return out, "higgsfield"


async def options(cid: str | None) -> RenderSettingsOptionsOutput:
    found, source = await models(cid)
    return RenderSettingsOptionsOutput(defaults=defaults(), models=found, models_source=source)
