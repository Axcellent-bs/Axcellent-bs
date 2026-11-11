"""BGM 후처리(ffmpeg): 영상의 오디오를 모두 버리고 AI가 만든 BGM만 입힌다.

흐름(명령 2개):
1. BGM을 영상 길이(최대 `MAX_VIDEO_S`=8초)로 자르고 페이드 인·아웃을 넣어 AAC로 인코딩한다.
2. 영상 스트림만(`-map 0:v:0`, 입력 오디오는 모두 제외) 복사(`-c:v copy`)하고 1의 결과를 붙인다.

안전 규칙:
- ffmpeg는 인자 목록으로만 부른다(`shell=True`·셸 문자열 금지).
  경로는 절대 경로로 풀어 인자로만 쓴다.
- ffmpeg 경로는 `MARKETING_FFMPEG_BIN`(기본 `ffmpeg`)에서만 온다. 없으면 `ffmpeg_not_found`.
- 결과 mp4를 박스 검사(`hdlr`의 `soun`)해 오디오 트랙이 1개인지 본다.
  아니면 파일을 지우고 실패.
- 실행한 인자 목록을 결과(`commands`)에 남긴다. 증빙용이므로 키·토큰은 인자에 들어가지 않는다.
- 테스트는 `runner`에 가짜 실행기를 넘긴다(실제 ffmpeg를 부르지 않는다).
"""

from __future__ import annotations

import hashlib
import subprocess
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path

from marketing_agent.media.config import ffmpeg_bin

MAX_VIDEO_S = 8.0  # safety 기준(video_duration)·단가표 max_duration_s와 같다
FADE_IN_RANGE = (0.1, 0.3)
FADE_OUT_RANGE = (0.5, 1.0)
DEFAULT_FADE_IN_S = 0.2
DEFAULT_FADE_OUT_S = 0.8
AUDIO_BITRATE = "128k"
FFMPEG_TIMEOUT_S = 120
MAX_FILE_BYTES = 64 * 1024 * 1024


