"""회사마다 자기 Higgsfield 계정 연결(원격 MCP OAuth, PKCE). 가짜 Higgsfield·더미 값만 쓴다."""

# ruff: noqa: F811 - 회사 AI 경로 테스트의 준비(fixture)를 그대로 쓴다

from __future__ import annotations

import base64
import hashlib
import json
import logging
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from fastapi.testclient import TestClient

from ax_contracts.schemas.company_ai import COMPANY_AI_KEY_NAMES
from ax_dashboard_api import higgsfield_mcp_oauth as hm
from ax_dashboard_api import oauth_link
from ax_secret_store import get_secret

from .test_company_ai_routes import ROWS, _people, client, env, people  # noqa: F401

BASE = "/api/dashboard/marketing/higgsfield"
CONNECT, CALLBACK, COMPLETE = f"{BASE}/connect", f"{BASE}/callback", f"{BASE}/complete"
DISCONNECT, CONNECTION = f"{BASE}/disconnect", f"{BASE}/connection"
REFRESH = "DUMMY-HF-REFRESH-0001"
ACCESS = "DUMMY-HF-ACCESS-0001"


def _id_token(claims: dict[str, Any]) -> str:
    body = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    return f"eyJhbGciOiJub25lIn0.{body}.sig"


@pytest.fixture
def hf(monkeypatch: pytest.MonkeyPatch, env: Path) -> dict[str, Any]:  # noqa: F811
    monkeypatch.setenv(hm.CLIENT_ID_ENV, "DUMMY-PUBLIC-CLIENT")
    monkeypatch.setenv(hm.REDIRECT_ENV, "https://app.example.test/dashboard-api" + CALLBACK)
    seen: dict[str, Any] = {"posts": [], "answer": None}

    def higgsfield(request: httpx.Request) -> httpx.Response:
        assert str(request.url) == hm.TOKEN_URL
        seen["posts"].append({k: v[0] for k, v in parse_qs(request.content.decode()).items()})
        if seen["answer"] is not None:
            return seen["answer"]
        return httpx.Response(
            200,
            json={
                "access_token": ACCESS,
                "refresh_token": REFRESH,
                "expires_in": 3600,
                "id_token": _id_token({"email": "owner@company.test", "sub": "u1"}),
                "scope": "openid email offline_access",
                "token_type": "Bearer",
            },
        )

    monkeypatch.setattr(
        hm, "CLIENT", lambda: httpx.AsyncClient(transport=httpx.MockTransport(higgsfield))
    )
    return seen


def _start(client: TestClient, who: dict[str, str]) -> tuple[str, dict[str, list[str]]]:
    res = client.post(CONNECT, headers=who)
    assert res.status_code == 200, res.text
    query = parse_qs(urlparse(res.json()["data"]["url"]).query)
    return query["state"][0], query


def test_token_name_is_a_company_key_name_and_an_oauth_name() -> None:
    assert hm.TOKEN_NAME in COMPANY_AI_KEY_NAMES
    assert hm.TOKEN_NAME.endswith("_REFRESH_TOKEN")  # store_oauth_token만 받는 끝


def test_connect_sends_only_the_pkce_challenge(
    client: TestClient,
    people: Any,
    hf: dict[str, Any],  # noqa: F811
) -> None:
    assert client.post(CONNECT).status_code == 401
    assert client.post(CONNECT, headers=people["a-3"]).status_code == 403  # 회원은 못 한다
    res = client.post(CONNECT, headers=people["a-1"])
    url = res.json()["data"]["url"]
    assert url.startswith(hm.AUTH_URL + "?")
    query = parse_qs(urlparse(url).query)
    state = query["state"][0]
    verifier = oauth_link.pkce_verifier(state)
    assert query["code_challenge_method"] == ["S256"]
    want = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).decode()
    assert query["code_challenge"] == [want.rstrip("=")]
    assert query["resource"] == [hm.RESOURCE] and query["client_id"] == ["DUMMY-PUBLIC-CLIENT"]
    assert set(query["scope"][0].split()) == {"openid", "email", "offline_access"}
    assert verifier not in url and 43 <= len(verifier) <= 128
    # 서버 비밀이 없으면 같은 state로도 verifier를 만들 수 없다(비밀이 바뀌면 다른 값)
    assert oauth_link.pkce_verifier(state) == verifier
    assert hf["posts"] == []


def test_admin_connects_through_callback_then_complete(
    client: TestClient,
    people: Any,
    hf: dict[str, Any],  # noqa: F811
    caplog: pytest.LogCaptureFixture,
) -> None:
    caplog.set_level(logging.DEBUG)
    state, _ = _start(client, people["a-1"])
    back = client.get(CALLBACK, params={"state": state, "code": "c1"})
    assert back.status_code == 200 and "higgsfield=finish#" in back.text
    assert "higgsfield_code=c1" in back.text.split("#", 1)[1]
    assert hf["posts"] == [] and get_secret(hm.TOKEN_NAME, tenant="catbooks") == ""

    done = client.post(COMPLETE, json={"code": "c1", "state": state}, headers=people["a-1"])
    assert done.status_code == 200, done.text
    sent = hf["posts"][0]
    assert sent["grant_type"] == "authorization_code" and sent["code"] == "c1"
    assert sent["code_verifier"] == oauth_link.pkce_verifier(state)
    assert sent["resource"] == hm.RESOURCE and "client_secret" not in sent
    assert get_secret(hm.TOKEN_NAME, tenant="catbooks") == REFRESH
    assert get_secret(hm.TOKEN_NAME, tenant="nubi") == ""  # 다른 회사는 따로
    assert done.json()["data"]["email"] == "owner@company.test"
    assert REFRESH not in done.text and ACCESS not in done.text

    view = client.get(CONNECTION, headers=people["a-1"]).json()["data"]
    assert view["connected"] is True and view["app_configured"] is True
    assert view["email"] == "owner@company.test" and view["connected_by"] == "민지"
    assert REFRESH not in json.dumps(view)
    assert client.get(CONNECTION, headers=people["a-3"]).status_code == 403

    out = client.post(DISCONNECT, headers=people["a-1"])
    assert out.status_code == 200
    assert get_secret(hm.TOKEN_NAME, tenant="catbooks") == ""
    assert client.get(CONNECTION, headers=people["a-1"]).json()["data"]["email"] is None
    again = client.post(DISCONNECT, headers=people["a-1"])
    assert again.status_code == 409 and again.json()["error"]["code"] == "NOT_CONNECTED"
    for secret in (REFRESH, ACCESS, oauth_link.pkce_verifier(state)):
        assert secret not in caplog.text


