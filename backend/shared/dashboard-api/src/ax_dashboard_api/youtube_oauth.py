"""회사마다 자기 유튜브 채널 연결 (2026-11-23, 마케팅 › SNS·미디어 › SNS 계정 연결).

- AXcellent가 만든 구글 OAuth 앱 하나(서버 설정 `MARKETING_YOUTUBE_CLIENT_ID`·`_CLIENT_SECRET`·
  `MARKETING_YOUTUBE_REDIRECT_URI`)로, 회사 관리자가 [유튜브 연결]을 누르면 자기 채널 주인 계정으로
  구글에 동의한다. 돌아온 refresh token은 그 회사의 키 저장소(tenant=회사 ID,
  `MARKETING_YOUTUBE_REFRESH_TOKEN`)에만 저장한다. 회사마다 채널이 다르다
- 순서 (회사·사람 묶기는 `oauth_link.py`)
  1) `POST /marketing/youtube/connect`(관리자 세션): 동의 화면 주소.
     state에 회사·시작한 계정을 서명해 넣는다
  2) `GET /marketing/youtube/callback`(로그인 없이 열림, 구글이 Authorization을 안 붙인다):
     state 서명만 보고 코드를 바꾸지 않은 채 화면으로 돌려보낸다(코드·state는 주소의 `#` 뒤에만,
     서버 로그·Referer에 안 남게)
  3) `POST /marketing/youtube/complete`(관리자 세션): 화면이 코드·state를 보낸다. 연결을 시작한
     계정·회사 = 지금 로그인한 계정·회사일 때만 코드를 토큰으로 바꿔 넣는다. 다른 사람에게 동의 화면
     주소를 보내 그 사람 채널을 우리 회사에 묶는 일을 막는다
- 값(코드·토큰)은 로그·응답·이력에 넣지 않는다. 이력에는 "유튜브 연결/끊기"와 누가만 남긴다
- 끊기(`POST /marketing/youtube/disconnect`)는 관리자만, 키 저장소에서 지운다
"""

from __future__ import annotations

import html
import os
from datetime import UTC, datetime
from typing import Any
from urllib.parse import quote, urlencode

import httpx
from fastapi import APIRouter, Header, Query
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from ax_dashboard_api import company_ai, oauth_link
from ax_dashboard_api.envelope import ApiError, ok

router = APIRouter(prefix="/api/dashboard")

PROVIDER = "youtube"
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
    human = company_ai._admin(authorization)  # noqa: SLF001 - 관리자 세션만
    company_id = company_ai._need_company()  # noqa: SLF001
    if not human.account_id:
        raise ApiError(401, "SESSION_REQUIRED", "로그인한 계정으로만 연결할 수 있어요")
    client_id, _, redirect = _configured()
    query = {
        "client_id": client_id,
        "redirect_uri": redirect,
        "response_type": "code",
        "scope": " ".join(SCOPES),
        "access_type": "offline",
        "prompt": "consent",  # refresh token을 늘 받는다
        "include_granted_scopes": "true",
        "state": oauth_link.make_state(PROVIDER, company_id, human.account_id),
    }
    return ok({"url": f"{AUTH_URL}?{urlencode(query)}", "company_id": company_id})


def _page(url: str, result: str) -> HTMLResponse:
    """화면으로 돌아가는 작은 페이지. 주소는 이스케이프한다(코드 값이 HTML을 깨지 못하게)."""
    safe = html.escape(url, quote=True)
    body = (
        '<!doctype html><meta charset="utf-8">'
        f'<meta http-equiv="refresh" content="0;url={safe}">'
        f'<p>유튜브 연결: {html.escape(result)}. <a href="{safe}">마케팅 화면으로 돌아가기</a></p>'
    )
    return HTMLResponse(
        body, headers={"Cache-Control": "no-store", "Referrer-Policy": "no-referrer"}
    )


def _back(result: str) -> HTMLResponse:
    return _page(f"{BACK}?youtube={quote(result)}", result)


@router.get("/marketing/youtube/callback", response_model=None)
def youtube_callback(
    state: str = Query(default="", max_length=2000),
    code: str = Query(default="", max_length=2000),
    error: str = Query(default="", max_length=200),
) -> HTMLResponse:
    """구글이 돌려보내는 주소. 토큰은 바꾸지 않고 화면으로 넘긴다(마치기는 `complete`)."""
    if oauth_link.read_state(state, PROVIDER) is None:
        return _back("bad_state")
    if error or not code:
        return _back("denied")
    fragment = urlencode({"youtube_code": code, "youtube_state": state})
    return _page(f"{BACK}?youtube=finish#{fragment}", "finish")


class CompleteBody(BaseModel):
    code: str = Field(min_length=1, max_length=2000)
    state: str = Field(min_length=1, max_length=2000)


@router.post("/marketing/youtube/complete")
async def youtube_complete(
    body: CompleteBody, authorization: str | None = Header(default=None)
) -> dict:
    """화면이 부르는 연결 마치기. 시작한 계정·회사와 같을 때만 토큰을 그 회사 키 저장소에 넣는다."""
    human = company_ai._admin(authorization)  # noqa: SLF001
    company_id = company_ai._need_company()  # noqa: SLF001
    oauth_link.require_owner(body.state, PROVIDER, human, company_id)
    client_id, client_secret, redirect = _configured()
    try:
        async with CLIENT() as client:
            res = await client.post(
                TOKEN_URL,
                data={
                    "code": body.code,
                    "client_id": client_id,
                    "client_secret": client_secret,
                    "redirect_uri": redirect,
                    "grant_type": "authorization_code",
                },
            )
        token = res.json().get("refresh_token") if res.status_code == 200 else None
    except (httpx.HTTPError, ValueError) as err:
        raise ApiError(502, "GOOGLE_FAILED", "구글에서 토큰을 받지 못했어요") from err
    if not token:
        raise ApiError(
            502, "NO_REFRESH_TOKEN", "구글이 갱신 토큰을 주지 않았어요. 다시 연결해 주세요"
        )
    svc = company_ai._store()  # noqa: SLF001
    try:
        svc.store_oauth_token(company_id, TOKEN_NAME, str(token), f"youtube_oauth:{human.name}")
    except Exception as err:  # noqa: BLE001 - 값은 오류에 넣지 않는다
        raise ApiError(503, "STORE_FAILED", "키 저장소에 넣지 못했어요") from err
    from ax_secret_store import resolver

    resolver.invalidate()
    _history(company_id, "youtube_connect", human.name)
    return ok({"company_id": company_id, "connected": True})


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
