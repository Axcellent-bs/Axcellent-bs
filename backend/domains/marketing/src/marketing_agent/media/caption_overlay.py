"""자막·엔드카드 얹기(ffmpeg drawtext): AI 영상이 못 그리는 글자를 코드로 얹는다 (크레딧 0).

- 장면 자막: 스토리보드 칸의 `caption_ko`를 그 칸의 `start_s`~`end_s` 동안 화면 아래쪽에.
  시간이 없는 칸은 영상 길이를 칸 수로 똑같이 나눈 자리를 쓴다. 빈 자막 칸은 건너뛴다.
- 엔드카드: 마지막 2초에 회사 표시 이름 + 행동 유도 문구(CTA). 둘 다 없으면 만들지 않는다.
- 글자는 임시 폴더의 파일로 쓰고 `drawtext=textfile=`로 넘긴다(이스케이프 문제를 피한다).
  끝나면 임시 폴더를 지운다. 한 줄은 16자까지, 넘으면 줄바꿈. 화면 아래 22% 자리, 반투명 상자.
- 글꼴: 한글 글꼴 파일이 필요하다. 서버 설정 `MARKETING_OVERLAY_FONT`(파일 경로). 없거나 파일이
  없으면 `font_not_found`(영상 흐름은 계속, 부르는 쪽이 `skipped`로 기록한다).

`audio_mix.py`와 같은 규칙:
- ffmpeg는 인자 목록으로만 부른다(셸 금지). 경로는 `MARKETING_FFMPEG_BIN`(기본 `ffmpeg`)에서만,
  실행 파일이 없으면 `ffmpeg_not_found`. 시간 제한이 있다.
- 실행한 인자 목록을 결과(`commands`)에 남긴다. 키·토큰은 인자에 들어가지 않는다.
- 테스트는 `runner`에 가짜 실행기를 넘긴다(실제 ffmpeg를 부르지 않는다).
- 원본 영상은 건드리지 않는다. 결과는 새 파일이다.
"""

from __future__ import annotations

import hashlib
import os
import tempfile
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path

from marketing_agent.media.audio_mix import AudioMixError, Runner, subprocess_runner
from marketing_agent.media.config import ffmpeg_bin

FONT_ENV = "MARKETING_OVERLAY_FONT"
LINE_CHARS = 16  # 한 줄 글자 수
END_CARD_S = 2.0
BOTTOM_RATIO = 0.22  # 화면 아래 22%
FFMPEG_TIMEOUT_S = 120
MAX_FILE_BYTES = 200 * 1024 * 1024
MAX_CAPTION_CHARS = 60


class OverlayError(Exception):
    """얹기 실패. `code`는 닫힌 집합이고 메시지에는 경로·키를 넣지 않는다.

    font_not_found · ffmpeg_not_found · ffmpeg_timeout · ffmpeg_failed · video_not_found
    · invalid_duration · nothing_to_overlay · output_missing · bad_path"""

    def __init__(self, code: str, detail: str = "") -> None:
        super().__init__(f"{code}: {detail}" if detail else code)
        self.code = code


@dataclass(frozen=True)
class Caption:
    text: str
    start_s: float
    end_s: float


@dataclass(frozen=True)
class OverlayResult:
    output_path: Path
    sha256: str
    captions: int  # 얹은 장면 자막 수(엔드카드는 세지 않는다)
    end_card: bool
    commands: tuple[tuple[str, ...], ...]
    ffmpeg_bin: str
    speed: float = 1.0  # 실제로 한 배속(안 했으면 1.0)


def font_path() -> Path | None:
    """`MARKETING_OVERLAY_FONT`의 글꼴 파일. 설정이 없거나 파일이 없으면 None."""
    raw = os.getenv(FONT_ENV, "").strip()
    if not raw:
        return None
    path = Path(raw)
    return path if path.is_file() else None


