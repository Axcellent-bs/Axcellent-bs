"""skill `prepare_sns_post_from_media`: 미디어 생성 → 게시 규격 변환 → AI 라벨 → 게시 초안·점검.

"반자동": AI가 만들고 초안·점검 결과까지 내고, 게시는 사람이 `publish_sns`(C등급)를 승인해야 한다.
이 skill은 `publish_sns`를 부르지 않고, 승인·게시로 이어지는 경로도 만들지 않는다.

순서(앞 단계가 막히면 뒤 단계는 하지 않는다):
 0. 입력 검증 → 출처(공지·쿠폰) 확인 → 미디어 폴더·ffmpeg·글꼴 확인.
    돈이 드는 생성 전에 끝낸다(생성한 뒤에야 후처리를 못 하는 일이 없게).
 1. `generate_media_flow`(영상은 BGM 모드 포함). 안전 관문 B등급·건당/월 상한을 그대로 거친다.
    `executed`가 아니면(보류·거절·상한) 그 결과를 그대로 돌려준다. 같은 입력으로 다시 부르면
    장부에서 재생되어 비용이 다시 나지 않는다.
 2. 규격 변환(ffmpeg, Pillow 없음). 이미지는 비율 4:5~1.91:1·너비 320~1440px로 맞추고
    (넘치는 쪽을 가운데 기준으로 자른다), 영상은 ffprobe로 보고 필요할 때만(가로 1920 초과·fps 밖)
    H.264로 다시 인코딩한다. 길이는 고칠 수 없어 벗어나면 거절한다.
 3. `sns_label.apply_label`(오버레이 + XMP/MP4 메타). 결과는 생성 폴더의 `labeled.jpg|mp4`이고
    `meta.json`에 `ai_label`이 기록된다(`check_sns_post`가 이 기록을 본다).
 4. `draft_sns_post`(자산은 `MARKETING_MEDIA_DIR` 기준 상대 `asset_ref`) → `check_sns_post`.

결과에는 `publish_sns`에 넣을 값(`publish_input`)을 담지만 부르지는 않는다. 키·서명 URL·절대 경로는
결과·로그에 넣지 않는다(ffmpeg 오류는 종류만 남긴다).
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import shutil
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, StrictInt, ValidationError, model_validator

from ax_contracts.schemas import AgentRequest, AgentResult, Evidence, TaskStatus
from ax_contracts.schemas.common import AxModel
from marketing_agent import sns, sns_label, sns_media
from marketing_agent.media.audio_mix import AudioMixError, Runner, subprocess_runner
from marketing_agent.media.bgm_flow import generate_media_flow
from marketing_agent.media.config import MediaSettings, ffmpeg_bin
from marketing_agent.media.skill import FromDecision
from marketing_agent.notice import NoticeStore
from marketing_agent.sns import PlatformOptions, SnsCouponFacts, SnsStore

ACTION = "prepare_sns_post_from_media"
_MEDIA_REF = r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}"
FFMPEG_TIMEOUT_S = 600
# 영상 다시 인코딩(규격 맞춤 때만). 라벨 단계가 한 번 더 인코딩하므로 이쪽은 높은 화질로 둔다
NORMALIZE_CRF = 16
NORMALIZE_FPS = 30  # fps가 23~60 밖일 때 맞출 값
PREPARED_IMAGE = "prepared.png"
PREPARED_VIDEO = "prepared.mp4"
MARK_KEY = "sns_prepare"  # meta.json에 남기는 재사용 표식


class PrepareError(Exception):
    """단계 실패. `code`는 닫힌 집합이고 메시지에는 경로·키·ffmpeg 원문을 넣지 않는다."""

    def __init__(self, stage: str, code: str) -> None:
        super().__init__(f"{stage}: {code}")
        self.stage = stage
        self.code = code


class PrepareSnsPostInput(AxModel):
    """`generate_media` 입력(자유 프롬프트 없음) + `draft_sns_post` 출처(정확히 하나)."""

    notice_id: str | None = None
    coupon: SnsCouponFacts | None = None
    media_kind: Literal["image", "video"] = "image"
    scene_template_id: str = Field(min_length=1)
    fields: dict[str, Any] = Field(default_factory=dict)
    resolution: str | None = Field(default=None, pattern=r"^(\d{3,4}x\d{3,4}|\d{3,4}p)$")
    quality: Literal["low", "medium", "high"] | None = None  # 이미지만
    # 릴스 최소 3초. 3초 미만은 생성 전에 거절한다(비용을 쓰고 나서 규격 위반을 알지 않게)
    duration_s: StrictInt | None = Field(default=None, ge=3, le=60)
    claimed_cost_krw: StrictInt | None = Field(default=None, ge=0)
    # `MARKETING_AUDIO_MODE=bgm_overlay`일 때 영상에 필요(`generate_media_flow`와 같다)
    bgm_scene_template_id: str | None = None
    bgm_fields: dict[str, Any] | None = None
    # 생성 장부·폴더의 묶음 id(`generate_media.post_draft_id`). 비우면 입력에서 정한다
    media_ref: str | None = Field(default=None, pattern=rf"^{_MEDIA_REF}$")
    platform_options: PlatformOptions = Field(default_factory=PlatformOptions)

    @model_validator(mode="after")
    def _exactly_one_source(self) -> PrepareSnsPostInput:
        if (self.notice_id is None) == (self.coupon is None):
            raise ValueError("notice_id와 coupon 중 하나만 준다")
        return self

    def reel(self) -> bool:
        return self.media_kind == "video"


# --- 규격 계획(순수 계산) -------------------------------------------------------


@dataclass(frozen=True)
class ImagePlan:
    crop: tuple[int, int] | None  # 가운데 기준으로 자를 (가로, 세로)
    size: tuple[int, int]  # 최종 (가로, 세로)
    changed: bool


def plan_image(width: int, height: int) -> ImagePlan:
    """인스타 이미지 규격(`sns_media`의 상수)에 맞는 자르기·크기를 계산한다."""
    if width <= 0 or height <= 0:
        raise PrepareError("convert", "source_unreadable")
    crop_w, crop_h = width, height
    if width / height < sns_media.IMAGE_ASPECT_MIN:  # 너무 세로로 김: 위·아래를 자른다
        crop_h = int(width / sns_media.IMAGE_ASPECT_MIN)
    elif width / height > sns_media.IMAGE_ASPECT_MAX:  # 너무 가로로 김: 좌·우를 자른다
        crop_w = int(height * sns_media.IMAGE_ASPECT_MAX)
    out_w = min(max(crop_w, sns_media.IMAGE_WIDTH_MIN), sns_media.IMAGE_WIDTH_MAX)
    out_h = round(out_w * crop_h / crop_w)
    # 반올림으로 비율이 경계를 1px 넘는 일을 막는다
    if out_w / out_h < sns_media.IMAGE_ASPECT_MIN:
        out_h = int(out_w / sns_media.IMAGE_ASPECT_MIN)
    elif out_w / out_h > sns_media.IMAGE_ASPECT_MAX:
        out_h = -int(-out_w // sns_media.IMAGE_ASPECT_MAX)
    cropped = (crop_w, crop_h) != (width, height)
    return ImagePlan(
        crop=(crop_w, crop_h) if cropped else None,
        size=(out_w, out_h),
        changed=cropped or (out_w, out_h) != (width, height),
    )


def image_filter(plan: ImagePlan) -> str:
    parts = []
    if plan.crop is not None:
        parts.append(f"crop={plan.crop[0]}:{plan.crop[1]}")
    parts.append(f"scale={plan.size[0]}:{plan.size[1]}:flags=lanczos")
    parts.append("format=rgb24")  # 투명도는 JPEG에 없다
    return ",".join(parts)


@dataclass(frozen=True)
class VideoPlan:
    filters: tuple[str, ...]
    reasons: tuple[str, ...]

    @property
    def needs_reencode(self) -> bool:
        return bool(self.filters)


def plan_video(probe: dict[str, Any]) -> VideoPlan:
    """ffprobe 결과로 다시 인코딩이 필요한지 계산한다. 길이처럼 고칠 수 없는 위반은 거절한다.

    컨테이너·코덱(H.264/AAC)은 라벨 단계가 항상 다시 인코딩해 맞추므로 여기서 다루지 않는다.
    """
    video = next((s for s in probe.get("streams", []) if s.get("codec_type") == "video"), None)
    if video is None:
        raise PrepareError("convert", "video_unreadable")
    fmt = probe.get("format", {})
    try:
        duration = float(fmt.get("duration") or video.get("duration"))
    except (TypeError, ValueError):
        raise PrepareError("convert", "video_duration_unknown") from None
    if not sns_media.REEL_DURATION_MIN_S <= duration <= sns_media.REEL_DURATION_MAX_S:
        raise PrepareError("convert", "video_duration_out_of_range")  # 길이는 고칠 수 없다
    filters: list[str] = []
    reasons: list[str] = []
    width = video.get("width")
    if isinstance(width, int) and width > sns_media.REEL_WIDTH_MAX:
        filters.append(f"scale={sns_media.REEL_WIDTH_MAX}:-2")
        reasons.append("width_over_limit")
    fps = sns_media._ratio(video.get("avg_frame_rate")) or sns_media._ratio(
        video.get("r_frame_rate")
    )
    if fps is None or not sns_media.REEL_FPS_MIN <= fps <= sns_media.REEL_FPS_MAX:
        filters.append(f"fps={NORMALIZE_FPS}")
        reasons.append("fps_out_of_range")
    return VideoPlan(tuple(filters), tuple(reasons))


# --- 도구 확인·실행 -------------------------------------------------------------


def preflight_tools() -> str | None:
    """후처리에 필요한 도구가 없으면 이유 코드. 생성(비용) 전에 부른다."""
    if shutil.which(ffmpeg_bin()) is None or shutil.which("ffmpeg") is None:
        return "ffmpeg_not_found"
    if shutil.which("ffprobe") is None:
        return "ffprobe_not_found"
    try:
        sns_label.find_font(sns_label.load_config())
    except sns_label.LabelError as error:
        return error.code
    return None


def _run(args: list[str], runner: Runner) -> None:
    try:
        done = runner(args, FFMPEG_TIMEOUT_S)
    except AudioMixError as error:
        raise PrepareError("convert", error.code) from None
    if done.returncode != 0:
        raise PrepareError("convert", "ffmpeg_failed")  # 원문에 경로가 섞일 수 있어 남기지 않는다


@dataclass(frozen=True)
class Converted:
    path: Path
    details: dict[str, Any]


def convert_image(source: Path, out_dir: Path, runner: Runner) -> Converted:
    probe = sns_media.run_ffprobe(source)
    video = next((s for s in (probe or {}).get("streams", []) if s.get("width")), None)
    if not video or not isinstance(video.get("width"), int):
        raise PrepareError("convert", "source_unreadable")
    width, height = video["width"], video["height"]
    plan = plan_image(width, height)
    details: dict[str, Any] = {
        "kind": "image",
        "source_size": [width, height],
        "output_size": list(plan.size),
        "cropped": plan.crop is not None,
        "reencoded": False,
    }
    if not plan.changed and source.suffix.lower() in (".jpg", ".jpeg", ".png"):
        return Converted(source, details)  # 규격 안이면 그대로 라벨 단계로(JPEG는 거기서 만든다)
    out = out_dir / PREPARED_IMAGE
    args = [
        ffmpeg_bin(), "-y", "-hide_banner", "-loglevel", "error",
        "-i", str(source), "-frames:v", "1", "-vf", image_filter(plan), str(out),
    ]  # fmt: skip
    _run(args, runner)
    details["reencoded"] = True
    return Converted(out, details)


def convert_video(source: Path, out_dir: Path, runner: Runner) -> Converted:
    probe = sns_media.run_ffprobe(source)
    if not probe:
        raise PrepareError("convert", "source_unreadable")
    plan = plan_video(probe)
    details: dict[str, Any] = {
        "kind": "video",
        "reencoded": plan.needs_reencode,
        "reasons": list(plan.reasons),
    }
    if not plan.needs_reencode:
        return Converted(source, details)  # 코덱·컨테이너는 라벨 단계가 H.264/AAC mp4로 만든다
    out = out_dir / PREPARED_VIDEO
    args = [
        ffmpeg_bin(), "-y", "-hide_banner", "-loglevel", "error", "-i", str(source),
        "-map", "0:v:0", "-map", "0:a:0?", "-vf", ",".join(plan.filters),
        "-c:v", "libx264", "-preset", "medium", "-crf", str(NORMALIZE_CRF),
        "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "128k", "-ar", "48000", str(out),
    ]  # fmt: skip
    _run(args, runner)
    return Converted(out, details)


# --- 라벨(재사용 포함) ----------------------------------------------------------


def _read_meta(path: Path) -> dict[str, Any]:
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _reusable_label(
    out_dir: Path, kind: str, origin_sha: str, config: sns_label.LabelConfig
) -> sns_label.LabelResult | None:
    """같은 원본·같은 라벨 설정의 결과가 있으면 다시 만들지 않는다(초안 id가 안정된다)."""
    meta = _read_meta(out_dir / "meta.json")
    mark, label = meta.get(MARK_KEY), meta.get("ai_label")
    final = out_dir / ("labeled.jpg" if kind == "image" else "labeled.mp4")
    if not (isinstance(mark, dict) and isinstance(label, dict) and final.is_file()):
        return None
    sha = sns_media.sha256_file(final)
    if (
        mark.get("origin_sha256") != origin_sha
        or label.get("labeled_sha256") != sha
        or label.get("config_sha256") != config.sha256
    ):
        return None
    machine = {
        "ai_generated": "true",
        "ai_generator_model": str(label.get("ai_generator_model", "")),
        "ai_generated_at": str(label.get("ai_generated_at", "")),
    }
    return sns_label.LabelResult(
        final,
        sha,
        out_dir / str(label.get("source_file", "")),
        str(label.get("source_sha256", "")),
        out_dir / "meta.json",
        kind,
        machine,
        str(label.get("text", "")),
    )


def label_media(
    prepared: Path, out_dir: Path, *, model: str, origin_sha: str, kind: str
) -> tuple[sns_label.LabelResult, bool]:
    """(결과, 재사용 여부). 실패는 `PrepareError("label", 코드)`."""
    try:
        config = sns_label.load_config()
        reused = _reusable_label(out_dir, kind, origin_sha, config)
        if reused is not None:
            return reused, True
        result = sns_label.apply_label(
            prepared, out_dir, generator_model=model, config=config, media_kind=kind
        )
    except sns_label.LabelError as error:
        raise PrepareError("label", error.code) from None  # detail에는 ffmpeg 원문이 섞인다
    meta_path = out_dir / "meta.json"
    meta = _read_meta(meta_path)
    meta[MARK_KEY] = {
        "origin_sha256": origin_sha,
        "prepared_file": prepared.name,
        "prepared_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }
    meta_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8")
    return result, False


# --- skill ---------------------------------------------------------------------


def _failed(request: AgentRequest, outcome: str, **output: Any) -> AgentResult:
    return AgentResult(
        meta=request.meta, status=TaskStatus.FAILED, outcome=outcome, output=output or None
    )


def _media_ref(data: PrepareSnsPostInput) -> str:
    if data.media_ref:
        return data.media_ref
    seed = json.dumps(
        [
            data.notice_id,
            data.coupon.campaign_id if data.coupon else None,
            data.media_kind,
            data.scene_template_id,
            data.fields,
            data.bgm_scene_template_id,
            data.bgm_fields,
        ],
        sort_keys=True,
        ensure_ascii=False,
    )
    return f"prep_{hashlib.sha256(seed.encode()).hexdigest()[:16]}"


def _generate_input(data: PrepareSnsPostInput, media_ref: str) -> dict[str, Any]:
    raw = data.model_dump(
        mode="json",
        include={
            "media_kind",
            "scene_template_id",
            "fields",
            "resolution",
            "quality",
            "duration_s",
            "claimed_cost_krw",
            "bgm_scene_template_id",
            "bgm_fields",
        },
    )
    out = {k: v for k, v in raw.items() if v is not None}
    out.setdefault("fields", {})
    out["post_draft_id"] = media_ref
    # 이미지는 bgm 키를 받지 않는다(받으면 입력 검증에서 막힌다)
    if data.media_kind != "video":
        out.pop("bgm_scene_template_id", None)
        out.pop("bgm_fields", None)
    return out


def _is_inside(path: Path, root: Path) -> bool:
    try:
        return path.resolve().is_relative_to(root)
    except OSError:
        return False


async def prepare_sns_post_from_media(
    request: AgentRequest,
    *,
    notices: NoticeStore,
    store: SnsStore,
    ledger: Any,
    from_decision: FromDecision,
    settings: MediaSettings | None = None,
    runner: Runner = subprocess_runner,
) -> AgentResult:
    try:
        data = PrepareSnsPostInput.model_validate(request.input)
    except ValidationError as error:
        return _failed(
            request,
            "invalid_input",
            errors=[f"{'.'.join(map(str, e['loc']))}: {e['type']}" for e in error.errors()],
        )
    settings = settings or MediaSettings.from_env()
    media_kind = "reel" if data.reel() else "image"

    # 0) 생성 전 확인. 여기서 막히면 비용이 나지 않는다
    draft_in = sns.DraftSnsPostInput(
        notice_id=data.notice_id,
        coupon=data.coupon,
        media_kind=media_kind,
        platform_options=data.platform_options,
    )
    blocked = sns.preflight_draft(request, draft_in, notices)
    if blocked is not None:
        return blocked
    root = sns_media.media_root()
    if root is None:
        return _failed(request, "media_dir_not_configured", stage="preflight")
    if not _is_inside(settings.media_dir, root):
        return _failed(request, "media_dir_mismatch", stage="preflight")
    missing_tool = preflight_tools()
    if missing_tool is not None:
        return _failed(request, "post_tools_unavailable", stage="preflight", reason=missing_tool)

    # 1) 생성. 안전 관문(B)·상한은 generate_media_flow가 그대로 적용한다
    media_ref = _media_ref(data)
    gen_request = request.model_copy(
        update={"skill": "generate_media", "input": _generate_input(data, media_ref)}
    )
    generated = await generate_media_flow(
        gen_request, ledger=ledger, from_decision=from_decision, settings=settings
    )
    if generated.outcome != "generated" or generated.output is None:
        # 보류·거절·상한·공급자 실패를 그대로 돌려준다. 같은 입력으로 다시 부르면 생성은 재생된다
        return generated.model_copy(
            update={"output": (generated.output or {}) | {"stage": "generate_media"}}
        )
    gen = generated.output

    # 2~3) 변환·라벨 (파일 작업이라 스레드에서)
    asset = Path(gen["asset_ref"])
    out_dir = asset.parent
    kind = "image" if data.media_kind == "image" else "video"
    if not _is_inside(asset, root):
        return _failed(request, "media_dir_mismatch", stage="generate_media")
    try:
        origin_sha = sns_media.sha256_file(asset)
        converted, label, reused = await asyncio.to_thread(
            _convert_and_label, asset, out_dir, kind, gen["model_id"], origin_sha, runner
        )
    except PrepareError as error:
        return _failed(
            request,
            f"{error.stage}_failed",
            stage=error.stage,
            reason=error.code,
            media_id=gen["media_id"],
            cost_krw=gen.get("total_cost_krw", gen["cost_krw"]),
            note="생성 비용은 이미 장부에 남았다. 같은 입력으로 다시 부르면 생성은 재생된다",
        )

    # 4) 초안 → 점검
    labeled_ref = sns_media.asset_ref_for(label.labeled_path)
    draft_request = request.model_copy(
        update={
            "skill": "draft_sns_post",
            "input": {
                **({"notice_id": data.notice_id} if data.notice_id else {}),
                **({"coupon": data.coupon.model_dump(mode="json")} if data.coupon else {}),
                "media_kind": media_kind,
                "asset_path": labeled_ref,
                "platform_options": data.platform_options.model_dump(mode="json"),
            },
        }
    )
    drafted = await sns.draft_sns_post(draft_request, notices, store)
    if (
        drafted.status != TaskStatus.COMPLETED
        or drafted.output is None
        or "caption" not in (drafted.output)
    ):
        return drafted.model_copy(
            update={
                "output": (drafted.output or {}) | {"stage": "draft", "media_id": gen["media_id"]}
            }
        )
    draft = drafted.output
    check_request = request.model_copy(
        update={
            "skill": "check_sns_post",
            "input": {
                "post_draft_id": draft["post_draft_id"],
                "caption": draft["caption"],
                "is_ai_generated": True,  # 게시 API로 보낼 값(`publish_sns`도 True 고정)
            },
        }
    )
    checked = await sns.check_sns_post(check_request, store)
    check = checked.output or {}
    ready = check.get("verdict") == "approved" and check.get("publishable") is True

    output = {
        "stage": "draft_ready" if ready else "check_rejected",
        "post_draft_id": draft["post_draft_id"],
        "media_kind": media_kind,
        "asset_ref": draft["asset_ref"],
        "asset_sha256": draft["asset_sha256"],
        "caption": draft["caption"],
        "platform_options": draft["platform_options"],
        "generation": {
            "media_id": gen["media_id"],
            "provider": gen["provider"],
            "model_id": gen["model_id"],
            "resolution": gen["resolution"],
            "cost_krw": gen["cost_krw"],
            "total_cost_krw": gen.get("total_cost_krw", gen["cost_krw"]),
            "execution_id": gen["execution_id"],
            "replayed": bool(gen.get("replayed")),
            "audio_mode": gen.get("audio_mode", "off"),
            "muted": gen.get("muted"),
        },
        "conversion": converted.details,
        "label": {
            "labeled_file": label.labeled_path.name,
            "labeled_sha256": label.labeled_sha256,
            "text": label.text,
            "reused": reused,
            "machine_mark": label.mark,
        },
        "check": check,
        # 사람이 승인해 `publish_sns`(C등급)를 부를 때 쓸 값. 이 skill은 부르지 않는다
        "publish_input": {
            "post_draft_id": draft["post_draft_id"],
            "asset_ref": draft["asset_ref"],
            "asset_sha256": draft["asset_sha256"],
            "caption": draft["caption"],
            "media_kind": media_kind,
            "share_to_feed": bool(draft["platform_options"].get("share_to_feed", True)),
        },
        "next_step": "publish_sns는 C등급이라 사람 승인이 필요하다. 이 skill은 게시하지 않는다.",
    }
    evidence = Evidence(
        queries=[ACTION],
        data_summary=f"{kind} 생성·변환·라벨·초안·점검 ({'통과' if ready else '점검 거절'})",
        collected_at=datetime.now(UTC),
        ref_ids=[draft["post_draft_id"], gen["media_id"], gen["execution_id"]],
    )
    return AgentResult(
        meta=request.meta,
        status=TaskStatus.COMPLETED,
        outcome=None if ready else "rejected_by_check",
        output=output,
        evidence=evidence,
        fallback=True,  # 캡션은 LLM이 아니라 고정 템플릿이다(`draft_sns_post`와 같다)
    )


def _convert_and_label(
    asset: Path, out_dir: Path, kind: str, model: str, origin_sha: str, runner: Runner
) -> tuple[Converted, sns_label.LabelResult, bool]:
    converter = convert_image if kind == "image" else convert_video
    converted = converter(asset, out_dir, runner)
    label, reused = label_media(
        converted.path, out_dir, model=model, origin_sha=origin_sha, kind=kind
    )
    return converted, label, reused
