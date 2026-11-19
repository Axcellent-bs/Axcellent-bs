"""키 관리 화면 경로(시스템 > 키 관리): 암호화 DB(ax_secret_store)에 고객사별 비밀값을 넣고 바꾼다.

- 조회 API는 값을 돌려주지 않는다(없다). 상태·끝 글자(4자 이하)·최근 변경만 보인다.
  한 번 넣은 값은 다시 볼 수 없고 교체만 된다. 값은 요청 본문으로만 들어오고 즉시 암호화되며
  응답·로그·오류에 나오지 않는다.
- 권한은 로그인 세션 기준이다(`accounts.secret_flags`): 운영자·관리자 중 계정 프로필의
  `secret_editor`가 켜진 사람이 `secret_tenants`(담당 고객사)의 키만 보고 바꾼다.
  일반회원·에이전트·공용 토큰은 읽기도 쓰기도 못 한다(403). 담당이 아닌 운영자·관리자는
  빈 목록과 읽기 전용 안내만 받는다.
- 추가는 담당자가 바로(마케팅 skill `add_secret`, A). 교체·삭제는 요청 → 다른 사람의 승인
  (안전 관문 C `change_secret`) → '승인됨 → 적용'(같은 skill 재호출).
  안전 서버에는 지문과 끝 글자만 간다.
- `AX_SECRET_STORE`가 `env`(기본)이면 저장소는 꺼져 있다(목록은 비어 있고 쓰기는 409).
- 쓰기 경로는 nginx 게이트가 POST로 열려 있어야 한다(`/api/dashboard/secrets/...`).
"""

from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Depends, Header
from pydantic import BaseModel, Field

from ax_dashboard_api import accounts, marketing_client
from ax_dashboard_api.auth import require_human
from ax_dashboard_api.envelope import ApiError, ok
from ax_secret_store import resolver
from ax_secret_store.errors import SecretStoreError
from ax_secret_store.service import SecretService

router = APIRouter(prefix="/api/dashboard/secrets", dependencies=[Depends(require_human)])
CONTEXT = "secret_store"
# 화면이 '미설정'으로 보여 주는 권장 이름(고객사별 자주 쓰는 키). 목록에 없는 이름도 넣을 수 있다
SUGGESTED_NAMES = [
    "MARKETING_IMAGE_API_KEY",
    "MARKETING_VIDEO_API_KEY",
    "MARKETING_AUDIO_API_KEY",
    "MARKETING_SEEDANCE_API_KEY",
    "MARKETING_KLING_API_KEY",
    "MARKETING_FAL_API_KEY",
    "MARKETING_REPLICATE_API_KEY",
    "MARKETING_WAN_API_KEY",
    "MARKETING_HIGGSFIELD_API_KEY",
    "MARKETING_PIXVERSE_API_KEY",
    "MARKETING_PIKA_API_KEY",
    "MARKETING_LUMA_API_KEY",
    "MARKETING_VIDU_API_KEY",
    "MARKETING_RUNWAY_API_KEY",
    "AX_AGENT_TOKENS",
]
STATUS_BY_CODE = {
    "invalid_tenant": 422,
    "invalid_name": 422,
    "invalid_value": 422,
    "already_exists": 409,
    "not_found": 404,
    "request_not_found": 404,
    "request_expired": 410,
    "request_mismatch": 409,
}
MESSAGES = {
    "invalid_tenant": "고객사 ID 형식이 아니에요 (영소문자·숫자·-·_, 40자 이하)",
    "invalid_name": "키 이름 형식이 아니에요 (영대문자·숫자·_, 3~64자)",
    "invalid_value": "값이 맞지 않아요 (8~4096자, 앞뒤 공백·줄바꿈 없음)",
    "already_exists": "이미 있는 키예요. 바꾸려면 교체를 요청해 주세요",
    "not_found": "그런 키가 없어요",
    "request_not_found": "그런 요청이 없어요",
    "request_expired": "요청이 오래돼 만료됐어요. 다시 요청해 주세요",
    "request_mismatch": "요청 종류가 맞지 않아요",
}


class ValueBody(BaseModel):
    # 길이 검사는 서비스가 한다(검증 오류 응답에 값이 실리지 않게 여기서는 형식만 본다)
    value: str = Field(default="", repr=False)
    reason: str = Field(default="", max_length=200)

    def __repr__(self) -> str:  # 값이 로그·예외에 실리지 않게
        return "ValueBody(...)"


class ReasonBody(BaseModel):
    reason: str = Field(default="", max_length=200)


def _store() -> SecretService:
    if not resolver.is_db_mode():
        raise ApiError(409, "SECRET_STORE_DISABLED", "키 저장소가 꺼져 있어요 (서버 설정 env 모드)")
    try:
        return resolver.service()
    except SecretStoreError as error:
        raise ApiError(
            503, "SECRET_STORE_UNAVAILABLE", f"키 저장소를 열지 못했다 ({error.code})"
        ) from None


def _api_error(error: SecretStoreError) -> ApiError:
    status = STATUS_BY_CODE.get(error.code)
    if status is None:
        return ApiError(503, "SECRET_STORE_UNAVAILABLE", f"키 저장소 오류 ({error.code})")
    return ApiError(status, error.code.upper(), MESSAGES[error.code])


