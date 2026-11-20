"""우리 키 사용량 예산 설정 스킬(화면에서 종류별 월 예산을 정한다).

원화 단가표·건당 금액 상한은 없다. 예산은 공급자 단위 그대로의 월 사용량이다:
영상 초(`video_seconds_monthly`)·이미지 장(`image_count_monthly`)·BGM 곡(`audio_count_monthly`).
우리 키(`MARKETING_MEDIA_KEY_OWNER=server`, 기본)에만 걸고 고객사 키(`customer`)는 안 건다.
스킬 이름(`*_media_cost_limits`)과 안전 액션 이름은 예전 그대로다(계약은 추가만).

- `get_media_cost_limits`(읽기): 현재 값·출처·이번 달 종류별 사용량과 남은 %·코드 상수 범위·
  안전 정책 현황·대기 요청·이력.
- `lower_media_cost_limits`: 예산을 낮추는 변경. 재무·총괄이 셀프 승인을 허용해 둔
  경우(기본 꺼짐)에만 주의사항 확인(`confirmed`) 뒤 바로 적용하는 A. 꺼져 있으면
  상향과 같은 사람 승인 C(`change_media_cost_limits`)로 간다.
- `raise_media_cost_limits`: 예산을 올리는 변경. 늘 사람 승인 C. 요청자(`requested_by`)와 승인자는
  달라야 한다(안전 관문이 막는다). 승인 뒤 같은 요청을 다시 보내면 적용된다. 값이 없던 것을 처음
  정하는 것도 상향이다.
- `set_cost_limit_option`(A): 하향 셀프 승인 허용 on/off. 변경 이력을 남긴다.
쓰기는 요청자가 dashboard-api일 때만(Agent Card). 권한(limit_editor·policy_editor)·
로그인 세션 검사는 대시보드 API가 하고, 사람 이름은 토큰에서 채운다.
저장 전 검사: 하한 1, 코드 절대 상한, 한 번에 올림·내림 섞기 금지. 예전 원화 칸
(`video_per_item_krw`·`monthly_budget_krw`)을 보내면 거절한다(`deprecated_krw_limit`).
진행 중 생성 건은 영향이 없다. Slack 등 알림은 보내지 않는다(변경 이력·승인 목록까지).
TODO: 알림 연동.
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
from marketing_agent.media import policy_view
from marketing_agent.media import quota_store as store
from marketing_agent.media import usage as usage_mod
from marketing_agent.media.config import MediaSettings
from marketing_agent.media.ledger import LedgerError, MediaLedger

# 안전 관문 액션(registry.toml)
ACTION_LOWER = "lower_media_cost_limits"
ACTION_CHANGE = "change_media_cost_limits"
ACTION_OPTION = "set_cost_limit_option"
READ_ACTION = "get_media_cost_limits"


class ListInput(AxModel):
    pass


class LimitsOutput(AxModel):
    month: str
    # 예전 필드(원화 합계). 원화는 더 세지 않아 늘 None이다(계약은 추가만)
    month_total_krw: int | None = None
    # 종류별 이번 달 사용량·예산·남은 %: {video|image|audio: {unit, used, limit, remaining_pct}}
    usage: dict[str, Any] = Field(default_factory=dict)
    limits: dict[str, Any]
    bounds: dict[str, Any]
    options: dict[str, Any]
    policy: dict[str, Any]
    requests: list[dict[str, Any]] = Field(default_factory=list)
    history: list[dict[str, Any]] = Field(default_factory=list)


class ChangeInput(AxModel):
    video_seconds_monthly: int | None = None
    image_count_monthly: int | None = None
    audio_count_monthly: int | None = None
    # 예전 원화 칸. 보내면 거절한다(`deprecated_krw_limit`). 입력 계약은 지우지 않는다
    video_per_item_krw: int | None = None
    monthly_budget_krw: int | None = None
    reason: str = Field(default="", max_length=200)
    confirmed: bool = False
    updated_by: str = Field(min_length=1, max_length=64)


class OptionInput(AxModel):
    lower_self_approve: bool
    reason: str = Field(default="", max_length=200)
    updated_by: str = Field(min_length=1, max_length=64)


def _fail(request: AgentRequest, outcome: str, **output: Any) -> AgentResult:
    return AgentResult(
        meta=request.meta, status=TaskStatus.FAILED, outcome=outcome, output=dict(output)
    )


def _invalid(request: AgentRequest, error: ValidationError) -> AgentResult:
    errors = [f"{'.'.join(map(str, e['loc']))}: {e['type']}" for e in error.errors()]
    return _fail(request, "invalid_input", errors=errors)


def _evidence(action: str, summary: str) -> Evidence:
    return Evidence(
        queries=[action],
        data_summary=summary,
        collected_at=datetime.now(UTC),
        ref_ids=["cost_limits"],
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


def _month_usage(ledger: MediaLedger | None) -> tuple[str, dict[str, int] | None]:
    month = datetime.now(UTC).strftime("%Y-%m")
    if ledger is None:
        return month, None
    try:
        return month, ledger.month_usage_all(month)
    except LedgerError:
        return month, None


def _sources(settings: MediaSettings) -> dict[str, str]:
    """종류별 값의 출처: ui(화면) | env(환경변수) | none."""
    out: dict[str, str] = {}
    for kind in store.FIELD_BY_KIND:
        if store.quota(kind) is not None:
            out[f"{kind}_source"] = "ui"
        else:
            out[f"{kind}_source"] = "env" if settings.quota(kind) is not None else "none"
    return out


async def state(ledger: MediaLedger | None) -> dict[str, Any]:
    settings = MediaSettings.from_env()
    month, used = _month_usage(ledger)
    policy = policy_view.with_limits(await policy_view.fetch())
    limits = settings.quotas()
    return {
        "month": month,
        "month_total_krw": None,
        "usage": usage_mod.snapshot(used, limits) if used is not None else {},
        "limits": {
            **{field: limits[kind] for kind, field in store.FIELD_BY_KIND.items()},
            "key_owner": settings.key_owner,
            "quota_applies": settings.quota_applies,
            **_sources(settings),
            **store.meta(),
        },
        "bounds": {
            "min": store.MIN_AMOUNT,
            "max": dict(store.ABSOLUTE_MAX),
            "units": {field: usage_mod.UNITS[kind] for kind, field in store.FIELD_BY_KIND.items()},
        },
        "options": {"lower_self_approve": store.lower_self_approve(), **store.option_meta()},
        "policy": policy,
        "requests": store.requests(),
        "history": store.history(),
    }


def _result(
    request: AgentRequest, outcome: str, output: dict[str, Any], evidence: Evidence | None = None
) -> AgentResult:
    return AgentResult(
        meta=request.meta,
        status=TaskStatus.COMPLETED,
        outcome=outcome,
        output=output,
        evidence=evidence or _evidence(READ_ACTION, "사용량 예산 현황"),
    )


async def get_limits(request: AgentRequest, *, ledger: MediaLedger | None) -> AgentResult:
    return _result(request, "state", await state(ledger))


def _current(settings: MediaSettings) -> dict[str, int | None]:
    return {field: settings.quota(kind) for kind, field in store.FIELD_BY_KIND.items()}


def _direction(old: int | None, new: int) -> str:
    if old is None or new > old:
        return "up"  # 값이 없던 것을 정하는 것도 상향이다(없으면 실제 호출이 막혀 있었다)
    return "down" if new < old else "same"


async def _check(
    request: AgentRequest,
    changes: dict[str, int],
    current: dict[str, int | None],
    ledger: MediaLedger | None,
) -> tuple[str, list[dict[str, Any]]] | AgentResult:
    """저장 전 검사. 통과하면 (방향, 경고), 아니면 실패 결과."""
    for field, new in changes.items():
        if new < store.MIN_AMOUNT:
            return _fail(request, "below_minimum", field=field, min=store.MIN_AMOUNT)
        if new > store.absolute_max(field):
            return _fail(request, "over_absolute_max", field=field, max=store.absolute_max(field))
    kinds = {f: _direction(current[f], v) for f, v in changes.items()}
    changed = {k for k in kinds.values() if k != "same"}
    if not changed:
        return _fail(request, "no_change")
    if changed == {"up", "down"}:
        return _fail(request, "mixed_direction")
    direction = changed.pop()
    warnings: list[dict[str, Any]] = []
    if direction == "down":
        _, used = _month_usage(ledger)
        for kind, field in store.FIELD_BY_KIND.items():
            if field in changes and used is not None and changes[field] < used[kind]:
                warnings.append(
                    {
                        "code": "quota_below_month_usage",
                        "media_kind": kind,
                        "unit": usage_mod.UNITS[kind],
                        "month_used": used[kind],
                        "limit": changes[field],
                    }
                )
    return direction, warnings


async def change(
    request: AgentRequest,
    *,
    wanted: str,
    ledger: MediaLedger | None,
    from_decision: Any,
) -> AgentResult:
    """`wanted`: `down`(하향 스킬) | `up`(상향 스킬)."""
    try:
        data = ChangeInput.model_validate(request.input)
    except ValidationError as error:
        return _invalid(request, error)
    reason = data.reason.strip()
    if not reason:
        return _fail(request, "reason_required")
    if data.video_per_item_krw is not None or data.monthly_budget_krw is not None:
        return _fail(request, "deprecated_krw_limit")
    changes = {f: v for f in store.FIELDS if (v := getattr(data, f)) is not None}
    if not changes:
        return _fail(request, "invalid_input", errors=["변경할 값이 없다"])
    checked = await _check(request, changes, _current(MediaSettings.from_env()), ledger)
    if isinstance(checked, AgentResult):
        return checked
    direction, warnings = checked
    if wanted == "down" and direction != "down":
        return _fail(request, "not_a_decrease")
    if wanted == "up" and direction != "up":
        return _fail(request, "not_an_increase")
    self_approve = direction == "down" and store.lower_self_approve()
    if self_approve and not data.confirmed:
        return _fail(request, "confirmation_required", warnings=warnings, changes=changes)
    action = ACTION_LOWER if self_approve else ACTION_CHANGE
    params = {
        "changes": changes,
        "direction": direction,
        "reason": reason,
        "requested_by": data.updated_by,
    }
    ev = _evidence(action, f"사용량 예산 {direction} 변경 ({data.updated_by})")
    blocked = await _gate(request, action, params, ev, from_decision)
    kind = "raise" if direction == "up" else "lower"
    if blocked:
        review_id = (blocked.output or {}).get("review_id")
        if blocked.outcome == "held" and isinstance(review_id, str):
            store.add_request(
                {
                    "id": review_id,
                    "kind": kind,
                    "requested_by": data.updated_by,
                    "requested_at": datetime.now(UTC).isoformat(timespec="seconds"),
                    "reason": reason,
                    "payload": changes,
                }
            )
        return blocked
    try:
        store.set_values(changes, data.updated_by, kind, reason)
        store.drop_requests(changes)
    except store.CostLimitStoreError:
        return _fail(request, "limit_store_error")
    out = await state(ledger)
    out["warnings"] = warnings
    return _result(request, "raised" if direction == "up" else "lowered", out, ev)


async def set_option(
    request: AgentRequest, *, ledger: MediaLedger | None, from_decision: Any
) -> AgentResult:
    try:
        data = OptionInput.model_validate(request.input)
    except ValidationError as error:
        return _invalid(request, error)
    reason = data.reason.strip()
    if not reason:
        return _fail(request, "reason_required")
    if data.lower_self_approve == store.lower_self_approve():
        return _fail(request, "no_change")
    ev = _evidence(ACTION_OPTION, f"하향 셀프 승인 {data.lower_self_approve} ({data.updated_by})")
    params = {
        "lower_self_approve": data.lower_self_approve,
        "reason": reason,
        "requested_by": data.updated_by,
    }
    blocked = await _gate(request, ACTION_OPTION, params, ev, from_decision)
    if blocked:
        return blocked
    try:
        store.set_option(data.lower_self_approve, data.updated_by, reason)
    except store.CostLimitStoreError:
        return _fail(request, "limit_store_error")
    return _result(request, "option_saved", await state(ledger), ev)
