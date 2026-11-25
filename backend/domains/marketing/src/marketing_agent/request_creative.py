"""홍보 영상 의뢰에 회사 대표 캐릭터 붙이기 (2026-11-23).

총괄 홍보 영상 흐름(`promo_video`)에서 의뢰자가 회사 대표 캐릭터를 골랐을 때만 쓴다. 캐릭터가 없는
의뢰는 지금 그대로 `generate_media`(B)로 만든다.

영상 준비(`prepare_video_draft_from_plan`, read) — `prepare_storyboard`
- 대표 캐릭터 기록(CR)을 의뢰용 새 기록으로 복사한다(`creative_records.copy_for_request`, 번호는
  회사·의뢰·캐릭터로 정해 다시 불려도 하나). 대표 캐릭터에 시트 그림이 있으면 파일째 복사해 다시
  만들지 않는다.
- 기획 제작안 장면(`video_plan.scenes`: 화면 구성 글·영문 장면 프롬프트)으로 스토리보드를 코드로
  조립해 그 기록에 붙인다(`storyboard.build_output`, LLM 없음. 장면 수 = 칸 수, 최대 8).
  캐릭터 고정 문장은 이미지 시트의 `consistency_en`, 그림체는 제작안의 `style`이다.

영상 만들기(`generate_media` 입력에 `creative_record_id`가 있을 때) — `make_video`
- 총괄이 부르면 `async_generate`가 긴 작업으로 받으므로 여기서는 끝까지 기다려도 된다.
- 그림: 시트 그림이 없을 때만 1장(올린 캐릭터의 작은 미리보기를 참조로) + 스토리보드 그림 1장.
  `creative_render`의 그림 생성 그대로다: 장부 예약(장 수) → `execute()` generate_media(B,
  media_kind=image) → executed일 때만 Higgsfield. 의뢰 한 건에 최대 그림 2장.
- 영상: `creative_render.request_video` = 영상 초 예약 → `execute()` generate_creative_video(C).
  보류되면 `video_held`(+review_id)로 끝내고, 총괄이 그 단계를 "영상 승인 대기"로 둔다(실패로 치지
  않는다). 총괄이 다시 부르면 `check_video`가 같은 멱등 키로 관문을 다시 묻는다: 승인이면 그때
  만들고, 거절이면 `video_rejected`. 관문 밖에서 Higgsfield를 부르는 길은 없다.
- 다 만든 영상은 지금 의뢰 영상과 같은 길로 잇는다: 결과를 `generated`로 돌려주면
  `request_video.attach_label_after_generate`가 AI 라벨 → SNS 초안 연결·점검을 하고, 기록 폴더의
  `meta.json`으로 의뢰 영상 목록(제작 기록 · 의뢰로 만든 영상, #1612)과 유튜브 올리기에 이어진다.
"""

from __future__ import annotations

import base64
import binascii
import json
import logging
import re
import shutil
from datetime import UTC, datetime
from typing import Any

from ax_contracts.schemas import AgentRequest, AgentResult, Evidence, TaskStatus
from ax_contracts.schemas.marketing_character import VIRTUAL_HUMAN_LABEL
from ax_contracts.schemas.marketing_storyboard import MAX_CHARACTER_EN, MAX_STYLE_EN
from marketing_agent import creative_records as _creative
from marketing_agent import creative_render as _render
from marketing_agent import keyframes as _keyframes
from marketing_agent import storyboard as _storyboard
from marketing_agent.character_sheet import VIRTUAL_STYLE_EN
from marketing_agent.media.ledger import MediaLedger

logger = logging.getLogger("marketing.request_creative")

RECORD_ID = re.compile(r"CR-[0-9a-f]{12}")
INPUT_KEY = "creative_record_id"
MAX_SCENES = 8
# 칸마다 샷 크기를 돌려 쓴다(같은 크기가 이어지지 않게). 앵글은 눈높이
SHOT_CYCLE = ("wide", "medium", "close_up", "detail", "full", "medium", "close_up", "wide")
WAIT_LIMIT_S = _render.JOB_TIMEOUT_S + 120.0
VIDEO_HELD = "video_held"
VIDEO_REJECTED = "video_rejected"
DONE_STATES = {"done", "failed", "blocked", "unavailable", "rejected"}


def _now() -> datetime:
    return datetime.now(UTC)


def _cut(text: Any, n: int) -> str:
    return " ".join(str(text or "").split())[:n]


