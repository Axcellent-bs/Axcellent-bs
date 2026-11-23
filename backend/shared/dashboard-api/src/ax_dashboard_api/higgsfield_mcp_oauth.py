"""회사마다 자기 Higgsfield 계정 연결 (원격 MCP + OAuth, 구독 크레딧, 2026-11-23).

마케팅 › SNS·미디어 › SNS 계정 연결의 [Higgsfield 연결 (구독 크레딧)]. 지금까지는 Higgsfield를
API 키(`MARKETING_HIGGSFIELD_API_KEY`, API 크레딧)로 불렀다. 이제 회사 관리자가 **자기 회사의**
Higgsfield 계정으로 동의하면, 마케팅이 그 계정의 구독 크레딧으로 그림·영상을 만든다. 회사마다
계정이 다르다(여러 회사가 한 계정을 같이 쓰는 길은 없다).

- Higgsfield 원격 MCP(`https://mcp.higgsfield.ai/mcp`)의 OAuth(공개 클라이언트, PKCE S256 필수,
  `resource`=MCP 주소). AXcellent 클라이언트는 한 번 등록해 두었다(서버 설정
  `MARKETING_HIGGSFIELD_MCP_CLIENT_ID`·`MARKETING_HIGGSFIELD_MCP_REDIRECT_URI`, 비밀값 아님)
- 순서는 유튜브(`youtube_oauth.py`)와 같다. 회사·사람 묶기는 `oauth_link.py`
  1) `POST /marketing/higgsfield/connect`(관리자 세션): 동의 화면 주소. state에 회사·시작한 계정을
     서명해 넣고, 주소에는 PKCE challenge만 싣는다(verifier는 서버가 state로 다시 만든다)
  2) `GET /marketing/higgsfield/callback`(로그인 없이 열림): state 서명만 보고 코드를 바꾸지 않은 채
     화면으로 돌려보낸다(코드·state는 주소의 `#` 뒤에만)
  3) `POST /marketing/higgsfield/complete`(관리자 세션): 연결을 시작한 계정·회사 = 지금 로그인한
     계정·회사일 때만 코드를 토큰으로 바꾼다. refresh token만 그 회사 키 저장소
     (`MARKETING_HIGGSFIELD_MCP_REFRESH_TOKEN`)에 넣고 access token은 버린다(마케팅이 필요할 때
     갱신한다. 갱신해서 토큰이 바뀌면 마케팅이 `store_oauth_token(expect=...)`으로 바꿔 넣는다)
- 화면용 표시값(동의한 Higgsfield 계정 이메일·연결한 사람·시각)은 회사 AI 폴더의
  `higgsfield_mcp.json`에 둔다(비밀값 아님, 0600). 이메일은 토큰 응답의 id_token에서 읽는다
  (Higgsfield 토큰 주소에서 TLS로 바로 받은 값이라 서명 검증 없이 표시에만 쓴다)
- 값(코드·토큰)은 로그·응답·이력에 넣지 않는다. 이력에는 "Higgsfield 연결/끊기"와 누가만 남긴다
- 끊기(`POST /marketing/higgsfield/disconnect`)는 관리자만. 키 저장소와 표시값을 지운다.
  Higgsfield 쪽 앱 권한은 Higgsfield 계정 설정에서 지울 수 있다(화면 안내)
"""

from __future__ import annotations

import base64
import json
import os
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Header, Query
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from ax_dashboard_api import company_ai, oauth_link
from ax_dashboard_api.envelope import ApiError, ok

router = APIRouter(prefix="/api/dashboard")

PROVIDER = "higgsfield_mcp"
CLIENT_ID_ENV = "MARKETING_HIGGSFIELD_MCP_CLIENT_ID"
REDIRECT_ENV = "MARKETING_HIGGSFIELD_MCP_REDIRECT_URI"
TOKEN_NAME = "MARKETING_HIGGSFIELD_MCP_REFRESH_TOKEN"
AUTH_URL = "https://mcp.higgsfield.ai/oauth2/authorize"
TOKEN_URL = "https://mcp.higgsfield.ai/oauth2/token"
RESOURCE = "https://mcp.higgsfield.ai/mcp"
SCOPES = ("openid", "email", "offline_access")
INFO_FILE = "higgsfield_mcp.json"
BACK = "/marketing/sns"
MAX_EMAIL = 254
# 테스트에서 바꿔 끼운다
CLIENT = lambda: httpx.AsyncClient(timeout=20)  # noqa: E731


