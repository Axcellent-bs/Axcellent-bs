"""긴 영상(단일 클립 한도 초과) 이어 붙이기: 비용 합산, 클립 순차 생성, ffmpeg concat.

클립 계획은 `video_limits.plan_clips`가 만든다. 여기는 그 계획을 실행한다.
- 클립은 한 번에 하나씩 순차 생성한다. 앞 클립이 실패하면 멈추고 이미 만든 파일은 지운다
  (이미 난 비용은 장부 예약(전체 추정액)이 그대로 센다).
- `extend` 클립은 앞 클립의 공급자 작업 id를 `Continuation`으로 넘긴다. `image` 클립은 앞 클립의
  마지막 프레임을 ffmpeg로 뽑아(jpeg) 시작 이미지로 넘긴다.
- 합치기는 ffmpeg concat(`-f concat -c copy`). 무음 규칙(`remove_audio`)이면 `-an`으로 합치고
  합친 파일에 오디오 트랙이 있으면 실패시킨다(`audio_track_present`). 합친 파일의 sha256과
  클립별 usage 합산을 결과로 돌려준다.
- ffmpeg는 인자 목록으로만 부른다. 경로·실행 함수는 주입할 수 있다(시험용 가짜 ffmpeg).
"""

from __future__ import annotations

import hashlib
import shutil
import subprocess
from collections.abc import Callable
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from marketing_agent.media.base import (
    Continuation,
    MediaProvider,
    MediaProviderError,
    MediaRequest,
    MediaResult,
    Usage,
)
from marketing_agent.media.config import ffmpeg_bin
from marketing_agent.media.gemini_video import has_audio_track
from marketing_agent.media.video_limits import Clip

FFMPEG_TIMEOUT_S = 180
MAX_FINAL_BYTES = 512 * 1024 * 1024
Runner = Callable[..., Any]


class VideoChainError(MediaProviderError):
    """이어 붙이기 실패. 메시지는 사유 코드뿐이다."""


def _ffmpeg(args: list[str], run: Runner, code: str) -> None:
    try:
        done = run(args, capture_output=True, timeout=FFMPEG_TIMEOUT_S, check=False)
    except FileNotFoundError:
        raise VideoChainError("ffmpeg_not_found") from None
    except subprocess.TimeoutExpired:
        raise VideoChainError(f"{code}_timeout") from None
    except OSError:
        raise VideoChainError(code) from None
    if done.returncode != 0:
        raise VideoChainError(code)


def last_frame(clip: Path, out: Path, *, ffmpeg: str, run: Runner) -> Path:
    """클립의 마지막 프레임을 jpeg로 뽑는다."""
    _ffmpeg(
        [ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-sseof", "-0.5", "-i", str(clip),
         "-frames:v", "1", "-q:v", "2", str(out)],
        run,
        "last_frame_failed",
    )  # fmt: skip
    if not out.is_file() or out.stat().st_size == 0:
        raise VideoChainError("last_frame_failed")
    return out


def concat(clips: list[Path], out: Path, *, remove_audio: bool, ffmpeg: str, run: Runner) -> None:
    listing = out.parent / "_concat.txt"
    listing.write_text("".join(f"file '{p.as_posix()}'\n" for p in clips), encoding="utf-8")
    args = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-f", "concat", "-safe", "0",
            "-i", str(listing)]  # fmt: skip
    args += ["-map", "0:v:0", "-an"] if remove_audio else ["-map", "0"]
    args += ["-c", "copy", "-movflags", "+faststart", str(out)]
    try:
        _ffmpeg(args, run, "concat_failed")
    finally:
        listing.unlink(missing_ok=True)
    if not out.is_file() or out.stat().st_size == 0 or out.stat().st_size > MAX_FINAL_BYTES:
        raise VideoChainError("concat_failed")
    if remove_audio and has_audio_track(out.read_bytes()):
        out.unlink(missing_ok=True)
        raise VideoChainError("audio_track_present")


def _sum_usage(parts: list[Usage | None]) -> Usage | None:
    known = [u for u in parts if u is not None]
    if not known or len(known) != len(parts):
        return None  # 일부 클립 사용량을 모르면 합산하지 않는다(정산은 추정액으로)
    inputs = [u.input_tokens for u in known]
    outputs = [u.output_tokens for u in known]
    return Usage(
        input_tokens=None if None in inputs else sum(i for i in inputs if i is not None),
        output_tokens=None if None in outputs else sum(o for o in outputs if o is not None),
    )


def generate_chain(
    adapter: MediaProvider,
    request: MediaRequest,
    clips: list[Clip],
    *,
    ffmpeg: str | None = None,
    run: Runner = subprocess.run,
) -> MediaResult:
    """계획된 클립을 순차 생성하고 ffmpeg로 이어 붙인다. `request.duration_s`는 총 길이다."""
    binary = ffmpeg or ffmpeg_bin()
    work = request.output_dir / "_clips"
    work.mkdir(parents=True, exist_ok=True)
    results: list[MediaResult] = []
    try:
        for index, clip in enumerate(clips):
            cont: Continuation | None = None
            if clip.mode == "extend":
                cont = Continuation("extend", previous_job_id=results[-1].provider_job_id)
            elif clip.mode == "image":
                frame = last_frame(
                    results[-1].asset_path, work / f"frame_{index}.jpg", ffmpeg=binary, run=run
                )
                cont = Continuation("image", image_path=frame)
            clip_dir = work / f"clip_{index}"
            clip_dir.mkdir(parents=True, exist_ok=True)
            results.append(
                adapter.generate(
                    replace(
                        request, duration_s=clip.duration_s, output_dir=clip_dir, continuation=cont
                    )
                )
            )
        final = request.output_dir / "asset.mp4"
        concat(
            [r.asset_path for r in results],
            final,
            remove_audio=request.remove_audio,
            ffmpeg=binary,
            run=run,
        )
        data = final.read_bytes()
    finally:
        shutil.rmtree(work, ignore_errors=True)
    return MediaResult(
        asset_path=final,
        sha256=hashlib.sha256(data).hexdigest(),
        provider=results[0].provider,
        model_id=results[0].model_id,
        usage=_sum_usage([r.usage for r in results]),
        generated_at=datetime.now(UTC),
        provider_job_id=",".join(r.provider_job_id for r in results)[:200],
        audio_stripped=request.remove_audio and any(r.audio_stripped for r in results),
        clip_count=len(results),
    )
