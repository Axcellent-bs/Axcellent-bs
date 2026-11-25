"""제작 기록 영상 기술 검사 (11/26 H). ffmpeg로 싸게 잡을 수 있는 것만 코드가 먼저 본다.

영상이 나오면 사람이 끝까지 봐야 깨진 곳을 알았다. 검은 화면·멈춘 화면·길이는 코드가 먼저 본다.
자막·소리는 우리 후처리(`creative_overlay`)가 얹어서 늘 있으므로 검사하지 않는다(의뢰 대사는 영상
전에 기획·대본에서 본다).

- 대상: 내보낼 영상(`creative_render.publish_video_part`: 자막·엔드카드를 얹은 영상, 없으면 원본)
- 검은 화면: `blackdetect=d=0.5:pix_th=0.10` → 구간 (엔드카드 마지막 2초 안은 뺀다)
- 멈춘 화면: `freezedetect=n=-60dB:d=1.5` → 구간 (엔드카드 마지막 2초 안은 뺀다)
- 길이: `creative_overlay.probe_duration` → 목표(기획 제작안 길이, 없으면 기록의 목표 길이) ±1초
- ffmpeg가 없거나 실패하면 그 검사만 error("확인 못 함"). 흐름은 계속
- 결과: 기록 `render.qc = {video_sha, part, checks: [{id, status, detail_ko, ranges}], at}`
  (계약 `CreativeVideoQc`). 같은 영상 sha면 다시 돌리지 않는다
- 자동으로 다시 만들지 않는다. 다시 만들기는 사람이 화면 버튼으로(`request_video(regenerate=True)`)
"""

from __future__ import annotations

import asyncio
import logging
import re
import subprocess
from collections.abc import Callable
from pathlib import Path
from typing import Any

from marketing_agent import creative_records as _creative
from marketing_agent import creative_render as _render

logger = logging.getLogger("marketing.creative_qc")

QC_STATE = "qc"
END_CARD_S = 2.0  # 엔드카드(마지막 2초)는 일부러 멈춘·단색 화면이라 뺀다
BLACK_MIN_S = 0.5
FREEZE_MIN_S = 1.5
DURATION_TOLERANCE_S = 1.0
TIMEOUT_S = 120
BLACK_FILTER = f"blackdetect=d={BLACK_MIN_S}:pix_th=0.10"
FREEZE_FILTER = f"freezedetect=n=-60dB:d={FREEZE_MIN_S}"

QcRunner = Callable[[list[str], int], tuple[int, str]]  # (인자, 시간 제한) → (종료 코드, stderr)


class FfmpegMissing(Exception):
    """ffmpeg 실행 파일이 없다."""


def subprocess_runner(args: list[str], timeout_s: int) -> tuple[int, str]:
    """인자 목록으로만 부른다. stderr 전체를 돌려준다(검사 줄이 많아도 자르지 않음)."""
    try:
        done = subprocess.run(  # noqa: S603 - 인자 목록으로만 부른다
            args, capture_output=True, text=True, timeout=timeout_s, check=False,
            encoding="utf-8", errors="replace",
        )  # fmt: skip
    except FileNotFoundError:
        raise FfmpegMissing from None
    return done.returncode, done.stderr or ""


# 테스트가 바꾸는 자리: ffmpeg 실행기, 영상 길이 읽기
RUNNER: QcRunner = subprocess_runner


def _probe(video: Path) -> float | None:
    from marketing_agent import creative_overlay

    return creative_overlay.probe_duration(video)


PROBE: Callable[[Path], float | None] = _probe

_BLACK = re.compile(r"black_start:\s*([\d.]+)\s+black_end:\s*([\d.]+)")
_FREEZE_START = re.compile(r"freeze_start:\s*([\d.]+)")
_FREEZE_END = re.compile(r"freeze_end:\s*([\d.]+)")