def _configured() -> tuple[str, str]:
    client_id = oauth_link.app_value(CLIENT_ID_ENV)
    redirect = oauth_link.app_value(REDIRECT_ENV)
    if not (client_id and redirect):
        raise ApiError(
            409,
            "HIGGSFIELD_APP_MISSING",
            "AXcellent Higgsfield 연결 설정(MARKETING_HIGGSFIELD_MCP_CLIENT_ID·REDIRECT_URI)이"
            " 서버에 없어요",
        )
    return client_id, redirect


def _history(company_id: str, action: str, who: str) -> None:
    folder = company_ai._folder(company_id)  # noqa: SLF001 - 같은 회사 AI 이력 파일을 쓴다
    if folder is None:
        return
    folder.mkdir(parents=True, exist_ok=True)
    entry = {"by": who[:64], "action": action, "name": TOKEN_NAME}
    company_ai._log(folder, entry)  # noqa: SLF001


def _email_of(id_token: object) -> str | None:
    """id_token(JWT) 가운데 칸의 `email`. 표시용이라 서명은 보지 않는다. 못 읽으면 None."""
    if not isinstance(id_token, str) or id_token.count(".") != 2:
        return None
    body = id_token.split(".")[1]
    try:
        claims = json.loads(base64.urlsafe_b64decode(body + "=" * (-len(body) % 4)))
    except (ValueError, TypeError):
        return None
    email = claims.get("email") if isinstance(claims, dict) else None
    if not isinstance(email, str) or "@" not in email or len(email) > MAX_EMAIL:
        return None
    return email


