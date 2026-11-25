"""제작 기록 영상에 자막·엔드카드를 코드로 얹는다 (2026-11-25).

AI 영상은 글자를 잘 못 그린다. 장면 자막(스토리보드 칸 `caption_ko`)과 마지막 2초의 회사 이름 +
`cta_ko`를 ffmpeg로 얹으면 글자가 깨지지 않고 크레딧도 0이다(`media/caption_overlay.py`).

- 언제: `video` 또는 `final_video`가 기록에 붙을 때마다 최신 영상
  (`creative_render.latest_video_part`)에 한 번 자동(`creative_render`가 부른다).
  BGM을 입힌 완성 영상이 나중에 붙으면 다시 얹는다.
- 결과: 새 칸 `render.overlay_video`(파일 `overlay.mp4`)와 상태 `render.overlay`
  `{status: applied|skipped|failed|running, error, captions, source_part, source_sha256, edits}`.
  원본 영상·완성 영상은 그대로 둔다. 어느 파일을 내보낼지는
  `creative_render.publish_video_part` 한 곳이 고른다
- 실패해도 영상·게시 흐름은 멈추지 않는다: 글꼴·ffmpeg가 없으면 `skipped`(`font_not_found`·
  `ffmpeg_not_found`), 그 밖의 실패는 `failed`. 그동안은 최신 영상이 그대로 내보내진다
- 다시 얹기(`redo`): 사람이 자막을 고친 뒤 부른다. 고친 글은 `render.overlay.edits`(칸 번호 → 글)에
  남겨(스토리보드는 건드리지 않는다) 다음 자동 얹기에도 쓴다. 크레딧·안전 관문 없음
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from pathlib import Path
from typing import Any

from ax_agent_template import company_scope as _request_company
from ax_agent_template.companies import get_company
from marketing_agent import creative_records as _creative
from marketing_agent import creative_render as _render
from marketing_agent import sns_media
from marketing_agent.media import caption_overlay as _co
from marketing_agent.media.audio_mix import Runner, subprocess_runner

logger = logging.getLogger("marketing.creative_overlay")

MAX_EDITS = 12
# 테스트가 바꾸는 자리: ffmpeg 실행기, 영상 길이 읽기
RUNNER: Runner = subprocess_runner


def probe_duration(video: Path) -> float | None:
    """영상 길이(초). ffprobe가 없거나 읽지 못하면 None."""
    data = sns_media.run_ffprobe(video) or {}
    try:
        value = float((data.get("format") or {}).get("duration"))
    except (TypeError, ValueError):
        return None
    return value if value > 0 else None


PROBE: Callable[[Path], float | None] = probe_duration


def probe_audio(video: Path) -> bool:
    """소리 트랙이 있나. ffprobe가 없거나 읽지 못하면 True(있다고 보고 `0:a?`로 맡긴다)."""
    data = sns_media.run_ffprobe(video)
    if not data or not isinstance(data.get("streams"), list):
        return True
    return any(isinstance(s, dict) and s.get("codec_type") == "audio" for s in data["streams"])


PROBE_AUDIO: Callable[[Path], bool] = probe_audio


def record_speed(render: dict[str, Any]) -> float:
    """영상을 요청할 때 정한 실제 배속(`render.video.speed`, 없으면 요약). 옛 기록은 1.0."""
    video = render.get("video") or {}
    raw = video.get("speed")
    if raw is None:
        raw = (video.get("summary") or {}).get("speed")
    return _render.speed_value(raw)

_busy: set[tuple[str, str]] = set()
_again: set[tuple[str, str]] = set()


class OverlayRefused(Exception):
    """다시 얹기 거절 코드: not_found · video_not_ready · in_progress · invalid_input."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


def _company_label(cid: str | None) -> str:
    """엔드카드에 쓸 회사 표시 이름. 회사 설정에 없으면 빈 글(생략)."""
    company = get_company(cid or _request_company.active())
    return " ".join(str(company.name or "").split()) if company is not None else ""


