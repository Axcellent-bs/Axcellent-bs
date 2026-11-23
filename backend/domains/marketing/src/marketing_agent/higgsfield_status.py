"""Higgsfield 연결 상태 (skill `higgsfield_status`, read, 2026-11-20).

키가 있으면 `GET https://api.higgsfield.ai/requests/<없는 번호>/status`를 한 번 보낸다.
문서상 잘못된 인증은 401 `Invalid credentials`이고, 키가 맞으면 없는 요청이라
404 같은 다른 코드가 온다. 이미지를 만들지 않고 크레딧도 쓰지 않는다.
화면이 자주 물어도 Higgsfield를 두드리지 않게 결과를 잠깐 기억한다.

- 키 값은 로그·결과에 내지 않는다(`Authorization: Key <ID>:<SECRET>` 헤더로만 쓴다).
- 이미지 모델 경로(`MARKETING_HIGGSFIELD_IMAGE_MODEL`)는 설정값을 그대로 보여 준다(비밀값 아님).

회사 계정 연결(원격 MCP, 11/23): 요청 회사가 Higgsfield 계정을 연결했으면 그 계정에
`balance`(읽기, 크레딧을 쓰지 않는다)를 한 번 물어 `source=mcp`·남은 크레딧·요금제를 돌려준다.
그림·영상도 이 계정으로 만든다(`creative_render.route`). 연결이 거절되면 `invalid_key`(다시 연결).
없으면 예전 키 확인 그대로.
"""

from __future__ import annotations

import logging
import os
import time
from datetime import UTC, datetime

import httpx

from ax_contracts.schemas.marketing_higgsfield import (
    HiggsfieldConnection,
    HiggsfieldSource,
    HiggsfieldStatusOutput,
)
from ax_secret_store import get_secret
from marketing_agent.media.config import HIGGSFIELD_API_KEY_ENV, HIGGSFIELD_IMAGE_MODEL_ENV

logger = logging.getLogger("marketing.higgsfield_status")

PROBE_URL = "https://api.higgsfield.ai/requests/00000000-0000-0000-0000-000000000000/status"
TIMEOUT_S = 8.0
CACHE_S = 20.0

_cache: dict[str, tuple[float, HiggsfieldStatusOutput]] = {}  # 회사 ID → (시각, 결과)


def _image_model() -> str | None:
    return os.getenv(HIGGSFIELD_IMAGE_MODEL_ENV, "").strip()[:120] or None


def _out(
    key: bool,
    connection: HiggsfieldConnection,
    message: str,
    code: int | None = None,
    *,
    source: HiggsfieldSource | None = None,
    credits: float | None = None,
    plan: str | None = None,
) -> HiggsfieldStatusOutput:
    return HiggsfieldStatusOutput(
        key_configured=key,
        connection=connection,
        http_status=code,
        image_model=_image_model(),
        checked_at=datetime.now(UTC),
        message_ko=message,
        source=source if source is not None else ("api_key" if key else None),
        credits=credits if credits is None or credits >= 0 else None,
        plan=plan,
    )