def _save_info(company_id: str, email: str | None, who: str) -> None:
    folder = company_ai._folder(company_id)  # noqa: SLF001
    if folder is None:
        return
    folder.mkdir(parents=True, exist_ok=True)
    info = {"email": email, "connected_by": who[:64], "connected_at": datetime.now(UTC).isoformat()}
    path = folder / INFO_FILE
    tmp = path.with_suffix(".json.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(json.dumps(info, ensure_ascii=False))
    os.replace(tmp, path)


def _load_info(company_id: str) -> dict[str, Any]:
    folder = company_ai._folder(company_id)  # noqa: SLF001
    if folder is None:
        return {}
    try:
        raw = json.loads((folder / INFO_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return raw if isinstance(raw, dict) else {}


def _drop_info(company_id: str) -> None:
    folder = company_ai._folder(company_id)  # noqa: SLF001
    if folder is not None:
        (folder / INFO_FILE).unlink(missing_ok=True)


@router.post("/marketing/higgsfield/connect")
def higgsfield_connect(authorization: str | None = Header(default=None)) -> dict:
    """회사 관리자가 누르는 [Higgsfield 연결]: Higgsfield 동의 화면 주소(화면이 그 주소로 이동)."""
    human = company_ai._admin(authorization)  # noqa: SLF001 - 관리자 세션만
    company_id = company_ai._need_company()  # noqa: SLF001
    if not human.account_id:
        raise ApiError(401, "SESSION_REQUIRED", "로그인한 계정으로만 연결할 수 있어요")
    client_id, redirect = _configured()
    state = oauth_link.make_state(PROVIDER, company_id, human.account_id)
    query = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect,
        "scope": " ".join(SCOPES),
        "state": state,
        "code_challenge": oauth_link.pkce_challenge(oauth_link.pkce_verifier(state)),
        "code_challenge_method": "S256",
        "resource": RESOURCE,
    }
    return ok({"url": f"{AUTH_URL}?{urlencode(query)}", "company_id": company_id})


def _back(result: str) -> HTMLResponse:
    return oauth_link.back_page(f"{BACK}?higgsfield={result}", result, "Higgsfield")


@router.get("/marketing/higgsfield/callback", response_model=None)
def higgsfield_callback(
    state: str = Query(default="", max_length=2000),
    code: str = Query(default="", max_length=2000),
    error: str = Query(default="", max_length=200),
) -> HTMLResponse:
    """Higgsfield가 돌려보내는 주소. 토큰은 바꾸지 않고 화면으로 넘긴다(마치기는 `complete`)."""
    if oauth_link.read_state(state, PROVIDER) is None:
        return _back("bad_state")
    if error or not code:
        return _back("denied")
    fragment = urlencode({"higgsfield_code": code, "higgsfield_state": state})
    return oauth_link.back_page(f"{BACK}?higgsfield=finish#{fragment}", "finish", "Higgsfield")


class CompleteBody(BaseModel):
    code: str = Field(min_length=1, max_length=2000)
    state: str = Field(min_length=1, max_length=2000)


async def _exchange(code: str, state: str, client_id: str, redirect: str) -> dict[str, Any]:
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "redirect_uri": redirect,
        "client_id": client_id,
        "code_verifier": oauth_link.pkce_verifier(state),
        "resource": RESOURCE,
    }
    try:
        async with CLIENT() as client:
            res = await client.post(TOKEN_URL, data=data)
        body = res.json() if res.status_code == 200 else None
    except (httpx.HTTPError, ValueError) as err:
        raise ApiError(502, "HIGGSFIELD_FAILED", "Higgsfield에서 토큰을 받지 못했어요") from err
    if not isinstance(body, dict):
        raise ApiError(502, "HIGGSFIELD_FAILED", "Higgsfield에서 토큰을 받지 못했어요")
    return body


@router.post("/marketing/higgsfield/complete")
async def higgsfield_complete(
    body: CompleteBody, authorization: str | None = Header(default=None)
) -> dict:
    """화면이 부르는 연결 마치기. 시작한 계정·회사와 같을 때만 그 회사 키 저장소에 넣는다."""
    human = company_ai._admin(authorization)  # noqa: SLF001
    company_id = company_ai._need_company()  # noqa: SLF001
    oauth_link.require_owner(body.state, PROVIDER, human, company_id)
    client_id, redirect = _configured()
    got = await _exchange(body.code, body.state, client_id, redirect)
    token = got.get("refresh_token")
    if not isinstance(token, str) or not token:
        raise ApiError(
            502,
            "NO_REFRESH_TOKEN",
            "Higgsfield가 갱신 토큰을 주지 않았어요. 다시 연결해 주세요",
        )
    svc = company_ai._store()  # noqa: SLF001
    try:
        svc.store_oauth_token(company_id, TOKEN_NAME, token, f"higgsfield_oauth:{human.name}")
    except Exception as err:  # noqa: BLE001 - 값은 오류에 넣지 않는다
        raise ApiError(503, "STORE_FAILED", "키 저장소에 넣지 못했어요") from err
    from ax_secret_store import resolver

    resolver.invalidate()
    email = _email_of(got.get("id_token"))
    _save_info(company_id, email, human.name)
    _history(company_id, "higgsfield_connect", human.name)
    return ok({"company_id": company_id, "connected": True, "email": email})


@router.post("/marketing/higgsfield/disconnect")
def higgsfield_disconnect(authorization: str | None = Header(default=None)) -> dict:
    """회사 Higgsfield 계정 연결 끊기(관리자). 키 저장소의 refresh token과 표시값을 지운다."""
    human = company_ai._admin(authorization)  # noqa: SLF001
    company_id = company_ai._need_company()  # noqa: SLF001
    svc = company_ai._store()  # noqa: SLF001
    try:
        req = svc.stage("delete", company_id, TOKEN_NAME, None, human.name)
        svc.apply(req["id"], human.name)
    except Exception as err:  # noqa: BLE001
        raise ApiError(409, "NOT_CONNECTED", "연결된 Higgsfield 계정이 없어요") from err
    from ax_secret_store import resolver

    resolver.invalidate()
    _drop_info(company_id)
    _history(company_id, "higgsfield_disconnect", human.name)
    return ok({"company_id": company_id, "connected": False})


def connection(company_id: str) -> dict[str, Any]:
    """화면용: 앱 설정이 있는지, 이 회사가 연결했는지, 누구 계정인지(값 없이)."""
    from ax_secret_store import get_secret, is_db_mode

    has = False
    if is_db_mode():  # env 모드에는 회사별 칸이 없다(설치 하나의 값을 여러 회사가 같이 쓰지 않게)
        try:
            has = bool(get_secret(TOKEN_NAME, tenant=company_id).strip())
        except Exception:  # noqa: BLE001
            has = False
    info = _load_info(company_id) if has else {}
    app = all(oauth_link.app_value(n) for n in (CLIENT_ID_ENV, REDIRECT_ENV))
    return {
        "app_configured": app,
        "connected": has,
        "email": info.get("email") if isinstance(info.get("email"), str) else None,
        "connected_by": info.get("connected_by"),
        "connected_at": info.get("connected_at"),
    }


@router.get("/marketing/higgsfield/connection")
def higgsfield_connection(authorization: str | None = Header(default=None)) -> dict:
    """회사 Higgsfield 계정 연결 상태(관리자). 크레딧·요금제는 `/marketing/higgsfield/status`."""
    company_ai._admin(authorization)  # noqa: SLF001
    company_id = company_ai._need_company()  # noqa: SLF001
    return ok({"company_id": company_id, **connection(company_id)})
