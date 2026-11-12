"""SNS 게시용 미디어(이미지·릴스) 규격 검사. `check_sns_post`가 쓴다.

- 규격은 Instagram Content Publishing 공식 문서 기준이다(설계 문서 6절, 분석 A-1). 숫자는 이 파일의
  상수 한 곳에서만 바꾼다. 공식 값이 바뀌면 `[확인 필요]`로 다시 본다.
- 이미지(JPEG)는 외부 도구 없이 헤더를 직접 읽는다. 릴스(영상)는 `ffprobe`로 읽고,
  `ffprobe`가 없으면
  "검사 못 함"(`unavailable`)으로 돌려준다. 못 읽은 값을 지어내거나 통과시키지 않는다.
- 자산 경로는 `MARKETING_MEDIA_DIR` 안쪽만 받는다(요청이 임의 파일을 읽게 하지 않는다).
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

MEDIA_DIR_ENV = "MARKETING_MEDIA_DIR"

# --- Instagram 규격 (공식 문서 확인분, 2026-11-05 분석) ---
IMAGE_MAX_BYTES = 8 * 1024 * 1024
IMAGE_ASPECT_MIN = 4 / 5  # 가로/세로. 4:5 ~ 1.91:1
IMAGE_ASPECT_MAX = 1.91
IMAGE_WIDTH_MIN = 320
IMAGE_WIDTH_MAX = 1440
REEL_MAX_BYTES = 300 * 1024 * 1024
REEL_DURATION_MIN_S = 3.0
REEL_DURATION_MAX_S = 15 * 60.0
REEL_WIDTH_MAX = 1920
REEL_FPS_MIN = 23.0
REEL_FPS_MAX = 60.0
REEL_CONTAINERS = ("mp4", "mov")
REEL_VIDEO_CODECS = ("h264", "hevc")
REEL_AUDIO_CODECS = ("aac",)
REEL_ASPECT = 9 / 16
CAROUSEL_MIN = (
    2  # 캐러셀 2~10장 [확인 필요: 공식 문서 대조 기록(sns-design.md)에 장수가 없다. 요청서 기준]
)
CAROUSEL_MAX = 10
# 캐러셀은 모든 장이 첫 장 비율로 잘린다(공식 문서). 그래서 장마다 비율이 같아야 한다(상대 오차)
CAROUSEL_ASPECT_TOLERANCE = 0.01
CAPTION_MAX = 2200
HASHTAG_MAX = 30
MENTION_MAX = 20
FFPROBE_TIMEOUT_S = 30

_SOF_MARKERS = frozenset(range(0xC0, 0xD0)) - {0xC4, 0xC8, 0xCC}
_XMP_HEADER = b"http://ns.adobe.com/xap/1.0/\x00"
_EPS = 1e-6


class MediaPathError(Exception):
    """자산 경로를 쓸 수 없다. `code`는 닫힌 집합이다."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


@dataclass
class MediaReport:
    kind: str
    problems: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    details: dict[str, Any] = field(default_factory=dict)
    unavailable: bool = False  # 검사 도구가 없어 검사하지 못함(통과가 아니다)


def media_root() -> Path | None:
    raw = os.getenv(MEDIA_DIR_ENV, "").strip()
    return Path(raw).resolve() if raw else None


def resolve_asset(ref: str) -> Path:
    """`ref`(미디어 폴더 기준 상대 경로, 또는 그 안쪽 절대 경로)를 실제 파일로 바꾼다."""
    root = media_root()
    if root is None:
        raise MediaPathError("media_dir_not_configured")
    candidate = Path(ref)
    path = (candidate if candidate.is_absolute() else root / candidate).resolve()
    if not path.is_relative_to(root):
        raise MediaPathError("asset_outside_media_dir")
    if not path.is_file():
        raise MediaPathError("asset_not_found")
    return path


def asset_ref_for(path: Path) -> str:
    root = media_root()
    assert root is not None
    return path.resolve().relative_to(root).as_posix()


def carousel_sha256(item_shas: list[str]) -> str:
    """캐러셀 전체의 내용 해시. 장수·각 장의 해시·순서가 하나라도 다르면 다르다."""
    canonical = f"carousel:{len(item_shas)}:" + ",".join(item_shas)
    return hashlib.sha256(canonical.encode()).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