def wrap_text(text: str, width: int = LINE_CHARS) -> str:
    """띄어쓰기 단위로 `width`자씩 줄바꿈한다. 띄어쓰기 없는 긴 낱말은 `width`자에서 자른다."""
    lines: list[str] = []
    cur = ""
    for word in " ".join(str(text).split()).split(" "):
        while len(word) > width:  # 한 낱말이 한 줄보다 길면 잘라 낸다
            if cur:
                lines.append(cur)
                cur = ""
            lines.append(word[:width])
            word = word[width:]
        if not word:
            continue
        if not cur:
            cur = word
        elif len(cur) + 1 + len(word) <= width:
            cur = f"{cur} {word}"
        else:
            lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)
    return "\n".join(lines)


def plan_captions(shots: Sequence[dict[str, object]], duration_s: float) -> list[Caption]:
    """칸 목록(`caption_ko`·`start_s`·`end_s`)에서 자막 시간표. 빈 자막 칸은 건너뛴다.

    시간이 없는(또는 앞뒤가 맞지 않는) 칸은 영상 길이를 전체 칸 수로 똑같이 나눈 자리를 쓴다."""
    count = len(shots)
    out: list[Caption] = []
    for i, shot in enumerate(shots):
        text = " ".join(str(shot.get("caption_ko") or "").split())[:MAX_CAPTION_CHARS]
        if not text:
            continue
        start, end = _num(shot.get("start_s")), _num(shot.get("end_s"))
        if start is None or end is None or end <= start:
            start, end = duration_s * i / count, duration_s * (i + 1) / count
        start, end = max(0.0, start), min(duration_s, end)
        if end > start:
            out.append(Caption(text, start, end))
    return out


