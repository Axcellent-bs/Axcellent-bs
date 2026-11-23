"""회사마다 자기 인스타그램 계정을 로그인으로 연결 (Meta 통합 로그인, 2026-11-23).

마케팅 › SNS·미디어 › SNS 계정 연결의 [인스타그램 연결]. 지금까지는 회사 관리자가 Meta에서
받은 토큰·계정 ID를 직접 넣었다(`MARKETING_IG_ACCESS_TOKEN`·`_USER_ID`, 그대로 남는다).
이제 관리자가 로그인으로 동의하면 장기 토큰(60일)을 받아 그 회사 키 저장소에 넣고,
만료 전에 저절로 연장한다.

- AXcellent Meta 앱은 `instagram.com/oauth/authorize`를 쓸 수 없고 통합 로그인(Facebook 로그인
  + 구성 `config_id`)만 된다(11/23 Meta 콘솔 확인). 동의 주소는 `facebook.com/<버전>/dialog/oauth`
  에 client_id·config_id·redirect_uri·response_type=code·state만 싣는다(권한은 config_id가 정해
  scope를 넣지 않는다). 구성의 기본 응답이 토큰이어도 코드로 받게
  `override_default_response_type=true`를 붙인다. `display=popup`은 붙이지 않는다(같은 창 이동)
- 앱 값 `MARKETING_IG_APP_ID`(Facebook 앱 ID)·`_APP_SECRET`·`_CONFIG_ID`·`_REDIRECT_URI`는
  키 저장소 기본 칸 → 서버 환경값 순(`oauth_link.app_value`). 코드에 값을 두지 않는다.
  없으면 화면에 "앱 설정 없음"
- 순서는 유튜브(`youtube_oauth.py`)·Higgsfield(`higgsfield_mcp_oauth.py`)와 같다(회사·사람 묶기는
  `oauth_link.py`)
  1) `POST /marketing/instagram/connect`(관리자 세션): 동의 화면 주소(state에 회사·시작한 계정)
  2) `GET /marketing/instagram/callback`(로그인 없이 열림): state 서명만 보고 코드를 바꾸지 않은 채
     화면으로 돌려보낸다(코드·state는 주소의 `#` 뒤에만). 코드 끝 `#_`는 지운다
  3) `POST /marketing/instagram/complete`(관리자 세션): 시작한 계정·회사 = 지금 로그인한 계정·회사일
     때만 코드를 `graph.facebook.com/<버전>/oauth/access_token`으로 토큰으로 바꾼다
- 받은 토큰의 종류는 미리 알 수 없어서 두 곳의 `/me`를 차례로 물어 판정하고(`_kind`), 회사별로
  남긴다(`instagram_link`의 `kind`). 이후 장기 토큰·연장·게시는 그 판정을 따른다
  - `ig`(graph.instagram.com이 받음): `ig_exchange_token`으로 장기 토큰, 연장은 `ig_refresh_token`
  - `fb`(Facebook 사용자 토큰): `fb_exchange_token`으로 장기 토큰, 인스타 비즈니스 계정은
    `/me/accounts?fields=instagram_business_account{id,username}`에서 찾는다. 연장은 장기 토큰으로
    `fb_exchange_token`을 다시 부른다(거절되면 다시 연결)
- 장기 토큰은 `MARKETING_IG_OAUTH_REFRESH_TOKEN`(`store_oauth_token`), 표시값은
  `instagram_login.json`
- 직접 넣은 `MARKETING_IG_USER_ID`가 있는데 찾은 계정 ID와 다르면 화면에 알린다(`user_id_mismatch`)
- 자동 연장(`refresh_due_once`, 서버 주기 작업): 만료 `REFRESH_BEFORE`(7일) 안이면 새 토큰을 받아
  `store_oauth_token(expect=지금 값)`으로 바꾼다. `stale_token`(다른 곳이 먼저 바꿈)이면 다음 주기에
  다시 읽는다. 실패는 표시값 `refresh_error`에 남기고 화면이 "다시 연결 필요"를 보인다
- 끊기(`POST /marketing/instagram/disconnect`, 관리자): 키 저장소의 토큰과 표시값을 지운다
- 값(코드·토큰·앱 비밀)은 로그·응답·이력에 넣지 않는다. 토큰·비밀이 쿼리에 실리는 GET은 실패해도
  예외 사슬을 남기지 않는다(예외 문자열에 주소가 들어가지 않게)
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import urlencode

import httpx
from fastapi import APIRouter, Header, Query
from fastapi.responses import HTMLResponse
from pydantic import BaseModel, Field

from ax_agent_template import instagram_link
from ax_dashboard_api import company_ai, oauth_link
from ax_dashboard_api.envelope import ApiError, ok

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/api/dashboard")
instagram_link.redact_http_logs()  # httpx 요청 로그의 쿼리 토큰·앱 비밀을 가린다

PROVIDER = "instagram"
APP_ID_ENV = "MARKETING_IG_APP_ID"
APP_SECRET_ENV = "MARKETING_IG_APP_SECRET"
CONFIG_ID_ENV = "MARKETING_IG_CONFIG_ID"
REDIRECT_ENV = "MARKETING_IG_REDIRECT_URI"
APP_ENVS = (APP_ID_ENV, APP_SECRET_ENV, CONFIG_ID_ENV, REDIRECT_ENV)
VERSION_ENV = "MARKETING_IG_FB_API_VERSION"
DEFAULT_VERSION = "v21.0"
MANUAL_USER_ID = "MARKETING_IG_USER_ID"
TOKEN_NAME = instagram_link.TOKEN_NAME
IG_API = instagram_link.API_BASE
FB_API = instagram_link.FB_API_BASE
BACK = "/marketing/sns"
REFRESH_BEFORE = timedelta(days=7)
REFRESH_INTERVAL_ENV = "MARKETING_IG_REFRESH_INTERVAL_SECONDS"
REFRESH_INTERVAL_DEFAULT = 6 * 3600
LONG_TTL_DEFAULT = 60 * 24 * 3600  # 응답에 expires_in이 없을 때(문서: 60일)
_USERNAME = re.compile(r"^[A-Za-z0-9._]{1,30}$")
_USER_ID = re.compile(r"^[0-9]{1,30}$")
_VERSION = re.compile(r"^v[0-9]{1,3}\.[0-9]{1,2}$")
# 테스트에서 바꿔 끼운다
CLIENT = lambda: httpx.AsyncClient(timeout=20)  # noqa: E731


def _version() -> str:
    raw = (os.getenv(VERSION_ENV) or "").strip()
    return raw if _VERSION.fullmatch(raw) else DEFAULT_VERSION


def _fb(path: str, version: str | None = None) -> str:
    return f"{FB_API}/{version or _version()}/{path.lstrip('/')}"


def _configured() -> dict[str, str]:
    values = {n: oauth_link.app_value(n) for n in APP_ENVS}
    if not all(values.values()):
        raise ApiError(
            409,
            "INSTAGRAM_APP_MISSING",
            "AXcellent 인스타그램 앱 설정(MARKETING_IG_APP_ID·APP_SECRET·CONFIG_ID·REDIRECT_URI)이"
            " 서버에 없어요",
        )
    return values


def _history(company_id: str, action: str, who: str) -> None:
    folder = company_ai._folder(company_id)  # noqa: SLF001 - 같은 회사 AI 이력 파일을 쓴다
    if folder is None:
        return
    folder.mkdir(parents=True, exist_ok=True)
    company_ai._log(folder, {"by": who[:64], "action": action, "name": TOKEN_NAME})  # noqa: SLF001


def _clean_code(code: str) -> str:
    """Meta가 돌려주는 코드 끝의 `#_`(주소 꼬리)를 지운다."""
    code = code.strip()
    return code[:-2] if code.endswith("#_") else code


# ---------------------------------------------------------------- 1) 시작
class ConnectBody(BaseModel):
    switch: bool = False  # [다른 계정으로 다시 연결]: 로그인을 다시 묻는다


@router.post("/marketing/instagram/connect")
def instagram_connect(
    body: ConnectBody | None = None, authorization: str | None = Header(default=None)
) -> dict:
    """회사 관리자가 누르는 [인스타그램 연결]: Meta 동의 화면 주소(화면이 그 주소로 이동)."""
    human = company_ai._admin(authorization)  # noqa: SLF001 - 관리자 세션만
    company_id = company_ai._need_company()  # noqa: SLF001
    if not human.account_id:
        raise ApiError(401, "SESSION_REQUIRED", "로그인한 계정으로만 연결할 수 있어요")
    app = _configured()
    query = {
        "client_id": app[APP_ID_ENV],
        "config_id": app[CONFIG_ID_ENV],
        "redirect_uri": app[REDIRECT_ENV],
        "response_type": "code",
        "override_default_response_type": "true",
        "state": oauth_link.make_state(PROVIDER, company_id, human.account_id),
    }
    if body is not None and body.switch:
        query["auth_type"] = "reauthenticate"
    url = f"https://www.facebook.com/{_version()}/dialog/oauth?{urlencode(query)}"
    return ok({"url": url, "company_id": company_id})


# ---------------------------------------------------------------- 2) 돌아오기
def _back(result: str) -> HTMLResponse:
    return oauth_link.back_page(f"{BACK}?instagram={result}", result, "인스타그램")


@router.get("/marketing/instagram/callback", response_model=None)
def instagram_callback(
    state: str = Query(default="", max_length=2000),
    code: str = Query(default="", max_length=2000),
    error: str = Query(default="", max_length=200),
) -> HTMLResponse:
    """Meta가 돌려보내는 주소. 토큰은 바꾸지 않고 화면으로 넘긴다(마치기는 `complete`)."""
    if oauth_link.read_state(state, PROVIDER) is None:
        return _back("bad_state")
    code = _clean_code(code)
    if error or not code:
        return _back("denied")
    fragment = urlencode({"instagram_code": code, "instagram_state": state})
    return oauth_link.back_page(f"{BACK}?instagram=finish#{fragment}", "finish", "인스타그램")


# ---------------------------------------------------------------- 3) 마치기
class CompleteBody(BaseModel):
    code: str = Field(min_length=1, max_length=2000)
    state: str = Field(min_length=1, max_length=2000)


def _fail(what: str) -> ApiError:
    return ApiError(502, "INSTAGRAM_FAILED", f"Meta에서 {what}을(를) 받지 못했어요")


async def _get_json(client: httpx.AsyncClient, url: str, params: dict[str, str]) -> dict[str, Any]:
    """토큰·비밀이 쿼리에 실리는 GET. 실패하면 `_status`=0, 예외 사슬을 남기지 않는다."""
    try:
        res = await client.get(url, params=params)
        body = res.json()
    except (httpx.HTTPError, ValueError):
        return {"_status": 0}
    out = body if isinstance(body, dict) else {}
    return out | {"_status": res.status_code}


def _expiry(body: dict[str, Any]) -> str:
    seconds = body.get("expires_in")
    if not isinstance(seconds, int) or isinstance(seconds, bool) or seconds <= 0:
        seconds = LONG_TTL_DEFAULT
    return (datetime.now(UTC) + timedelta(seconds=seconds)).isoformat()


def _name(raw: object) -> str | None:
    return raw if isinstance(raw, str) and _USERNAME.fullmatch(raw) else None


async def _kind(client: httpx.AsyncClient, token: str) -> tuple[str, dict[str, Any]]:
    """받은 토큰의 종류: graph.instagram.com이 받으면 `ig`, graph.facebook.com이 받으면 `fb`."""
    ig = await _get_json(
        client, f"{IG_API}/me", {"fields": "user_id,username", "access_token": token}
    )
    if ig.get("_status") == 200 and (ig.get("user_id") or ig.get("id")):
        return "ig", ig
    fb = await _get_json(client, _fb("me"), {"fields": "id", "access_token": token})
    if fb.get("_status") == 200 and fb.get("id"):
        return "fb", fb
    raise _fail("쓸 수 있는 토큰")


async def _ig_long(client: httpx.AsyncClient, token: str, secret: str) -> dict[str, Any]:
    got = await _get_json(
        client,
        f"{IG_API}/access_token",
        {"grant_type": "ig_exchange_token", "client_secret": secret, "access_token": token},
    )
    if got.get("_status") != 200 or not isinstance(got.get("access_token"), str):
        raise _fail("장기 토큰")
    return got


async def _fb_long(client: httpx.AsyncClient, token: str, app: dict[str, str]) -> dict[str, Any]:
    got = await _get_json(
        client,
        _fb("oauth/access_token"),
        {
            "grant_type": "fb_exchange_token",
            "client_id": app[APP_ID_ENV],
            "client_secret": app[APP_SECRET_ENV],
            "fb_exchange_token": token,
        },
    )
    if got.get("_status") != 200 or not isinstance(got.get("access_token"), str):
        raise _fail("장기 토큰")
    return got


async def _fb_account(client: httpx.AsyncClient, token: str) -> dict[str, Any]:
    """Facebook 페이지에 연결된 인스타 비즈니스 계정(첫 번째)."""
    got = await _get_json(
        client,
        _fb("me/accounts"),
        {"fields": "instagram_business_account{id,username}", "access_token": token},
    )
    pages = got.get("data") if got.get("_status") == 200 else None
    for page in pages if isinstance(pages, list) else []:
        acct = page.get("instagram_business_account") if isinstance(page, dict) else None
        if isinstance(acct, dict) and _USER_ID.fullmatch(str(acct.get("id") or "")):
            return {"user_id": str(acct["id"]), "username": _name(acct.get("username"))}
    raise ApiError(
        409,
        "NO_INSTAGRAM_ACCOUNT",
        "이 Facebook 계정의 페이지에 연결된 인스타그램 비즈니스 계정을 찾지 못했어요",
    )


async def _exchange(code: str, app: dict[str, str]) -> tuple[str, str, dict[str, Any]]:
    """코드 → (장기 토큰, 만료 시각, {kind, api_version, user_id, username})."""
    async with CLIENT() as client:
        got = await _get_json(
            client,
            _fb("oauth/access_token"),
            {
                "client_id": app[APP_ID_ENV],
                "client_secret": app[APP_SECRET_ENV],
                "redirect_uri": app[REDIRECT_ENV],
                "code": _clean_code(code),
            },
        )
        short = got.get("access_token")
        if got.get("_status") != 200 or not isinstance(short, str) or not short:
            raise _fail("토큰")
        kind, me = await _kind(client, short)
        if kind == "ig":
            long = await _ig_long(client, short, app[APP_SECRET_ENV])
            user_id = str(me.get("user_id") or me.get("id") or "")
            if not _USER_ID.fullmatch(user_id):
                raise _fail("계정 정보")
            profile = {"kind": "ig", "api_version": "", "user_id": user_id,
                       "username": _name(me.get("username"))}  # fmt: skip
        else:
            long = await _fb_long(client, short, app)
            acct = await _fb_account(client, long["access_token"])
            profile = {"kind": "fb", "api_version": _version(), **acct}
    return long["access_token"], _expiry(long), profile


def _manual_user_id(company_id: str) -> str:
    from ax_secret_store import get_secret

    try:
        return (get_secret(MANUAL_USER_ID, tenant=company_id) or "").strip()
    except Exception:  # noqa: BLE001 - 비교용이라 못 읽으면 비교하지 않는다
        return ""


@router.post("/marketing/instagram/complete")
async def instagram_complete(
    body: CompleteBody, authorization: str | None = Header(default=None)
) -> dict:
    """화면이 부르는 연결 마치기. 시작한 계정·회사와 같을 때만 그 회사 키 저장소에 넣는다."""
    human = company_ai._admin(authorization)  # noqa: SLF001
    company_id = company_ai._need_company()  # noqa: SLF001
    oauth_link.require_owner(body.state, PROVIDER, human, company_id)
    app = _configured()
    svc = company_ai._store()  # noqa: SLF001 - 키 저장소가 꺼져 있으면 코드를 바꾸기 전에 멈춘다
    token, expires_at, profile = await _exchange(body.code, app)
    try:
        svc.store_oauth_token(company_id, TOKEN_NAME, token, f"instagram_oauth:{human.name}")
    except Exception:  # noqa: BLE001 - 값은 오류에 넣지 않는다
        raise ApiError(503, "STORE_FAILED", "키 저장소에 넣지 못했어요") from None
    from ax_secret_store import resolver

    resolver.invalidate()
    manual = _manual_user_id(company_id)
    info = {
        **profile,
        "expires_at": expires_at,
        "connected_by": human.name[:64],
        "connected_at": datetime.now(UTC).isoformat(),
        "refreshed_at": None,
        "refresh_error": None,
        "user_id_mismatch": bool(manual and manual != profile["user_id"]),
    }
    instagram_link.save(company_id, info)
    _history(company_id, "instagram_connect", human.name)
    return ok({"company_id": company_id, **_view(info, True)})


# ---------------------------------------------------------------- 끊기·상태
@router.post("/marketing/instagram/disconnect")
def instagram_disconnect(authorization: str | None = Header(default=None)) -> dict:
    """회사 인스타 로그인 연결 끊기(관리자). 키 저장소의 토큰과 표시값을 지운다."""
    human = company_ai._admin(authorization)  # noqa: SLF001
    company_id = company_ai._need_company()  # noqa: SLF001
    svc = company_ai._store()  # noqa: SLF001
    try:
        req = svc.stage("delete", company_id, TOKEN_NAME, None, human.name)
        svc.apply(req["id"], human.name)
    except Exception as err:  # noqa: BLE001
        raise ApiError(409, "NOT_CONNECTED", "로그인으로 연결된 인스타그램이 없어요") from err
    from ax_secret_store import resolver

    resolver.invalidate()
    instagram_link.drop(company_id)
    _history(company_id, "instagram_disconnect", human.name)
    return ok({"company_id": company_id, "connected": False})


def _token(company_id: str) -> str:
    from ax_secret_store import get_secret, is_db_mode

    if not is_db_mode():  # env 모드에는 회사별 칸이 없다
        return ""
    try:
        return (get_secret(TOKEN_NAME, tenant=company_id) or "").strip()
    except Exception:  # noqa: BLE001
        return ""


def _view(info: dict[str, Any], connected: bool) -> dict[str, Any]:
    def shown(key: str) -> Any:
        return info.get(key) if connected else None

    name = _name(shown("username"))
    return {
        "app_configured": all(oauth_link.app_value(n) for n in APP_ENVS),
        "connected": connected,
        "kind": shown("kind"),
        "username": name,
        "profile_url": f"https://www.instagram.com/{name}/" if name else None,
        "expires_at": shown("expires_at"),
        "refreshed_at": shown("refreshed_at"),
        "refresh_error": shown("refresh_error"),
        "needs_reconnect": connected and instagram_link.needs_reconnect(info),
        "user_id_mismatch": connected and info.get("user_id_mismatch") is True,
        "connected_by": shown("connected_by"),
        "connected_at": shown("connected_at"),
    }


def connection(company_id: str) -> dict[str, Any]:
    """화면용: 앱 설정·연결 여부·@username·만료일(값 없이)."""
    connected = bool(_token(company_id))
    return _view(instagram_link.load(company_id) if connected else {}, connected)


@router.get("/marketing/instagram/connection")
def instagram_connection(authorization: str | None = Header(default=None)) -> dict:
    """회사 인스타 로그인 연결 상태(관리자). 게시용 확인은 `/marketing/instagram/status`."""
    company_ai._admin(authorization)  # noqa: SLF001
    company_id = company_ai._need_company()  # noqa: SLF001
    return ok({"company_id": company_id, **connection(company_id)})


# ---------------------------------------------------------------- 자동 연장
def _companies() -> list[str]:
    """표시값 파일이 있는 회사(로그인으로 연결한 회사)."""
    root = company_ai.base_dir()
    if root is None or not root.is_dir():
        return []
    return sorted(p.name for p in root.iterdir() if (p / instagram_link.INFO_FILE).is_file())


async def _renew(info: dict[str, Any], current: str) -> dict[str, Any]:
    """판정해 둔 종류대로 새 장기 토큰을 받는다. 응답 그대로(`_status` 포함)."""
    async with CLIENT() as client:
        if info.get("kind") == "fb":
            app = {n: oauth_link.app_value(n) for n in (APP_ID_ENV, APP_SECRET_ENV)}
            if not all(app.values()):
                return {"_status": 0, "_reason": "app_missing"}
            version = str(info.get("api_version") or "") or None
            return await _get_json(
                client,
                _fb("oauth/access_token", version),
                {
                    "grant_type": "fb_exchange_token",
                    "client_id": app[APP_ID_ENV],
                    "client_secret": app[APP_SECRET_ENV],
                    "fb_exchange_token": current,
                },
            )
        return await _get_json(
            client,
            f"{IG_API}/refresh_access_token",
            {"grant_type": "ig_refresh_token", "access_token": current},
        )


def _mark(company_id: str, info: dict[str, Any], code: str) -> None:
    instagram_link.save(company_id, info | {"refresh_error": code})


async def refresh_company(company_id: str, now: datetime | None = None) -> str:
    """한 회사: 만료 7일 안이면 연장한다. 결과: skipped · refreshed · stale · expired · failed."""
    from ax_secret_store import resolver
    from ax_secret_store.errors import SecretStoreError

    now = now or datetime.now(UTC)
    info = instagram_link.load(company_id)
    current = _token(company_id)
    at = instagram_link.expires_at(info)
    if not current or at is None or at - now > REFRESH_BEFORE:
        return "skipped"
    if at <= now:
        _mark(company_id, info, "expired")
        return "expired"
    got = await _renew(info, current)
    new = got.get("access_token")
    if got.get("_status") != 200 or not isinstance(new, str) or not new:
        status = got.get("_status")
        code = got.get("_reason") or (
            "invalid_token" if status in (400, 401, 403) else f"http_{status}"
        )
        _mark(company_id, info, str(code))
        logger.warning("인스타 토큰 연장 실패: %s (%s)", code, company_id)
        return "failed"
    try:
        company_ai._store().store_oauth_token(  # noqa: SLF001
            company_id, TOKEN_NAME, new, "instagram_refresh", expect=current
        )
    except SecretStoreError as err:
        if err.code == "stale_token":  # 다른 곳이 먼저 바꿨다. 다음 주기에 다시 읽는다
            resolver.invalidate()
            return "stale"
        _mark(company_id, info, "store_failed")
        return "failed"
    except ApiError:
        _mark(company_id, info, "store_failed")
        return "failed"
    resolver.invalidate()
    fresh = info | {"expires_at": _expiry(got), "refreshed_at": now.isoformat()}
    instagram_link.save(company_id, fresh | {"refresh_error": None})
    return "refreshed"


async def refresh_due_once(now: datetime | None = None) -> dict[str, str]:
    """로그인으로 연결한 회사마다 한 번. 한 회사의 실패가 다른 회사를 막지 않는다."""
    out: dict[str, str] = {}
    for cid in _companies():
        try:
            out[cid] = await refresh_company(cid, now)
        except Exception as error:  # noqa: BLE001
            logger.warning("인스타 토큰 연장 오류: %s (%s)", type(error).__name__, cid)
            out[cid] = "failed"
    return out


def refresh_interval() -> int:
    try:
        return int((os.getenv(REFRESH_INTERVAL_ENV) or "").strip() or REFRESH_INTERVAL_DEFAULT)
    except ValueError:
        return REFRESH_INTERVAL_DEFAULT


async def refresh_loop() -> None:
    """서버 주기 작업. 0 이하면 돌지 않는다(main이 시작하지 않는다)."""
    await asyncio.sleep(60)
    while True:
        await refresh_due_once()
        await asyncio.sleep(refresh_interval())
