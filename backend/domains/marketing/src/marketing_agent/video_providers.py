"""영상 공급자 선택·커스텀 공급자 스킬.

- `list_video_providers`(읽기): 선택 가능한 공급자 목록·현재 선택·커스텀 스펙(키 값 없음).
- `select_video_provider`(A): 공급자·모델을 고른다. 환경변수보다 우선한다(`video_store`).
- `save_custom_video_provider`(C): 커스텀 공급자 스펙을 저장한다. 외부 주소로 키가 나가는 설정이라
  늘 사람 승인을 거친다. 새 공급자는 `enabled=false`로만 저장되고, 켜는 것도 같은 C 승인이다.
- `delete_custom_video_provider`(A): 지운다. 선택돼 있었으면 환경변수 설정으로 돌아간다.
쓰기는 요청자가 dashboard-api일 때만(Agent Card), 사람 이름(`updated_by`)은 대시보드 API가
토큰에서 채운다.
스펙 검사는 `media/custom_spec.py`가 저장 전에 한다(실패하면 승인 요청도 만들지 않는다).
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any

from pydantic import Field, ValidationError

from ax_agent_template.execute import execute
from ax_contracts.schemas import AgentRequest, AgentResult, Evidence, TaskStatus
from ax_contracts.schemas.common import AxModel
from marketing_agent.media import video_store
from marketing_agent.media.custom_spec import PROVIDER_PREFIX, SpecError, validate_spec
from marketing_agent.media.video_catalog import list_video_providers as catalog

SELECT = "select_video_provider"
SAVE = "save_custom_video_provider"
DELETE = "delete_custom_video_provider"


class ListInput(AxModel):
    pass


class ProvidersOutput(AxModel):
    selected: dict[str, Any]
    providers: list[dict[str, Any]]
    custom_host_allowlist_set: bool
    history: list[dict[str, Any]] = Field(default_factory=list)


class SelectInput(AxModel):
    provider: str = Field(min_length=1, max_length=64)
    model: str = Field(default="", max_length=64)
    updated_by: str = Field(min_length=1, max_length=64)


class SaveInput(AxModel):
    spec: dict[str, Any]
    updated_by: str = Field(min_length=1, max_length=64)


class DeleteInput(AxModel):
    id: str = Field(min_length=1, max_length=32)
    updated_by: str = Field(min_length=1, max_length=64)


def _fail(request: AgentRequest, outcome: str, **output: Any) -> AgentResult:
    return AgentResult(
        meta=request.meta, status=TaskStatus.FAILED, outcome=outcome, output=dict(output)
    )


def _ok(request: AgentRequest, outcome: str, evidence: Evidence | None = None) -> AgentResult:
    return AgentResult(
        meta=request.meta,
        status=TaskStatus.COMPLETED,
        outcome=outcome,
        output=catalog(),
        evidence=evidence or _evidence("list_video_providers", "영상 공급자 목록", "video"),
    )


def _invalid(request: AgentRequest, error: ValidationError) -> AgentResult:
    errors = [f"{'.'.join(map(str, e['loc']))}: {e['type']}" for e in error.errors()]
    return _fail(request, "invalid_input", errors=errors)


def _evidence(action: str, summary: str, ref: str) -> Evidence:
    return Evidence(
        queries=[action], data_summary=summary, collected_at=datetime.now(UTC), ref_ids=[ref]
    )


async def _gate(
    request: AgentRequest,
    action: str,
    params: dict[str, Any],
    evidence: Evidence,
    from_decision: Any,
) -> AgentResult | None:
    digest = hashlib.sha256(json.dumps(params, sort_keys=True, default=str).encode()).hexdigest()
    key = f"{request.meta.context_id}:{action}:{digest[:24]}"
    decision = await execute(
        action, params, request.meta.model_copy(update={"idempotency_key": key}), evidence
    )
    return from_decision(request, decision, evidence)


async def list_providers(request: AgentRequest) -> AgentResult:
    return _ok(request, "state")


async def select_provider(request: AgentRequest, *, from_decision: Any) -> AgentResult:
    try:
        data = SelectInput.model_validate(request.input)
    except ValidationError as error:
        return _invalid(request, error)
    provider = data.provider.strip().lower()
    row = next((p for p in catalog()["providers"] if p["id"] == provider), None)
    if row is None:
        return _fail(request, "unknown_provider")
    if not row["selectable"]:
        return _fail(request, "provider_disabled")
    model = data.model.strip()
    if row["kind"] == "builtin" and provider != "mock" and model not in row["models"]:
        return _fail(request, "model_not_priced", models=row["models"])  # 단가 없는 모델은 막힌다
    ev = _evidence(SELECT, f"영상 공급자 {provider} 선택 ({data.updated_by})", provider)
    params = {"provider": provider, "model": model, "updated_by": data.updated_by}
    blocked = await _gate(request, SELECT, params, ev, from_decision)
    if blocked:
        return blocked
    try:
        video_store.set_selection(provider, model, data.updated_by)
    except video_store.VideoStoreError:
        return _fail(request, "video_store_error")
    return _ok(request, "selected", ev)


async def save_custom(request: AgentRequest, *, from_decision: Any) -> AgentResult:
    try:
        data = SaveInput.model_validate(request.input)
        spec = validate_spec(data.spec)
    except ValidationError as error:
        return _invalid(request, error)
    except SpecError as error:
        return _fail(request, "invalid_spec", reason=str(error))
    existing = video_store.custom_specs()
    if spec.id not in existing and len(existing) >= video_store.MAX_PROVIDERS:
        return _fail(request, "too_many_custom_providers")
    ev = _evidence(
        SAVE, f"커스텀 영상 공급자 {spec.id} 저장 요청 ({data.updated_by})", spec.provider_name
    )
    params = {
        "id": spec.id,
        "enabled": spec.enabled,
        "base_host": spec.baseUrl,
        "spec_sha256": hashlib.sha256(
            json.dumps(spec.model_dump(mode="json"), sort_keys=True).encode()
        ).hexdigest(),
        "updated_by": data.updated_by,
    }
    blocked = await _gate(request, SAVE, params, ev, from_decision)
    if blocked:
        return blocked
    try:
        video_store.save_custom(spec, data.updated_by)
    except video_store.VideoStoreError as error:
        return _fail(request, "video_store_error", reason=str(error))
    return _ok(request, "saved", ev)


async def delete_custom(request: AgentRequest, *, from_decision: Any) -> AgentResult:
    try:
        data = DeleteInput.model_validate(request.input)
    except ValidationError as error:
        return _invalid(request, error)
    if data.id not in video_store.custom_specs():
        return _fail(request, "not_found")
    ev = _evidence(DELETE, f"커스텀 영상 공급자 {data.id} 삭제 ({data.updated_by})", data.id)
    params = {"id": data.id, "updated_by": data.updated_by}
    blocked = await _gate(request, DELETE, params, ev, from_decision)
    if blocked:
        return blocked
    try:
        video_store.delete_custom(data.id, data.updated_by)
    except video_store.VideoStoreError:
        return _fail(request, "video_store_error")
    return _ok(request, "deleted", ev)


__all__ = ["PROVIDER_PREFIX", "delete_custom", "list_providers", "save_custom", "select_provider"]