# --- JPEG 헤더 ---


def _jpeg_segments(path: Path):
    """JPEG 세그먼트를 (marker, 내용 bytes)로 차례로 준다. SOS 앞까지만 읽는다."""
    with path.open("rb") as handle:
        if handle.read(2) != b"\xff\xd8":
            return
        while True:
            byte = handle.read(1)
            if not byte:
                return
            if byte != b"\xff":
                continue
            marker_byte = handle.read(1)
            while marker_byte == b"\xff":  # 채움 바이트
                marker_byte = handle.read(1)
            if not marker_byte:
                return
            marker = marker_byte[0]
            if marker in (0x00, 0x01) or 0xD0 <= marker <= 0xD8:
                continue
            if marker in (0xD9, 0xDA):  # EOI, SOS: 헤더 끝
                return
            length_bytes = handle.read(2)
            if len(length_bytes) < 2:
                return
            length = int.from_bytes(length_bytes, "big")
            if length < 2:
                return
            body = handle.read(length - 2)
            yield marker, body


def carousel_aspect_mismatch(sizes: list[tuple[int, int]]) -> bool:
    """장 크기((가로, 세로) 목록)의 비율이 첫 장과 허용 오차 밖으로 다르면 True."""
    ratios = [w / h for w, h in sizes if h > 0]
    if len(ratios) < 2:
        return False
    first = ratios[0]
    return any(abs(r - first) / first > CAROUSEL_ASPECT_TOLERANCE for r in ratios[1:])


