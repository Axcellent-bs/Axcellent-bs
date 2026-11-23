"""회사 인스타그램 연결 상태와 프로필 주소 (read, 2026-11-23).

마케팅 › SNS·미디어 › SNS 계정 연결 카드가 "연결됨" 옆에 프로필 링크를 보이려고 쓴다.
- 로그인한 회사의 토큰·계정 ID(`instagram_graph._company_secret`)로
  Instagram API `me?fields=username`을 읽는다
- 결과: missing_keys(토큰·계정 ID 없음) · invalid_credentials(토큰 거절·만료)
  · ok(username·프로필 주소) · error
- 회사마다 60초 기억한다. 토큰 값은 응답·로그에 넣지 않는다
"""

from __future__ import annotations

import logging
import re
import time
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger(__name__)

STATUS_TTL = 60.0
PROFILE_BASE = "https://www.instagram.com/"
_USERNAME = re.compile(r"^[A-Za-z0-9._]{1,30}$")
# 토큰이 거절되거나 만료됐을 때 Graph API가 주는 코드
_AUTH_CODES = {102, 190, 10, 200}
_cache: dict[str, tuple[float, dict[str, Any]]] = {}


def _company() -> str:
    from ax_agent_template import company_ai

    return company_ai.company_id() or ""


def _out(connection: str, username: str | None = None) -> dict[str, Any]:
    url = f"{PROFILE_BASE}{username}/" if username else None
    return {
        "connection": connection,
        "username": username,
        "profile_url": url,
        "checked_at": datetime.now(UTC).isoformat(),
    }


async def probe() -> dict[str, Any]:
    from marketing_agent.sns_publish.instagram import IgApiError, IgResponseLost
    from marketing_agent.sns_publish.instagram_graph import GraphConfigError, GraphInstagramAdapter

    try:
        adapter = GraphInstagramAdapter.from_env()
    except GraphConfigError:
        return _out("missing_keys")
    try:
        body = await adapter._request("GET", "me", params={"fields": "username"})  # noqa: SLF001
    except IgApiError as error:
        code = getattr(error, "code", None)
        return _out("invalid_credentials" if code in _AUTH_CODES else "error")
    except IgResponseLost:
        return _out("error")
    name = str(body.get("username") or "")
    return _out("ok", name if _USERNAME.fullmatch(name) else None)


async def status(force: bool = False) -> dict[str, Any]:
    """회사마다 60초 기억한다."""
    cid = _company()
    now = time.monotonic()
    hit = _cache.get(cid)
    if hit and not force and now - hit[0] < STATUS_TTL:
        return hit[1]
    try:
        value = await probe()
    except Exception as error:  # noqa: BLE001 - 상태 조회는 화면을 막지 않는다
        logger.warning("instagram status 실패: %s", type(error).__name__)
        value = _out("error")
    _cache[cid] = (now, value)
    return value


def clear_cache() -> None:
    _cache.clear()