def test_someone_else_cannot_finish_a_link_another_admin_started(
    client: TestClient,
    people: Any,
    hf: dict[str, Any],  # noqa: F811
) -> None:
    """관리자 민지가 받은 동의 주소를 다른 관리자·회원·로그인 안 한 사람이 마쳐도 안 들어간다."""
    state, _ = _start(client, people["a-1"])
    other = client.post(COMPLETE, json={"code": "c", "state": state}, headers=people["a-2"])
    assert other.status_code == 403 and other.json()["error"]["code"] == "NOT_LINK_OWNER"
    member = client.post(COMPLETE, json={"code": "c", "state": state}, headers=people["a-3"])
    assert member.status_code == 403
    assert client.post(COMPLETE, json={"code": "c", "state": state}).status_code == 401
    assert hf["posts"] == []  # Higgsfield에 코드를 보내지도 않았다
    assert get_secret(hm.TOKEN_NAME, tenant="catbooks") == ""


def test_state_from_another_company_or_provider_is_refused(
    client: TestClient,
    people: Any,
    hf: dict[str, Any],  # noqa: F811
) -> None:
    nubi = oauth_link.make_state(hm.PROVIDER, "nubi", "a-1")
    res = client.post(COMPLETE, json={"code": "c", "state": nubi}, headers=people["a-1"])
    assert res.status_code == 403 and res.json()["error"]["code"] == "NOT_LINK_OWNER"
    youtube = oauth_link.make_state("youtube", "catbooks", "a-1")
    res = client.post(COMPLETE, json={"code": "c", "state": youtube}, headers=people["a-1"])
    assert res.status_code == 400 and res.json()["error"]["code"] == "BAD_STATE"
    assert "higgsfield=bad_state" in client.get(CALLBACK, params={"state": youtube}).text
    assert hf["posts"] == []


def test_callback_refuses_forged_or_expired_state_and_escapes_the_code(
    client: TestClient,
    hf: dict[str, Any],  # noqa: F811
) -> None:
    forged = oauth_link.make_state(hm.PROVIDER, "catbooks", "a-1")[:-3] + "AAA"
    assert "higgsfield=bad_state" in client.get(CALLBACK, params={"state": forged}).text
    old = oauth_link.make_state(hm.PROVIDER, "catbooks", "a-1", now=1.0)
    assert "higgsfield=bad_state" in client.get(CALLBACK, params={"state": old, "code": "c"}).text
    good = oauth_link.make_state(hm.PROVIDER, "catbooks", "a-1")
    assert "higgsfield=denied" in client.get(CALLBACK, params={"state": good, "error": "x"}).text
    res = client.get(CALLBACK, params={"state": good, "code": '"><script>alert(1)</script>'})
    assert "<script>" not in res.text
    assert res.headers["referrer-policy"] == "no-referrer"
    assert hf["posts"] == []


def test_no_refresh_token_or_higgsfield_error_stores_nothing(
    client: TestClient,
    people: Any,
    hf: dict[str, Any],  # noqa: F811
) -> None:
    state, _ = _start(client, people["a-1"])
    hf["answer"] = httpx.Response(200, json={"access_token": ACCESS, "expires_in": 3600})
    res = client.post(COMPLETE, json={"code": "c", "state": state}, headers=people["a-1"])
    assert res.status_code == 502 and res.json()["error"]["code"] == "NO_REFRESH_TOKEN"
    hf["answer"] = httpx.Response(400, json={"error": "invalid_grant"})
    res = client.post(COMPLETE, json={"code": "c", "state": state}, headers=people["a-1"])
    assert res.status_code == 502 and res.json()["error"]["code"] == "HIGGSFIELD_FAILED"
    assert ACCESS not in res.text
    assert get_secret(hm.TOKEN_NAME, tenant="catbooks") == ""


def test_connect_without_app_settings_says_so(
    client: TestClient,
    people: Any,  # noqa: F811
) -> None:
    res = client.post(CONNECT, headers=people["a-1"])
    assert res.status_code == 409 and res.json()["error"]["code"] == "HIGGSFIELD_APP_MISSING"
    view = client.get(CONNECTION, headers=people["a-1"]).json()["data"]
    assert view == {
        "company_id": "catbooks",
        "app_configured": False,
        "connected": False,
        "email": None,
        "connected_by": None,
        "connected_at": None,
    }


@pytest.mark.parametrize(
    "token",
    [None, "not-a-jwt", "a.!!!.c", _id_token({"sub": "u1"}), _id_token({"email": "x" * 300})],
)
def test_email_is_only_read_from_a_plain_id_token(token: object) -> None:
    assert hm._email_of(token) is None  # noqa: SLF001
