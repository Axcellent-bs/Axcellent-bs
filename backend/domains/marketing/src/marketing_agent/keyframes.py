"""키프레임 영상: 기획 SVG 구도 → 실사 키프레임 → Kling 4.0 한 번에 (2026-11-25).

11/24 멘토링 "SVG로 구도를 먼저 잡고 → 첫·마지막 프레임 → 사이 동작 묘사". 15초를 프롬프트 하나로
만들면 장면 구분이 흐리고 인물이 바뀐다. 그래서 장면 경계마다 한 장씩 실사 그림을 먼저 만들고,
영상 모델이 그 그림들을 순서대로 지나가게 한다.

역할 (#1780 뒤)
- 기획: 장면별 `start_frame`·`end_frame`(한국어)·`action_en`·`camera_en`과 첫·끝 화면 SVG
  (`start_svg`·`end_svg`)를 넘긴다. 마케팅은 SVG를 LLM으로 새로 그리지 않는다(LLM 호출 0).
- 마케팅: 프레임 그림 프롬프트(영문, 코드 템플릿)·구도 PNG(`media/svg_raster.py`)·그림·영상
  프롬프트·모델·크레딧.

프레임 = 장면 경계마다 한 장 (`plan_frames`)
- 장면 1의 첫 화면, 그리고 장면 i의 끝 화면(= 장면 i+1의 첫 화면). 장면 수 + 1장, 최대 10장.
- 장면 i의 end_svg와 장면 i+1의 start_svg가 둘 다 있으면 end_svg(같은 순간). 글도 end_frame 먼저.
- 재료(글 start_frame·end_frame 또는 SVG)가 빈 경계는 뺀다(글이 없고 SVG만 있으면 장면 화면 구성
  글로 그린다). 남은 프레임이 2장 미만이면 키프레임 방식을 쓰지 않고 지금 흐름(스토리보드 그림
  또는 가상 인물 원본 + #1769 프롬프트)으로 간다.
- SVG가 비었거나 그릴 수 없으면 그 프레임은 구도 PNG 없이 프롬프트만으로 그린다.

키프레임 방식을 쓰는 때 (`active`): 회사 계정(MCP)으로 만들고, 영상 모델이 키프레임을 받고
(`creative_mcp.takes_keyframes`, 지금 kling4_0), 프레임이 2장 이상일 때.

그림 (프레임마다 그림 1장): 지금 그림 관문 그대로 — `image_credits_ok` → 미리 보기(예상 크레딧·잔액)
→ 장부 예약 → `execute()` generate_media(B, image) → executed일 때만 Higgsfield. 참조는
[구도 PNG, 가상 인물 원본, 추가 참조…] 순서(구도 먼저). 실제 사람 사진(real_photo)·캐릭터 없음이면
구도 PNG만. 한 장이 실패하면 그 장만 실패로 두고 [다시 그리기](장당 그림 1회)로 고친다.

영상: 키프레임이 모두 done일 때만. params mode image-to-video, start_image = 첫 프레임,
end_image = 마지막, image_references = 사이 프레임 순서대로. 프롬프트는 장면별
"a-bs: {action_en} {camera_en}"(시각 = 기획 시각 × 배속) + 글자 금지 문장(#1769 규칙).

기록 `render.keyframes = {status, frames: [{index, at_s, scene_n, svg_source("planning"|"none"),
text_ko, camera_en, svg, prompt_en, layout_asset, layout_png_media_id, asset, media_id, sha256,
status, error, credits_estimate, attempt}], approved_at}`.
`svg_sanitize.py`는 고치지 않는다(세션2가 그라데이션·16KB를 연다).
래스터는 그라데이션을 미리 받는다.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ax_contracts.schemas import RequestMeta
from marketing_agent import creative_mcp as _mcp
from marketing_agent import creative_records as _creative
from marketing_agent import creative_render as _render
from marketing_agent import render_settings as _settings
from marketing_agent.character_sheet import VIRTUAL_STYLE_EN
from marketing_agent.higgsfield_mcp import log_line
from marketing_agent.media import svg_raster
from marketing_agent.media import usage as usage_mod
from marketing_agent.media.ledger import MediaLedger
from marketing_agent.svg_sanitize import sanitize_svg

logger = logging.getLogger("marketing.keyframes")

STATE = "keyframes"  # render.keyframes
PLAN_SCENES_KEY = "plan_scenes"  # 의뢰 스토리보드에 남긴 기획 장면(키프레임 칸·SVG)
MAX_FRAMES = _mcp.MAX_KEYFRAMES
MAX_IMAGE_REFS = 4  # 그림 참조 상한: 구도 + 인물 원본 + 추가 참조
REFERENCE = "keyframes"  # render.video.summary.reference
NO_TEXT_IMAGE = "No text, letters, captions, logos, signs, real people or real brands."
FOLLOW_LAYOUT = "Follow the composition and pose of the first reference exactly."
SAME_PERSON_AFTER_LAYOUT = "Same person as the other references."
SAME_PERSON = "Same person as the references."
OPENING = (
    "Make one continuous short film that moves through the keyframes in order: the start image, "
    "then each reference image as an in-between keyframe, ending on the end image. "
    "Keep the same person, outfit and look throughout."
)
SCENE_FIELDS = (
    "n", "start_s", "end_s", "visual", "start_frame", "end_frame", "action_en", "camera_en",
    "start_svg", "end_svg",
)  # fmt: skip


def _now() -> datetime:
    return datetime.now(UTC)


def _one(text: Any) -> str:
    return " ".join(str(text or "").split())


# ---------------------------------------------------------------- 기획 장면 → 프레임 (순수)
def plan_scenes(plan: Any) -> list[dict[str, Any]]:
    """기획 제작안 장면에서 키프레임에 쓸 칸만 꺼낸다(SVG는 `svg_sanitize`로 다시 정리).

    예전 제작안(칸 없음)이어도 장면은 남긴다(프레임을 고를 때 빈 글은 빠진다)."""
    out: list[dict[str, Any]] = []
    for i, s in enumerate((getattr(plan, "scenes", None) or [])[:8], start=1):
        row = {k: getattr(s, k, None) for k in SCENE_FIELDS}
        for k in ("start_svg", "end_svg"):
            row[k] = (sanitize_svg(row[k]) or "") if isinstance(row[k], str) and row[k] else ""
        for k, n in TEXT_MAX.items():
            row[k] = _one(row[k])[:n]
        for k in ("start_s", "end_s"):
            row[k] = _seconds(row[k])
        row["n"] = int(_seconds(row["n"]) or i)
        out.append(row)
    return out


# 계약 VideoScene과 같은 상한 (마케팅은 느슨한 VideoPlanIn으로 받는다)
TEXT_MAX = {"visual": 200, "start_frame": 200, "end_frame": 200, "action_en": 400, "camera_en": 120}


def _seconds(raw: Any) -> float:
    try:
        value = float(raw)
    except (TypeError, ValueError):
        return 0.0
    return value if 0 <= value <= 120 else 0.0


def plan_frames(scenes: list[dict[str, Any]] | None) -> list[dict[str, Any]]:
    """장면 경계마다 프레임 한 장. 글이 빈 경계는 빼고, 10장을 넘으면 첫·끝을 남기고 앞에서부터."""
    rows = sorted(
        (s for s in scenes or [] if isinstance(s, dict)), key=lambda s: int(s.get("n") or 0)
    )
    if not rows:
        return []
    found: list[dict[str, Any]] = []

    def add(scene: dict[str, Any], at: Any, text: str, svg: str) -> None:
        # 재료(글 또는 SVG)가 있는 경계만. 글이 없으면 장면 화면 구성 글로 그린다
        if text or svg:
            found.append({
                "at_s": float(at or 0),
                "scene_n": int(scene.get("n") or 0),
                "text_ko": text or _one(scene.get("visual")),
                "camera_en": _one(scene.get("camera_en")),
                "svg": svg or "",
                "svg_source": "planning" if svg else "none",
            })  # fmt: skip

    first = rows[0]
    add(first, first.get("start_s"), _one(first.get("start_frame")), first.get("start_svg") or "")
    for i, scene in enumerate(rows):
        nxt = rows[i + 1] if i + 1 < len(rows) else {}
        text = _one(scene.get("end_frame")) or _one(nxt.get("start_frame"))
        svg = scene.get("end_svg") or nxt.get("start_svg") or ""
        add(scene, scene.get("end_s"), text, svg)
    if len(found) > MAX_FRAMES:
        found = [*found[: MAX_FRAMES - 1], found[-1]]
    for index, frame in enumerate(found):
        frame["index"] = index
    return found


def frame_prompt(
    frame: dict[str, Any],
    total: int,
    *,
    style_en: str | None,
    character_en: str | None,
    aspect: str,
    layout: bool,
    person: bool,
) -> str:
    """프레임 그림 프롬프트(영문). 글자·실존 인물·브랜드를 요청하지 않는다."""
    parts = [
        _one(style_en),
        f"Still keyframe {int(frame.get('index') or 0) + 1} of {total} "
        f"for one short {aspect} video.",
        f"Shows: {_one(frame.get('text_ko'))}.",
    ]
    if frame.get("camera_en"):
        parts.append(f"Camera: {_one(frame.get('camera_en'))}.")
    if character_en:
        parts.append(f"Character: {_one(character_en)}.")
    if layout:
        parts.append(FOLLOW_LAYOUT)
        if person:
            parts.append(SAME_PERSON_AFTER_LAYOUT)
    elif person:
        parts.append(SAME_PERSON)
    parts.append(NO_TEXT_IMAGE)
    return " ".join(p for p in parts if p)[:2000]


def video_prompt(
    scenes: list[dict[str, Any]] | None,
    style_en: str | None,
    speed: float = 1.0,
    negative_en: str | None = None,
) -> str:
    """키프레임 영상 프롬프트. 장면별 "a-bs: {action_en} {camera_en}"(시각 × 배속) + 공통 style·
    negative + 글자 금지.

    #1769 규칙 그대로: 자막·CTA·대사 인용을 넣지 않고 끝에 `NO_TEXT`를 늘 남긴다."""
    head = f"{OPENING} {_one(style_en)}".strip()
    avoid = _one(negative_en)[:300]
    tail = (f" Avoid: {avoid.rstrip('.')}." if avoid else "") + f" {_render.NO_TEXT}"
    rows = [s for s in scenes or [] if isinstance(s, dict)]
    lines = []
    for s in rows:
        action = _one(s.get("action_en")) or _one(s.get("visual"))
        if not action:
            continue
        start = float(s.get("start_s") or 0) * speed
        end = float(s.get("end_s") or 0) * speed
        what = f"{action} {_one(s.get('camera_en'))}".strip()
        lines.append(f"{_render._secs(start)}-{_render._secs(end)}s: {what.rstrip('.')}.")  # noqa: SLF001
    room = _render.VIDEO_PROMPT_MAX - len(head) - len(tail) - 1
    share = max(0, room // max(len(lines), 1) - 1)
    body = " ".join(line[:share] for line in lines)
    return f"{head} {body}{tail}" if body else f"{head}{tail}"


# ---------------------------------------------------------------- 기록
def _scenes_of(rec: dict[str, Any]) -> list[dict[str, Any]]:
    rows = (rec.get("storyboard") or {}).get(PLAN_SCENES_KEY)
    return [r for r in rows if isinstance(r, dict)] if isinstance(rows, list) else []


def state(rec: dict[str, Any] | None) -> dict[str, Any]:
    return dict(((rec or {}).get("render") or {}).get(STATE) or {})


def frames_of(rec: dict[str, Any]) -> list[dict[str, Any]]:
    """기록에 만든 프레임이 있으면 그것, 없으면 기획 장면으로 고른 프레임."""
    made = state(rec).get("frames")
    if isinstance(made, list) and made:
        return [f for f in made if isinstance(f, dict)]
    return plan_frames(_scenes_of(rec))


def active(rec: dict[str, Any] | None, via: str, cid: str | None = None) -> bool:
    """키프레임 방식으로 만드나: 회사 계정 + 키프레임 모델 + 프레임 2장 이상."""
    if not rec or via != _render.MCP:
        return False
    if not _mcp.takes_keyframes(_render.mcp_video_model(cid)):
        return False
    return len(frames_of(rec)) >= 2


def all_done(rec: dict[str, Any]) -> bool:
    frames = frames_of(rec)
    return len(frames) >= 2 and all(f.get("status") == "done" for f in frames)


def _overall(frames: list[dict[str, Any]]) -> str:
    states = {f.get("status") for f in frames}
    if states == {"done"}:
        return "done"
    if "running" in states:
        return "running"
    if states & {"failed", "blocked", "unavailable"}:
        return "failed"
    return "pending"


def _write(record_id: str, update: dict[str, Any]) -> dict[str, Any]:
    store = _creative.store()
    render = store.get_render(record_id)
    cur = dict(render.get(STATE) or {})
    cur.update(update)
    frames = cur.get("frames") or []
    cur["status"] = _overall(frames)
    cur["at"] = _now().isoformat()
    render[STATE] = cur
    store.set_render(record_id, render)
    return cur


def _set_frame(record_id: str, index: int, **values: Any) -> dict[str, Any]:
    store = _creative.store()
    frames = [dict(f) for f in state(store.get(record_id)).get("frames") or []]
    if not 0 <= index < len(frames):
        return {}
    frames[index].update(values)
    frames[index]["at"] = _now().isoformat()
    _write(record_id, {"frames": frames})
    return frames[index]


def mark_approved(record_id: str) -> None:
    """영상 승인(관문 executed) 시각. 키프레임 확인 = 지금 영상 승인 버튼 그대로."""
    if state(_creative.store().get(record_id)).get("frames"):
        _write(record_id, {"approved_at": _now().isoformat()})


def _style(rec: dict[str, Any]) -> str:
    if _creative.upload_kind(rec.get("sheet")) == "virtual_human":
        return VIRTUAL_STYLE_EN  # 가상 인물: 실사
    return _one((rec.get("storyboard") or {}).get("style_en"))


def _person_original(rec: dict[str, Any]) -> bool:
    """가상 인물·일러스트 원본을 참조로 쓸 수 있나(real_photo·캐릭터 없음이면 False)."""
    sheet = (rec.get("render") or {}).get("sheet_image") or {}
    original = sheet.get("source") == _render.ORIGINAL_SOURCE or sheet.get("original") is True
    return _render.is_reference_kind(rec) and original and sheet.get("status") == "done"


def prepare(record_id: str, cid: str | None) -> dict[str, Any]:
    """기록에 render.keyframes가 없으면 프레임 표를 만든다(그림 프롬프트까지, 크레딧 0)."""
    store = _creative.store()
    rec = store.get(record_id) or {}
    cur = state(rec)
    if cur.get("frames"):
        return cur
    frames = plan_frames(_scenes_of(rec))
    aspect = _render.video_aspect(cid)
    person = _person_original(rec)
    character = _one((rec.get("sheet") or {}).get("consistency_en"))
    rows = []
    for f in frames:
        rows.append({
            **f,
            "prompt_en": frame_prompt(f, len(frames), style_en=_style(rec), character_en=character,
                                      aspect=aspect, layout=bool(f["svg"]), person=person),
            "layout_asset": None,
            "layout_png_media_id": None,  # 구도 PNG를 Higgsfield에 올린 media id
            "asset": None,
            "media_id": None,  # 키프레임을 영상 만들 때 올린 media id
            "sha256": None,
            "status": "pending",
            "error": None,
            "attempt": 0,
        })  # fmt: skip
    return _write(record_id, {"frames": rows, "aspect": aspect, "approved_at": None})


def _layout_png(
    record_id: str, frame: dict[str, Any], aspect: str, cid: str | None
) -> bytes | None:
    """구도 PNG(그릴 수 없으면 None = 프롬프트만)."""
    svg = frame.get("svg") or ""
    if not svg:
        return None
    try:
        png = svg_raster.to_png(svg, *svg_raster.size_for(aspect))
    except svg_raster.RasterError as error:
        logger.info("키프레임 %s %s 구도를 그리지 못함: %s", record_id, frame.get("index"), error)
        return None
    name = f"layout_{int(frame.get('index') or 0)}.png"
    try:
        path = _render._dir(record_id, cid) / name  # noqa: SLF001 - 같은 기록 폴더
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(png)
    except OSError:
        return png
    _set_frame(record_id, int(frame.get("index") or 0), layout_asset=name)
    return png


def frame_path(record_id: str, index: int, cid: str | None = None) -> Path:
    return _render._dir(record_id, cid) / f"keyframe_{index}.jpg"  # noqa: SLF001


def _refs(
    record_id: str, rec: dict[str, Any], layout: bytes | None, cid: str | None
) -> list[bytes]:
    """그림 참조: [구도 PNG, 인물 원본, 추가 참조…] 순서(구도 먼저), 상한 `MAX_IMAGE_REFS`."""
    refs: list[bytes] = [layout] if layout else []
    if _person_original(rec):
        try:
            refs.append(_render._ref_bytes(record_id, "sheet_image", cid, rec))  # noqa: SLF001
        except OSError:
            pass
        refs += _render.extra_reference_bytes(record_id, rec, cid)
    return refs[:MAX_IMAGE_REFS]


async def render_frame(
    ledger: MediaLedger, meta: RequestMeta, record_id: str, index: int, cid: str | None
) -> dict[str, Any]:
    """프레임 한 장을 그린다(지금 그림 관문 그대로). 돌려주는 값은 그 프레임."""
    store = _creative.store()
    rec = store.get(record_id) or {}
    frames = state(rec).get("frames") or []
    if not 0 <= index < len(frames):
        return {}
    frame = frames[index]
    attempt = int(frame.get("attempt") or 0) + 1
    part = f"keyframe_{index}_{attempt}"  # 다시 그리기마다 새 멱등 키(장부·관문)
    via = _render.route(cid)
    if not _settings.effective(cid).image_credits_ok:  # 미리 보기·예약 전에 멈춘다
        return _set_frame(record_id, index, status="blocked", error=_render.IMAGE_CREDITS_DISABLED)
    why = _render.configured("image", via, cid)
    if why:
        return _set_frame(record_id, index, status="unavailable", error=why)
    _set_frame(record_id, index, status="running", error=None, attempt=attempt)
    aspect = str(state(rec).get("aspect") or _render.video_aspect(cid))
    layout = _layout_png(record_id, frame, aspect, cid)
    rec = store.get(record_id) or rec
    refs = _refs(record_id, rec, layout, cid)
    negative = str((rec.get("storyboard") or {}).get("negative_prompt_en") or "")
    params = _mcp.image_params(_render.mcp_image_model(cid), str(frame.get("prompt_en") or ""),
                               negative, aspect)  # fmt: skip
    credits = None
    try:
        credits = await _render._mcp_preflight(cid, "image", params)  # noqa: SLF001
    except _render._ERRORS as error:  # noqa: SLF001
        logger.warning("%s", log_line("keyframes: 미리 보기", error, record_id))
        return _set_frame(record_id, index, status="blocked", error=_render._code(error))  # noqa: SLF001
    decision, seq, blocked = await _render._gate_image(  # noqa: SLF001
        ledger, meta, record_id, part, via, credits, cid
    )
    if blocked or decision is None or seq is None:
        return _set_frame(record_id, index, status="blocked", error=blocked,
                          credits_estimate=credits)  # fmt: skip
    target = frame_path(record_id, index, cid)
    uploaded: list[str] = []
    try:
        async with _render.CLIENT() as client:
            sha = await _render._mcp_render(  # noqa: SLF001
                client, cid, record_id, part, "image", params, refs[0] if refs else None,
                extra_refs=refs[1:], target=target, uploaded=uploaded,
            )  # fmt: skip
    except _render._ERRORS as error:  # noqa: SLF001
        code = _render._code(error)  # noqa: SLF001
        logger.warning("%s", log_line(f"keyframes: 그림 {index}", error, record_id))
        _render._safe(lambda: ledger.mark_failed(seq, _now()))  # noqa: SLF001
        stopped = code == _mcp.UNLIM_UNAVAILABLE
        return _set_frame(record_id, index, status="blocked" if stopped else "failed", error=code,
                          credits_estimate=credits)  # fmt: skip
    usage = usage_mod.planned("image", None)
    _render._safe(lambda: ledger.settle(  # noqa: SLF001
        seq, model_id=_render._model("image", via, cid), usage=usage,  # noqa: SLF001
        usage_source="estimate", result_json='{"provider":"higgsfield_mcp"}', now=_now(),
    ))  # fmt: skip
    layout_id = uploaded[0] if layout and uploaded else None  # 구도가 있으면 첫 참조가 구도
    return _set_frame(record_id, index, status="done", error=None, asset=target.name, sha256=sha,
                      credits_estimate=credits, execution_id=decision.execution_id,
                      layout_png_media_id=layout_id, media_id=None)  # fmt: skip


async def ensure(
    ledger: MediaLedger, meta: RequestMeta, record_id: str, cid: str | None
) -> str | None:
    """프레임을 모두 그린다(이미 done인 장은 다시 안 그림). 하나라도 못 그리면 이유 코드."""
    cur = prepare(record_id, cid)
    for frame in cur.get("frames") or []:
        if frame.get("status") in ("done", "running"):
            continue
        await render_frame(ledger, meta, record_id, int(frame.get("index") or 0), cid)
    rec = _creative.store().get(record_id) or {}
    bad = [f for f in frames_of(rec) if f.get("status") != "done"]
    if bad:
        return f"keyframe_{bad[0].get('status') or 'failed'}"
    return None


def redraw(ledger: MediaLedger, meta: RequestMeta, record_id: str, index: int) -> str | None:
    """[다시 그리기] 한 장(뒤에서 돈다). 못 하면 이유 코드: no_keyframes·bad_index·in_progress·
    video_started."""
    rec = _creative.store().get(record_id) or {}
    frames = state(rec).get("frames") or []
    if not frames:
        return "no_keyframes"
    if not 0 <= index < len(frames):
        return "bad_index"
    if frames[index].get("status") == "running":
        return "in_progress"
    video = (rec.get("render") or {}).get("video") or {}
    if video.get("status") in ("running", "done"):
        return "video_started"
    cid = video.get("company_id") or _render.request_company(meta)
    _set_frame(record_id, index, status="running", error=None)  # 응답에 바로 보이게
    _render._spawn(render_frame(ledger, meta, record_id, index, cid))  # noqa: SLF001
    return None


def summary(rec: dict[str, Any]) -> dict[str, Any]:
    """승인 요약에 넣을 키프레임 수·키프레임 크레딧(아는 값만 더한다)."""
    frames = frames_of(rec)
    known = [float(f["credits_estimate"]) for f in frames
             if isinstance(f.get("credits_estimate"), (int, float))]  # fmt: skip
    return {"keyframes": len(frames), "keyframe_credits": round(sum(known), 2) if known else None}


def video_params(
    rec: dict[str, Any], seconds: int, cid: str | None, speed: float
) -> dict[str, Any]:
    """미리 보기·만들기 params(미디어 없이). mode는 image-to-video."""
    negative = (rec.get("storyboard") or {}).get("negative_prompt_en")
    prompt = video_prompt(_scenes_of(rec), _style(rec), speed, negative)
    model = _render.mcp_video_model(cid)
    params = _mcp.video_params(model, prompt, _render.video_aspect(cid), seconds)
    return {**params, "mode": _mcp.KEYFRAME_MODE}


async def render_video(
    client: Any, cid: str | None, record_id: str, rec: dict[str, Any], params: dict[str, Any]
) -> str:
    """키프레임을 순서대로 올리고 start·중간·end로 영상을 만든다. 돌려주는 값은 sha256."""
    hf = _mcp.Higgsfield(client, cid or "")
    ids: list[str] = []
    for f in frames_of(rec)[:MAX_FRAMES]:
        index = int(f.get("index") or 0)
        data = frame_path(record_id, index, cid).read_bytes()
        ids.append(await _mcp.upload(hf, data, f"{record_id}-keyframe-{index}.jpg", "image/jpeg"))
        _set_frame(record_id, index, media_id=ids[-1])
    full = _mcp.with_keyframes(params, ids)
    job = await _mcp.submit(hf, "video", full)
    _render._set(record_id, "video", request_id=job)  # noqa: SLF001
    url = await _mcp.wait(hf, job, _render.SLEEP)
    return await _render._download(client, url, _render.asset_path(record_id, "video", cid))  # noqa: SLF001


__all__ = [
    "PLAN_SCENES_KEY",
    "REFERENCE",
    "active",
    "all_done",
    "ensure",
    "frame_prompt",
    "frames_of",
    "plan_frames",
    "plan_scenes",
    "prepare",
    "redraw",
    "render_frame",
    "video_prompt",
]
