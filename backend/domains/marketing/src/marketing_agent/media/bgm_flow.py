"""영상 + AI BGM 흐름(`MARKETING_AUDIO_MODE=bgm_overlay`일 때만 동작).

순서: 영상 생성(`generate_media`, 소스 오디오는 받되 쓰지 않는다) → BGM 생성(`generate_media`,
`media_kind="audio"`) → `overlay_bgm`(영상 오디오 제거 + BGM 입힘). 두 생성 모두 같은 안전 관문
`execute("generate_media")`를 거친다. 모드가 `off`(기본)이거나 영상 요청이 아니면 `generate_media`를
그대로 부른다(이전 동작과 같다).

- 입력은 영상 요청에 `bgm_scene_template_id`·`bgm_fields`를 더한 것이다. 두 값이 없으면 영상을
  만들기 전에 거절한다(영상 비용을 쓴 뒤에 알게 되지 않도록).
- fail-closed: 영상이 막히거나 실패하면 그 결과를 그대로 돌려준다. BGM 호출이 관문에서 보류·거절되면
  BGM 공급자를 부르지 않고 `outcome="bgm_<원래 결과>"`(예: `bgm_held`)와 영상 정보를 돌려준다.
  같은 입력으로 다시 부르면 이미 끝난 영상은 장부에서 재생(replay)되어 비용이 다시 나지 않는다.
- 후처리가 실패하면 `bgm_postprocess_failed`(이유 코드만)로 끝낸다.
  이미 난 생성 비용은 장부에 남아 있다.
- 결과에 증빙을 담는다: BGM 모델 ID·프롬프트 해시·공급자 요청 ID·관문 실행 ID, ffmpeg 명령 목록,
  최종 mp4 sha256. 최종 mp4가 `asset_ref`·`sha256`이고, 원본 영상은 `source_video_*`다.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any, Literal

from pydantic import ValidationError

from ax_contracts.schemas import AgentRequest, AgentResult, Evidence, TaskStatus
from ax_contracts.schemas.common import AxModel
from marketing_agent.media import audio_mix
from marketing_agent.media.audio_mix import AudioMixError, Runner, subprocess_runner
from marketing_agent.media.config import MediaSettings
from marketing_agent.media.ledger import MediaLedger
from marketing_agent.media.skill import (
    ACTION,
    FromDecision,
    GeneratedMedia,
    _fail,
    generate_media,
)

BGM_TEMPLATE_KEY = "bgm_scene_template_id"
BGM_FIELDS_KEY = "bgm_fields"
FINAL_NAME = "asset_bgm.mp4"


class BgmEvidence(AxModel):
    media_id: str
    provider: str
    model_id: str
    provider_job_id: str
    prompt_sha256: str
    request_sha256: str
    scene_template_id: str
    asset_ref: str
    sha256: str
    cost_krw: int
    execution_id: str
    ai_generated: Literal[True] = True
    replayed: bool = False


class PostprocessEvidence(AxModel):
    ffmpeg_bin: str
    commands: list[list[str]]
    audio_tracks: int
    duration_s: float
    final_sha256: str


class GeneratedVideoWithBgm(GeneratedMedia):
    """영상 결과 + BGM 증빙. `asset_ref`·`sha256`은 최종 mp4(BGM만 입힌 영상)를 가리킨다."""

    audio_mode: Literal["bgm_overlay"] = "bgm_overlay"
    source_video_asset_ref: str
    source_video_sha256: str
    bgm: BgmEvidence
    postprocess: PostprocessEvidence
    total_cost_krw: int  # 영상 + BGM 확정액


def _split_input(raw: dict[str, Any]) -> tuple[dict[str, Any], str | None, dict[str, Any] | None]:
    video_input = dict(raw)
    template = video_input.pop(BGM_TEMPLATE_KEY, None)
    fields = video_input.pop(BGM_FIELDS_KEY, None)
    return (
        video_input,
        template if isinstance(template, str) else None,
        (fields if isinstance(fields, dict) else None),
    )


async def generate_media_flow(
    request: AgentRequest,
    *,
    ledger: MediaLedger,
    from_decision: FromDecision,
    settings: MediaSettings | None = None,
    prices_path: Path | None = None,
    templates_path: Path | None = None,
    runner: Runner = subprocess_runner,
) -> AgentResult:
    settings = settings or MediaSettings.from_env()
    common: dict[str, Any] = {
        "ledger": ledger,
        "from_decision": from_decision,
        "settings": settings,
        "prices_path": prices_path,
        "templates_path": templates_path,
    }
    is_video = isinstance(request.input, dict) and request.input.get("media_kind") == "video"
    if settings.audio_mode != "bgm_overlay" or not is_video:
        return await generate_media(request, **common)  # off(기본)·이미지·직접 요청은 그대로

    video_input, bgm_template, bgm_fields = _split_input(dict(request.input))
    if not bgm_template or not bgm_fields:
        return _fail(
            request,
            "invalid_input",
            errors=[f"{BGM_TEMPLATE_KEY}/{BGM_FIELDS_KEY}: required_in_bgm_overlay"],
        )
    try:
        draft_id = str(video_input["post_draft_id"])
    except KeyError:
        return await generate_media(request.model_copy(update={"input": video_input}), **common)

    # 1) 영상 (소스 오디오가 있어도 받는다. 쓰지 않고 3)에서 버린다)
    video = await generate_media(
        request.model_copy(update={"input": video_input}), keep_source_audio=True, **common
    )
    if video.outcome != "generated" or video.output is None:
        return video

    # 2) BGM (같은 안전 관문)
    bgm_request = request.model_copy(
        update={
            "input": {
                "post_draft_id": draft_id,
                "media_kind": "audio",
                "scene_template_id": bgm_template,
                "fields": bgm_fields,
            }
        }
    )
    bgm = await generate_media(bgm_request, **common)
    if bgm.outcome != "generated" or bgm.output is None:
        detail = {
            "stage": "bgm",
            "video_media_id": video.output["media_id"],
            "video_asset_ref": video.output["asset_ref"],
            "video_execution_id": video.output["execution_id"],
            "retry_replays_video": True,
        }
        return bgm.model_copy(
            update={
                "outcome": f"bgm_{bgm.outcome}",
                "output": (bgm.output or {}) | detail,
            }
        )

    # 3) 후처리: 영상 오디오 제거 + BGM
    video_path, bgm_path = Path(video.output["asset_ref"]), Path(bgm.output["asset_ref"])
    final_path = video_path.with_name(FINAL_NAME)
    try:
        mixed = await asyncio.to_thread(
            audio_mix.overlay_bgm,
            video_path,
            bgm_path,
            final_path,
            duration_s=float(video.output["duration_s"]),
            runner=runner,
        )
    except AudioMixError as error:
        return _fail(
            request,
            "bgm_postprocess_failed",
            reason=error.code,
            video_media_id=video.output["media_id"],
            bgm_media_id=bgm.output["media_id"],
        )

    output = _final_output(video.output, bgm.output, mixed)
    try:
        output = GeneratedVideoWithBgm.model_validate(output).model_dump(
            mode="json", exclude={"replayed"}
        )
        (final_path.parent / "bgm_overlay.json").write_text(
            json.dumps(output, ensure_ascii=False, indent=2), encoding="utf-8"
        )
    except (ValidationError, OSError):
        return _fail(request, "storage_error", media_id=video.output["media_id"])
    evidence = (
        video.evidence or Evidence(queries=[ACTION], data_summary="", ref_ids=[])
    ).model_copy(
        update={
            "queries": [ACTION, f"{ACTION}:bgm"],
            "ref_ids": [
                draft_id,
                video.output["media_id"],
                video.output["execution_id"],
                bgm.output["media_id"],
                bgm.output["execution_id"],
            ],
        }
    )
    return AgentResult(
        meta=request.meta,
        status=TaskStatus.COMPLETED,
        outcome="generated",
        output=output
        | {"replayed": bool(video.output.get("replayed") and bgm.output.get("replayed"))},
        evidence=evidence,
    )


def _final_output(
    video: dict[str, Any], bgm: dict[str, Any], mixed: audio_mix.MixResult
) -> dict[str, Any]:
    return video | {
        "asset_ref": str(mixed.output_path),
        "sha256": mixed.sha256,
        "muted": False,  # 원본 소리는 버렸고 AI BGM이 들어 있다
        "audio_mode": "bgm_overlay",
        "source_video_asset_ref": video["asset_ref"],
        "source_video_sha256": video["sha256"],
        "bgm": {
            "media_id": bgm["media_id"],
            "provider": bgm["provider"],
            "model_id": bgm["model_id"],
            "provider_job_id": bgm["provider_job_id"],
            "prompt_sha256": bgm["prompt_sha256"],
            "request_sha256": bgm["request_sha256"],
            "scene_template_id": bgm["scene_template_id"],
            "asset_ref": bgm["asset_ref"],
            "sha256": bgm["sha256"],
            "cost_krw": bgm["cost_krw"],
            "execution_id": bgm["execution_id"],
            "replayed": bool(bgm.get("replayed")),
        },
        "postprocess": {
            "ffmpeg_bin": mixed.ffmpeg_bin,
            "commands": [list(c) for c in mixed.commands],
            "audio_tracks": mixed.audio_tracks,
            "duration_s": mixed.duration_s,
            "final_sha256": mixed.sha256,
        },
        "total_cost_krw": video["cost_krw"] + bgm["cost_krw"],
    }
