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
    reason: str | None = None,
    detail: str | None = None,
) -> HiggsfieldStatusOutput:
    return HiggsfieldStatusOutput(
        reason_code=reason[:60] if reason else None,
        detail_ko=detail[:160] if detail else None,
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


UNREACHABLE_KO = "Higgsfield에 닿지 못했어요"


def mcp_failure(d: dict[str, object]) -> tuple[HiggsfieldConnection, str]:
    """회사 계정 연결 확인이 실패했을 때 (connection, message_ko).

    사유별 문구는 화면 시안 표 그대로.

    connection 값은 예전과 같다(invalid_key = 다시 연결, 그 밖 unreachable). 옛 화면 호환."""
    code = str(d.get("reason") or "")
    status, tool = d.get("status"), d.get("tool") or "도구"
    detail, stage = d.get("detail"), d.get("stage") or "요청"
    if code == "client_id_missing":
        return "unreachable", "서버에 Higgsfield 연결 설정(CLIENT_ID)이 없어요"
    if code in ("reconnect_needed", "not_connected"):
        return "invalid_key", "Higgsfield 계정 연결이 끊겼어요. 다시 연결해 주세요"
    if code == "unauthorized":
        return "invalid_key", "Higgsfield가 연결을 거절했어요. 다시 연결해 주세요"
    if code in ("network", "timeout"):
        return "unreachable", "서버에서 Higgsfield까지 가지 못했어요(네트워크·시간 초과)"
    if code.startswith("http_"):
        return "unreachable", f"Higgsfield가 {code.removeprefix('http_')} 오류를 돌려줬어요"
    if code == "refresh_failed":
        got = f"({status})" if status else ""
        return "unreachable", f"연결 토큰을 새로 받지 못했어요{got}. 계속되면 다시 연결해 주세요"
    if code == "tool_error":
        said = f': "{detail}"' if detail else ""
        return "unreachable", f"계정에는 닿았는데 {tool}가 오류로 답했어요{said}"
    if code in ("bad_response", "rpc_error"):
        return "unreachable", "Higgsfield 응답을 읽지 못했어요(우리 쪽 확인 필요)"
    if code == "client_error":
        what = detail or d.get("error") or "오류"
        return "unreachable", f"요청을 보내기 전에 막혔어요({stage}: {what})"
    return "unreachable", UNREACHABLE_KO


async def probe_mcp(cid: str, client: httpx.AsyncClient | None = None) -> HiggsfieldStatusOutput:
    """회사 계정 연결에 `balance`를 한 번 물어 본다(크레딧을 쓰지 않는다)."""
    from marketing_agent import creative_mcp
    from marketing_agent.higgsfield_mcp import McpError, describe, log_line
    from marketing_agent.higgsfield_mcp_auth import McpAuthError

    key = bool(higgsfield_key())
    try:
        if client is None:
            async with httpx.AsyncClient(timeout=TIMEOUT_S) as own:
                credits, plan = await creative_mcp.balance(creative_mcp.Higgsfield(own, cid))
        else:
            credits, plan = await creative_mcp.balance(creative_mcp.Higgsfield(client, cid))
    except (McpAuthError, McpError, httpx.HTTPError, httpx.InvalidURL, ValueError) as exc:
        # 11/24: 사유·단계·도구·예외 종류·정리한 문장·시각을 남긴다 (값 없이)
        logger.warning("%s", log_line("higgsfield_status: Higgsfield 계정 연결 확인", exc, cid))
        d = describe(exc)
        if isinstance(exc, ValueError):  # 응답 칸 모양이 달라 읽지 못함
            d = {**d, "reason": "bad_response"}
        connection, message = mcp_failure(d)
        detail = d.get("detail")
        return _out(key, connection, message[:200], source="mcp", reason=str(d["reason"]),
                    detail=str(detail) if detail else None)  # fmt: skip
    message = "Higgsfield 회사 계정에 연결돼 있어요(구독 크레딧)"
    return _out(key, "ok", message, source="mcp", credits=credits, plan=plan)


def higgsfield_key(cid: str | None = None) -> str:
    """그 회사의 Higgsfield 키(키 저장소 tenant=회사 ID). 회사에 없으면 설치 기본 키.

    회사마다 자기 Higgsfield 계정을 쓴다(SNS·미디어 › 계정·연결, 11/23). env 모드는 환경변수와 같다.
    `cid`를 주면 그 회사(11/24, 뒤에서 도는 그림·영상), 없으면 로그인한 회사(예전 그대로).
    """
    from ax_agent_template import company_ai

    try:
        own = get_secret(HIGGSFIELD_API_KEY_ENV, tenant=cid or company_ai.company_id()).strip()
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
    except (httpx.HTTPError, httpx.InvalidURL) as exc:
        from marketing_agent.higgsfield_mcp import from_http_error, log_line

        got = from_http_error(exc, stage=None, tool=None)
        logger.warning("%s", log_line("higgsfield_status: Higgsfield 키 확인", got))
        return _out(True, "unreachable", "Higgsfield에 닿지 못했어요(시간 초과·네트워크)",
                    reason=got.code, detail=got.detail)  # fmt: skip
    code = response.status_code
    if code in (401, 403):
        return _out(True, "invalid_key", "Higgsfield가 키를 거절했어요", code,
                    reason="unauthorized")  # fmt: skip
    if code >= 500:
        return _out(True, "unreachable", f"Higgsfield가 {code} 오류를 돌려줬어요", code,
                    reason=f"http_{code}")  # fmt: skip
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
