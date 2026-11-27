"""회사마다 자기 인스타그램 계정을 로그인으로 연결(Meta 통합 로그인)과 60일 토큰 자동 연장.

가짜 Meta API(graph.facebook.com·graph.instagram.com)와 더미 값만 쓴다. 받은 토큰이 어느 API용인지
`/me`로 판정하는 두 경로(ig·fb)를 모두 본다.
"""

# ruff: noqa: F811 - 회사 AI 경로 테스트의 준비(fixture)를 그대로 쓴다

from __future__ import annotations

import asyncio
import json
import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from fastapi.testclient import TestClient

from ax_agent_template import instagram_link
from ax_contracts.schemas.company_ai import COMPANY_AI_KEY_NAMES
from ax_dashboard_api import company_ai, oauth_link
from ax_dashboard_api import instagram_oauth as io
from ax_secret_store import get_secret, resolver

from .test_company_ai_routes import ROWS, _people, client, env, people  # noqa: F401

BASE = "/api/dashboard/marketing/instagram"
CONNECT, CALLBACK, COMPLETE = f"{BASE}/connect", f"{BASE}/callback", f"{BASE}/complete"
DISCONNECT, CONNECTION = f"{BASE}/disconnect", f"{BASE}/connection"
SHORT, LONG, NEWER = "DUMMY-SHORT-0001", "DUMMY-LONG-0001", "DUMMY-LONG-0002"
SECRET = "DUMMY-APP-SECRET"
IG_ID = "17841400000000009"


class Meta:
    """가짜 Meta. `kind`가 ig면 graph.instagram.com만, fb면 graph.facebook.com만 토큰을 받는다."""

    def __init__(self) -> None:
        self.kind = "ig"
        self.calls: list[tuple[str, dict[str, str]]] = []
        self.refresh: httpx.Response | None = None
        self.business = False  # 비즈니스용 시스템 사용자 토큰
        self.lifetime: int | None = None  # 코드 교환 응답의 expires_in
        self.exchange: httpx.Response | None = None  # 코드 교환 응답을 통째로 바꾼다
        self.assigned: httpx.Response | None = None
        self.accounts: list[dict[str, Any]] = [{"id": IG_ID, "username": "nubitour_lab"}]
        self.me: dict[str, Any] | None = None  # graph.facebook.com/me 응답을 통째로 바꾼다
        self.debug: dict[str, Any] | None = None  # debug_token의 data
        self.pages: list[dict[str, Any]] = [
            {"id": "p1", "instagram_business_account": {"id": IG_ID, "username": "catbooks.lab"}}
        ]

    def handle(self, request: httpx.Request) -> httpx.Response:
        url = urlparse(str(request.url))
        q = {k: v[0] for k, v in parse_qs(url.query).items()}
        where = f"{url.netloc}{url.path}"
        self.calls.append((where, q))
        token = q.get("access_token") or q.get("fb_exchange_token")
        if where.endswith("/oauth/access_token") and "code" in q:
            assert q["client_secret"] == SECRET and q["client_id"] == "1000"
            if self.exchange is not None:
                return self.exchange
            body: dict[str, Any] = {"access_token": SHORT, "token_type": "bearer"}
            if self.lifetime:
                body["expires_in"] = self.lifetime
            return httpx.Response(200, json=body)
        if where == "graph.instagram.com/me":
            if self.kind != "ig":
                return httpx.Response(400, json={"error": {"code": 190}})
            return httpx.Response(200, json={"user_id": IG_ID, "username": "catbooks.lab"})
        if where.endswith("/me") and "graph.facebook.com" in where:
            if self.kind != "fb":
                return httpx.Response(400, json={"error": {"code": 190}})
            if self.me is not None:
                return httpx.Response(200, json=self.me)
            if q.get("fields") == "client_business_id" and self.business:
                return httpx.Response(200, json={"client_business_id": "900", "id": "su1"})
            return httpx.Response(200, json={"id": "fbuser1"})
        if where.endswith("/debug_token") and self.debug is not None:
            assert q["input_token"] == SHORT and q["access_token"] == f"1000|{SECRET}"
            return httpx.Response(200, json={"data": self.debug})
        if where.endswith(f"/{IG_ID}"):
            return httpx.Response(200, json={"id": IG_ID, "username": "nubitour_lab"})
        if where.endswith("/me/assigned_instagram_accounts"):
            assert q["access_token"] == SHORT
            return self.assigned or httpx.Response(200, json={"data": self.accounts})
        if where == "graph.instagram.com/access_token":
            assert q["grant_type"] == "ig_exchange_token" and token == SHORT
            return httpx.Response(200, json={"access_token": LONG, "expires_in": 5184000})
        if where.endswith("/oauth/access_token") and q.get("grant_type") == "fb_exchange_token":
            if self.refresh is not None and token == LONG:
                return self.refresh
            return httpx.Response(200, json={"access_token": LONG if token == SHORT else NEWER,
                                             "expires_in": 5184000})  # fmt: skip
        if where.endswith("/me/accounts"):
            assert token == LONG
            return httpx.Response(200, json={"data": self.pages})
        if where == "graph.instagram.com/refresh_access_token":
            assert q["grant_type"] == "ig_refresh_token"
            return self.refresh or httpx.Response(
                200, json={"access_token": NEWER, "expires_in": 5184000}
            )
        return httpx.Response(404)


