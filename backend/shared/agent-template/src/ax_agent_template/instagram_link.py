"""회사 인스타그램 로그인 연결의 공용 이름과 표시값 파일 (2026-11-23).

dashboard-api(`instagram_oauth.py`, 연결·연장·끊기)가 쓰고 마케팅(게시 어댑터·상태)이 읽는다.
두 쪽이 서로 import하지 않도록 이름·파일 모양을 여기 한 곳에 둔다.

- 토큰: 회사 키 저장소(tenant=회사 ID)의 `MARKETING_IG_OAUTH_REFRESH_TOKEN`. 로그인 연결
  (Meta 통합 로그인)로 받은 60일 장기 토큰이다. 연장할 때마다 값이 바뀌어
  `store_oauth_token(expect=...)`로 넣는다(이름이 `_REFRESH_TOKEN`으로 끝나야 그 길을 쓴다).
  사람이 직접 넣던 `MARKETING_IG_ACCESS_TOKEN`과 따로다
- 토큰 종류(`kind`)는 연결할 때 판정해 회사마다 남긴다(Meta 통합 로그인이 어느 쪽 토큰을 줄지
  미리 알 수 없어서다, 11/23). `ig`면 graph.instagram.com, `fb`(Facebook 사용자 토큰)면
  graph.facebook.com/<버전>만 쓴다. 두 API의 토큰을 섞지 않는다(`api_of`)
- 표시값(비밀 아님): 회사 AI 폴더의 `instagram_login.json`(0600). 토큰 종류·API 버전·인스타 계정 ID
  (`user_id`, 게시 주소에 쓴다)·@username·만료 시각·연결한 사람·연장 실패 사유. 토큰 값은 넣지
  않는다
"""

from __future__ import annotations

import json
import logging
import os
import re
from datetime import UTC, datetime
from typing import Any

from ax_agent_template import company_ai

TOKEN_NAME = "MARKETING_IG_OAUTH_REFRESH_TOKEN"
API_BASE = "https://graph.instagram.com"
FB_API_BASE = "https://graph.facebook.com"
INFO_FILE = "instagram_login.json"


_SECRET_QUERY = re.compile(
    r"((?:access_token|client_secret|fb_exchange_token|code)=)[^&\s\"']+", re.IGNORECASE
)


class _RedactQuery(logging.Filter):
    """httpx 요청 로그(`HTTP Request: GET <주소>`)의 쿼리 속 토큰·앱 비밀·코드를 가린다.

    Meta Graph의 GET은 토큰을 쿼리로 받아서, httpx가 INFO로 남기는 주소에 값이 그대로 실린다."""

    def filter(self, record: logging.LogRecord) -> bool:
        try:
            text = record.getMessage()
        except Exception:  # noqa: BLE001 - 로그 모양이 달라도 로그는 막지 않는다
            return True
        hidden = _SECRET_QUERY.sub(r"\1***", text)
        if hidden != text:
            record.msg, record.args = hidden, None
        return True


def redact_http_logs() -> None:
    """httpx 로거에 가리개를 한 번만 단다(여러 번 불러도 하나)."""
    target = logging.getLogger("httpx")
    if not any(isinstance(f, _RedactQuery) for f in target.filters):
        target.addFilter(_RedactQuery())


def load(cid: str | None) -> dict[str, Any]:
    """이 회사의 로그인 연결 표시값. 없거나 깨졌으면 빈 dict."""
    where = company_ai.folder(cid) if cid else None
    if where is None:
        return {}
    try:
        raw = json.loads((where / INFO_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return raw if isinstance(raw, dict) else {}


def save(cid: str, info: dict[str, Any]) -> None:
    """표시값을 통째로 바꿔 쓴다(임시 파일 → 바꿔치기, 0600). 폴더 설정이 없으면 그냥 둔다."""
    where = company_ai.folder(cid)
    if where is None:
        return
    where.mkdir(parents=True, exist_ok=True)
    path = where / INFO_FILE
    tmp = path.with_suffix(".json.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(json.dumps(info, ensure_ascii=False))
    os.replace(tmp, path)


def drop(cid: str) -> None:
    where = company_ai.folder(cid)
    if where is not None:
        (where / INFO_FILE).unlink(missing_ok=True)


def expires_at(info: dict[str, Any]) -> datetime | None:
    raw = info.get("expires_at")
    if not isinstance(raw, str):
        return None
    try:
        at = datetime.fromisoformat(raw)
    except ValueError:
        return None
    return at if at.tzinfo else at.replace(tzinfo=UTC)


def api_of(info: dict[str, Any]) -> tuple[str, str]:
    """이 회사 토큰을 쓸 API (주소, 버전). 연결할 때 판정해 둔 값(`kind`)을 따른다.

    - `ig`: Instagram 로그인 토큰 → graph.instagram.com(버전 없이)
    - `fb`: 통합 로그인의 Facebook 사용자 토큰·비즈니스용 토큰 → graph.facebook.com/<버전>
    """
    if info.get("kind") == "fb":
        version = str(info.get("api_version") or "").strip("/")
        return FB_API_BASE, version
    return API_BASE, ""


def needs_reconnect(info: dict[str, Any], now: datetime | None = None) -> bool:
    """만료됐거나 연장이 거절됐으면 사람이 다시 연결해야 한다."""
    at = expires_at(info)
    if at is not None and at <= (now or datetime.now(UTC)):
        return True
    return info.get("refresh_error") in ("expired", "invalid_token")
