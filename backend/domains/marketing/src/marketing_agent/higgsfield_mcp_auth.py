"""회사 Higgsfield 계정 연결의 토큰 (원격 MCP OAuth, 2026-11-23).

- 갱신 토큰: 회사 키 저장소(tenant=회사 ID) `MARKETING_HIGGSFIELD_MCP_REFRESH_TOKEN`.
  dashboard-api가 회사 관리자의 동의를 받아 넣는다. 회사마다 자기 계정이다.
  키 저장소가 db 모드가 아니면(env 모드) 연결이 없는 것으로 본다
  (설치 하나의 환경값을 여러 회사가 같이 쓰지 않게)
- 어느 회사인가: 그 요청의 회사(`company_scope.active()`, 서버가 `meta.tenant_id`로 건다).
  요청 밖(스스로 도는 루틴)이면 설치 회사(`company_ai.company_id()`)
- access token은 이 프로세스 메모리에만 둔다(회사별, 만료 1분 전까지). 저장하지 않는다
- 갱신(`grant_type=refresh_token`, `resource`=MCP 주소, 공개 클라이언트라 비밀 없음)에서
  Higgsfield가 새 갱신 토큰을 주면 `store_oauth_token(expect=옛 토큰)`으로 바꿔 넣는다.
  그 사이 다른 곳이 먼저 바꿨으면(`stale_token`) 덮어쓰지 않는다(받은 access token은 그대로 쓴다)
- 갱신이 거절되면(400·401) 저장된 토큰을 다시 읽어, 다른 곳이 이미 바꿔 놨으면
  그것으로 한 번 더 한다.
  그래도 안 되면 `reconnect_needed`(화면에서 다시 연결)
- 값(토큰)은 로그·예외에 넣지 않는다. 예외에는 사유 코드만 담는다
"""

from __future__ import annotations

import asyncio
import logging
import os
import time
from dataclasses import dataclass, field

import httpx

logger = logging.getLogger("marketing.higgsfield_mcp_auth")

TOKEN_NAME = "MARKETING_HIGGSFIELD_MCP_REFRESH_TOKEN"
CLIENT_ID_ENV = "MARKETING_HIGGSFIELD_MCP_CLIENT_ID"  # 공개 클라이언트 ID(비밀값 아님)
TOKEN_URL = "https://mcp.higgsfield.ai/oauth2/token"
RESOURCE = "https://mcp.higgsfield.ai/mcp"
EARLY_S = 60.0  # 만료 이만큼 전이면 새로 받는다
DEFAULT_TTL_S = 300.0  # 응답에 expires_in이 없을 때
ROTATED_BY = "higgsfield_mcp_refresh"


class McpAuthError(Exception):
    """사유 코드(`str(오류)`)와 원인 칸 (값 없이).

    코드: `not_connected`·`client_id_missing`·`reconnect_needed`·`refresh_failed`(+갱신 응답 코드
    `status`)·갱신 요청이 닿지 않으면 `network`·`timeout`·`client_error`(11/24).
    단계는 늘 refresh."""

    def __init__(self, code: str, *, status: int | None = None, detail: str | None = None,
                 error_class: str | None = None) -> None:  # fmt: skip
        super().__init__(code)
        self.stage = "refresh"
        self.tool = None
        self.status = status
        self.detail = detail
        self.error_class = error_class


@dataclass
class _Access:
    token: str = field(repr=False)
    expires_at: float


_cache: dict[str, _Access] = {}
_locks: dict[str, asyncio.Lock] = {}


def company() -> str | None:
    """이 요청의 회사(정식 ID). 요청 밖이면 설치 회사."""
    from ax_agent_template import company_ai, company_scope

    return company_scope.active() or company_ai.company_id()


def _service():  # noqa: ANN202 - ax_secret_store.SecretService
    from ax_secret_store import resolver

    return resolver.service()


def stored_refresh_token(cid: str) -> str:
    """키 저장소에서 바로 읽은 갱신 토큰(캐시 없이). 없거나 db 모드가 아니면 빈 문자열."""
    from ax_secret_store import is_db_mode

    if not is_db_mode():
        return ""
    try:
        return (_service().reveal_for_runtime(cid, TOKEN_NAME) or "").strip()
    except Exception as error:  # noqa: BLE001 - 키 저장소 장애는 연결 없음으로 본다
        logger.warning("Higgsfield 연결 토큰을 읽지 못함 (%s)", type(error).__name__)
        return ""