def _folder(record_id: str, cid: str | None) -> Path:
    return _render.asset_path(record_id, "video", cid).parent


def _source_file(record_id: str, cid: str | None, info: dict[str, Any]) -> Path | None:
    name = str(info.get("asset") or "")
    if not name or "/" in name or "\\" in name or name.startswith("."):
        return None
    return _folder(record_id, cid) / name


def _shots(rec: dict[str, Any], edits: dict[str, str]) -> list[dict[str, Any]]:
    """스토리보드 칸에 사람이 고친 자막(`edits`)을 덮은 목록."""
    out = []
    for i, shot in enumerate((rec.get("storyboard") or {}).get("shots") or [], start=1):
        if not isinstance(shot, dict):
            continue
        row = dict(shot)
        key = str(row.get("no") or i)
        if key in edits:
            row["caption_ko"] = edits[key]
        out.append(row)
    return out


def _duration(rec: dict[str, Any], part: str, video: Path, shots: list[dict[str, Any]]) -> float:
    """영상 길이: 파일에서 읽은 값 → 기록에 남은 초 → 칸의 마지막 끝 시간. 모르면 0."""
    if (found := PROBE(video)) is not None:
        return found
    render = rec.get("render") or {}
    for name in (part, "video"):
        try:
            seconds = float((render.get(name) or {}).get("seconds"))
        except (TypeError, ValueError):
            continue
        if seconds > 0:
            return seconds
    ends = [e for s in shots if isinstance(e := s.get("end_s"), (int, float)) and e > 0]
    return float(max(ends)) if ends else 0.0


def _already(render: dict[str, Any], part: str, sha: str | None) -> bool:
    """이 영상에는 이미 얹었다(같은 원본 칸·같은 파일)."""
    cur = render.get(_render.OVERLAY_STATE) or {}
    done = render.get(_render.OVERLAY_PART) or {}
    return (
        cur.get("status") == "applied"
        and cur.get("source_part") == part
        and cur.get("source_sha256") == sha
        and done.get("status") == "done"
    )


async def apply(
    record_id: str,
    cid: str | None = None,
    *,
    edits: dict[str, str] | None = None,
    force: bool = False,
) -> dict[str, Any]:
    """최신 영상에 자막·엔드카드를 얹고 `render.overlay`(상태)를 돌려준다.

    자동(기본)은 이미 얹은 영상이면 건너뛴다. 다른 얹기가 도는 중이면 끝난 뒤 한 번 더
    확인하게 맡기고 돌아온다. `force`(다시 얹기)는 도는 중이면 `OverlayRefused("in_progress")`."""
    cid = cid or _request_company.active()
    key = (cid or "", record_id)
    if key in _busy:
        if force:
            raise OverlayRefused("in_progress")
        _again.add(key)  # 도는 얹기가 끝나면 최신 영상을 한 번 더 본다
        return dict(_creative.store().get_render(record_id).get(_render.OVERLAY_STATE) or {})
    _busy.add(key)
    try:
        while True:
            _again.discard(key)
            state = await _run(record_id, cid, edits, force)
            edits, force = None, False
            if key not in _again:
                return state
    finally:
        _busy.discard(key)
        _again.discard(key)


