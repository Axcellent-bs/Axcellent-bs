"""skill `generate_media`: 이미지·영상 생성 (등급 B, 검증자 safety 코드 규칙. 등록은 safety 쪽).

순서: 입력 검증 → 프롬프트 만들기·검사 → 같은 키 재요청이면 이전 결과 → 공급자·환율·단가 확인 →
비용 계산 → 건당·월 상한 → 장부 예약 → `execute("generate_media")` → `executed`일 때만 공급자 호출 →
저장·사후 정산. 공급자 함수는 상한을 넘거나 관문이 막으면 호출하지 않는다.

- 상태를 바꾸는 일(돈이 나가는 외부 호출)은 관문이 `executed`를 돌려준 뒤에만 한다.
- 모든 결과에 `ai_generated: true`를 남긴다. 영상은 무음(오디오 제거)으로만 받는다.
- 글자는 모델이 그리지 않는다(프롬프트 규칙). 제목·가격·AI 라벨 오버레이는 다른 단계가 맡는다.
- 멱등 키: `<context_id>:generate_media:<post_draft_id>:<request_sha[:10]>`.
  `request_sha`는 프롬프트와 종류·해상도·품질·길이·무음 여부를 함께 해시한 값이라
  같은 프롬프트라도 설정이 바뀌면 다른 키다.
- 재시도(같은 키로 다시 호출)할 때마다 비용을 다시 계산하고 상한과 다시 비교한다.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import re
from collections.abc import Callable
from datetime import UTC, datetime
from decimal import Decimal
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
from marketing_agent.media.base import MediaKind, MediaRequest, MediaResult
from marketing_agent.media.config import MOCK, MediaSettings
from marketing_agent.media.ledger import LedgerError, MediaLedger
from marketing_agent.media.mock import MOCK_COST_KRW
from marketing_agent.media.pricing import PriceNotFound
from marketing_agent.media.prompts import PromptRejected, get_template, render_prompt
from marketing_agent.media.registry import get_adapter

ACTION = "generate_media"
_DRAFT_ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_-]{0,63}")
FromDecision = Callable[[AgentRequest, ExecuteDecision, Evidence], AgentResult | None]


class GenerateMediaInput(AxModel):
    """프롬프트 문장은 받지 않는다. 템플릿 id와 화이트리스트 필드 값만 받는다."""

    post_draft_id: str = Field(min_length=1, max_length=64)
    media_kind: Literal["image", "video"]
    scene_template_id: str = Field(min_length=1)
    fields: dict[str, Any] = Field(default_factory=dict)
    resolution: str | None = Field(default=None, pattern=r"^(\d{3,4}x\d{3,4}|\d{3,4}p)$")
    quality: Literal["low", "medium", "high"] | None = None  # 이미지만
    duration_s: StrictInt | None = Field(default=None, ge=1, le=60)  # 영상만
    claimed_cost_krw: StrictInt | None = Field(default=None, ge=0)  # 요청 쪽 추정값(다르면 C)


class GeneratedMedia(AxModel):
    """생성 결과. 비용·사용량 출처와 AI 생성 사실(`ai_generated`, 항상 true)을 함께 남긴다."""

    media_id: str
    media_kind: Literal["image", "video"]
    asset_ref: str
    sha256: str
    provider: str
    model_id: str
    resolution: str
    quality: str | None = None
    duration_s: int | None = None
    muted: bool | None = None  # 영상은 true(무음), 이미지는 None
    cost_krw: int  # 확정액(정수 원)
    cost_est_krw: int  # 호출 전 추정액
    usd_actual: str | None = None
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


def _now() -> datetime:
    return datetime.now(UTC)


def _fail(request: AgentRequest, outcome: str, **output: Any) -> AgentResult:
    return AgentResult(meta=request.meta, status=TaskStatus.FAILED, outcome=outcome, output=output)


def _sha(*parts: object) -> str:
    return hashlib.sha256(json.dumps(parts, ensure_ascii=False).encode()).hexdigest()


def _is_image_size(resolution: str) -> bool:
    return "x" in resolution


async def generate_media(
    request: AgentRequest,
    *,
    ledger: MediaLedger,
    from_decision: FromDecision,
    settings: MediaSettings | None = None,
    prices_path: Path | None = None,
    templates_path: Path | None = None,
) -> AgentResult:
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

    # --- 프롬프트 (자유 입력 금지) ---
    try:
        template = get_template(data.scene_template_id, templates_path)
        if template.kind != data.media_kind:
            return _fail(request, "invalid_input", errors=["scene_template_id: kind_mismatch"])
        prompt = render_prompt(data.scene_template_id, data.fields, templates_path)
    except PromptRejected as rejected:
        return _fail(request, "prompt_rejected", reason_code=rejected.code, field=rejected.field)

    kind: MediaKind = data.media_kind
    resolution = data.resolution or template.resolution
    quality = (data.quality or template.quality) if kind == "image" else None
    duration_s = (data.duration_s or template.duration_s) if kind == "video" else None
    if (kind == "image" and (data.duration_s is not None or not _is_image_size(resolution))) or (
        kind == "video" and (data.quality is not None or _is_image_size(resolution))
    ):
        return _fail(
            request, "invalid_input", errors=["resolution/quality/duration_s: kind_mismatch"]
        )

    prompt_sha = hashlib.sha256(prompt.encode()).hexdigest()
    request_sha = _sha(prompt_sha, kind, resolution, quality, duration_s, "muted")
    media_id = f"med_{request_sha[:12]}"
    key = f"{request.meta.context_id}:{ACTION}:{data.post_draft_id}:{request_sha[:10]}"

    # --- 같은 키로 이미 끝난 요청은 이전 결과 ---
    try:
        previous = ledger.settled_for(key)
    except LedgerError:
        return _fail(request, "storage_error")
    if previous is not None and previous.result_json:
        return _completed(request, json.loads(previous.result_json), replayed=True)

    # --- 공급자·환율·단가 확인 (호출 전, fail-closed) ---
    provider_name = settings.provider(kind)
    adapter = get_adapter(provider_name)
    if adapter is None:
        return _fail(request, "provider_adapter_missing", provider=provider_name)
    today = _now().date()
    usd_est: Decimal | None = None
    entry = None
    if provider_name == MOCK:
        model = "mock"
        krw_est = MOCK_COST_KRW[kind]
    else:
        model = settings.model(kind)
        if not model:
            return _fail(request, "model_not_configured", provider=provider_name)
        problem = settings.fx_problem(today)
        if problem is not None:
            return _fail(request, problem)
        if settings.max_cost_krw_per_item is None or settings.monthly_budget_krw is None:
            return _fail(request, "cost_limit_not_configured")
        try:
            entry = pricing.find_entry(
                provider=provider_name,
                model=model,
                kind=kind,
                resolution=resolution,
                quality=quality,
                path=prices_path,
            )
            usd_est = pricing.estimate_usd(entry, prompt=prompt, duration_s=duration_s)
        except PriceNotFound:
            return _fail(request, "price_not_found", provider=provider_name, resolution=resolution)
        assert settings.fx_usd_krw is not None and settings.margin_pct is not None  # noqa: S101
        krw_est = pricing.krw_estimate(usd_est, settings.fx_usd_krw, settings.margin_pct)

    # --- 상한 (공급자 호출 전) ---
    if settings.max_cost_krw_per_item is not None and krw_est > settings.max_cost_krw_per_item:
        return _fail(
            request,
            "cost_over_single",
            cost_krw=krw_est,
            limit_krw=settings.max_cost_krw_per_item,
        )
    now = _now()
    month = now.strftime("%Y-%m")
    try:
        reservation = ledger.reserve(
            key=key,
            month=month,
            media_kind=kind,
            provider=provider_name,
            model_id=model,
            krw_est=krw_est,
            usd_est=usd_est,
            monthly_cap=settings.monthly_budget_krw,
            now=now,
        )
    except LedgerError:
        return _fail(request, "storage_error")
    if reservation.outcome == "over_budget":
        return _fail(
            request,
            "budget_exceeded",
            cost_krw=krw_est,
            month_total_krw=reservation.month_total,
            limit_krw=settings.monthly_budget_krw,
        )
    if reservation.outcome == "settled" and reservation.row and reservation.row.result_json:
        return _completed(request, json.loads(reservation.row.result_json), replayed=True)
    if reservation.outcome == "in_progress" or reservation.row is None:
        return _fail(request, "in_progress")
    seq = reservation.row.seq

    # --- 안전 관문 ---
    params: dict[str, Any] = {
        "cost_krw": krw_est,
        "media_kind": kind,
        "resolution": resolution,
        "provider": provider_name,
        "scene_template_id": data.scene_template_id,
        "post_draft_id": data.post_draft_id,
        "request_sha256": request_sha,
        "ai_generated": True,
        "summary": f"{kind} 생성 {resolution} 예상 {krw_est}원 (AI 생성)",
    }
    if duration_s is not None:
        params["duration_s"] = duration_s
    if data.claimed_cost_krw is not None:
        params["claimed_cost_krw"] = data.claimed_cost_krw
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
        remove_audio=True,
        model="" if provider_name == MOCK else model,
        output_dir=out_dir,
    )
    try:
        out_dir.mkdir(parents=True, exist_ok=True)
        result = await asyncio.to_thread(adapter.generate, media_request)
        asset_sha = hashlib.sha256(result.asset_path.read_bytes()).hexdigest()
        if asset_sha != result.sha256:
            raise ValueError("asset_hash_mismatch")
    except Exception as error:  # 예외 내용에 비밀이 섞일 수 있어 종류만 남긴다
        _safe(lambda: ledger.mark_failed(seq, _now()))
        return _fail(request, "provider_failed", error_type=type(error).__name__)

    # --- 저장·사후 정산 ---
    usd_actual, krw_actual, source = _settle_amounts(
        result, entry, settings, kind, resolution, quality, prompt, usd_est, krw_est, prices_path
    )
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
        "muted": True if kind == "video" else None,
        "cost_krw": krw_actual,
        "cost_est_krw": krw_est,
        "usd_actual": str(usd_actual) if usd_actual is not None else None,
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
    output = GeneratedMedia.model_validate(output).model_dump(mode="json", exclude={"replayed"})
    meta = output | {"prompt": prompt, "fields": data.fields, "idempotency_key": key}
    try:
        (out_dir / "meta.json").write_text(
            json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
        )
        ledger.settle(
            seq,
            model_id=result.model_id,
            usd_actual=usd_actual,
            krw_actual=krw_actual,
            fx=settings.fx_usd_krw,
            fx_as_of=settings.fx_as_of.isoformat() if settings.fx_as_of else None,
            usage_source=source,
            result_json=json.dumps(output, ensure_ascii=False),
            now=_now(),
        )
    except (LedgerError, OSError):
        # 생성은 끝났고 비용이 났을 수 있다. 예약(추정액)은 그대로 남겨 월 누적에 센다
        return _fail(request, "storage_error", media_id=media_id)
    return _completed(request, output, replayed=False, evidence=evidence)


def _safe(action: Callable[[], None]) -> None:
    try:
        action()
    except LedgerError:  # 풀지 못해도 예약이 남아 월 누적이 더 보수적일 뿐이다
        pass


def _settle_amounts(
    result: MediaResult,
    entry: pricing.PriceEntry | None,
    settings: MediaSettings,
    kind: MediaKind,
    resolution: str,
    quality: str | None,
    prompt: str,
    usd_est: Decimal | None,
    krw_est: int,
    prices_path: Path | None,
) -> tuple[Decimal | None, int, Literal["actual", "estimate"]]:
    """응답 사용량이 있으면 실제 비용, 없으면 추정액을 확정액으로 남긴다(보수적)."""
    usage = result.usage
    if usage is None or usage.output_tokens is None or entry is None or settings.fx_usd_krw is None:
        return usd_est, krw_est, "estimate"
    try:  # 응답이 알려 준 모델 ID의 단가로 계산한다
        actual_entry = pricing.find_entry(
            provider=result.provider,
            model=result.model_id,
            kind=kind,
            resolution=resolution,
            quality=quality,
            path=prices_path,
        )
    except PriceNotFound:
        return usd_est, krw_est, "estimate"
    input_tokens = (
        usage.input_tokens
        if usage.input_tokens is not None
        else pricing.input_tokens_estimate(prompt)
    )
    usd = pricing.usd_for(
        actual_entry, input_tokens=input_tokens, output_tokens=usage.output_tokens
    )
    return usd, pricing.krw_actual(usd, settings.fx_usd_krw), "actual"


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
