"""노션 계정 연결 (OAuth, 공개 통합). 사람은 버튼 → 노션 로그인 → 페이지 고르기만 한다.

- 회사에 한 번: 노션 개발자 설정에서 공개(Public) 통합을 만들고 돌아오기 주소를 등록한 뒤
  `PLANNING_NOTION_CLIENT_ID`·`PLANNING_NOTION_CLIENT_SECRET`을 서버 .env에 넣는다.
  돌아오기 주소는 `PLANNING_NOTION_REDIRECT_URI`
  (기본 http://localhost:8101/api/planning/notion/oauth/callback).
- 시작(`start_url`)은 관리자만 (api.py `ADMIN`).
  한 번 쓰는 state를 10분 동안 기억해 위조 요청을 막는다.
- 돌아오면(`exchange`) 코드를 토큰으로 바꿔 notion_store에 저장한다.
  토큰은 화면으로 돌려주지 않는다.
- 끝나면 화면 연결 페이지로 보낸다 (`PLANNING_WEB_URL`, 기본 http://localhost:3090).
"""

from __future__ import annotations

import base64
import logging
import os
import re
import secrets
import time
from urllib.parse import urlencode, urlparse

import httpx

from planning_agent.notion_import import API, NotionError

log = logging.getLogger(__name__)

AUTHORIZE = f"{API}/oauth/authorize"
TOKEN = f"{API}/oauth/token"
STATE_TTL = 600.0
DEFAULT_REDIRECT = "http://localhost:8101/api/planning/notion/oauth/callback"

_states: dict[str, float] = {}


def client_id() -> str:
    return os.getenv("PLANNING_NOTION_CLIENT_ID", "").strip()


def _secret() -> str:
    return os.getenv("PLANNING_NOTION_CLIENT_SECRET", "").strip()


def _safe_url(url: str) -> bool:
    """https만 허용한다. 개발용 localhost·127.0.0.1만 http를 허용한다."""
    u = urlparse(url)
    if u.scheme == "https" and u.netloc:
        return True
    return u.scheme == "http" and (u.hostname or "") in ("localhost", "127.0.0.1")


def ready() -> bool:
    """회사가 공개 통합을 등록해 두었는지 (둘 다 있어야 계정 연결 버튼이 켜진다).
    돌아오기·화면 주소가 안전하지 않으면(실서버 http) 켜지 않는다."""
    urls_ok = _safe_url(redirect_uri()) and _safe_url(web_url("x"))
    return bool(client_id() and _secret()) and urls_ok


def redirect_uri() -> str:
    return os.getenv("PLANNING_NOTION_REDIRECT_URI", "").strip() or DEFAULT_REDIRECT


def web_url(result: str, reason: str = "") -> str:
    base = os.getenv("PLANNING_WEB_URL", "").strip() or "http://localhost:3090"
    tail = f"&reason={reason}" if reason else ""
    return f"{base.rstrip('/')}/connections?notion={result}{tail}"


_SAFE = re.compile(r"[^a-z0-9_]")


def safe_code(code: str) -> str:
    """화면 주소에 붙여도 되는 오류 코드 (영소문자·숫자·밑줄, 40자)"""
    return _SAFE.sub("", (code or "").lower())[:40]


def _oauth_error(res: httpx.Response) -> tuple[str, str]:
    try:
        body = res.json()
    except ValueError:
        return "", ""
    if not isinstance(body, dict):
        return "", ""
    code = safe_code(str(body.get("error") or body.get("code") or ""))
    desc = " ".join(str(body.get("error_description") or body.get("message") or "").split())
    return code, desc[:160]


def start_url() -> str:
    """노션 동의 화면 주소. 한 번 쓰는 state를 붙인다."""
    if not ready():
        raise NotionError("NOTION_OAUTH_OFF", "노션 공개 통합이 등록돼 있지 않습니다")
    now = time.monotonic()
    for k, t in list(_states.items()):
        if now - t > STATE_TTL:
            _states.pop(k, None)
    state = secrets.token_urlsafe(24)
    _states[state] = now
    query = {
        "client_id": client_id(),
        "response_type": "code",
        "owner": "user",
        "redirect_uri": redirect_uri(),
        "state": state,
    }
    return f"{AUTHORIZE}?{urlencode(query)}"


def take_state(state: str) -> bool:
    """state가 우리가 낸 것이고 10분 안이면 True (한 번만 쓴다)."""
    t = _states.pop(state or "", None)
    return t is not None and time.monotonic() - t <= STATE_TTL


async def exchange(code: str, client: httpx.AsyncClient | None = None) -> tuple[str, str]:
    """코드를 토큰으로 바꾼다 → (토큰, 워크스페이스 이름)."""
    basic = base64.b64encode(f"{client_id()}:{_secret()}".encode()).decode()
    headers = {"Authorization": f"Basic {basic}", "Content-Type": "application/json"}
    body = {"grant_type": "authorization_code", "code": code, "redirect_uri": redirect_uri()}
    own = client is None
    http = client or httpx.AsyncClient(timeout=15.0)
    try:
        res = await http.post(TOKEN, headers=headers, json=body)
    except httpx.HTTPError as exc:
        raise NotionError("NOTION_UNREACHABLE", "노션에 연결하지 못했습니다") from exc
    finally:
        if own:
            await http.aclose()
    if res.status_code >= 400:
        # 노션이 준 오류 코드만 남긴다 (토큰·코드·비밀 값은 남기지 않는다)
        code, desc = _oauth_error(res)
        log.warning("notion oauth token exchange failed: %s %s %s", res.status_code, code, desc)
        raise NotionError(
            "NOTION_OAUTH_FAILED", f"노션 계정 연결 실패 ({res.status_code} {code})".strip(), code
        )
    data = res.json()
    token = str(data.get("access_token") or "")
    if not token:
        raise NotionError("NOTION_OAUTH_FAILED", "노션이 토큰을 주지 않았습니다")
    return token, str(data.get("workspace_name") or "노션")


def reset_for_tests() -> None:
    _states.clear()