async def _run(
    record_id: str, cid: str | None, edits: dict[str, str] | None, force: bool
) -> dict[str, Any]:
    store = _creative.store()
    rec = store.get(record_id)
    if rec is None:
        raise OverlayRefused("not_found")
    render = rec.get("render") or {}
    part = _render.latest_video_part(rec)
    if part is None:
        raise OverlayRefused("video_not_ready")
    info = render.get(part) or {}
    sha = info.get("sha256")
    prev = render.get(_render.OVERLAY_STATE) or {}
    merged = {**(prev.get("edits") or {}), **(edits or {})}
    if not force and _already(render, part, sha):
        return dict(prev)
    base = {
        "source_part": part,
        "source_sha256": sha,
        "edits": merged,
        "captions": 0,
        "end_card": False,
    }

    def state(status: str, error: str | None = None, **more: Any) -> dict[str, Any]:
        return _render._set(  # noqa: SLF001 - 같은 render 칸 규칙
            record_id, _render.OVERLAY_STATE, **{**base, **more, "status": status, "error": error}
        )

    source = _source_file(record_id, cid, info)
    if source is None or not source.is_file():
        return state("failed", "video_not_found")
    # 11/25 재생 속도: 더 길게 만든 영상을 같은 ffmpeg 한 번에 빠르게 튼다. 배속 1.0이면 지금 그대로
    speed = record_speed(render)
    fast = speed > 1.0
    base["speed"] = speed
    base["speed_applied"] = False
    base["captions_skipped"] = None
    font = _co.font_path()
    if font is None and not fast:
        return state("skipped", "font_not_found")
    shots = _shots(rec, merged)
    made = _duration(rec, part, source, shots)
    duration = made / speed  # 결과 길이 = 빨라진 영상 길이(기획 길이). 자막 시각도 이 기준
    if duration <= 0:
        return state("skipped", "duration_unknown")
    plan = _co.plan_captions(shots, duration) if font is not None else []
    board = rec.get("storyboard") or {}
    cta = " ".join(str(board.get("cta_ko") or "").split())
    card = ("\n".join(x for x in (_company_label(cid), cta) if x) or None) if font else None
    if not plan and card is None and not fast:
        return state("skipped", "nothing_to_overlay")
    # 글꼴이 없거나 얹을 글이 없으면 배속만 한다(자막을 건너뛴 이유는 `captions_skipped`)
    if font is None:
        base["captions_skipped"] = "font_not_found"
    elif not plan and card is None:
        base["captions_skipped"] = "nothing_to_overlay"
    state("running")
    output = _folder(record_id, cid) / _render.FILES[_render.OVERLAY_PART]
    try:
        result = await asyncio.to_thread(
            _co.apply_overlay,
            source,
            output,
            captions=plan,
            end_card=card,
            duration_s=duration,
            font=font,
            runner=RUNNER,
            speed=speed,
            has_audio=PROBE_AUDIO(source) if fast else True,
        )
    except _co.OverlayError as error:
        status = "skipped" if error.code in ("ffmpeg_not_found", "font_not_found") else "failed"
        return state(status, error.code)
    except Exception as error:  # noqa: BLE001 - 어떤 실패도 영상 흐름을 멈추지 않는다
        logger.warning("creative_overlay: 얹기 실패 %s (%s)", type(error).__name__, record_id)
        return state("failed", "overlay_error")
    _render._set(  # noqa: SLF001
        record_id,
        _render.OVERLAY_PART,
        status="done",
        error=None,
        asset=_render.FILES[_render.OVERLAY_PART],
        sha256=result.sha256,
        source="overlay",
        source_part=part,
        model=info.get("model"),
    )
    return state(
        "applied",
        None,
        captions=result.captions,
        end_card=result.end_card,
        speed_applied=result.speed > 1.0,
    )


async def redo(
    record_id: str, captions: dict[str, str], cid: str | None = None
) -> dict[str, dict[str, Any]]:
    """사람이 고친 자막(칸 번호 → 글, 빈 글이면 그 칸 자막 없음)으로 다시 얹는다.

    돌려주는 값: `{"overlay": render.overlay, "overlay_video": render.overlay_video}`."""
    if not captions or len(captions) > MAX_EDITS:
        raise OverlayRefused("invalid_input")
    cid = cid or _request_company.active()
    clean = {str(k): " ".join(str(v).split())[: _co.MAX_CAPTION_CHARS] for k, v in captions.items()}
    await apply(record_id, cid, edits=clean, force=True)
    render = _creative.store().get_render(record_id)
    return {
        "overlay": dict(render.get(_render.OVERLAY_STATE) or {}),
        "overlay_video": dict(render.get(_render.OVERLAY_PART) or {}),
    }