# ---------------------------------------------------------------- 영상 준비: 기록 복사 + 스토리보드
def _copy_sheet_image(brand_id: str, record_id: str) -> None:
    """대표 캐릭터의 시트 그림이 있으면 의뢰 기록으로 파일째 복사한다(다시 만들지 않는다)."""
    store = _creative.store()
    if (store.get_render(record_id).get("sheet_image") or {}).get("status") == "done":
        return
    item = store.get_render(brand_id).get("sheet_image") or {}
    asset = item.get("asset")
    if item.get("status") != "done" or asset not in _render.UPLOAD_FILES["sheet_image"]:
        return
    source = _render._dir(brand_id) / str(asset)  # noqa: SLF001 - 같은 모듈 묶음의 파일 자리
    target = _render._dir(record_id) / str(asset)  # noqa: SLF001
    try:
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source, target)
    except OSError:
        logger.warning("대표 캐릭터 시트 그림 복사 실패: %s → %s", brand_id, record_id)
        return
    _render._set(  # noqa: SLF001
        record_id,
        "sheet_image",
        status="done",
        error=None,
        asset=asset,
        sha256=item.get("sha256"),
        source="brand_copy",
        copied_from=brand_id,
        # 올린 원본(가상 인물·일러스트)을 복사한 것이면 영상 참조로 쓸 수 있다(video_reference)
        original=item.get("source") == _render.ORIGINAL_SOURCE,
    )


def plan_storyboard(plan: Any, sheet: dict[str, Any]) -> dict[str, Any] | None:
    """기획 제작안 장면으로 스토리보드(StoryboardPromptOutput 모양)를 코드로 만든다.

    장면이 없으면 None."""
    scenes = [s for s in (getattr(plan, "scenes", None) or []) if _cut(s.prompt, 10)][:MAX_SCENES]
    if not scenes:
        return None
    shots = [
        _storyboard.LooseShot(
            scene_ko=_cut(getattr(s, "visual", None) or s.prompt, 200),
            shot_size=SHOT_CYCLE[i % len(SHOT_CYCLE)],
            angle="eye_level",
            composition_ko=_cut(getattr(s, "visual", None) or "", 200),
            has_character=True,
            subject_en=_cut(s.prompt, 300),
            # 11/25: 기획이 그린 SVG 콘티(장면 첫 화면)가 있으면 칸 SVG로 그대로 쓴다
            # (build_output이 다시 정리한다. 칸에 SVG가 있으면 board_svg는 LLM을 부르지 않는다)
            svg=getattr(s, "start_svg", "") or "",
        )
        for i, s in enumerate(scenes)
    ]
    concept = _cut(plan.concept, 80) or "홍보 영상"
    # 11/24 AI 가상 인물: 회사 그림체(style_en, 예: flat illustration) 대신 실사·참조와 같은 사람
    virtual = _creative.upload_kind(sheet) == "virtual_human"
    style = VIRTUAL_STYLE_EN if virtual else getattr(plan, "style", None)
    read = _storyboard.StoryRead(
        title_ko=concept,
        logline_ko=_cut(plan.script, 300) or concept,
        shots=shots,
        style_en=_cut(style, MAX_STYLE_EN),
    )
    character = _cut(sheet.get("consistency_en"), MAX_CHARACTER_EN) or None
    out = _storyboard.build_output(read, character, read.style_en, len(shots))
    board = out.model_dump(mode="json")
    duration = getattr(plan, "duration_s", None)
    # 영상 길이는 기획 제작안 길이 (상한은 서버 설정)
    if isinstance(duration, int) and duration > 0:
        board[_render.PLAN_DURATION_KEY] = duration
    # 11/25 키프레임: 기획 장면의 첫·끝 화면 글·SVG·동작·카메라 (keyframes.plan_frames가 고른다)
    board[_keyframes.PLAN_SCENES_KEY] = _keyframes.plan_scenes(plan)
    return board


def plan_svg_missing(plan: Any) -> str:
    """기획 제작안에 콘티 SVG가 없는 이유 코드 (로그용, 값·글은 넣지 않는다).

    fallback_reason=…(규칙 초안) · svg_reason=…(기획 포즈 단계 실패) · sanitize_dropped(정리 탈락)
    · unknown(예전 제작안 등)."""
    fallback = getattr(plan, "fallback_reason", "") or ""
    if isinstance(fallback, str) and fallback:
        return f"fallback_reason={_cut(fallback, 20)}"
    svg_reason = getattr(plan, "svg_reason", "") or ""
    if isinstance(svg_reason, str) and svg_reason:
        return f"svg_reason={_cut(svg_reason, 20)}"
    if any(getattr(s, "start_svg", "") for s in getattr(plan, "scenes", None) or []):
        return "sanitize_dropped"
    return "unknown"


