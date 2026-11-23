"""회사마다 자기 유튜브 채널 연결 (2026-11-23, 마케팅 › SNS·미디어 › SNS 계정 연결).

- AXcellent가 만든 구글 OAuth 앱 하나(서버 설정 `MARKETING_YOUTUBE_CLIENT_ID`·`_CLIENT_SECRET`·
  `MARKETING_YOUTUBE_REDIRECT_URI`)로, 회사 관리자가 [유튜브 연결]을 누르면 자기 채널 주인 계정으로
  구글에 동의한다. 돌아온 refresh token은 그 회사의 키 저장소(tenant=회사 ID,
  `MARKETING_YOUTUBE_REFRESH_TOKEN`)에만 저장한다. 회사마다 채널이 다르다
- 동의 화면 주소는 관리자 세션만 받는다(`POST /marketing/youtube/connect`)
- 구글이 돌려보내는 주소(`GET /marketing/youtube/callback`)는 토큰 없이 열려 있다
  (구글이 Authorization을 붙이지 않는다). 대신 서버 비밀(`AX_SESSION_SECRET`)로 서명한
  `state`(회사 ID·만료 10분·임의값)를 확인한다
- 값(코드·토큰)은 로그·응답·이력에 넣지 않는다. 이력에는 "유튜브 연결/끊기"와 누가만 남긴다
- 끊기(`POST /marketing/youtube/disconnect`)는 관리자만, 키 저장소에서 지운다
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import time
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Header, Query
from fastapi.responses import HTMLResponse

from ax_agent_template import human_auth
from ax_dashboard_api import company_ai
from ax_dashboard_api.envelope import ApiError, ok

router = APIRouter(prefix="/api/dashboard")

CLIENT_ID_ENV = "MARKETING_YOUTUBE_CLIENT_ID"
CLIENT_SECRET_ENV = "MARKETING_YOUTUBE_CLIENT_SECRET"
REDIRECT_ENV = "MARKETING_YOUTUBE_REDIRECT_URI"
TOKEN_NAME = "MARKETING_YOUTUBE_REFRESH_TOKEN"
AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
SCOPES = (
    "https://www.googleapis.com/auth/youtube.upload",
    "https://www.googleapis.com/auth/youtube.readonly",
)
STATE_TTL = 600
BACK = "/marketing/sns"
# 테스트에서 바꿔 끼운다
CLIENT = lambda: httpx.AsyncClient(timeout=20)  # noqa: E731


def _app_value(name: str) -> str:
    """앱(AXcellent) 단위 값: 키 저장소 기본 tenant, 없으면 환경값."""
    from ax_secret_store import get_secret

    try:
        return (get_secret(name) or os.getenv(name, "")).strip()
    except Exception:  # noqa: BLE001
        return os.getenv(name, "").strip()


def _secret() -> bytes:
    raw = os.getenv(human_auth.SESSION_SECRET_ENV, "")
    if len(raw) < 32:
        raise ApiError(
            409, "SESSION_SECRET_MISSING", "서버 로그인 설정이 없어 연결을 시작할 수 없어요"
        )
    return raw.encode("utf-8")


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def make_state(company_id: str, now: float | None = None) -> str:
    body = json.dumps(
        {"c": company_id, "e": int((now or time.time()) + STATE_TTL), "n": secrets.token_hex(8)},
        separators=(",", ":"),
    )
    sig = hmac.new(_secret(), body.encode(), hashlib.sha256).digest()
    return f"{_b64(body.encode())}.{_b64(sig)}"


def read_state(state: str, now: float | None = None) -> str | None:
    """서명·만료가 맞으면 회사 ID, 아니면 None."""
    try:
        body_b64, sig_b64 = state.split(".", 1)
        body = _unb64(body_b64)
        want = hmac.new(_secret(), body, hashlib.sha256).digest()
        if not hmac.compare_digest(want, _unb64(sig_b64)):
            return None
        data = json.loads(body)
    except (ValueError, ApiError):
        return None
    if int(data.get("e", 0)) < int(now or time.time()):
        return None
    cid = data.get("c")
    return cid if isinstance(cid, str) and cid else None


def _configured() -> tuple[str, str, str]:
    values = (_app_value(CLIENT_ID_ENV), _app_value(CLIENT_SECRET_ENV), _app_value(REDIRECT_ENV))
    if not all(values):
        raise ApiError(
            409,
            "YOUTUBE_APP_MISSING",
            "AXcellent 유튜브 앱 설정(MARKETING_YOUTUBE_CLIENT_ID·SECRET·REDIRECT_URI)이"
            " 서버에 없어요",
        )
    return values


def _history(company_id: str, action: str, who: str) -> None:
    folder = company_ai._folder(company_id)  # noqa: SLF001 - 같은 회사 AI 이력 파일을 쓴다
    if folder is None:
        return
    folder.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).isoformat()
    entry = {"at": stamp, "by": who[:64], "action": action, "name": TOKEN_NAME}
    company_ai._log(folder, entry)  # noqa: SLF001


@router.post("/marketing/youtube/connect")
def youtube_connect(authorization: str | None = Header(default=None)) -> dict:
    """회사 관리자가 누르는 [유튜브 연결]: 구글 동의 화면 주소를 돌려준다(화면이 그 주소로 이동)."""
    company_ai._admin(authorization)  # noqa: SLF001 - 관리자 세션만
    company_id = company_ai._need_company()  # noqa: SLF001
    client_id, _, redirect = _configured()
    query = {
        "client_id": client_id,
        "redirect_uri": redirect,
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "access_type": "offline",
        "prompt": "consent",  # refresh token을 늘 받는다
        "include_granted_scopes": "true",
        "state": make_state(company_id),
    }
    return ok({"url": f"{AUTH_URL}?{urlencode(query)}", "company_id": company_id})


def _back(result: str) -> HTMLResponse:
    """화면으로 돌아가는 작은 페이지(결과 코드만 붙인다. 코드·토큰은 넣지 않는다)."""
    url = f"{BACK}?youtube={result}"
    body = (
        '<!doctype html><meta charset="utf-8">'
        f'<meta http-equiv="refresh" content="0;url={url}">'
        f'<p>유튜브 연결 결과: {result}. <a href="{url}">마케팅 화면으로 돌아가기</a></p>'
    )
    return HTMLResponse(
        body, headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"}
    )


@router.get("/marketing/youtube/callback", response_model=None)
async def youtube_callback(
    state: str = Query(default="", max_length=2000),
    code: str = Query(default="", max_length=2000),
    error: str = Query(default="", max_length=200),
) -> HTMLResponse:
    """구글이 돌려보내는 주소. 서명한 state의 회사 키 저장소에 refresh token을 넣는다."""
    company_id = read_state(state)
    if company_id is None:
        return _back("bad_state")
    if error or not code:
        return _back("denied")
    try:
        client_id, client_secret, redirect = _configured()
    except ApiError:
        return _back("app_missing")
    try:
        async with CLIENT() as client:
            res = await client.post(
                TOKEN_URL,
                data={
                    "code": code,
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "redirect_uri": redirect,
                    "grant_type": "authorization_code",
                },
            )
        token = res.json().get("refresh_token") if res.status_code == 200 else None
    except (httpx.HTTPError, ValueError):
        return _back("google_failed")
    if not token:
        return _back("no_refresh_token")
    try:
        svc = company_ai._store()  # noqa: SLF001
        svc.import_value(company_id, TOKEN_NAME, str(token), "youtube_oauth", overwrite=True)
        from ax_secret_store import resolver

        resolver.invalidate()
    except Exception:  # noqa: BLE001 - 키 저장소가 꺼졌거나 실패
        return _back("store_failed")
    _history(company_id, "youtube_connect", "youtube_oauth")
    return _back("connected")


@router.post("/marketing/youtube/disconnect")
def youtube_disconnect(authorization: str | None = Header(default=None)) -> dict:
    """회사 유튜브 연결 끊기(관리자). 키 저장소에서 refresh token을 지운다."""
    human = company_ai._admin(authorization)  # noqa: SLF001
    company_id = company_ai._need_company()  # noqa: SLF001
    svc = company_ai._store()  # noqa: SLF001
    try:
        req = svc.stage("delete", company_id, TOKEN_NAME, None, human.name)
        svc.apply(req["id"], human.name)
    except Exception as err:  # noqa: BLE001
        raise ApiError(409, "NOT_CONNECTED", "연결된 유튜브가 없어요") from err
    from ax_secret_store import resolver

    resolver.invalidate()
    _history(company_id, "youtube_disconnect", human.name)
    return ok({"company_id": company_id, "connected": False})


def connection(company_id: str) -> dict[str, Any]:
    """화면용: 앱 설정이 있는지, 이 회사가 연결했는지(값 없이)."""
    from ax_secret_store import get_secret

    try:
        has = bool(get_secret(TOKEN_NAME, tenant=company_id).strip())
    except Exception:  # noqa: BLE001
        has = False
    app = all(_app_value(n) for n in (CLIENT_ID_ENV, CLIENT_SECRET_ENV, REDIRECT_ENV))
    return {"app_configured": app, "company_connected": has}