@pytest.fixture
def meta(monkeypatch: pytest.MonkeyPatch, env: Path) -> Meta:  # noqa: F811
    monkeypatch.setenv(io.APP_ID_ENV, "1000")
    monkeypatch.setenv(io.APP_SECRET_ENV, SECRET)
    monkeypatch.setenv(io.CONFIG_ID_ENV, "2000")
    monkeypatch.setenv(io.REDIRECT_ENV, "https://app.example.test/dashboard-api" + CALLBACK)
    fake = Meta()
    monkeypatch.setattr(
        io, "CLIENT", lambda: httpx.AsyncClient(transport=httpx.MockTransport(fake.handle))
    )
    return fake


def _start(client: TestClient, who: dict[str, str]) -> str:
    res = client.post(CONNECT, headers=who)
    assert res.status_code == 200, res.text
    return parse_qs(urlparse(res.json()["data"]["url"]).query)["state"][0]


def _connect(client: TestClient, who: dict[str, str]) -> dict[str, Any]:
    state = _start(client, who)
    done = client.post(COMPLETE, json={"code": "c1#_", "state": state}, headers=who)
    assert done.status_code == 200, done.text
    return done.json()["data"]


def test_token_name_is_a_company_key_name_and_an_oauth_name() -> None:
    assert io.TOKEN_NAME in COMPANY_AI_KEY_NAMES and io.TOKEN_NAME.endswith("_REFRESH_TOKEN")
    assert io.TOKEN_NAME != "MARKETING_IG_ACCESS_TOKEN"  # 직접 넣는 토큰과 따로다


def test_connect_builds_the_facebook_dialog_with_config_id(
    client: TestClient,
    people: Any,
    meta: Meta,  # noqa: F811
) -> None:
    assert client.post(CONNECT).status_code == 401
    assert client.post(CONNECT, headers=people["a-3"]).status_code == 403  # 회원은 못 한다
    url = client.post(CONNECT, headers=people["a-1"]).json()["data"]["url"]
    parsed = urlparse(url)
    assert parsed.netloc == "www.facebook.com" and parsed.path.endswith("/dialog/oauth")
    q = parse_qs(parsed.query)
    assert q["client_id"] == ["1000"] and q["config_id"] == ["2000"]
    assert q["response_type"] == ["code"] and "scope" not in q and "display" not in q
    assert q["redirect_uri"][0].endswith(CALLBACK)
    assert oauth_link.read_state(q["state"][0], io.PROVIDER).company_id == "catbooks"
    again = client.post(CONNECT, json={"switch": True}, headers=people["a-1"]).json()["data"]["url"]
    assert parse_qs(urlparse(again).query)["auth_type"] == ["reauthenticate"]
    assert SECRET not in url and meta.calls == []