def connected(cid: str | None = None) -> bool:
    """이 회사가 Higgsfield 계정을 연결했는가(값 없이)."""
    from ax_agent_template import company_ai

    cid = cid or company()
    return bool(cid) and bool(company_ai.key(TOKEN_NAME, cid))


def client_id() -> str:
    """AXcellent 앱 단위 값: 키 저장소 기본 tenant, 없으면 환경값(dashboard-api와 같은 순서)."""
    from ax_secret_store import get_secret

    try:
        value = get_secret(CLIENT_ID_ENV).strip()
    except Exception:  # noqa: BLE001
        value = ""
    return value or os.getenv(CLIENT_ID_ENV, "").strip()


def forget(cid: str | None = None) -> None:
    """메모리의 access token을 버린다(401을 받았을 때, 테스트)."""
    if cid is None:
        _cache.clear()
    else:
        _cache.pop(cid, None)


async def _refresh(client: httpx.AsyncClient, refresh: str, cid_app: str) -> httpx.Response:
    return await client.post(
        TOKEN_URL,
        data={
            "grant_type": "refresh_token",
            "refresh_token": refresh,
            "client_id": cid_app,
            "resource": RESOURCE,
        },
    )


def _keep_rotated(cid: str, old: str, new: str) -> None:
    from ax_secret_store import resolver
    from ax_secret_store.errors import STALE_TOKEN, SecretStoreError

    try:
        _service().store_oauth_token(cid, TOKEN_NAME, new, ROTATED_BY, expect=old)
    except SecretStoreError as error:
        if error.code == STALE_TOKEN:  # 다른 곳이 먼저 바꿨다. 그 값을 덮지 않는다
            logger.warning("Higgsfield 갱신 토큰을 다른 곳이 먼저 바꿨다 (%s)", cid)
        else:
            logger.error("Higgsfield 새 갱신 토큰을 넣지 못함 (%s, %s)", cid, error.code)
    except Exception as error:  # noqa: BLE001
        logger.error("Higgsfield 새 갱신 토큰을 넣지 못함 (%s, %s)", cid, type(error).__name__)
    resolver.invalidate()


async def access_token(client: httpx.AsyncClient, cid: str, *, force: bool = False) -> str:
    """이 회사 계정의 access token. 필요하면 갱신한다(회사마다 한 번에 하나)."""
    lock = _locks.setdefault(cid, asyncio.Lock())
    async with lock:
        hit = _cache.get(cid)
        if hit is not None and not force and hit.expires_at - EARLY_S > time.monotonic():
            return hit.token
        _cache.pop(cid, None)
        old = stored_refresh_token(cid)
        if not old:
            raise McpAuthError("not_connected")
        app = client_id()
        if not app:
            raise McpAuthError("client_id_missing")
        try:
            res = await _refresh(client, old, app)
            if res.status_code in (400, 401):
                again = stored_refresh_token(cid)
                if not again or again == old:
                    raise McpAuthError("reconnect_needed", status=res.status_code)
                old = again  # 다른 곳이 이미 바꿔 둔 토큰으로 한 번 더
                res = await _refresh(client, old, app)
                if res.status_code in (400, 401):
                    raise McpAuthError("reconnect_needed", status=res.status_code)
            if res.status_code != 200:
                raise McpAuthError("refresh_failed", status=res.status_code)
            body = res.json()
        except (httpx.HTTPError, httpx.InvalidURL) as error:
            # 11/24: 갱신 요청이 닿지 않음 → network·timeout·client_error
            # (예전에는 모두 refresh_failed)
            from marketing_agent.higgsfield_mcp import from_http_error

            got = from_http_error(error, stage="refresh", tool=None)
            raise McpAuthError(got.code, detail=got.detail, error_class=got.error_class) from error
        except ValueError as error:  # 200인데 JSON이 아님
            raise McpAuthError("refresh_failed", status=200,
                               error_class=type(error).__name__) from error  # fmt: skip
        token = body.get("access_token") if isinstance(body, dict) else None
        if not isinstance(token, str) or not token:
            raise McpAuthError("refresh_failed", status=200)
        new = body.get("refresh_token")
        if isinstance(new, str) and new and new != old:
            _keep_rotated(cid, old, new)
        try:
            ttl = float(body.get("expires_in") or DEFAULT_TTL_S)
        except (TypeError, ValueError):
            ttl = DEFAULT_TTL_S
        _cache[cid] = _Access(token=token, expires_at=time.monotonic() + max(ttl, 1.0))
        return token
