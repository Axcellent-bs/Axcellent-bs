"""영상 비용 상한·안전 정책 화면 경로.

- 권한은 로그인 세션 기준이다(`accounts.editor_flags`). 읽기는 로그인한 운영자·관리자 모두,
  상한 변경은 `limit_editor`, 하향 셀프 승인 스위치와 정책 변경은 `policy_editor`만 한다.
  일반회원·에이전트·공용 토큰은 읽기도 쓰기도 못 한다(403). 사람 이름은 토큰에서만 채운다.
- 마케팅 skill(`get/lower/raise_media_cost_limits`, `set_cost_limit_option`)을 A2A로 부른다.
  스킬 실패(검증 거절·보류)는 오류가 아니라 판단 결과라 200으로 그대로 돌려준다.
- 정책(안전 임계값)은 안전 서버가 가진다. 여기서는 읽기와 요청·적용 호출을 사람 세션 토큰을 그대로
  붙여 전달만 한다(값 검사·승인 확인·파일 쓰기는 안전 서버 몫). 이 서버는 정책 파일을 쓰지 않는다.
- 쓰기 경로는 nginx 게이트가 메서드별로 열려 있어야 한다(POST).
"""

from __future__ import annotations

import uuid
from typing import Any

import httpx
from fastapi import APIRouter, Depends, Header
from pydantic import BaseModel, Field

from ax_agent_template import config
from ax_dashboard_api import accounts, marketing_client
from ax_dashboard_api.auth import require_human
from ax_dashboard_api.envelope import ApiError, ok

router = APIRouter(prefix="/api/dashboard", dependencies=[Depends(require_human)])
CONTEXT = "cost_limits"
REASON = Field(min_length=1, max_length=200)


class LowerBody(BaseModel):
    video_per_item_krw: int | None = Field(default=None, ge=0, le=10_000_000)
    monthly_budget_krw: int | None = Field(default=None, ge=0, le=100_000_000)
    reason: str = REASON
    confirmed: bool = False


class RaiseBody(BaseModel):
    video_per_item_krw: int | None = Field(default=None, ge=0, le=10_000_000)
    monthly_budget_krw: int | None = Field(default=None, ge=0, le=100_000_000)
    reason: str = REASON


class OptionBody(BaseModel):
    lower_self_approve: bool
    reason: str = REASON


class PolicyRequestBody(BaseModel):
    rule_key: str = Field(min_length=1, max_length=64)
    requested_value: int = Field(ge=0, le=100_000_000)
    reason: str = REASON


async def _skill(skill: str, payload: dict[str, Any]) -> dict[str, Any]:
    out = await marketing_client.call(
        skill, payload, campaign_id=CONTEXT, key=f"ui:cost-limits:{uuid.uuid4().hex[:12]}"
    )
    if out["status"] == "failed" and out["outcome"] == "invalid_input":
        raise ApiError(400, "INVALID_INPUT", "입력이 맞지 않는다 (invalid_input)")
    return out


def _need(flag: bool, code: str, message: str) -> None:
    if not flag:
        raise ApiError(403, code, message)


@router.get("/marketing/cost-limits")
async def get_cost_limits(authorization: str | None = Header(None)) -> dict:
    """현재 비용 상한·정책 현황·대기 요청·이력. 로그인한 운영자·관리자만."""
    _, limit_editor, policy_editor = accounts.editor_flags(authorization)
    out = await _skill("get_media_cost_limits", {})
    output = dict(out.get("output") or {})
    output["viewer"] = {"can_edit": limit_editor, "can_edit_policy": policy_editor}
    return ok({**out, "output": output})


@router.post("/marketing/cost-limits/lower")
async def lower_cost_limits(body: LowerBody, authorization: str | None = Header(None)) -> dict:
    human, limit_editor, _ = accounts.editor_flags(authorization)
    _need(limit_editor, "LIMIT_EDITOR_REQUIRED", "비용 상한 변경 권한이 있는 계정만 바꿀 수 있어요")
    payload = {**body.model_dump(exclude_none=True), "updated_by": human.name}
    return ok(await _skill("lower_media_cost_limits", payload))