def test_ig_token_is_judged_and_stored_with_its_api(
    client: TestClient,
    people: Any,
    meta: Meta,
    caplog: pytest.LogCaptureFixture,  # noqa: F811
) -> None:
    caplog.set_level(logging.DEBUG)
    state = _start(client, people["a-1"])
    back = client.get(CALLBACK, params={"state": state, "code": "c1#_"})
    assert "instagram=finish#" in back.text and "instagram_code=c1&" in back.text.replace(
        "&amp;", "&"
    )
    assert (
        meta.calls == [] and get_secret(io.TOKEN_NAME, tenant="catbooks") == ""
    )  # 콜백은 안 바꾼다

    done = client.post(
        COMPLETE, json={"code": "c1#_", "state": state}, headers=people["a-1"]
    ).json()["data"]
    assert meta.calls[0][1]["code"] == "c1"  # 끝의 #_를 지웠다
    assert done["kind"] == "ig" and done["username"] == "catbooks.lab"
    assert done["profile_url"] == "https://www.instagram.com/catbooks.lab/"
    assert get_secret(io.TOKEN_NAME, tenant="catbooks") == LONG
    assert get_secret(io.TOKEN_NAME, tenant="nubi") == ""  # 다른 회사는 따로
    info = instagram_link.load("catbooks")
    assert (info["kind"], info["user_id"]) == ("ig", IG_ID)
    assert instagram_link.api_of(info) == ("https://graph.instagram.com", "")
    view = client.get(CONNECTION, headers=people["a-1"]).json()["data"]
    assert view["connected"] is True and view["needs_reconnect"] is False and view["expires_at"]
    for secret in (SHORT, LONG, SECRET):
        assert secret not in json.dumps(view) and secret not in json.dumps(done)
        assert secret not in caplog.text

    out = client.post(DISCONNECT, headers=people["a-1"])
    assert out.status_code == 200 and get_secret(io.TOKEN_NAME, tenant="catbooks") == ""
    assert instagram_link.load("catbooks") == {}
    assert client.post(DISCONNECT, headers=people["a-1"]).status_code == 409


def test_fb_token_finds_the_business_account_on_graph_facebook(
    client: TestClient,
    people: Any,
    meta: Meta,  # noqa: F811
) -> None:
    meta.kind = "fb"
    done = _connect(client, people["a-1"])
    assert done["kind"] == "fb" and done["username"] == "catbooks.lab"
    info = instagram_link.load("catbooks")
    assert info["user_id"] == IG_ID and info["api_version"] == io.DEFAULT_VERSION
    assert instagram_link.api_of(info) == ("https://graph.facebook.com", io.DEFAULT_VERSION)
    grants = [q.get("grant_type") for _, q in meta.calls]
    assert "fb_exchange_token" in grants and "ig_exchange_token" not in grants
    assert get_secret(io.TOKEN_NAME, tenant="catbooks") == LONG


def test_fb_without_a_linked_instagram_account_stores_nothing(
    client: TestClient,
    people: Any,
    meta: Meta,  # noqa: F811
) -> None:
    meta.kind, meta.pages = "fb", [{"id": "p1"}]
    state = _start(client, people["a-1"])
    res = client.post(COMPLETE, json={"code": "c", "state": state}, headers=people["a-1"])
    assert res.status_code == 409 and res.json()["error"]["code"] == "NO_INSTAGRAM_ACCOUNT"
    assert get_secret(io.TOKEN_NAME, tenant="catbooks") == ""


def test_unknown_token_kind_stores_nothing(client: TestClient, people: Any, meta: Meta) -> None:  # noqa: F811
    meta.kind = "neither"
    state = _start(client, people["a-1"])
    res = client.post(COMPLETE, json={"code": "c", "state": state}, headers=people["a-1"])
    assert res.status_code == 424 and res.json()["error"]["code"] == "INSTAGRAM_FAILED"
    assert SHORT not in res.text and get_secret(io.TOKEN_NAME, tenant="catbooks") == ""