def _num(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return float(value)


def _sec(value: float) -> str:
    return f"{value:.3f}".rstrip("0").rstrip(".") or "0"


def _quote(path: Path) -> str:
    """filtergraph 안의 파일 경로: 역슬래시는 `/`로, `:`는 `\\:`로, 전체를 작은따옴표로 감싼다."""
    text = str(path).replace("\\", "/")
    if "'" in text or "\n" in text:
        raise OverlayError("bad_path")
    return "'" + text.replace(":", "\\:") + "'"


def _drawtext(font: Path, textfile: Path, start: float, end: float, *, size: str) -> str:
    return (
        f"drawtext=fontfile={_quote(font)}:textfile={_quote(textfile)}"
        f":fontcolor=white:fontsize={size}:line_spacing=8:text_align=center"
        f":box=1:boxcolor=black@0.55:boxborderw=14"
        f":x=(w-text_w)/2:y=h*{1 - BOTTOM_RATIO:.2f}"
        f":enable='between(t,{_sec(start)},{_sec(end)})'"
    )


def _speed(value: float) -> str:
    return f"{value:.3f}".rstrip("0").rstrip(".")


def build_args(
    ffmpeg: str,
    video: Path,
    out: Path,
    font: Path | None,
    text_files: Sequence[tuple[Path, float, float, str]],
    *,
    speed: float = 1.0,
    has_audio: bool = True,
) -> list[str]:
    """ffmpeg 인자 목록. `text_files`는 (글 파일, 시작, 끝, 글자 크기 식).

    11/25 배속(`speed` > 1): 영상 `setpts=PTS/배속`을 먼저 하고 그 뒤에 자막·엔드카드를 얹는다
    (자막 시각은 빨라진 영상 기준 = 기획 시각). 소리는 `atempo=배속`(소리 트랙이 없으면 생략).
    배속 1.0이면 지금 인자 그대로다."""
    parts: list[str] = []
    fast = speed > 1.0
    if fast:
        parts.append(f"setpts=PTS/{_speed(speed)}")
    if text_files:
        if font is None:
            raise OverlayError("font_not_found")
        parts.extend(_drawtext(font, f, s, e, size=size) for f, s, e, size in text_files)
    if not parts:
        raise OverlayError("nothing_to_overlay")
    audio = ["-c:a", "copy"]
    if fast and has_audio:
        audio = ["-af", f"atempo={_speed(speed)}", "-c:a", "aac", "-b:a", "128k"]
    return [
        ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
        "-i", str(video), "-vf", ",".join(parts),
        "-map", "0:v:0", "-map", "0:a?",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "20", "-pix_fmt", "yuv420p",
        *audio, "-movflags", "+faststart", str(out),
    ]  # fmt: skip


def apply_overlay(
    video: Path,
    output: Path,
    *,
    captions: Sequence[Caption],
    end_card: str | None,
    duration_s: float,
    font: Path | None = None,
    runner: Runner = subprocess_runner,
    ffmpeg: str | None = None,
    timeout_s: int = FFMPEG_TIMEOUT_S,
    speed: float = 1.0,
    has_audio: bool = True,
) -> OverlayResult:
    """영상(`video`)에 자막·엔드카드를 얹은 mp4를 `output`에 쓴다. 원본은 그대로.

    `duration_s`는 결과 영상 길이(배속을 했으면 빨라진 길이 = 기획 길이)다.
    11/25 `speed` > 1이면 같은 ffmpeg 한 번에 배속도 한다. 자막·엔드카드가 없으면(글꼴이 없어
    건너뛸 때 등) 배속만 한다. 그때는 글꼴이 필요 없다."""
    if duration_s <= 0:
        raise OverlayError("invalid_duration")
    fast = speed > 1.0
    texts = bool(end_card) or any(c.text for c in captions)
    font = font or font_path()
    if texts and (font is None or not font.is_file()):
        raise OverlayError("font_not_found")
    video, output = video.resolve(), output.resolve()
    if not video.is_file():
        raise OverlayError("video_not_found")
    if output == video:
        raise OverlayError("output_overwrites_input")
    card_from = max(0.0, duration_s - END_CARD_S)
    card = "\n".join(
        [ln for part in (end_card or "").split("\n") if (ln := wrap_text(part))]
    ).strip()
    shown = [c for c in captions if c.text and c.start_s < (card_from if card else duration_s)]
    if not shown and not card and not fast:
        raise OverlayError("nothing_to_overlay")
    binary = ffmpeg or ffmpeg_bin()
    tmp_out = output.with_name(f"{output.stem}.tmp{output.suffix}")
    with tempfile.TemporaryDirectory(prefix="overlay-") as folder:
        items: list[tuple[Path, float, float, str]] = []
        for i, cap in enumerate(shown):
            end = min(cap.end_s, card_from) if card else cap.end_s
            if end <= cap.start_s:
                continue
            path = Path(folder) / f"cap{i}.txt"
            path.write_text(wrap_text(cap.text), encoding="utf-8")
            items.append((path, cap.start_s, end, "h/26"))
        if card:
            path = Path(folder) / "end.txt"
            path.write_text(card, encoding="utf-8")
            items.append((path, card_from, duration_s, "h/22"))
        if not items and not fast:
            raise OverlayError("nothing_to_overlay")
        args = build_args(
            binary, video, tmp_out, font if items else None, items,
            speed=speed, has_audio=has_audio,
        )  # fmt: skip
        try:
            try:
                done = runner(args, timeout_s)
            except AudioMixError as error:  # ffmpeg_not_found · ffmpeg_timeout · ffmpeg_failed
                raise OverlayError(error.code) from None
            if done.returncode != 0:
                raise OverlayError("ffmpeg_failed", done.stderr.strip()[-200:])
            if not tmp_out.is_file() or not 0 < tmp_out.stat().st_size <= MAX_FILE_BYTES:
                raise OverlayError("output_missing")
            data = tmp_out.read_bytes()
            os.replace(tmp_out, output)
        finally:
            tmp_out.unlink(missing_ok=True)
    return OverlayResult(
        output_path=output,
        sha256=hashlib.sha256(data).hexdigest(),
        captions=sum(1 for _, _, _, size in items if size == "h/26"),
        end_card=bool(card),
        commands=(tuple(args),),
        ffmpeg_bin=binary,
        speed=speed if fast else 1.0,
    )