def black_ranges(stderr: str) -> list[list[float]]:
    return [[float(a), float(b)] for a, b in _BLACK.findall(stderr)]


def freeze_ranges(stderr: str, duration: float | None) -> list[list[float]]:
    """freeze_start·freeze_end 짝. 끝이 없으면(영상 끝까지 멈춤) 영상 길이로 닫는다."""
    starts = [float(x) for x in _FREEZE_START.findall(stderr)]
    ends = [float(x) for x in _FREEZE_END.findall(stderr)]
    out = []
    for i, s in enumerate(starts):
        e = ends[i] if i < len(ends) else (duration if duration is not None else s)
        out.append([s, e])
    return out


def outside_end_card(ranges: list[list[float]], duration: float | None,
                     min_s: float) -> list[list[float]]:  # fmt: skip
    """엔드카드(마지막 2초) 안 구간은 빼고, 걸친 구간은 그 앞까지만.

    남은 길이가 min_s보다 짧으면 뺀다."""
    if duration is None:
        return [r for r in ranges if r[1] - r[0] >= min_s]
    limit = duration - END_CARD_S
    out = []
    for start, end in ranges:
        if start >= limit:
            continue
        end = min(end, limit)
        if end - start >= min_s:
            out.append([round(start, 2), round(end, 2)])
    return out


def _sec(x: float) -> str:
    return f"{x:.1f}"


def _ranges_ko(ranges: list[list[float]]) -> str:
    return ", ".join(f"{_sec(a)}~{_sec(b)}초" for a, b in ranges)


def _detect(video: Path, vf: str) -> tuple[str | None, str | None]:
    """(stderr, 실패 이유). ffmpeg가 없거나 실패하면 이유."""
    from marketing_agent.media.config import ffmpeg_bin

    args = [ffmpeg_bin(), "-hide_banner", "-nostats", "-i", str(video), "-vf", vf, "-an",
            "-f", "null", "-"]  # fmt: skip
    try:
        code, err = RUNNER(args, TIMEOUT_S)
    except FfmpegMissing:
        return None, "ffmpeg가 없어 확인 못 함"
    except subprocess.TimeoutExpired:
        return None, "ffmpeg 시간 초과로 확인 못 함"
    except OSError as error:
        return None, f"ffmpeg를 부르지 못해 확인 못 함 ({type(error).__name__})"
    if code != 0:
        return None, "ffmpeg 실패로 확인 못 함"
    return err, None


def check_black(video: Path, duration: float | None) -> dict[str, Any]:
    err, why = _detect(video, BLACK_FILTER)
    if why:
        return {"id": "black", "status": "error", "detail_ko": why, "ranges": []}
    found = outside_end_card(black_ranges(err or ""), duration, BLACK_MIN_S)
    if found:
        return {"id": "black", "status": "warn", "ranges": found,
                "detail_ko": f"{_ranges_ko(found)} 검은 화면"}  # fmt: skip
    return {"id": "black", "status": "pass", "ranges": [],
            "detail_ko": f"{BLACK_MIN_S:g}초 넘는 검은 구간 없음"}  # fmt: skip


def check_freeze(video: Path, duration: float | None) -> dict[str, Any]:
    err, why = _detect(video, FREEZE_FILTER)
    if why:
        return {"id": "freeze", "status": "error", "detail_ko": why, "ranges": []}
    found = outside_end_card(freeze_ranges(err or "", duration), duration, FREEZE_MIN_S)
    if found:
        return {"id": "freeze", "status": "warn", "ranges": found,
                "detail_ko": f"{_ranges_ko(found)} 움직임 거의 없음"}  # fmt: skip
    return {"id": "freeze", "status": "pass", "ranges": [],
            "detail_ko": f"{FREEZE_MIN_S:g}초 넘게 멈춘 구간 없음"}  # fmt: skip


