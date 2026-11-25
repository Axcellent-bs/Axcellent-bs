"""모션 참조 영상 (2026-11-25).

제작 기록에 사람이 올린 모션 참조 영상(`render.motion_video`, motion.mp4)이 있으면, 회사 계정(MCP)
영상 모델이 `video_references`를 받을 때 그 영상의 동작·카메라 움직임만 따라 만든다.
배경·사람·글자는 따라 하지 않고 캐릭터는 지금처럼 참조 이미지(가상 인물 원본)를 쓴다.

- 영상 모델에 보내기 전에 앞 `MAX_SECONDS`초만 남기고 소리를 뺀다(ffmpeg, 인자 목록으로만).
  ffmpeg가 없거나 실패하면 모션 참조 없이 지금처럼 만든다(영상 만들기를 막지 않는다).
- 대화방 의뢰 카드(영상 승인 대기)와 제작 기록에서 올릴 수 있다. 승인 뒤 영상을 만들 때 읽는다.
"""

from __future__ import annotations

import logging
import tempfile
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from marketing_agent.media.audio_mix import RunOutput, subprocess_runner
from marketing_agent.media.config import ffmpeg_bin

logger = logging.getLogger("marketing.motion_ref")

PART = "motion_video"
FILE = "motion.mp4"
MAX_SECONDS = 10  # 영상 모델에 보내는 모션 참조 길이 상한
TIMEOUT_S = 60
PROMPT_EN = (
    "Follow the dance moves, body movement, gestures and camera motion of the reference video "
    "closely. Do not copy its background, its people, or any text or logo from it; keep the "
    "character from the reference images and the setting from this prompt."
)
RUNNER: Callable[[Sequence[str], int], RunOutput] = subprocess_runner


def present(render: dict[str, Any] | None, folder: Path) -> Path | None:
    """기록에 붙은 모션 참조 영상 파일. 없으면 None."""
    item = (render or {}).get(PART) or {}
    path = folder / FILE
    if item.get("status") != "done" or item.get("asset") != FILE or not path.is_file():
        return None
    return path


def trimmed_bytes(path: Path, seconds: int = MAX_SECONDS) -> bytes | None:
    """앞 `seconds`초만, 소리 없이 다시 쓴 mp4 바이트. 실패하면 None."""
    with tempfile.TemporaryDirectory(prefix="motion-") as folder:
        out = Path(folder) / "motion.mp4"
        args = [
            ffmpeg_bin(), "-y", "-v", "error", "-i", str(path), "-t", str(seconds),
            "-an", "-c:v", "libx264", "-preset", "veryfast", "-crf", "23",
            "-pix_fmt", "yuv420p", "-movflags", "+faststart", str(out),
        ]  # fmt: skip
        try:
            done = RUNNER(args, TIMEOUT_S)
        except Exception as error:  # noqa: BLE001 - 모션 참조는 부가 기능이라 막지 않는다
            logger.warning("모션 참조 자르기 실패: %s", type(error).__name__)
            return None
        if done.returncode != 0 or not out.is_file() or out.stat().st_size == 0:
            logger.warning("모션 참조 자르기 실패: returncode=%s", done.returncode)
            return None
        return out.read_bytes()


def with_prompt(params: dict[str, Any]) -> dict[str, Any]:
    """영상 프롬프트 앞에 모션 참조 문장을 붙인다(상한은 기존 프롬프트 길이 자르기에 맡긴다)."""
    prompt = str(params.get("prompt") or "")
    return {**params, "prompt": f"{PROMPT_EN} {prompt}".strip()}