def prepare_storyboard(brand_id: str, context_id: str, plan: Any) -> tuple[str | None, str | None]:
    """(의뢰용 기록 번호, 못 쓴 이유).

    대표 캐릭터 기록이 이 회사에 없으면 (None, "record_not_found")."""
    if not RECORD_ID.fullmatch(brand_id):
        return None, "record_not_found"
    store = _creative.store()
    record_id = store.copy_for_request(brand_id, context_id)
    if record_id is None:
        return None, "record_not_found"
    _copy_sheet_image(brand_id, record_id)
    rec = store.get(record_id) or {}
    board = plan_storyboard(plan, rec.get("sheet") or {})
    if board is None:
        return record_id, "no_scenes"
    if not any(s.get("svg") for s in board["shots"]):  # 마케팅이 대신 그린다(`board_svg`)
        logger.info("기획 콘티 SVG 없음 → 마케팅이 그림: %s %s", record_id, plan_svg_missing(plan))
    if not rec.get("storyboard"):  # 같은 의뢰를 다시 준비하면 처음 붙인 스토리보드를 둔다
        store.attach_storyboard(record_id, _cut(plan.script, 1000), len(board["shots"]), board)
    return record_id, None


# ---------------------------------------------------------------- 영상 만들기
def _result(request: AgentRequest, status: TaskStatus, outcome: str, **output: Any) -> AgentResult:
    return AgentResult(
        meta=request.meta,
        status=status,
        outcome=outcome,
        output=output,
        evidence=Evidence(
            queries=["request_creative"],
            data_summary=f"의뢰 캐릭터 영상 {output.get(INPUT_KEY)}: {outcome}",
            collected_at=_now(),
            ref_ids=[str(output.get(INPUT_KEY) or "")],
        ),
    )


def _part(record_id: str, part: str) -> dict[str, Any]:
    return _creative.store().get_render(record_id).get(part) or {}


async def _wait_part(record_id: str, part: str) -> dict[str, Any]:
    """그 단계가 끝날 때까지(running이 아닐 때까지) 기다린다."""
    waited = 0.0
    while True:
        cur = _part(record_id, part)
        if cur.get("status") != "running" or waited > WAIT_LIMIT_S:
            return cur
        await _render.SLEEP(_render.POLL_S)
        waited += _render.POLL_S


def _thumb_ref(rec: dict[str, Any]) -> bytes | None:
    raw = rec.get("thumbnail_base64")
    if not isinstance(raw, str) or not raw:
        return None
    try:
        return base64.b64decode(raw, validate=True)
    except (binascii.Error, ValueError):
        return None


async def _ensure_images(
    request: AgentRequest, ledger: MediaLedger, record_id: str, rec: dict[str, Any]
) -> str | None:
    """시트 그림(없을 때만)·스토리보드 그림을 만든다. 못 만들면 이유 코드.

    11/24: 회사를 요청 메타에서 한 번 정해(`request_company`) 경로·키·설정·폴더에 같은 값을 쓴다.
    예전에는 `_render_image`에 경로를 넘기지 않아 회사 계정을 연결했어도 늘 키 길(기본값 API)로 갔다
    (운영 wr_aa0669b6f7·wr_cb45ce73c4: 안전 기록 provider "higgsfield")."""
    meta = request.meta
    cid = _render.request_company(meta)
    sheet = await _wait_part(record_id, "sheet_image")
    if sheet.get("status") != "done":
        via = _render.route(cid)
        _render._log_route(via, cid, "sheet_image", record_id)  # noqa: SLF001
        why = _render.configured("image", via, cid)
        if why:
            return why
        prompt = str((rec.get("sheet") or {}).get("prompt_en") or "")
        negative = str((rec.get("sheet") or {}).get("negative_prompt_en") or "")
        _render._set(  # noqa: SLF001
            record_id, "sheet_image", status="running", error=None, route=via, company_id=cid
        )
        await _render._render_image(  # noqa: SLF001 - 같은 관문·장부 순서를 그대로 쓴다
            ledger,
            meta,
            record_id,
            "sheet_image",
            prompt,
            negative,
            _thumb_ref(rec),
            _render.image_aspect(_render.SHEET_ASPECT, cid),
            via,
            cid,
        )
        sheet = _part(record_id, "sheet_image")
        if sheet.get("status") != "done":
            return f"sheet_image_{sheet.get('status') or 'failed'}"
    rec = _creative.store().get(record_id) or rec
    via = _render.route(cid)
    if _keyframes.active(rec, via, cid):
        # 11/25 키프레임 방식: 장면 경계마다 실사 그림 1장(스토리보드 그림은 만들지 않는다).
        # 하나라도 못 그리면 영상을 요청하지 않는다
        return await _keyframes.ensure(ledger, meta, record_id, cid)
    if not _render.board_needed(rec, via, cid):
        return None  # 11/25 가상 인물 원본이 영상 참조: 스토리보드 그림(크레딧)을 만들지 않는다
    _render.maybe_start_board(ledger, meta, record_id, cid)  # 이미 만들었거나 도는 중이면 안 한다
    board = await _wait_part(record_id, "board_image")
    if board.get("status") != "done":
        return f"board_image_{board.get('status') or 'missing'}"
    return None