async def probe_mcp(cid: str, client: httpx.AsyncClient | None = None) -> HiggsfieldStatusOutput:
    """회사 계정 연결에 `balance`를 한 번 물어 본다(크레딧을 쓰지 않는다)."""
    from marketing_agent import creative_mcp
    from marketing_agent.higgsfield_mcp import McpError
    from marketing_agent.higgsfield_mcp_auth import McpAuthError

    key = bool(higgsfield_key())
    try:
        if client is None:
            async with httpx.AsyncClient(timeout=TIMEOUT_S) as own:
                credits, plan = await creative_mcp.balance(creative_mcp.Higgsfield(own, cid))
        else:
            credits, plan = await creative_mcp.balance(creative_mcp.Higgsfield(client, cid))
    except McpAuthError as exc:
        logger.warning("Higgsfield 계정 연결 확인 실패: %s", exc)
        if str(exc) == "client_id_missing":
            return _out(
                key, "unreachable", "서버에 Higgsfield 연결 설정(CLIENT_ID)이 없어요", source="mcp"
            )
        if str(exc) in ("reconnect_needed", "not_connected"):
            return _out(
                key, "invalid_key", "Higgsfield 계정 연결이 끊겼어요. 다시 연결해 주세요",
                source="mcp",
            )  # fmt: skip
        return _out(key, "unreachable", "Higgsfield에 닿지 못했어요", source="mcp")
    except (McpError, httpx.HTTPError, ValueError) as exc:
        code = str(exc) if isinstance(exc, McpError) else type(exc).__name__
        logger.warning("Higgsfield 계정 연결 확인 실패: %s", code)
        if code == "unauthorized":
            return _out(
                key, "invalid_key", "Higgsfield가 연결을 거절했어요. 다시 연결해 주세요",
                source="mcp",
            )  # fmt: skip
        return _out(key, "unreachable", "Higgsfield에 닿지 못했어요", source="mcp")
    message = "Higgsfield 회사 계정에 연결돼 있어요(구독 크레딧)"
    return _out(key, "ok", message, source="mcp", credits=credits, plan=plan)


def higgsfield_key() -> str:
    """로그인한 회사의 Higgsfield 키(키 저장소 tenant=회사 ID). 회사에 없으면 설치 기본 키.

    회사마다 자기 Higgsfield 계정을 쓴다(SNS·미디어 › 계정·연결, 11/23). env 모드는 환경변수와 같다.
    """
    from ax_agent_template import company_ai

    try:
        own = get_secret(HIGGSFIELD_API_KEY_ENV, tenant=company_ai.company_id()).strip()
    except Exception:  # noqa: BLE001 - 키 저장소 장애는 기본 키로
        own = ""
    return own or get_secret(HIGGSFIELD_API_KEY_ENV).strip()


async def probe(client: httpx.AsyncClient | None = None) -> HiggsfieldStatusOutput:
    """회사 계정 연결이 있으면 그것을, 없으면 키를 Higgsfield에 한 번 보여 주고 상태를 돌려준다."""
    from marketing_agent import higgsfield_mcp_auth

    cid = higgsfield_mcp_auth.company()
    if cid and higgsfield_mcp_auth.connected(cid):
        return await probe_mcp(cid, client)
    key = higgsfield_key()
    if not key:
        return _out(False, "not_configured", "Higgsfield 키가 설정돼 있지 않아요")
    headers = {"Authorization": f"Key {key}"}
    try:
        if client is None:
            async with httpx.AsyncClient(timeout=TIMEOUT_S) as own:
                response = await own.get(PROBE_URL, headers=headers)
        else:
            response = await client.get(PROBE_URL, headers=headers, timeout=TIMEOUT_S)
    except httpx.HTTPError as exc:
        logger.warning("Higgsfield 연결 확인 실패: %s", type(exc).__name__)
        return _out(True, "unreachable", "Higgsfield에 닿지 못했어요(시간 초과·네트워크)")
    code = response.status_code
    if code in (401, 403):
        return _out(True, "invalid_key", "Higgsfield가 키를 거절했어요", code)
    if code >= 500:
        return _out(True, "unreachable", f"Higgsfield가 {code} 오류를 돌려줬어요", code)
    return _out(True, "ok", "Higgsfield에 연결돼 있어요", code)


async def status(now: float | None = None) -> HiggsfieldStatusOutput:
    """`CACHE_S` 동안은 지난 결과를 돌려준다."""
    from marketing_agent import higgsfield_mcp_auth

    t = time.monotonic() if now is None else now
    cid = higgsfield_mcp_auth.company() or ""
    hit = _cache.get(cid)
    if hit is not None and t - hit[0] < CACHE_S:
        return hit[1]
    out = await probe()
    _cache[cid] = (t, out)
    return out


def reset_cache() -> None:
    _cache.clear()