def test_business_token_is_kept_and_finds_the_assigned_account(
    client: TestClient,
    people: Any,
    meta: Meta,  # noqa: F811
) -> None:
    meta.kind, meta.business = "fb", True
    done = _connect(client, people["a-1"])
    assert done["kind"] == "fb" and done["username"] == "nubitour_lab"
    assert done["expires_at"] is None and done["auto_renew"] is False
    assert done["needs_reconnect"] is False
    info = instagram_link.load("catbooks")
    assert info["business"] is True and info["user_id"] == IG_ID
    assert instagram_link.api_of(info) == ("https://graph.facebook.com", io.DEFAULT_VERSION)
    assert get_secret(io.TOKEN_NAME, tenant="catbooks") == SHORT  # 바꾸지 않고 그대로
    assert "fb_exchange_token" not in [q.get("grant_type") for _, q in meta.calls]
    assert not [w for w, _ in meta.calls if w.endswith("/me/accounts")]
    before = len(meta.calls)
    assert asyncio.run(io.refresh_due_once()) == {"catbooks": "skipped"}
    assert len(meta.calls) == before


def test_business_token_with_expiry_is_not_renewed_but_expires(
    client: TestClient,
    people: Any,
    meta: Meta,  # noqa: F811
) -> None:
    meta.kind, meta.business, meta.lifetime = "fb", True, 5184000
    done = _connect(client, people["a-1"])
    assert datetime.fromisoformat(done["expires_at"]) > datetime.now(UTC) + timedelta(days=50)
    before = len(meta.calls)
    _expire_in(3)
    assert asyncio.run(io.refresh_due_once()) == {"catbooks": "skipped"}
    _expire_in(-1)
    assert asyncio.run(io.refresh_due_once()) == {"catbooks": "expired"}
    assert len(meta.calls) == before
    assert client.get(CONNECTION, headers=people["a-1"]).json()["data"]["needs_reconnect"] is True


def test_business_token_without_an_assigned_account_stores_nothing(
    client: TestClient,
    people: Any,
    meta: Meta,  # noqa: F811
) -> None:
    meta.kind, meta.business, meta.accounts = "fb", True, []
    state = _start(client, people["a-1"])
    res = client.post(COMPLETE, json={"code": "c", "state": state}, headers=people["a-1"])
    assert res.status_code == 409 and res.json()["error"]["code"] == "NO_INSTAGRAM_ACCOUNT"
    assert get_secret(io.TOKEN_NAME, tenant="catbooks") == ""


@pytest.mark.parametrize("step", ["exchange", "assigned"])
def test_failed_step_says_which_one_with_meta_numbers_only(
    client: TestClient,
    people: Any,
    meta: Meta,  # noqa: F811
    step: str,
) -> None:
    meta.kind, meta.business = "fb", True
    error = {"code": 100, "error_subcode": 36008, "message": f"bad {SECRET} {SHORT}"}
    setattr(meta, step, httpx.Response(400, json={"error": error}))
    state = _start(client, people["a-1"])
    res = client.post(COMPLETE, json={"code": "c", "state": state}, headers=people["a-1"])
    assert res.status_code == 424
    message = res.json()["error"]["message"]
    what = {"exchange": "토큰을", "assigned": "인스타 계정 목록을"}[step]
    assert what in message and "Meta 오류 100, 하위 36008" in message
    assert SECRET not in res.text and SHORT not in res.text
    assert get_secret(io.TOKEN_NAME, tenant="catbooks") == ""


def _granted(app_id: str = "1000", expires_at: int = 0) -> dict[str, Any]:
    return {
        "app_id": app_id,
        "is_valid": True,
        "type": "SYSTEM_USER",
        "expires_at": expires_at,
        "scopes": ["instagram_basic", "instagram_content_publish"],
        "granular_scopes": [{"scope": "instagram_content_publish", "target_ids": [IG_ID]}],
    }


def test_me_without_id_still_finds_the_assigned_account(
    client: TestClient,
    people: Any,
    meta: Meta,  # noqa: F811
) -> None:
    meta.kind, meta.me = "fb", {}
    done = _connect(client, people["a-1"])
    assert done["username"] == "nubitour_lab" and done["auto_renew"] is False
    assert instagram_link.load("catbooks")["business"] is True
    assert get_secret(io.TOKEN_NAME, tenant="catbooks") == SHORT


