"""Higgsfield 연결 상태 (skill `higgsfield_status`, read, 2026-11-20).

키가 있으면 `GET https://api.higgsfield.ai/requests/<없는 번호>/status`를 한 번 보낸다.
문서상 잘못된 인증은 401 `Invalid credentials`이고, 키가 맞으면 없는 요청이라
404 같은 다른 코드가 온다. 이미지를 만들지 않고 크레딧도 쓰지 않는다.
화면이 자주 물어도 Higgsfield를 두드리지 않게 결과를 잠깐 기억한다.

- 키 값은 로그·결과에 내지 않는다(`Authorization: Key <ID>:<SECRET>` 헤더로만 쓴다).
- 이미지 모델 경로(`MARKETING_HIGGSFIELD_IMAGE_MODEL`)는 설정값을 그대로 보여 준다(비밀값 아님).
"""

from __future__ import annotations

import logging
import os
import time
from datetime import UTC, datetime

import httpx

from ax_contracts.schemas.marketing_higgsfield import HiggsfieldConnection, HiggsfieldStatusOutput
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
    key: bool, connection: HiggsfieldConnection, message: str, code: int | None = None
) -> HiggsfieldStatusOutput:
    return HiggsfieldStatusOutput(
        key_configured=key,
        connection=connection,
        http_status=code,
        image_model=_image_model(),
        checked_at=datetime.now(UTC),
        message_ko=message,
    )


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
    """키를 Higgsfield에 한 번 보여 주고 연결 상태를 돌려준다."""
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
    from ax_agent_template import company_ai

    t = time.monotonic() if now is None else now
    cid = company_ai.company_id() or ""
    hit = _cache.get(cid)
    if hit is not None and t - hit[0] < CACHE_S:
        return hit[1]
    out = await probe()
    _cache[cid] = (t, out)
    return out


def reset_cache() -> None:
    _cache.clear()