def detected_format(path: Path) -> str:
    """머리 바이트로 본 형식(`jpeg`·`png`·`webp`·`gif`·`unknown`)."""
    with path.open("rb") as handle:
        head = handle.read(12)
    if head[:3] == b"\xff\xd8\xff":
        return "jpeg"
    if head[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if head[:4] == b"RIFF" and head[8:12] == b"WEBP":
        return "webp"
    if head[:4] == b"GIF8":
        return "gif"
    return "unknown"


def is_jpeg(path: Path) -> bool:
    with path.open("rb") as handle:
        return handle.read(3) == b"\xff\xd8\xff"


def jpeg_size(path: Path) -> tuple[int, int] | None:
    """(가로, 세로). 읽을 수 없으면 None."""
    for marker, body in _jpeg_segments(path):
        if marker in _SOF_MARKERS and len(body) >= 5:
            return int.from_bytes(body[3:5], "big"), int.from_bytes(body[1:3], "big")
    return None


def jpeg_xmp(path: Path) -> str | None:
    for marker, body in _jpeg_segments(path):
        if marker == 0xE1 and body.startswith(_XMP_HEADER):
            return body[len(_XMP_HEADER) :].decode("utf-8", "replace")
    return None


# --- 검사 ---


def check_image(path: Path) -> MediaReport:
    report = MediaReport("image")
    size_bytes = path.stat().st_size
    report.details["size_bytes"] = size_bytes
    if not is_jpeg(path):
        # 옛 png 산출물 등은 조용히 통과시키지 않는다: 사유 코드와 감지한 형식을 남긴다
        report.problems.append("image_not_jpeg")
        report.details["detected_format"] = detected_format(path)
        return report
    if size_bytes > IMAGE_MAX_BYTES:
        report.problems.append("image_too_large")
    dims = jpeg_size(path)
    if dims is None:
        report.problems.append("image_unreadable")
        return report
    width, height = dims
    report.details.update(width=width, height=height)
    if not IMAGE_WIDTH_MIN <= width <= IMAGE_WIDTH_MAX:
        report.problems.append("image_width_out_of_range")
    if height <= 0 or not (IMAGE_ASPECT_MIN - _EPS <= width / height <= IMAGE_ASPECT_MAX + _EPS):
        report.problems.append("image_aspect_out_of_range")
    return report


def _ratio(text: str | None) -> float | None:
    if not text:
        return None
    match = re.fullmatch(r"(\d+)/(\d+)", text)
    if not match or int(match.group(2)) == 0:
        return None
    return int(match.group(1)) / int(match.group(2))


def run_ffprobe(path: Path) -> dict[str, Any] | None:
    """ffprobe JSON. ffprobe가 없으면 None, 읽지 못하면 빈 dict."""
    ffprobe = shutil.which("ffprobe")
    if ffprobe is None:
        return None
    try:
        done = subprocess.run(  # noqa: S603 - 인자 목록으로만 부른다
            [
                ffprobe,
                "-v",
                "error",
                "-print_format",
                "json",
                "-show_format",
                "-show_streams",
                str(path),
            ],
            capture_output=True,
            timeout=FFPROBE_TIMEOUT_S,
            check=False,
        )
        if done.returncode != 0:
            return {}
        data = json.loads(done.stdout.decode("utf-8", "replace"))
    except (OSError, subprocess.SubprocessError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def check_reel(path: Path) -> MediaReport:
    report = MediaReport("reel")
    size_bytes = path.stat().st_size
    report.details["size_bytes"] = size_bytes
    probe = run_ffprobe(path)
    if probe is None:
        report.unavailable = True
        report.problems.append("media_check_unavailable_ffprobe_not_found")
        return report
    streams = probe.get("streams")
    if not streams:
        report.problems.append("video_unreadable")
        return report
    fmt = probe.get("format", {})
    video = next((s for s in streams if s.get("codec_type") == "video"), None)
    audio = next((s for s in streams if s.get("codec_type") == "audio"), None)
    if video is None:
        report.problems.append("video_stream_missing")
        return report

    container = str(fmt.get("format_name", ""))
    if not any(name in container.split(",") or name in container for name in REEL_CONTAINERS):
        report.problems.append("video_container_unsupported")
    codec = str(video.get("codec_name", ""))
    if codec not in REEL_VIDEO_CODECS:
        report.problems.append("video_codec_unsupported")
    if audio is not None and str(audio.get("codec_name", "")) not in REEL_AUDIO_CODECS:
        report.problems.append("audio_codec_unsupported")
    if size_bytes > REEL_MAX_BYTES:
        report.problems.append("video_too_large")

    try:
        duration = float(fmt.get("duration") or video.get("duration"))
    except (TypeError, ValueError):
        duration = None
    if duration is None or not REEL_DURATION_MIN_S <= duration <= REEL_DURATION_MAX_S:
        report.problems.append("video_duration_out_of_range")

    width, height = video.get("width"), video.get("height")
    if isinstance(width, int) and width > REEL_WIDTH_MAX:
        report.problems.append("video_width_too_large")
    fps = _ratio(video.get("avg_frame_rate")) or _ratio(video.get("r_frame_rate"))
    if fps is None or not REEL_FPS_MIN - _EPS <= fps <= REEL_FPS_MAX + _EPS:
        report.problems.append("video_fps_out_of_range")
    if isinstance(width, int) and isinstance(height, int) and height > 0:
        if abs(width / height - REEL_ASPECT) > 0.01:  # 9:16은 권장이라 경고만
            report.warnings.append("video_aspect_not_9_16")
    report.details.update(
        container=container,
        video_codec=codec,
        audio_codec=audio.get("codec_name") if audio else None,
        width=width,
        height=height,
        duration_s=duration,
        fps=fps,
    )
    tags = {str(k).lower(): str(v) for k, v in (fmt.get("tags") or {}).items()}
    report.details["tags"] = tags
    return report


def check_media(path: Path, kind: str) -> MediaReport:
    return check_image(path) if kind == "image" else check_reel(path)


# --- 기계판독 표시 읽기 ---


def read_machine_mark(path: Path, kind: str, report: MediaReport | None = None) -> dict[str, str]:
    """결과 파일에 들어 있는 `ai_generated` 표시를 읽는다. 없으면 빈 dict."""
    if kind == "image":
        xmp = jpeg_xmp(path) if is_jpeg(path) else None
        if not xmp:
            return {}
        return {
            key: value
            for key, value in re.findall(r'catbooks:(ai_[a-z_]+)="([^"]*)"', xmp)
            if key in ("ai_generated", "ai_generator_model", "ai_generated_at")
        }
    tags = (report.details.get("tags") if report else None) or {}
    return {k: v for k, v in tags.items() if k.startswith("ai_")}


def count_hashtags(caption: str) -> int:
    return len(re.findall(r"#[^\s#]+", caption))


def count_mentions(caption: str) -> int:
    return len(re.findall(r"(?<![\w.])@[A-Za-z0-9_.]+", caption))