@router.post("/marketing/cost-limits/raise")
async def raise_cost_limits(body: RaiseBody, authorization: str | None = Header(None)) -> dict:
    human, limit_editor, _ = accounts.editor_flags(authorization)
    _need(limit_editor, "LIMIT_EDITOR_REQUIRED", "비용 상한 변경 권한이 있는 계정만 바꿀 수 있어요")
    payload = {**body.model_dump(exclude_none=True), "updated_by": human.name}
    return ok(await _skill("raise_media_cost_limits", payload))


@router.post("/marketing/cost-limits/option")
async def set_cost_limit_option(body: OptionBody, authorization: str | None = Header(None)) -> dict:
    """하향 셀프 승인 허용 스위치. 정책 담당(총괄·재무)만."""
    human, _, policy_editor = accounts.editor_flags(authorization)
    _need(policy_editor, "POLICY_EDITOR_REQUIRED", "정책 담당 계정만 바꿀 수 있어요")
    payload = {**body.model_dump(), "updated_by": human.name}
    return ok(await _skill("set_cost_limit_option", payload))


# ---- 안전 정책(비용 임계값) 전용 화면: 안전 서버로 전달만 한다 ----


def _safety_url(path: str) -> str:
    return f"{config.agent_url('safety').rstrip('/')}/api/safety/cost-policy{path}"


async def _safety(
    method: str, path: str, authorization: str | None, body: dict | None = None
) -> Any:
    headers = {"Authorization": authorization} if authorization else {}
    try:
        async with httpx.AsyncClient() as client:
            response = await client.request(
                method, _safety_url(path), json=body, headers=headers, timeout=15.0
            )
    except httpx.HTTPError:
        raise ApiError(502, "SAFETY_UNREACHABLE", "안전 서버에 닿지 못했다") from None
    try:
        data = response.json()
    except ValueError:
        data = {}
    if response.status_code >= 400:
        error = data.get("error", {}) if isinstance(data, dict) else {}
        raise ApiError(
            response.status_code if response.status_code < 500 else 502,
            str(error.get("code") or "SAFETY_ERROR"),
            str(error.get("message") or "안전 서버가 요청을 받지 않았다"),
        )
    return data.get("data") if isinstance(data, dict) else None


@router.get("/cost-policy")
async def get_cost_policy(authorization: str | None = Header(None)) -> dict:
    """정책 창: 현재 값·기본값·절대 상한·변경 이력·대기 중인 요청. 로그인한 운영자·관리자만."""
    _, _, policy_editor = accounts.editor_flags(authorization)
    data = await _safety("GET", "", authorization)
    return ok({**(data or {}), "viewer": {"can_edit_policy": policy_editor}})


@router.post("/cost-policy/requests")
async def request_cost_policy_change(
    body: PolicyRequestBody, authorization: str | None = Header(None)
) -> dict:
    """정책 값 변경 요청(C, 사람 승인). 승인은 요청자와 다른 사람이 승인 화면에서 한다."""
    _, _, policy_editor = accounts.editor_flags(authorization)
    _need(policy_editor, "POLICY_EDITOR_REQUIRED", "정책 담당 계정만 요청할 수 있어요")
    return ok(await _safety("POST", "/requests", authorization, body.model_dump()))


@router.post("/cost-policy/requests/{review_id}/apply")
async def apply_cost_policy_change(
    review_id: str, authorization: str | None = Header(None)
) -> dict:
    """승인된 정책 변경을 적용한다. 값은 승인된 요청 기록에서만 온다(안전 서버가 확인)."""
    _, _, policy_editor = accounts.editor_flags(authorization)
    _need(policy_editor, "POLICY_EDITOR_REQUIRED", "정책 담당 계정만 적용할 수 있어요")
    return ok(await _safety("POST", f"/requests/{review_id}/apply", authorization))
