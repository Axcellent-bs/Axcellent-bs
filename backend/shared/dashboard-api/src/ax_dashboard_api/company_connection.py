"""회사 연결 API: 시스템 관리 > 회사 연결에서 고객사 판매 서버(shop-api 규격) 주소를 정한다.

- `GET    /api/dashboard/company-connection`: 지금 쓰는 주소와 어디서 정했는지
  (`connection`·`env`·`default`)
- `POST   /api/dashboard/company-connection/test`: 저장하지 않고 연결 확인만
- `PUT    /api/dashboard/company-connection`: 다시 확인하고, 모두 통과하면 저장
  (모든 서비스가 재시작 없이 따라감)
- `POST   /api/dashboard/company-connection/mode`: 내부 데이터(`internal`) ↔ 실제 연결(`connected`).
  저장한 주소는 지우지 않는다. 실제 연결로 켤 때는 다시 확인하고, 통과해야 켠다
- `DELETE /api/dashboard/company-connection`: 연결을 지우고 원래 주소(`AX_SHOP_API_URL`·기본값)로
- `POST /save`·`POST /clear`: PUT·DELETE와 같다(운영 서버 nginx 게이트가 POST만 열 수 있어서)

읽기·쓰기 모두 사람 토큰만 된다(`require_human`). 쓰기(확인 포함)는 로그인 세션이면 운영자·관리자만
(`EDITORS`). 주소 정책은
`ax_agent_template.company_connection.check_host`(사내 주소 또는 허용 목록 + https, 링크 로컬 차단).
확인은 서버가 그 주소로 직접 GET을 보내므로 아무 주소나 부를 수 있다.
사내 설치·사람 토큰 전제이고, 계정이 든 주소·http(s) 밖의 주소는 받지 않는다.
연결 확인은 토큰 없이 읽히는 경로(상태·상품·구매자)만 본다. 숫자는 받은 그대로만 쓴다.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal

import httpx
from fastapi import APIRouter, Depends, Request
from pydantic import BaseModel, Field

from ax_agent_template import company_connection as store
from ax_agent_template import company_scope, pinned_http
from ax_agent_template import tenant as tenants
from ax_agent_template.config import agent_url
from ax_dashboard_api.accounts import require_session_role
from ax_dashboard_api.auth import require_human
from ax_dashboard_api.envelope import ApiError, ok
from ax_dashboard_api.site_adapter import setup as adapter_setup
from ax_dashboard_api.site_adapter import upstream as adapter_upstream
from ax_dashboard_api.site_adapter.mapping import MappingError, load_mapping

TIMEOUT_SECONDS = 5.0
# 테스트에서 httpx.MockTransport로 바꿔 끼운다
TRANSPORT: httpx.AsyncBaseTransport | None = None

logger = logging.getLogger("ax.dashboard.company_connection")

router = APIRouter(
    prefix="/api/dashboard/company-connection",
    dependencies=[Depends(require_human)],
)
# 저장·확인·모드·해제는 운영자·고객사 관리자 세션만. 일반회원 세션은 403 ROLE_REQUIRED
# (일반회원이 연결을 끊거나 회사를 바꾸지 못하게, 회사 간 격리 시험 6). 공용 토큰은 그대로 둔다
EDITORS = [Depends(require_session_role("operator", "admin"))]


class ConnectionIn(BaseModel):
    url: str = Field(min_length=1, max_length=store.MAX_URL_LENGTH)
    # 회사 설정 폴더 id (config/tenants/<id>). 없으면 저장된 회사를 그대로 둔다
    tenant: str | None = Field(None, max_length=64)


def pick_tenant(given: str | None, found: store.Connection | None) -> str | None:
    """저장할 회사: 준 값(설정 폴더가 있어야 함, 없으면 422) → 저장된 회사."""
    if given:
        if not tenants.is_tenant(given):
            raise ApiError(422, "UNKNOWN_TENANT", f"회사 설정이 없어요: {given}")
        return given
    return found.tenant if found else None


def tenant_state() -> dict[str, Any]:
    """지금 회사와 고를 수 있는 회사. 연결 전이면 tenant None(캣북스 기본값 없음)."""
    listed = tenants.list_tenants()
    now = tenants.current()
    return {
        "tenant": next((t for t in listed if t["id"] == now), None) if now else None,
        "tenants": listed,
    }


def public_site_company() -> dict[str, str] | None:
    """로그인 전 화면에 보일 사이트 연결 회사: 회사 연결이 켜져 있을 때 그 회사 설정의 id·이름만.

    화면 `demo/company.ts`의 사이트 회사와 같은 조건이다. 주소·기록·연결한 사람은 넣지 않는다.
    """
    found = store.read_connection()
    tenant = tenant_state()["tenant"] if found and found.enabled else None
    if not isinstance(tenant, dict) or not tenant.get("id") or not tenant.get("name"):
        return None
    return {"id": str(tenant["id"]), "name": str(tenant["name"]), "site_url": ""}


class ModeIn(BaseModel):
    mode: Literal["internal", "connected"]


@dataclass(frozen=True)
class Check:
    key: str
    label: str
    ok: bool
    detail: str

    def as_dict(self) -> dict[str, Any]:
        return {"key": self.key, "label": self.label, "ok": self.ok, "detail": self.detail}


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _valid(url: str) -> str:
    """주소 형식과 호스트 정책(사내 주소·허용 목록, 링크 로컬 차단). 어긋나면 422."""
    try:
        valid = store.validate_shop_url(url)
        store.check_host(valid)
    except ValueError as exc:
        raise ApiError(422, "INVALID_URL", str(exc)) from exc
    return valid


async def _get(client: httpx.AsyncClient, url: str) -> tuple[Any | None, str | None]:
    """JSON 본문 또는 실패 이유."""
    try:
        response = await client.get(url)
    except httpx.TimeoutException:
        return None, f"{TIMEOUT_SECONDS:g}초 안에 답이 없음"
    except httpx.HTTPError as exc:
        return None, f"연결할 수 없음 ({type(exc).__name__})"
    if response.status_code != 200:
        return None, f"HTTP {response.status_code}"
    try:
        return response.json(), None
    except ValueError:
        return None, "JSON 응답이 아님"


CheckKey = Literal["health", "products", "users"]
# 목록 확인: 항목, 이름, 경로, 줄마다 있어야 하는 칸
LIST_CHECKS: tuple[tuple[CheckKey, str, str, tuple[str, ...]], ...] = (
    ("products", "상품", "/api/shop/products", ("product_id", "name", "price")),
    ("users", "구매자", "/api/shop/users", ("buyer_id",)),
)


def _list_problem(body: Any, need: tuple[str, ...]) -> tuple[int, str | None]:
    """목록 줄 수와 모양 문제(없으면 None)."""
    data = body.get("data") if isinstance(body, dict) else None
    if not isinstance(data, list):
        return 0, "응답의 data가 목록이 아님"
    for row in data:
        missing = [k for k in need if not isinstance(row, dict) or k not in row]
        if missing:
            return len(data), f"{', '.join(missing)} 칸이 없음"
    return len(data), None


async def run_checks(url: str) -> list[Check]:
    """판매 서버 상태·상품·구매자 읽기를 확인한다. 항목마다 통과 여부와 이유."""
    checks: list[Check] = []
    async with httpx.AsyncClient(
        timeout=TIMEOUT_SECONDS,
        follow_redirects=False,
        transport=TRANSPORT or pinned_http.guarded_transport(),  # 검사한 IP로만 연결
    ) as client:
        body, error = await _get(client, f"{url}/health")
        data = body.get("data") if isinstance(body, dict) else None
        status = data.get("status") if isinstance(data, dict) else None
        if error:
            checks.append(Check("health", "서버 응답", False, error))
        elif status != "ok":
            checks.append(Check("health", "서버 응답", False, "상태가 ok가 아님"))
        else:
            checks.append(Check("health", "서버 응답", True, "응답 정상"))
        for key, name, path, need in LIST_CHECKS:
            label = f"{name} 읽기"
            body, error = await _get(client, f"{url}{path}")
            if error:
                checks.append(Check(key, label, False, error))
                continue
            count, problem = _list_problem(body, need)
            if problem:
                checks.append(Check(key, label, False, problem))
            else:
                checks.append(Check(key, label, True, f"{name} {count:,}건 읽음"))
    return checks


def _install_env() -> str:
    """운영자가 준 `AX_SHOP_API_URL`. 설치 하나에 하나라 캣북스 화면에서만 쓴다.

    다른 회사(세션 회사)는 자기 회사 연결만 본다. 캣북스 주소를 보이거나 "연결됨"으로 세지 않는다
    (`config.agent_url`도 다른 회사는 이 값으로 빠지지 않는다).
    """
    if store.company_of() != company_scope.CATBOOKS:
        return ""
    return os.getenv("AX_SHOP_API_URL") or ""


def _internal_url() -> str:
    """내부 데이터 모드의 주소: 운영자가 준 `AX_SHOP_API_URL`. 없으면 ""(연결 안 됨)."""
    env = _install_env()
    return env.rstrip("/") if env else ""  # 캣북스 기본 주소 없음: 운영자가 준 주소가 없으면 빈 값


def _state() -> dict[str, Any]:
    found = store.read_connection()
    if found and found.enabled:
        source = "connection"
    elif _install_env():
        source = "env"
    else:
        source = "default"
    return {
        "shop_api_url": agent_url("shop-api"),
        "source": source,
        "mode": "connected" if source == "connection" else "internal",
        # 회사 사이트에 이어져 있는지(연결 파일 또는 운영자가 준 주소). 아니면 "연결 안 됨"
        "connected": source != "default",
        **tenant_state(),
        "internal_url": _internal_url(),
        "connection": found.as_dict() if found else None,
        "editable": bool(store.connection_file()),
        "history": store.read_history(5),
    }


def _require_editable() -> None:
    if not store.connection_file():
        raise ApiError(
            409, "CONNECTION_NOT_EDITABLE", f"{store.CONNECTION_FILE_ENV}가 없어 저장할 수 없다"
        )


async def _checked(url: str) -> list[Check]:
    """확인을 돌리고, 하나라도 실패하면 422로 이유를 알린다."""
    checks = await run_checks(url)
    failed = [c for c in checks if not c.ok]
    if failed:
        reasons = "; ".join(f"{c.label}: {c.detail}" for c in failed)
        raise ApiError(422, "CONNECTION_CHECK_FAILED", f"연결 확인에 실패했다 ({reasons})")
    return checks


async def _adapter_checked(base: str) -> None:
    """다른 형식 API: 저장한 짝으로 고객사 첫 쪽을 바꿔 본다. 안 되면 422."""
    try:
        mapping = load_mapping()
    except MappingError as exc:
        raise ApiError(422, "MAPPING_BROKEN", str(exc)) from exc
    if mapping is None:
        raise ApiError(422, "CONNECTION_CHECK_FAILED", "사이트 짝이 없어 켤 수 없다")
    try:
        await adapter_setup.verify(_valid(base), mapping, adapter_upstream.saved_token())
    except ApiError as exc:
        reason = f"연결 확인에 실패했다 ({exc.message})"
        raise ApiError(422, "CONNECTION_CHECK_FAILED", reason) from exc
    adapter_upstream.clear_cache()


def _human(request: Request) -> str:
    return getattr(request.state, "human", "unknown")


@router.get("")
def get_connection() -> dict:
    return ok(_state())


@router.post("/test", dependencies=EDITORS)
async def test_connection(body: ConnectionIn) -> dict:
    url = _valid(body.url)
    checks = await run_checks(url)
    return ok(
        {
            "url": url,
            "ok": all(c.ok for c in checks),
            "checks": [c.as_dict() for c in checks],
            "checked_at": _now(),
        }
    )


# POST /save·/clear: 운영 서버 nginx 게이트가 POST만 열 수 있어 같은 동작을 POST로도 받는다
@router.post("/save", dependencies=EDITORS)
@router.put("", dependencies=EDITORS)
async def save_connection(body: ConnectionIn, request: Request) -> dict:
    """주소를 확인하고 저장한다. 저장하면 실제 연결 모드가 된다."""
    _require_editable()
    url = _valid(body.url)
    tenant = pick_tenant(body.tenant, store.read_connection())
    checks = await _checked(url)
    store.save_connection(url, verified_at=_now(), updated_by=_human(request), tenant=tenant)
    logger.info("회사 연결 저장: %s (by %s)", url, _human(request))
    return ok({**_state(), "checks": [c.as_dict() for c in checks]})


@router.post("/mode", dependencies=EDITORS)
async def switch_mode(body: ModeIn, request: Request) -> dict:
    """내부 데이터 ↔ 실제 연결. 저장한 주소는 남긴다. 실제 연결로 켤 때는 다시 확인한다."""
    _require_editable()
    found = store.read_connection()
    if found is None:
        raise ApiError(409, "NO_CONNECTION", "저장한 연결이 없어 모드를 바꿀 수 없다")
    # 종류(shop-api 규격·다른 형식 API)와 변환기 주소는 그대로 둔다
    keep = {"kind": found.kind, "adapter": found.adapter, "tenant": found.tenant}
    if body.mode == "connected":
        if found.kind == "adapter" and found.adapter:
            await _adapter_checked(found.adapter["base_url"])
        else:
            await _checked(_valid(found.shop_api_url))
        store.save_connection(
            found.shop_api_url, verified_at=_now(), updated_by=_human(request), **keep
        )
    else:
        store.save_connection(
            found.shop_api_url,
            verified_at=found.verified_at,
            updated_by=_human(request),
            enabled=False,
            **keep,
        )
    logger.info("회사 연결 모드: %s (%s, by %s)", body.mode, found.shop_api_url, _human(request))
    return ok(_state())


@router.post("/clear", dependencies=EDITORS)
@router.delete("", dependencies=EDITORS)
def delete_connection(request: Request) -> dict:
    if store.clear_connection(_human(request)):
        logger.info("회사 연결 해제 (by %s)", _human(request))
    return ok(_state())