def _write_meta(record_id: str, post_draft_id: str, video: dict[str, Any]) -> None:
    """의뢰 영상 목록(`request_video._find_meta`)이 이 영상을 찾게 기록 폴더에 meta.json을 둔다."""
    meta = {
        "media_id": f"med_{record_id}",
        "post_draft_id": post_draft_id,
        "media_kind": "video",
        "duration_s": video.get("seconds"),
        "provider": _render.PROVIDER,
        "creative_record_id": record_id,
        "generated_at": video.get("at") or _now().isoformat(),
    }
    path = _render.asset_path(record_id, "video").parent / "meta.json"
    path.write_text(json.dumps(meta, ensure_ascii=False), encoding="utf-8")


def _generated(request: AgentRequest, record_id: str, video: dict[str, Any]) -> AgentResult:
    post_draft_id = str((request.input or {}).get("post_draft_id") or "")
    rec = _creative.store().get(record_id) or {}
    virtual = _creative.upload_kind(rec.get("sheet")) == "virtual_human"
    path = _render.asset_path(record_id, "video")
    try:
        _write_meta(record_id, post_draft_id, video)
    except OSError:
        logger.warning("의뢰 캐릭터 영상 meta.json을 쓰지 못함: %s", record_id)
    return _result(
        request,
        TaskStatus.COMPLETED,
        "generated",
        **{INPUT_KEY: record_id},
        media_id=f"med_{record_id}",
        media_kind="video",
        post_draft_id=post_draft_id,
        asset_ref=str(path),
        sha256=video.get("sha256"),
        provider=_render.PROVIDER,
        model_id=_render.video_model(),
        duration_s=video.get("seconds"),
        usage={"unit": "seconds", "amount": video.get("seconds")},
        usage_source="estimate",
        ai_generated=True,
        execution_id=video.get("execution_id"),
        # 11/24 AI 가상 인물로 만든 영상: AI 라벨 단계가 "AI 가상 인물" 표시·캡션 한 줄을 더한다
        ai_virtual_human=virtual,
        ai_disclosure=VIRTUAL_HUMAN_LABEL if virtual else None,
        seconds_clamped_from=video.get("seconds_clamped_from"),
    )


async def make_video(request: AgentRequest, ledger: MediaLedger) -> AgentResult:
    """캐릭터가 있는 의뢰의 영상 만들기. 보류면 `video_held`, 거절이면 `video_rejected`."""
    record_id = str((request.input or {}).get(INPUT_KEY) or "")
    out = {INPUT_KEY: record_id}
    rec = _creative.store().get(record_id) if RECORD_ID.fullmatch(record_id) else None
    if rec is None:
        return _result(request, TaskStatus.FAILED, "creative_record_missing", **out)
    _render.expire_stale(record_id)
    video = _part(record_id, "video")
    if video.get("status") == "rejected":  # 거절된 영상은 다시 승인에 올리지 않는다
        return _result(request, TaskStatus.FAILED, VIDEO_REJECTED, **out, retryable=False)
    if video.get("status") != "done":
        blocked = await _ensure_images(request, ledger, record_id, rec)
        if blocked:
            return _result(request, TaskStatus.FAILED, blocked, **out)
    video = _part(record_id, "video")
    if video.get("status") == "awaiting_approval":
        await _render.check_video(ledger, request.meta, record_id)  # 같은 멱등 키로 다시 묻는다
    elif video.get("status") not in ("running", "done"):
        who = f"의뢰 {request.meta.context_id.removeprefix('ctx_')}"
        try:
            await _render.request_video(
                ledger, request.meta, record_id, who, _render.request_company(request.meta)
            )
        except _render.RenderError as error:
            return _result(request, TaskStatus.FAILED, str(error), **out)
    video = await _wait_part(record_id, "video")
    status = video.get("status")
    if status == "awaiting_approval":
        return _result(
            request,
            TaskStatus.FAILED,
            VIDEO_HELD,
            **out,
            review_id=video.get("review_id"),
            retryable=False,
        )
    if status == "rejected":
        return _result(request, TaskStatus.FAILED, VIDEO_REJECTED, **out, retryable=False)
    if status != "done":
        return _result(
            request,
            TaskStatus.FAILED,
            f"video_{status or 'failed'}",
            **out,
            error=str(video.get("error") or ""),
        )
    return _generated(request, record_id, video)


__all__ = [
    "INPUT_KEY",
    "VIDEO_HELD",
    "VIDEO_REJECTED",
    "make_video",
    "plan_storyboard",
    "prepare_storyboard",
]
