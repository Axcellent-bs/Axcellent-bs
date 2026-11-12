"""이미지·영상에 AI 라벨 문구를 입힌다(ffmpeg `drawtext`). SNS 설계 5절, D3 단계.

- 문구·위치·크기·표시 시간·글꼴·화질은 `sns_label.toml` 한 곳에서만 읽는다(코드에 박지 않는다).
  문구 기본값은 `[확인 필요 — 팀·변호사 승인]` 임시 문구이고 확정 문구가 아니다.
- 결과는 `<out_dir>/labeled.jpg|mp4`와 `meta.json`이다. 원본은 `out_dir`에 그대로 두거나
  `raw.*`로 복사해
  보관하고 고치지 않는다.
- 기계판독 표시: 사람이 보는 오버레이와 함께 결과 파일 메타데이터에
  `ai_generated=true`, 생성 모델, 시각을
  넣는다. 이미지는 XMP(APP1, IPTC `trainedAlgorithmicMedia` 포함), 영상은 MP4 메타데이터 태그다.
  외부 모델이 넣은 SynthID·C2PA는 재저장하면 사라질 수 있어 이 표시로 대신하지 않고 따로 둔다.
- ffmpeg·ffprobe·한글 글꼴이 없으면 `LabelError`로 거절한다.
  조용히 건너뛰거나 라벨 없는 파일을 만들지 않는다.

명령줄: `python -m marketing_agent.sns_label <원본> <out_dir> --model <생성 모델 id>`
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
import tempfile
import tomllib
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from xml.sax.saxutils import quoteattr

from marketing_agent.sns_media import jpeg_xmp, run_ffprobe, sha256_file

CONFIG_ENV = "MARKETING_SNS_LABEL_CONFIG"
FONT_ENV = "MARKETING_SNS_LABEL_FONT"
DEFAULT_CONFIG = Path(__file__).with_name("sns_label.toml")
IMAGE_SUFFIXES = frozenset({".jpg", ".jpeg", ".png", ".webp", ".bmp"})
VIDEO_SUFFIXES = frozenset({".mp4", ".mov", ".m4v", ".webm", ".mkv"})
_SAFE = re.compile(r"[A-Za-z0-9#_.]+")
_POSITIONS = ("top", "center", "bottom")


class LabelError(Exception):
    """오버레이를 만들 수 없다. `code`는 닫힌 집합이고 `detail`에는 비밀값이 없다."""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code
        self.detail = detail


@dataclass(frozen=True)
class LabelConfig:
    text_status: str
    text_video: str
    text_image: str
    position: str
    margin_ratio: float
    side_margin_ratio: float
    font_size_ratio: float
    min_font_px: int
    char_width_factor: float
    line_spacing_px: int
    font_color: str
    box_color: str
    box_opacity: float
    box_border_ratio: float
    display_time: str
    font_file: str
    font_candidates: tuple[str, ...]
    jpeg_quality: int
    video_crf: int
    video_preset: str
    ffmpeg_timeout_s: int
    source: str = ""
    sha256: str = ""
    font_bundled: str = ""  # 패키지 폴더 기준 상대 경로(번들 글꼴)

    def text_for(self, kind: str) -> str:
        return self.text_image if kind == "image" else self.text_video


def load_config(path: str | Path | None = None) -> LabelConfig:
    chosen = Path(path) if path else Path(os.getenv(CONFIG_ENV, "").strip() or DEFAULT_CONFIG)
    try:
        raw_bytes = chosen.read_bytes()
        data = tomllib.loads(raw_bytes.decode("utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError) as error:
        raise LabelError("config_unreadable", type(error).__name__) from error
    try:
        text, layout, style = data["text"], data["layout"], data["style"]
        display, font, output = data["display"], data["font"], data["output"]
        config = LabelConfig(
            text_status=str(text["status"]),
            text_video=str(text["video"]).strip(),
            text_image=str(text["image"]).strip(),
            position=str(layout["position"]),
            margin_ratio=float(layout["margin_ratio"]),
            side_margin_ratio=float(layout["side_margin_ratio"]),
            font_size_ratio=float(layout["font_size_ratio"]),
            min_font_px=int(layout["min_font_px"]),
            char_width_factor=float(layout["char_width_factor"]),
            line_spacing_px=int(layout["line_spacing_px"]),
            font_color=str(style["font_color"]),
            box_color=str(style["box_color"]),
            box_opacity=float(style["box_opacity"]),
            box_border_ratio=float(style["box_border_ratio"]),
            display_time=str(display["time"]),
            font_file=str(font.get("file", "")).strip(),
            font_candidates=tuple(str(c) for c in font.get("candidates", [])),
            font_bundled=str(font.get("bundled", "")).strip(),
            jpeg_quality=int(output["jpeg_quality"]),
            video_crf=int(output["video_crf"]),
            video_preset=str(output["video_preset"]),
            ffmpeg_timeout_s=int(output["ffmpeg_timeout_s"]),
            source=str(chosen),
            sha256=hashlib.sha256(raw_bytes).hexdigest(),
        )
    except (KeyError, TypeError, ValueError) as error:
        raise LabelError("config_invalid", f"{type(error).__name__}: {error}") from error
    _validate(config)
    return config


def _validate(c: LabelConfig) -> None:
    problems: list[str] = []
    if not c.text_video or not c.text_image:
        problems.append("text_empty")
    if c.position not in _POSITIONS:
        problems.append("position")
    if c.display_time != "full":
        problems.append("display_time(only 'full')")
    for name in ("margin_ratio", "side_margin_ratio", "font_size_ratio", "char_width_factor"):
        if not 0 < getattr(c, name) < 1 + (name == "char_width_factor"):
            problems.append(name)
    if not 0 <= c.box_opacity <= 1:
        problems.append("box_opacity")
    if not 0 <= c.box_border_ratio <= 2:
        problems.append("box_border_ratio")
    if c.min_font_px < 1 or c.line_spacing_px < 0:
        problems.append("font_px")
    if not 1 <= c.jpeg_quality <= 100:
        problems.append("jpeg_quality")
    if not 0 <= c.video_crf <= 51:
        problems.append("video_crf")
    if not (_SAFE.fullmatch(c.font_color) and _SAFE.fullmatch(c.box_color)):
        problems.append("color")
    if not _SAFE.fullmatch(c.video_preset):
        problems.append("video_preset")
    if c.ffmpeg_timeout_s < 1:
        problems.append("ffmpeg_timeout_s")
    if problems:
        raise LabelError("config_invalid", ",".join(problems))


def bundled_font(config: LabelConfig) -> Path | None:
    """번들 글꼴(Pretendard Std Bold, OFL 1.1). 패키지 폴더 밖 경로는 쓰지 않는다."""
    if not config.font_bundled:
        return None
    base = Path(__file__).resolve().parent
    path = (base / config.font_bundled).resolve()
    return path if path.is_relative_to(base) and path.is_file() else None


def find_font(config: LabelConfig) -> str:
    """글꼴 순서: 환경변수 → toml `file` → 번들 → 시스템 후보. 없으면 거절한다(fail-closed)."""
    explicit = os.getenv(FONT_ENV, "").strip() or config.font_file
    if explicit:
        if not Path(explicit).is_file():
            raise LabelError("font_not_found", "설정한 글꼴 파일이 없다")
        return explicit
    bundled = bundled_font(config)
    if bundled is not None:
        return str(bundled)
    for candidate in config.font_candidates:
        if Path(candidate).is_file():
            return candidate
    raise LabelError("font_not_found", "한글 글꼴을 찾지 못했다(sns_label.toml [font])")


def _tools() -> tuple[str, str]:
    ffmpeg, ffprobe = shutil.which("ffmpeg"), shutil.which("ffprobe")
    if ffmpeg is None:
        raise LabelError("ffmpeg_not_found")
    if ffprobe is None:
        raise LabelError("ffprobe_not_found")
    return ffmpeg, ffprobe


def media_kind_of(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in IMAGE_SUFFIXES:
        return "image"
    if suffix in VIDEO_SUFFIXES:
        return "video"
    raise LabelError("unsupported_media_type", suffix)


def wrap_text(text: str, width_px: int, font_px: int, config: LabelConfig) -> str:
    """글자 폭을 `char_width_factor`로 어림해 줄을 나눈다(나눌 자리는 공백 우선)."""
    per_line = max(
        4, int(width_px * (1 - 2 * config.side_margin_ratio) / (font_px * config.char_width_factor))
    )
    lines: list[str] = []
    current = ""
    for word in text.split(" "):
        candidate = f"{current} {word}".strip()
        if len(candidate) <= per_line:
            current = candidate
            continue
        if current:
            lines.append(current)
        while len(word) > per_line:  # 공백 없이 긴 낱말은 글자 수로 자른다
            lines.append(word[:per_line])
            word = word[per_line:]
        current = word
    if current:
        lines.append(current)
    return "\n".join(lines)


def _escape_filter_path(path: str) -> str:
    return path.replace("\\", "/").replace(":", r"\:").replace("'", "")


def build_drawtext(
    config: LabelConfig, font: str, width: int, height: int, textfile: str, text: str
) -> str:
    font_px = max(config.min_font_px, round(height * config.font_size_ratio))
    wrapped = wrap_text(text, width, font_px, config)
    Path(textfile).write_text(wrapped, encoding="utf-8")
    margin = round(height * config.margin_ratio)
    y = {"top": str(margin), "center": "(h-text_h)/2", "bottom": f"h-text_h-{margin}"}[
        config.position
    ]
    border = max(1, round(font_px * config.box_border_ratio))
    return (
        f"drawtext=fontfile='{_escape_filter_path(font)}':textfile={Path(textfile).name}"
        f":fontsize={font_px}:fontcolor={config.font_color}:line_spacing={config.line_spacing_px}"
        f":x=(w-text_w)/2:y={y}"
        f":box=1:boxcolor={config.box_color}@{config.box_opacity}:boxborderw={border}"
    )


# --- 기계판독 표시 ---


def machine_mark(model: str, at: datetime) -> dict[str, str]:
    return {
        "ai_generated": "true",
        "ai_generator_model": model,
        "ai_generated_at": at.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


def build_xmp(mark: dict[str, str]) -> bytes:
    attrs = " ".join(f"catbooks:{k}={quoteattr(v)}" for k, v in mark.items())
    packet = (
        '<?xpacket begin="﻿" id="W5M0MpCehiHzreSzNTczkc9d"?>'
        '<x:xmpmeta xmlns:x="adobe:ns:meta/">'
        '<rdf:RDF xmlns:rdf="http://www.w3.org/1999/02/22-rdf-syntax-ns#">'
        '<rdf:Description rdf:about="" '
        'xmlns:Iptc4xmpExt="http://iptc.org/std/Iptc4xmpExt/2008-02-18/" '
        'xmlns:catbooks="urn:catbooks:ai-label:1.0:" '
        'Iptc4xmpExt:DigitalSourceType="http://cv.iptc.org/newscodes/digitalsourcetype/'
        f'trainedAlgorithmicMedia" {attrs}/>'
        "</rdf:RDF></x:xmpmeta>"
        '<?xpacket end="w"?>'
    )
    return packet.encode("utf-8")


def inject_xmp(jpeg: Path, mark: dict[str, str]) -> None:
    """JPEG에 XMP APP1 세그먼트를 넣는다(APP0 JFIF 뒤, 없으면 SOI 바로 뒤). 기존 XMP는 바꾼다."""
    data = jpeg.read_bytes()
    if data[:2] != b"\xff\xd8":
        raise LabelError("metadata_not_written", "JPEG가 아니다")
    payload = b"http://ns.adobe.com/xap/1.0/\x00" + build_xmp(mark)
    if len(payload) + 2 > 0xFFFF:
        raise LabelError("metadata_not_written", "XMP가 너무 크다")
    segment = b"\xff\xe1" + (len(payload) + 2).to_bytes(2, "big") + payload
    pos = 2
    if data[pos : pos + 2] == b"\xff\xe0":  # APP0 뒤에 둔다
        pos += 2 + int.from_bytes(data[pos + 2 : pos + 4], "big")
    tmp = jpeg.with_name(jpeg.name + ".tmp")
    tmp.write_bytes(data[:pos] + segment + data[pos:])
    os.replace(tmp, jpeg)


# --- 본체 ---


@dataclass(frozen=True)
class LabelResult:
    labeled_path: Path
    labeled_sha256: str
    source_path: Path
    source_sha256: str
    meta_path: Path
    media_kind: str
    mark: dict[str, str]
    text: str


def _run_ffmpeg(args: list[str], cwd: str, timeout: int) -> None:
    try:
        done = subprocess.run(  # noqa: S603 - 인자 목록으로만 부른다
            args, cwd=cwd, capture_output=True, timeout=timeout, check=False
        )
    except subprocess.TimeoutExpired as error:
        raise LabelError("ffmpeg_timeout") from error
    except OSError as error:
        raise LabelError("ffmpeg_failed", type(error).__name__) from error
    if done.returncode != 0:
        tail = done.stderr.decode("utf-8", "replace").strip().splitlines()[-3:]
        raise LabelError("ffmpeg_failed", " | ".join(tail)[:400])


def _probe_size(source: Path) -> tuple[int, int]:
    data = run_ffprobe(source)
    video = next(
        (s for s in (data or {}).get("streams", []) if s.get("codec_type") == "video"), None
    )
    if (
        not video
        or not isinstance(video.get("width"), int)
        or not isinstance(video.get("height"), int)
    ):
        raise LabelError("source_unreadable")
    return video["width"], video["height"]


def apply_label(
    source: str | Path,
    out_dir: str | Path,
    *,
    generator_model: str,
    config: LabelConfig | None = None,
    now: datetime | None = None,
    media_kind: str | None = None,
) -> LabelResult:
    src = Path(source)
    if not src.is_file():
        raise LabelError("source_not_found")
    if not generator_model.strip():
        raise LabelError("generator_model_required")
    cfg = config or load_config()
    kind = media_kind or media_kind_of(src)
    if kind not in ("image", "video"):
        raise LabelError("unsupported_media_type", kind)
    font = find_font(cfg)
    ffmpeg, _ = _tools()
    width, height = _probe_size(src)
    at = now or datetime.now(UTC)
    mark = machine_mark(generator_model.strip(), at)
    text = cfg.text_for(kind)

    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    source_sha = sha256_file(src)
    if src.resolve().parent != out.resolve():  # 원본 보관
        raw = out / f"raw{src.suffix.lower()}"
        shutil.copy2(src, raw)
        keep = raw
    else:
        keep = src
    suffix = ".jpg" if kind == "image" else ".mp4"
    final = out / f"labeled{suffix}"

    with tempfile.TemporaryDirectory(prefix="snslabel_") as work:
        draw = build_drawtext(cfg, font, width, height, str(Path(work) / "label.txt"), text)
        tmp_out = Path(work) / f"out{suffix}"
        base = [ffmpeg, "-y", "-hide_banner", "-loglevel", "error", "-i", str(src.resolve())]
        if kind == "image":
            qscale = max(2, min(31, round(2 + (100 - cfg.jpeg_quality) * 29 / 99)))
            args = [*base, "-vf", draw, "-frames:v", "1", "-q:v", str(qscale), str(tmp_out)]
        else:
            args = [
                *base,
                "-map", "0:v:0", "-map", "0:a:0?",
                "-vf", draw,
                "-c:v", "libx264", "-preset", cfg.video_preset, "-crf", str(cfg.video_crf),
                "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-b:a", "128k", "-ar", "48000",
                "-movflags", "+faststart+use_metadata_tags",
                *[a for k, v in mark.items() for a in ("-metadata", f"{k}={v}")],
                str(tmp_out),
            ]  # fmt: skip
        _run_ffmpeg(args, work, cfg.ffmpeg_timeout_s)
        if kind == "image":
            inject_xmp(tmp_out, mark)
        shutil.move(str(tmp_out), final)

    _verify_mark(final, kind, mark)
    labeled_sha = sha256_file(final)
    meta_path = out / "meta.json"
    _write_meta(meta_path, cfg, kind, keep, source_sha, final, labeled_sha, mark, text, at)
    return LabelResult(final, labeled_sha, keep, source_sha, meta_path, kind, mark, text)


def _verify_mark(final: Path, kind: str, mark: dict[str, str]) -> None:
    if kind == "image":
        xmp = jpeg_xmp(final) or ""
        ok = 'catbooks:ai_generated="true"' in xmp
    else:
        fmt = (run_ffprobe(final) or {}).get("format", {})
        tags = {str(k).lower(): str(v) for k, v in (fmt.get("tags") or {}).items()}
        ok = all(tags.get(k) == v for k, v in mark.items())
    if not ok:
        raise LabelError("metadata_not_written", "결과 파일에서 기계판독 표시를 읽지 못했다")


def _write_meta(
    meta_path: Path,
    cfg: LabelConfig,
    kind: str,
    source: Path,
    source_sha: str,
    final: Path,
    labeled_sha: str,
    mark: dict[str, str],
    text: str,
    at: datetime,
) -> None:
    existing: dict[str, Any] = {}
    if meta_path.is_file():
        try:
            loaded = json.loads(meta_path.read_text(encoding="utf-8"))
            existing = loaded if isinstance(loaded, dict) else {}
        except (OSError, ValueError):
            existing = {}
    existing["ai_label"] = {
        "label_overlay": True,
        "machine_mark": True,
        "media_kind": kind,
        "text": text,
        "text_status": cfg.text_status,
        "config_source": Path(cfg.source).name,  # 경로는 기록하지 않는다
        "config_sha256": cfg.sha256,
        "source_file": source.name,
        "source_sha256": source_sha,
        "labeled_file": final.name,
        "labeled_sha256": labeled_sha,
        "ai_generated": True,
        "ai_generator_model": mark["ai_generator_model"],
        "ai_generated_at": mark["ai_generated_at"],
        "labeled_at": at.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    meta_path.write_text(json.dumps(existing, ensure_ascii=False, indent=2), encoding="utf-8")


def main(argv: list[str] | None = None) -> int:
    import argparse

    parser = argparse.ArgumentParser(description="이미지·영상에 AI 라벨 오버레이를 입힌다")
    parser.add_argument("source")
    parser.add_argument("out_dir")
    parser.add_argument("--model", required=True, help="생성 모델 id(기록용)")
    parser.add_argument("--config", default=None)
    args = parser.parse_args(argv)
    try:
        cfg = load_config(args.config)
        result = apply_label(args.source, args.out_dir, generator_model=args.model, config=cfg)
    except LabelError as error:
        print(f"거절: {error}")
        return 2
    print(f"{result.labeled_path} sha256={result.labeled_sha256}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