def test_account_is_found_from_the_token_grant_when_the_list_is_empty(
    client: TestClient,
    people: Any,
    meta: Meta,
    caplog: pytest.LogCaptureFixture,  # noqa: F811
) -> None:
    caplog.set_level(logging.DEBUG)
    later = int((datetime.now(UTC) + timedelta(days=60)).timestamp())
    meta.kind, meta.me, meta.accounts = "fb", {}, []
    meta.debug = _granted(expires_at=later)
    done = _connect(client, people["a-1"])
    assert done["username"] == "nubitour_lab"
    assert instagram_link.load("catbooks")["user_id"] == IG_ID
    assert int(datetime.fromisoformat(done["expires_at"]).timestamp()) == later
    assert SHORT not in caplog.text and SECRET not in caplog.text  # debug_token 주소도 가린다


@pytest.mark.parametrize("app_id", ["1000", "9999"])
def test_nothing_found_reports_names_without_values(
    client: TestClient,
    people: Any,
    meta: Meta,  # noqa: F811
    app_id: str,
) -> None:
    meta.kind, meta.me, meta.accounts = "fb", {"name": "비밀 이름"}, []
    meta.debug = _granted(app_id) | {"granular_scopes": []}
    state = _start(client, people["a-1"])
    res = client.post(COMPLETE, json={"code": "c", "state": state}, headers=people["a-1"])
    assert res.status_code == 409 and res.json()["error"]["code"] == "NO_INSTAGRAM_ACCOUNT"
    message = res.json()["error"]["message"]
    assert "instagram 오류 190" in message and "facebook 200[name]" in message
    if app_id == "1000":
        assert "토큰 종류 SYSTEM_USER" in message and "instagram_content_publish" in message
    else:  # 다른 앱의 토큰 정보는 믿지 않는다
        assert "SYSTEM_USER" not in message and "토큰 정보 200" in message
    for hidden in (SHORT, SECRET, IG_ID, "비밀 이름"):
        assert hidden not in res.text
    assert get_secret(io.TOKEN_NAME, tenant="catbooks") == ""


def test_manual_user_id_that_differs_is_flagged(
    client: TestClient,
    people: Any,
    meta: Meta,
    monkeypatch: pytest.MonkeyPatch,  # noqa: F811
) -> None:
    monkeypatch.setattr(io, "_manual_user_id", lambda cid: "123")
    assert _connect(client, people["a-1"])["user_id_mismatch"] is True
    monkeypatch.setattr(io, "_manual_user_id", lambda cid: IG_ID)
    assert _connect(client, people["a-1"])["user_id_mismatch"] is False


def test_someone_else_cannot_finish_and_forged_or_old_state_is_refused(
    client: TestClient,
    people: Any,
    meta: Meta,  # noqa: F811
) -> None:
    state = _start(client, people["a-1"])
    other = client.post(COMPLETE, json={"code": "c", "state": state}, headers=people["a-2"])
    assert other.status_code == 403 and other.json()["error"]["code"] == "NOT_LINK_OWNER"
    nubi = oauth_link.make_state(io.PROVIDER, "nubi", "a-1")
    res = client.post(COMPLETE, json={"code": "c", "state": nubi}, headers=people["a-1"])
    assert res.status_code == 403
    youtube = oauth_link.make_state("youtube", "catbooks", "a-1")
    res = client.post(COMPLETE, json={"code": "c", "state": youtube}, headers=people["a-1"])
    assert res.status_code == 400 and res.json()["error"]["code"] == "BAD_STATE"
    forged = state[:-3] + "AAA"
    assert "instagram=bad_state" in client.get(CALLBACK, params={"state": forged, "code": "c"}).text
    old = oauth_link.make_state(io.PROVIDER, "catbooks", "a-1", now=1.0)
    assert "instagram=bad_state" in client.get(CALLBACK, params={"state": old, "code": "c"}).text
    assert "instagram=denied" in client.get(CALLBACK, params={"state": state, "error": "x"}).text
    xss = client.get(CALLBACK, params={"state": state, "code": '"><script>alert(1)</script>'})
    assert "<script>" not in xss.text
    assert meta.calls == [] and get_secret(io.TOKEN_NAME, tenant="catbooks") == ""