class AudioMixError(Exception):
    """후처리 실패. `code`는 닫힌 집합이고 메시지에는 경로·키를 넣지 않는다."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code


@dataclass(frozen=True)
class RunOutput:
    returncode: int
    stderr: str = ""


Runner = Callable[[Sequence[str], int], RunOutput]


@dataclass(frozen=True)
class MixResult:
    output_path: Path
    sha256: str
    duration_s: float  # BGM을 자른 길이(= 영상 길이, 최대 8초)
    audio_tracks: int  # 항상 1
    commands: tuple[tuple[str, ...], ...]  # 실행한 ffmpeg 인자 목록(증빙)
    ffmpeg_bin: str


def subprocess_runner(args: Sequence[str], timeout_s: int) -> RunOutput:
    try:
        done = subprocess.run(  # noqa: S603 - 인자 목록으로만 부른다
            list(args), capture_output=True, text=True, timeout=timeout_s, check=False
        )
    except FileNotFoundError:
        raise AudioMixError("ffmpeg_not_found") from None
    except subprocess.TimeoutExpired:
        raise AudioMixError("ffmpeg_timeout") from None
    except OSError as error:
        raise AudioMixError("ffmpeg_failed", type(error).__name__) from None
    return RunOutput(done.returncode, (done.stderr or "")[-400:])


def count_tracks(mp4: bytes, handler: bytes) -> int:
    """mp4의 `hdlr` 박스 핸들러 타입(`soun`·`vide`)별 트랙 수(전체 파싱은 하지 않는다)."""
    count, start = 0, 0
    while (at := mp4.find(b"hdlr", start)) != -1:
        if mp4[at + 12 : at + 16] == handler:
            count += 1
        start = at + 4
    return count


def _seconds(value: float) -> str:
    return f"{value:.3f}".rstrip("0").rstrip(".")


def build_trim_args(
    ffmpeg: str, bgm: Path, out: Path, *, duration_s: float, fade_in_s: float, fade_out_s: float
) -> list[str]:
    fade_out_at = duration_s - fade_out_s
    filters = (
        f"afade=t=in:st=0:d={_seconds(fade_in_s)},"
        f"afade=t=out:st={_seconds(fade_out_at)}:d={_seconds(fade_out_s)}"
    )
    return [
        ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
        "-i", str(bgm), "-t", _seconds(duration_s), "-vn", "-af", filters,
        "-c:a", "aac", "-b:a", AUDIO_BITRATE, str(out),
    ]  # fmt: skip


def build_mux_args(ffmpeg: str, video: Path, audio: Path, out: Path) -> list[str]:
    return [
        ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
        "-i", str(video), "-i", str(audio),
        "-map", "0:v:0", "-map", "1:a:0",  # 입력 영상의 오디오는 어느 트랙도 가져오지 않는다
        "-c:v", "copy", "-c:a", "copy", "-shortest", "-movflags", "+faststart", str(out),
    ]  # fmt: skip


def overlay_bgm(
    video: Path,
    bgm: Path,
    output: Path,
    *,
    duration_s: float,
    fade_in_s: float = DEFAULT_FADE_IN_S,
    fade_out_s: float = DEFAULT_FADE_OUT_S,
    runner: Runner = subprocess_runner,
    ffmpeg: str | None = None,
    timeout_s: int = FFMPEG_TIMEOUT_S,
) -> MixResult:
    """영상(`video`)의 오디오를 버리고 BGM을 길이 `duration_s`로 잘라 입힌 mp4를 `output`에 쓴다."""
    if not 0 < duration_s <= MAX_VIDEO_S:
        raise AudioMixError("invalid_duration", f"0 < duration_s <= {_seconds(MAX_VIDEO_S)}")
    if not FADE_IN_RANGE[0] <= fade_in_s <= FADE_IN_RANGE[1]:
        raise AudioMixError("invalid_fade_in")
    if not FADE_OUT_RANGE[0] <= fade_out_s <= FADE_OUT_RANGE[1]:
        raise AudioMixError("invalid_fade_out")
    if fade_in_s + fade_out_s > duration_s:
        raise AudioMixError("fade_longer_than_clip")
    video, bgm, output = video.resolve(), bgm.resolve(), output.resolve()
    if not video.is_file():
        raise AudioMixError("video_not_found")
    if not bgm.is_file():
        raise AudioMixError("bgm_not_found")
    if output in (video, bgm):
        raise AudioMixError("output_overwrites_input")
    binary = ffmpeg or ffmpeg_bin()
    trimmed = output.with_name(f"{output.stem}.bgm.m4a")
    commands = (
        build_trim_args(
            binary, bgm, trimmed, duration_s=duration_s, fade_in_s=fade_in_s, fade_out_s=fade_out_s
        ),
        build_mux_args(binary, video, trimmed, output),
    )
    try:
        for args in commands:
            done = runner(args, timeout_s)
            if done.returncode != 0:
                raise AudioMixError("ffmpeg_failed", done.stderr.strip()[-200:])
        if not output.is_file() or output.stat().st_size > MAX_FILE_BYTES:
            raise AudioMixError("output_missing")
        data = output.read_bytes()
        audio_tracks = count_tracks(data, b"soun")
        if audio_tracks != 1 or count_tracks(data, b"vide") < 1:
            raise AudioMixError("output_track_check_failed", f"audio={audio_tracks}")
    except AudioMixError:
        output.unlink(missing_ok=True)  # 검사를 통과하지 못한 파일은 남기지 않는다
        raise
    finally:
        trimmed.unlink(missing_ok=True)
    return MixResult(
        output_path=output,
        sha256=hashlib.sha256(data).hexdigest(),
        duration_s=duration_s,
        audio_tracks=audio_tracks,
        commands=tuple(tuple(c) for c in commands),
        ffmpeg_bin=binary,
    )