def _need_editor(editor: bool, tenants: list[str], tenant: str) -> None:
    if not editor:
        raise ApiError(403, "SECRET_EDITOR_REQUIRED", "키 담당 권한이 있는 계정만 바꿀 수 있어요")
    if tenant not in tenants:
        raise ApiError(403, "TENANT_NOT_ASSIGNED", "담당 고객사가 아니에요")


async def _apply(skill: str, request_id: str, who: str) -> dict[str, Any]:
    """대기 요청 번호만 마케팅 skill에 보낸다(값 없음). 보류·거절은 판단 결과라 그대로 돌려준다."""
    out = await marketing_client.call(
        skill,
        {"request_id": request_id, "updated_by": who},
        campaign_id=CONTEXT,
        key=f"ui:secret:{request_id}:{uuid.uuid4().hex[:8]}",
    )
    return out


def _reason_error(out: dict[str, Any]) -> None:
    outcome = str(out.get("outcome") or "")
    if out.get("status") == "failed" and outcome in STATUS_BY_CODE:
        raise _api_error(SecretStoreError(outcome))
    if out.get("status") == "failed" and outcome == "store_disabled":
        raise ApiError(409, "SECRET_STORE_DISABLED", "키 저장소가 꺼져 있어요")


@router.get("")
def list_secrets(authorization: str | None = Header(None)) -> dict:
    """담당 고객사의 키 상태·대기 요청·이력. 값은 어디에도 없다."""
    _, editor, tenants = accounts.secret_flags(authorization)
    enabled = resolver.is_db_mode()
    viewer = {"can_edit": editor and enabled, "tenants": tenants, "enabled": enabled}
    if not (enabled and editor and tenants):
        empty: dict[str, Any] = {"tenants": [], "requests": [], "history": []}
        return ok({**empty, "viewer": viewer, "suggested_names": SUGGESTED_NAMES})
    svc = _store()
    try:
        secrets_by_tenant = {t: [] for t in tenants}
        for row in svc.list_status(tenants):
            secrets_by_tenant[row["tenant"]].append(row)
        data = {
            "tenants": [{"tenant": t, "secrets": secrets_by_tenant[t]} for t in tenants],
            "requests": svc.list_requests(tenants),
            "history": svc.history(tenants, 30),
        }
    except SecretStoreError as error:
        raise _api_error(error) from None
    return ok({**data, "viewer": viewer, "suggested_names": SUGGESTED_NAMES})


@router.post("/{tenant}/{name}/add")
async def add_secret(
    tenant: str, name: str, body: ValueBody, authorization: str | None = Header(None)
) -> dict:
    """키 추가: 담당자가 바로(A). 값은 암호화해 대기 요청에 두고 요청 번호만 마케팅에 넘긴다."""
    human, editor, tenants = accounts.secret_flags(authorization)
    _need_editor(editor, tenants, tenant)
    svc = _store()
    try:
        req = svc.stage("add", tenant, name, body.value, human.name, body.reason)
    except SecretStoreError as error:
        raise _api_error(error) from None
    out = await _apply("add_secret", req["id"], human.name)
    _reason_error(out)
    return ok({"request": req, "result": _safe(out)})


@router.post("/{tenant}/{name}/replace")
async def replace_secret(
    tenant: str, name: str, body: ValueBody, authorization: str | None = Header(None)
) -> dict:
    """교체 요청(C): 요청자와 다른 사람의 승인을 기다린다."""
    return await _request_change("replace", tenant, name, body.value, body.reason, authorization)


@router.post("/{tenant}/{name}/delete")
async def delete_secret(
    tenant: str, name: str, body: ReasonBody, authorization: str | None = Header(None)
) -> dict:
    """삭제 요청(C): 요청자와 다른 사람의 승인을 기다린다."""
    return await _request_change("delete", tenant, name, None, body.reason, authorization)


async def _request_change(
    op: str, tenant: str, name: str, value: str | None, reason: str, authorization: str | None
) -> dict:
    human, editor, tenants = accounts.secret_flags(authorization)
    _need_editor(editor, tenants, tenant)
    svc = _store()
    try:
        req = svc.stage(op, tenant, name, value, human.name, reason)
    except SecretStoreError as error:
        raise _api_error(error) from None
    out = await _apply("change_secret", req["id"], human.name)
    _reason_error(out)
    return ok({"request": req, "result": _safe(out)})


@router.post("/requests/{request_id}/apply")
async def apply_request(request_id: str, authorization: str | None = Header(None)) -> dict:
    """승인된 교체·삭제를 적용한다(같은 요청을 관문에 다시 보낸다). 승인 전이면 보류."""
    human, editor, tenants = accounts.secret_flags(authorization)
    svc = _store()
    try:
        pending = svc.pending(request_id)
    except SecretStoreError as error:
        raise _api_error(error) from None
    _need_editor(editor, tenants, pending.tenant)
    skill = "add_secret" if pending.op == "add" else "change_secret"
    out = await _apply(skill, request_id, human.name)
    _reason_error(out)
    return ok({"result": _safe(out)})


def _safe(out: dict[str, Any]) -> dict[str, Any]:
    """마케팅 결과에서 화면에 필요한 칸만(값은 애초에 없다)."""
    output = out.get("output") if isinstance(out.get("output"), dict) else {}
    keep = ("op", "tenant", "name", "hint", "review_id", "hold_reason", "grade")
    return {
        "status": out.get("status"),
        "outcome": out.get("outcome"),
        "output": {k: v for k, v in (output or {}).items() if k in keep},
    }