def test_without_app_settings_the_screen_says_so(client: TestClient, people: Any) -> None:  # noqa: F811
    res = client.post(CONNECT, headers=people["a-1"])
    assert res.status_code == 409 and res.json()["error"]["code"] == "INSTAGRAM_APP_MISSING"
    view = client.get(CONNECTION, headers=people["a-1"]).json()["data"]
    assert view["app_configured"] is False and view["connected"] is False


# ---------------------------------------------------------------- 자동 연장
def _expire_in(days: float) -> None:
    info = instagram_link.load("catbooks")
    info["expires_at"] = (datetime.now(UTC) + timedelta(days=days)).isoformat()
    instagram_link.save("catbooks", info)


def test_refresh_only_when_expiry_is_near_ig(client: TestClient, people: Any, meta: Meta) -> None:  # noqa: F811
    _connect(client, people["a-1"])
    assert asyncio.run(io.refresh_due_once()) == {"catbooks": "skipped"}  # 60일 남았다
    _expire_in(3)
    assert asyncio.run(io.refresh_due_once()) == {"catbooks": "refreshed"}
    resolver.invalidate()
    assert get_secret(io.TOKEN_NAME, tenant="catbooks") == NEWER
    info = instagram_link.load("catbooks")
    assert info["refresh_error"] is None and instagram_link.expires_at(info) > datetime.now(
        UTC
    ) + timedelta(days=50)
    assert ("graph.instagram.com/refresh_access_token") in [w for w, _ in meta.calls]


def test_refresh_fb_uses_fb_exchange(client: TestClient, people: Any, meta: Meta) -> None:  # noqa: F811
    meta.kind = "fb"
    _connect(client, people["a-1"])
    _expire_in(2)
    assert asyncio.run(io.refresh_due_once()) == {"catbooks": "refreshed"}
    resolver.invalidate()
    assert get_secret(io.TOKEN_NAME, tenant="catbooks") == NEWER
    assert "graph.instagram.com/refresh_access_token" not in [w for w, _ in meta.calls]


def test_refresh_failure_is_shown_as_reconnect_needed(
    client: TestClient, people: Any, meta: Meta
) -> None:  # noqa: F811
    _connect(client, people["a-1"])
    _expire_in(1)
    meta.refresh = httpx.Response(400, json={"error": {"code": 190}})
    assert asyncio.run(io.refresh_due_once()) == {"catbooks": "failed"}
    assert get_secret(io.TOKEN_NAME, tenant="catbooks") == LONG  # 그대로 둔다
    view = client.get(CONNECTION, headers=people["a-1"]).json()["data"]
    assert view["refresh_error"] == "invalid_token" and view["needs_reconnect"] is True


def test_expired_token_is_not_refreshed(client: TestClient, people: Any, meta: Meta) -> None:  # noqa: F811
    _connect(client, people["a-1"])
    _expire_in(-1)
    before = len(meta.calls)
    assert asyncio.run(io.refresh_due_once()) == {"catbooks": "expired"}
    assert len(meta.calls) == before
    assert client.get(CONNECTION, headers=people["a-1"]).json()["data"]["needs_reconnect"] is True


def test_stale_token_is_not_overwritten(
    client: TestClient,
    people: Any,
    meta: Meta,
    monkeypatch: pytest.MonkeyPatch,  # noqa: F811
) -> None:
    _connect(client, people["a-1"])
    _expire_in(3)
    # 연장 응답이 오는 사이 다른 곳이 먼저 토큰을 바꿨다
    company_ai._store().store_oauth_token("catbooks", io.TOKEN_NAME, "DUMMY-OTHER", "x")  # noqa: SLF001
    monkeypatch.setattr(io, "_token", lambda cid: LONG)
    assert asyncio.run(io.refresh_due_once()) == {"catbooks": "stale"}
    resolver.invalidate()
    assert get_secret(io.TOKEN_NAME, tenant="catbooks") == "DUMMY-OTHER"