def target_seconds(rec: dict[str, Any]) -> float | None:
    """목표 길이: 기획 제작안 길이(스토리보드 `plan_duration_s`) → 기록의 목표 길이(영상
    `plan_seconds` → `seconds`). 모르면 None."""
    video = (rec.get("render") or {}).get("video") or {}
    for raw in ((rec.get("storyboard") or {}).get(_render.PLAN_DURATION_KEY),
                video.get("plan_seconds"), video.get("seconds")):  # fmt: skip
        try:
            value = float(raw)
        except (TypeError, ValueError):
            continue
        if value > 0:
            return value
    return None


def check_duration(duration: float | None, target: float | None) -> dict[str, Any]:
    if duration is None:
        return {"id": "duration", "status": "error", "ranges": [],
                "detail_ko": "영상 길이를 읽지 못해 확인 못 함 (ffprobe 없음·실패)"}  # fmt: skip
    if target is None:
        return {"id": "duration", "status": "error", "ranges": [],
                "detail_ko": f"{_sec(duration)}초 (목표 길이를 몰라 확인 못 함)"}  # fmt: skip
    ok = abs(duration - target) <= DURATION_TOLERANCE_S
    detail = f"{_sec(duration)}초 (목표 {target:g}초 ±{DURATION_TOLERANCE_S:g})"
    return {"id": "duration", "status": "pass" if ok else "warn", "ranges": [],
            "detail_ko": detail}  # fmt: skip


def has_warn(render: dict[str, Any]) -> bool:
    """그 기록의 기술 검사에 확인 필요(warn)가 있나."""
    checks = ((render or {}).get(QC_STATE) or {}).get("checks") or []
    return any(isinstance(c, dict) and c.get("status") == "warn" for c in checks)


def _video_file(record_id: str, cid: str | None, info: dict[str, Any]) -> Path | None:
    name = str(info.get("asset") or "")
    if not name or "/" in name or "\\" in name or name.startswith("."):
        return None
    return _render.asset_path(record_id, "video", cid).parent / name


def inspect(record_id: str, cid: str | None) -> dict[str, Any] | None:
    """내보낼 영상을 검사해 render.qc에 남긴다. 영상이 없으면 None, 같은 sha면 그대로 돌려준다."""
    store = _creative.store()
    rec = store.get(record_id)
    if rec is None:
        return None
    render = rec.get("render") or {}
    part = _render.publish_video_part(rec)
    if part is None:
        return None
    info = render.get(part) or {}
    sha = info.get("sha256")
    prev = render.get(QC_STATE) or {}
    if sha and prev.get("video_sha") == sha and prev.get("checks"):
        return dict(prev)
    video = _video_file(record_id, cid, info)
    if video is None or not video.is_file():
        checks = [{"id": i, "status": "error", "detail_ko": "영상 파일이 없어 확인 못 함",
                   "ranges": []} for i in ("black", "freeze", "duration")]  # fmt: skip
    else:
        duration = PROBE(video)
        checks = [check_black(video, duration), check_freeze(video, duration),
                  check_duration(duration, target_seconds(rec))]  # fmt: skip
    from datetime import UTC, datetime

    result = {"video_sha": sha, "part": part, "checks": checks,
              "at": datetime.now(UTC).isoformat()}  # fmt: skip
    render = store.get_render(record_id)  # 검사하는 동안 바뀐 칸을 덮지 않게 다시 읽는다
    render[QC_STATE] = result
    store.set_render(record_id, render)
    return result


async def run(record_id: str, cid: str | None) -> dict[str, Any] | None:
    """`inspect`를 스레드에서 (ffmpeg가 이벤트 루프를 막지 않게).

    실패해도 예외를 밖으로 내지 않는다."""
    try:
        return await asyncio.to_thread(inspect, record_id, cid)
    except Exception:  # noqa: BLE001 - 기술 검사는 부가 기능이라 영상·게시 흐름을 막지 않는다
        logger.exception("creative_qc: 기술 검사 실패 (%s)", record_id)
        return None
