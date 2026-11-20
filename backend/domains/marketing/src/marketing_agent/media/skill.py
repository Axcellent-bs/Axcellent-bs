"""skill `generate_media`: 이미지·영상 생성 (등급 B, 검증자 safety 코드 규칙. 등록은 safety 쪽).

순서: 입력 검증 → 프롬프트 만들기·검사 → 같은 키 재요청이면 이전 결과 → 공급자·모델 확인 →
예상 사용량 계산 → (우리 키만) 월 사용량 예산 + 장부 예약 → `execute("generate_media")` →
`executed`일 때만 공급자 호출 → 저장·사후 정산. 공급자 함수는 예산을 넘기거나 관문이 막으면
호출하지 않는다. 원화 단가표·환율·건당 금액 상한은 쓰지 않는다. 사용량은 공급자 단위 그대로
(영상 초·이미지 장·BGM 곡)이고 토큰이 응답에 있으면 같이 남긴다(`usage.py`).

- 상태를 바꾸는 일(돈이 나가는 외부 호출)은 관문이 `executed`를 돌려준 뒤에만 한다.
- 모든 결과에 `ai_generated: true`를 남긴다. 영상 소리는 `MARKETING_AUDIO_MODE`가 정한다.
  `source`(기본): 공급자가 만든 소리를 그대로 둔다(`keep_source_audio`). 소리가 없는 공급자·모델은
  무음 영상이고, 클립을 이어 붙인 영상(`clip_count>1`)은 이음새·내용이 어긋나 소리를 버린다.
  `off`: 예전처럼 항상 무음으로 받는다. `bgm_overlay`: `bgm_flow`가 `keep_source_audio=True`로
  불러 받은 영상의 오디오를 `audio_mix`가 버리고 BGM을 입힌다.
  결과의 `muted`는 최종 파일에 소리가 없는지, `has_audio`·`audio_mode`는 그 근거다.
- 오디오(BGM, `media_kind="audio"`)는 `bgm_overlay`일 때만 받는다. 이미지·영상과 같은 순서로
  예산·장부·`execute()` 관문을 거치며, 관문이 보류하면 공급자를 부르지 않는다(fail-closed).
- 글자는 모델이 그리지 않는다(프롬프트 규칙). 제목·가격·AI 라벨 오버레이는 다른 단계가 맡는다.
- 멱등 키: `<context_id>:generate_media:<post_draft_id>:<request_sha[:10]>`.
  `request_sha`는 프롬프트와 종류·해상도·품질·길이·무음 여부를 함께 해시한 값이라
  같은 프롬프트라도 설정이 바뀌면 다른 키다.
- 재시도(같은 키로 다시 호출)할 때마다 사용량을 다시 계산하고 예산과 다시 비교한다.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import Field, StrictInt, ValidationError

from ax_agent_template.execute import execute
from ax_contracts.schemas import (
    AgentRequest,
    AgentResult,
    Evidence,
    ExecuteDecision,
    TaskStatus,
)
from ax_contracts.schemas.common import AxModel
from marketing_agent.media import pricing
from marketing_agent.media import usage as usage_mod
from marketing_agent.media.base import MediaKind, MediaProviderError, MediaRequest
from marketing_agent.media.config import MOCK, MediaSettings, ffmpeg_bin
from marketing_agent.media.gemini_video import has_audio_track
from marketing_agent.media.ledger import LedgerError, MediaLedger
from marketing_agent.media.pricing import PriceNotFound, PriceTableError
from marketing_agent.media.prompts import (
    PromptRejected,
    get_template,
    plan_field_names,
    render_prompt,
    render_prompt_detailed,
)
from marketing_agent.media.registry import get_adapter
from marketing_agent.media.usage import UsageAmount
from marketing_agent.media.video_chain import generate_chain
from marketing_agent.media.video_limits import (
    ABSOLUTE_MAX_DURATION_S,
    Clip,
    VideoCaps,
    VideoLimitError,
    effective_caps,
    plan_clips,
)

ACTION = "generate_media"
_DRAFT_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}")
FromDecision = Callable[[AgentRequest, ExecuteDecision, Evidence], AgentResult | None]


class GenerateMediaInput(AxModel):
    """프롬프트 문장은 받지 않는다. 템플릿 id와 화이트리스트 필드 값만 받는다."""

    post_draft_id: str = Field(min_length=1, max_length=64)
    media_kind: Literal["image", "video", "audio"]
    scene_template_id: str = Field(min_length=1)
    fields: dict[str, Any] = Field(default_factory=dict)
    resolution: str | None = Field(default=None, pattern=r"^(\d{3,4}x\d{3,4}|\d{3,4}p)$")
    quality: Literal["low", "medium", "high"] | None = None  # 이미지만
    duration_s: StrictInt | None = Field(  # 영상만(오디오는 받지 않는다). 설정 상한은 따로 본다
        default=None, ge=1, le=ABSOLUTE_MAX_DURATION_S
    )
    claimed_cost_krw: StrictInt | None = Field(default=None, ge=0)  # 요청 쪽 추정값(다르면 C)


class GeneratedMedia(AxModel):
    """생성 결과. 사용량·출처와 AI 생성 사실(`ai_generated`, 항상 true)을 함께 남긴다."""

    media_id: str
    media_kind: Literal["image", "video", "audio"]
    asset_ref: str
    sha256: str
    provider: str
    model_id: str
    resolution: str
    quality: str | None = None
    duration_s: int | None = None
    clip_count: int = 1  # 영상: 이어 붙인 클립 수(1이면 단일 클립)
    muted: bool | None = None  # 영상: 받은 파일에 소리가 없으면 true, 이미지·오디오는 None
    # 영상만. 소리 처리 기록: 모드(source·off·bgm_overlay), 받은 파일에 소리 트랙이 있는지, 비고
    audio_mode: str | None = None
    has_audio: bool | None = None
    audio_note: str | None = None  # 예: "clip_concat_muted"(이어 붙인 영상은 소리를 버림)
    # 원화 비용은 더 계산하지 않는다(늘 0). 예전 필드라 지우지 않고 남긴다(계약은 추가만)
    cost_krw: int = 0
    cost_est_krw: int = 0
    usd_actual: str | None = None
    # 사용량: {unit(seconds·images·tracks), amount, input_tokens, output_tokens}
    # 토큰은 응답에 있을 때만 들어간다
    usage: dict[str, Any] | None = None
    usage_source: Literal["actual", "estimate"]
    ai_generated: Literal[True] = True
    generated_at: datetime
    provider_job_id: str
    prompt_sha256: str
    request_sha256: str
    scene_template_id: str
    post_draft_id: str
    execution_id: str
    replayed: bool = False
    # 기획 데이터를 반영했으면 필드 이름·길이·해시 기록(값 전체는 없다). 안 썼으면 키가 없다
    plan_context: dict | None = None


def _now() -> datetime:
    return datetime.now(UTC)


def _fail(request: AgentRequest, outcome: str, **output: Any) -> AgentResult:
    return AgentResult(meta=request.meta, status=TaskStatus.FAILED, outcome=outcome, output=output)


def _sha(*parts: object) -> str:
    return hashlib.sha256(json.dumps(parts, ensure_ascii=False).encode()).hexdigest()


def _audio_marker(keep_source_audio: bool) -> str:
    """요청 해시에 넣는 소리 표식. 소리를 둔 영상과 무음 영상은 다른 장부 키다."""
    return "src_audio_overlay" if keep_source_audio else "muted"


def _is_image_size(resolution: str) -> bool:
    return "x" in resolution


def _video_duration(
    data: GenerateMediaInput, template_s: int | None, settings: MediaSettings
) -> tuple[int | None, str | None, dict[str, Any]]:
    """영상 길이와 설정 상한 검사. (길이, 거절 코드, 거절 상세)."""
    if settings.video_max_duration_problem is not None:
        return None, settings.video_max_duration_problem, {}
    limit = settings.video_max_duration_s
    # 길이를 안 정한 요청의 기본값(템플릿)은 설정 상한을 넘지 않게 맞춘다
    duration = data.duration_s or (min(template_s, limit) if template_s else None)
    if duration is not None and duration > limit:
        return duration, "duration_over_limit", {"duration_s": duration, "limit_s": limit}
    return duration, None, {}


def _clip_plan(
    adapter: object, entry: pricing.PriceEntry | None, duration_s: int | None
) -> list[Clip]:
    """공급자 클립 한도·단가표 한도로 클립을 나눈다. 못 나누면 VideoLimitError(결과 코드)."""
    caps = getattr(adapter, "video_caps", None) or VideoCaps()
    caps = effective_caps(caps, entry.max_duration_s if entry else None)
    return plan_clips(duration_s or 1, caps)


def _model_entry(
    provider: str,
    model: str,
    kind: MediaKind,
    resolution: str,
    quality: str | None,
    prices_path: Path | None,
) -> pricing.PriceEntry | None:
    """모델 능력표(클립 최대 길이)용 조회. 가격은 안 쓴다. 없거나 못 읽으면 None(막지 않는다)."""
    try:
        return pricing.find_entry(
            provider=provider,
            model=model,
            kind=kind,
            resolution=resolution,
            quality=quality,
            path=prices_path,
        )
    except (PriceNotFound, PriceTableError):
        return None


def _over_budget(
    request: AgentRequest, kind: MediaKind, expected: UsageAmount, used: int, cap: int
) -> AgentResult:
    return _fail(
        request,
        "quota_exceeded",
        media_kind=kind,
        unit=expected.unit,
        requested=expected.amount,
        month_used=used,
        limit=cap,
        remaining_pct=usage_mod.remaining_pct(used, cap),
    )


async def generate_media(
    request: AgentRequest,
    *,
    ledger: MediaLedger,
    from_decision: FromDecision,
    settings: MediaSettings | None = None,
    prices_path: Path | None = None,
    templates_path: Path | None = None,
    keep_source_audio: bool = False,
) -> AgentResult:
    """`keep_source_audio`는 `bgm_flow` 전용이다. 영상에 오디오가 있어도 실패시키지 않고 받는다."""
    settings = settings or MediaSettings.from_env()
    try:
        data = GenerateMediaInput.model_validate(request.input)
    except ValidationError as error:
        return _fail(
            request,
            "invalid_input",
            errors=[f"{'.'.join(map(str, e['loc']))}: {e['type']}" for e in error.errors()],
        )
    if not _DRAFT_ID.fullmatch(data.post_draft_id):
        return _fail(request, "invalid_input", errors=["post_draft_id: invalid_format"])
    if data.media_kind == "audio" and settings.audio_mode != "bgm_overlay":
        # 기본(off)에서는 audio가 입력 검증에서 막히던 때와 같은 결과다
        return _fail(request, "invalid_input", errors=["media_kind: audio_mode_off"])
    if keep_source_audio and data.media_kind != "video":
        return _fail(request, "invalid_input", errors=["keep_source_audio: video_only"])
    # source 모드(기본)의 영상은 공급자가 만든 소리를 그대로 둔다. bgm_flow는 직접 True로 부른다
    keep_source_audio = keep_source_audio or (
        data.media_kind == "video" and settings.audio_mode == "source"
    )

    # --- 프롬프트 (자유 입력 금지) ---
    try:
        template = get_template(data.scene_template_id, templates_path)
        if template.kind != data.media_kind:
            return _fail(request, "invalid_input", errors=["scene_template_id: kind_mismatch"])
        rendered = render_prompt_detailed(data.scene_template_id, data.fields, templates_path)
        prompt = rendered.prompt
    except PromptRejected as rejected:
        return _fail(request, "prompt_rejected", reason_code=rejected.code, field=rejected.field)
    plan_context = rendered.plan_record()

    kind: MediaKind = data.media_kind
    resolution = data.resolution or template.resolution
    quality = (data.quality or template.quality) if kind == "image" else None
    duration_s: int | None = None
    if kind == "video":
        duration_s, limit_code, limit_detail = _video_duration(data, template.duration_s, settings)
        if limit_code is not None:
            return _fail(request, limit_code, **limit_detail)
    if (
        kind == "audio"
    ):  # 클립 길이는 모델이 정하고(템플릿 resolution), 영상 길이에 맞추는 건 ffmpeg가 한다
        resolution = template.resolution
    if (
        (kind == "image" and (data.duration_s is not None or not _is_image_size(resolution)))
        or (kind == "video" and (data.quality is not None or _is_image_size(resolution)))
        or (kind == "audio" and (data.resolution or data.quality or data.duration_s is not None))
    ):
        return _fail(
            request, "invalid_input", errors=["resolution/quality/duration_s: kind_mismatch"]
        )

    prompt_sha = hashlib.sha256(prompt.encode()).hexdigest()
    audio_marker = _audio_marker(keep_source_audio)
    request_sha = _sha(prompt_sha, kind, resolution, quality, duration_s, audio_marker)
    media_id = f"med_{request_sha[:12]}"
    key = f"{request.meta.context_id}:{ACTION}:{data.post_draft_id}:{request_sha[:10]}"

    # --- 같은 키로 이미 끝난 요청은 이전 결과 ---
    try:
        previous = ledger.settled_for(key)
    except LedgerError:
        return _fail(request, "storage_error")
    if previous is not None and previous.result_json:
        return _completed(request, json.loads(previous.result_json), replayed=True)

    # --- 공급자·모델 확인 (호출 전, fail-closed) ---
    provider_name = settings.provider(kind)
    adapter = get_adapter(provider_name)
    if adapter is None:
        return _fail(request, "provider_adapter_missing", provider=provider_name)
    if (
        kind == "video"
        and not keep_source_audio
        and provider_name != MOCK
        and shutil.which(ffmpeg_bin()) is None
    ):  # 무음으로 받은 영상에 오디오가 섞여 오면 ffmpeg로 제거한다. 비용이 나기 전에 막는다
        return _fail(request, "ffmpeg_not_found")
    entry = None
    clips = [Clip("first", duration_s or 1)]
    if provider_name == MOCK:
        model = "mock"
    else:
        model = settings.model(kind)
        if not model:
            return _fail(request, "model_not_configured", provider=provider_name)
        if settings.quota_applies and settings.quota(kind) is None:
            return _fail(request, "quota_not_configured", media_kind=kind)
        entry = _model_entry(provider_name, model, kind, resolution, quality, prices_path)
    if kind == "video":
        try:
            clips = _clip_plan(adapter, entry, duration_s)
        except VideoLimitError as error:
            return _fail(request, str(error), provider=provider_name, duration_s=duration_s)
    expected = usage_mod.planned(kind, clips if kind == "video" else None)

    if (
        kind == "video"
        and keep_source_audio
        and len(clips) > 1
        and provider_name != MOCK
        and shutil.which(ffmpeg_bin()) is None
    ):  # 이어 붙이려면 ffmpeg가 필요하다. 비용이 나기 전에 막는다
        return _fail(request, "ffmpeg_not_found")

    # --- 사용량 예산(우리 키만, 공급자 호출 전). 이어 붙이면 사용량은 클립 전체의 합이다 ---
    cap = settings.quota(kind) if settings.quota_applies else None
    now = _now()
    month = now.strftime("%Y-%m")
    try:
        reservation = ledger.reserve(
            key=key,
            month=month,
            media_kind=kind,
            provider=provider_name,
            model_id=model,
            usage=expected,
            monthly_cap=cap,
            now=now,
        )
    except LedgerError:
        return _fail(request, "storage_error")
    if reservation.outcome == "over_budget" and cap is not None:
        return _over_budget(request, kind, expected, reservation.month_used, cap)
    if reservation.outcome == "settled" and reservation.row and reservation.row.result_json:
        return _completed(request, json.loads(reservation.row.result_json), replayed=True)
    if reservation.outcome == "in_progress" or reservation.row is None:
        # 같은 열쇠가 이미 도는 중이다. 다시 불러도 같은 답이니 재시도하지 말라고 알린다
        return _fail(request, "in_progress", retryable=False)
    seq = reservation.row.seq

    # --- 안전 관문 ---
    params: dict[str, Any] = {
        "media_kind": kind,
        "resolution": resolution,
        "provider": provider_name,
        "scene_template_id": data.scene_template_id,
        "post_draft_id": data.post_draft_id,
        "request_sha256": request_sha,
        "ai_generated": True,
        "usage_unit": expected.unit,
        "usage_amount": expected.amount,
        "summary": (
            f"{kind} 생성 {resolution} 예상 {expected.amount}"
            f"{usage_mod.UNIT_LABELS_KO[expected.unit]} (AI 생성)"
        ),
    }
    if duration_s is not None:
        params["duration_s"] = duration_s
        params["clip_count"] = len(clips)
    evidence = Evidence(
        queries=[ACTION],
        data_summary=f"{kind} 생성 요청 {data.post_draft_id}",
        collected_at=now,
        ref_ids=[data.post_draft_id, media_id],
    )
    decision = await execute(
        ACTION, params, request.meta.model_copy(update={"idempotency_key": key}), evidence
    )
    blocked = from_decision(request, decision, evidence)
    if blocked is not None:  # 보류·거절·무응답: 공급자를 부르지 않고 예약을 푼다
        _safe(lambda: ledger.release(seq))
        return blocked

    # --- 공급자 호출 (executed 뒤에만) ---
    out_dir = settings.media_dir / request.meta.tenant_id / request.meta.context_id / media_id
    media_request = MediaRequest(
        media_kind=kind,
        prompt=prompt,
        prompt_sha256=prompt_sha,
        resolution=resolution,
        quality=quality,
        duration_s=duration_s,
        # 이어 붙인 영상은 클립마다 소리가 따로 만들어져 이음새·내용이 맞지 않아 소리를 버린다
        remove_audio=(not keep_source_audio) or len(clips) > 1,
        model="" if provider_name == MOCK else model,
        output_dir=out_dir,
    )
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
        if len(clips) > 1:
            result = await asyncio.to_thread(generate_chain, adapter, media_request, clips)
        else:
            result = await asyncio.to_thread(adapter.generate, media_request)
        asset_sha = hashlib.sha256(result.asset_path.read_bytes()).hexdigest()
        if asset_sha != result.sha256:
            raise ValueError("asset_hash_mismatch")
    except Exception as error:  # 예외 내용에 비밀이 섞일 수 있어 종류만 남긴다
        _safe(lambda: ledger.mark_failed(seq, _now()))
        detail = {"detail": str(error)[:120]} if isinstance(error, MediaProviderError) else {}
        return _fail(request, "provider_failed", error_type=type(error).__name__, **detail)

    # --- 저장·사후 정산 ---
    used = usage_mod.measured(kind, expected, result)
    source: Literal["actual", "estimate"] = (
        "actual" if used.output_tokens is not None else "estimate"
    )
    muted: bool | None = None
    has_audio: bool | None = None
    audio_note: str | None = None
    if kind == "video":
        has_audio = has_audio_track(result.asset_path.read_bytes())
        muted = not has_audio
        if keep_source_audio and len(clips) > 1:
            audio_note = "clip_concat_muted"
    output = {
        "media_id": media_id,
        "media_kind": kind,
        "asset_ref": str(result.asset_path),
        "sha256": result.sha256,
        "provider": result.provider,
        "model_id": result.model_id,
        "resolution": resolution,
        "quality": quality,
        "duration_s": duration_s,
        "clip_count": result.clip_count,
        "muted": muted,
        "audio_mode": settings.audio_mode if kind == "video" else None,
        "has_audio": has_audio,
        "audio_note": audio_note,
        "cost_krw": 0,
        "cost_est_krw": 0,
        "usd_actual": None,
        "usage": used.as_dict(),
        "usage_source": source,
        "ai_generated": True,
        "generated_at": result.generated_at.isoformat(),
        "provider_job_id": result.provider_job_id,
        "prompt_sha256": prompt_sha,
        "request_sha256": request_sha,
        "scene_template_id": data.scene_template_id,
        "post_draft_id": data.post_draft_id,
        "execution_id": decision.execution_id,
    }
    if plan_context is not None:
        output["plan_context"] = plan_context
    output = GeneratedMedia.model_validate(output).model_dump(
        mode="json",
        exclude={"replayed"}
        | (set() if plan_context else {"plan_context"})
        | (set() if kind == "video" else {"audio_mode", "has_audio", "audio_note"})
        | (set() if audio_note else {"audio_note"}),
    )
    plan_names = set(plan_field_names(templates_path))  # 기획 원문은 meta에 다시 쓰지 않는다
    meta_fields = {k: v for k, v in data.fields.items() if k not in plan_names}
    meta = output | {"prompt": prompt, "fields": meta_fields, "idempotency_key": key}
    if kind == "video":
        meta["audio_stripped"] = result.audio_stripped
    try:
        (out_dir / "meta.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        ledger.settle(
            seq,
            model_id=result.model_id,
            usage=used,
            usage_source=source,
            result_json=json.dumps(output, ensure_ascii=False),
            now=_now(),
        )
    except (LedgerError, OSError):
        # 생성은 끝났고 비용이 났을 수 있다. 예약(추정 사용량)은 그대로 남겨 월 사용량에 센다
        return _fail(request, "storage_error", media_id=media_id)
    return _completed(request, output, replayed=False, evidence=evidence)


def _safe(action: Callable[[], None]) -> None:
    try:
        action()
    except LedgerError:  # 풀지 못해도 예약이 남아 월 사용량이 더 보수적일 뿐이다
        pass


def _completed(
    request: AgentRequest,
    output: dict[str, Any],
    *,
    replayed: bool,
    evidence: Evidence | None = None,
) -> AgentResult:
    evidence = evidence or Evidence(
        queries=[ACTION],
        data_summary=f"{output['media_kind']} 생성 결과 {output['media_id']}",
        collected_at=_now(),
        ref_ids=[output["post_draft_id"], output["media_id"]],
    )
    ref_ids = [output["post_draft_id"], output["media_id"], output["execution_id"]]
    return AgentResult(
        meta=request.meta,
        status=TaskStatus.COMPLETED,
        outcome="generated",
        output=output | {"replayed": replayed},
        evidence=evidence.model_copy(update={"ref_ids": ref_ids}),
    )


# --- 여러 건을 한 번에 만들기 전의 사용량 미리 계산 (캐러셀) ---


@dataclass(frozen=True)
class CostPlan:
    """생성 한 건의 호출 전 예상 사용량. `settled`면 장부에 이미 끝난 키라 다시 쓰지 않는다.

    이름은 예전 그대로(비용 미리 계산)지만 원화는 없다. 사용량은 영상 초·이미지 장·BGM 곡이다.
    """

    key: str
    usage: UsageAmount
    settled: bool
    # 영상: 길이(초) → 이어 붙일 클립 수(1초~설정 상한). 길이 선택·경고창 안내용.
    # 못 만드는 길이는 뺀다
    clips_by_duration: dict[int, int] | None = None


def _clip_count_table(
    adapter: object, entry: pricing.PriceEntry | None, settings: MediaSettings
) -> dict[int, int]:
    """길이별 클립 수. `_clip_plan`이 만드는 계획의 길이만 센다(외부 호출 없음)."""
    table: dict[int, int] = {}
    for seconds in range(1, settings.video_max_duration_s + 1):
        try:
            table[seconds] = len(_clip_plan(adapter, entry, seconds))
        except VideoLimitError:
            continue
    return table


def plan_cost(
    request: AgentRequest,
    *,
    ledger: MediaLedger,
    settings: MediaSettings,
    prices_path: Path | None = None,
    templates_path: Path | None = None,
    allow_video: bool = False,
    with_table: bool = False,
    allow_audio: bool = False,
) -> CostPlan | AgentResult:
    """`generate_media`(이미지)와 같은 규칙으로 호출 없이 예상 사용량만 계산한다. 막히면 결과 Task.

    `allow_video`면 영상(현재 소리 모드의 `generate_media`와 같은 키)도 계산한다.
    기본은 이미지 전용이다(캐러셀).

    입력 검증·프롬프트 검사·공급자 어댑터·모델 설정은 `generate_media`와 같은 규칙·결과 코드다.
    월 예산 검사와 장부 예약은 하지 않는다(부르는 쪽이 합계로 본다).
    같은 키가 이미 확정이면 `settled=True`(재생이라 사용량이 다시 늘지 않는다).
    """
    try:
        data = GenerateMediaInput.model_validate(request.input)
    except ValidationError as error:
        return _fail(
            request,
            "invalid_input",
            errors=[f"{'.'.join(map(str, e['loc']))}: {e['type']}" for e in error.errors()],
        )
    kind: MediaKind = data.media_kind
    allowed_kinds = (
        "image",
        *(("video",) if allow_video else ()),
        *(("audio",) if allow_audio else ()),
    )
    if not _DRAFT_ID.fullmatch(data.post_draft_id) or kind not in allowed_kinds:
        return _fail(request, "invalid_input", errors=["media_kind/post_draft_id: invalid"])
    try:
        template = get_template(data.scene_template_id, templates_path)
        if template.kind != kind:
            return _fail(request, "invalid_input", errors=["scene_template_id: kind_mismatch"])
        prompt = render_prompt(data.scene_template_id, data.fields, templates_path)
    except PromptRejected as rejected:
        return _fail(request, "prompt_rejected", reason_code=rejected.code, field=rejected.field)
    resolution = data.resolution or template.resolution
    quality = (data.quality or template.quality) if kind == "image" else None
    duration_s = None
    if kind == "video":
        duration_s, limit_code, limit_detail = _video_duration(data, template.duration_s, settings)
        if limit_code is not None:
            return _fail(request, limit_code, **limit_detail)
    if (kind == "image" and (data.duration_s is not None or not _is_image_size(resolution))) or (
        kind == "video" and (data.quality is not None or _is_image_size(resolution))
    ):
        return _fail(
            request, "invalid_input", errors=["resolution/quality/duration_s: kind_mismatch"]
        )
    prompt_sha = hashlib.sha256(prompt.encode()).hexdigest()
    keep_audio = kind == "video" and settings.audio_mode == "source"
    request_sha = _sha(prompt_sha, kind, resolution, quality, duration_s, _audio_marker(keep_audio))
    key = f"{request.meta.context_id}:{ACTION}:{data.post_draft_id}:{request_sha[:10]}"
    try:
        previous = ledger.settled_for(key)
    except LedgerError:
        return _fail(request, "storage_error")
    if previous is not None and previous.result_json:
        amount = previous.usage_actual or previous.usage_est or 0
        return CostPlan(key, UsageAmount(usage_mod.unit_for(kind), amount), True)

    provider_name = settings.provider(kind)
    adapter = get_adapter(provider_name)
    if adapter is None:
        return _fail(request, "provider_adapter_missing", provider=provider_name)
    entry = None
    if provider_name != MOCK:
        model = settings.model(kind)
        if not model:
            return _fail(request, "model_not_configured", provider=provider_name)
        if settings.quota_applies and settings.quota(kind) is None:
            return _fail(request, "quota_not_configured", media_kind=kind)
        entry = _model_entry(provider_name, model, kind, resolution, quality, prices_path)
    clips: list[Clip] | None = None
    if kind == "video":
        try:
            clips = _clip_plan(adapter, entry, duration_s)
        except VideoLimitError as error:
            return _fail(request, str(error), provider=provider_name, duration_s=duration_s)
    clip_table = None
    if with_table and kind == "video":
        clip_table = _clip_count_table(adapter, entry, settings)
    return CostPlan(key, usage_mod.planned(kind, clips), False, clip_table)
