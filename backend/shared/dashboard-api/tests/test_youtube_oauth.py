"""회사마다 유튜브 채널 연결(구글 OAuth). 가짜 구글·더미 값만 쓴다."""

# ruff: noqa: F811 - 회사 AI 경로 테스트의 준비(fixture)를 그대로 쓴다

from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from fastapi.testclient import TestClient

from ax_dashboard_api import oauth_link
from ax_dashboard_api import youtube_oauth as yo
from ax_secret_store import get_secret

from .test_company_ai_routes import ROWS, _people, client, env, people  # noqa: F401

CONNECT = "/api/dashboard/marketing/youtube/connect"
CALLBACK = "/api/dashboard/marketing/youtube/callback"
COMPLETE = "/api/dashboard/marketing/youtube/complete"


@pytest.fixture
def app_keys(monkeypatch: pytest.MonkeyPatch, env: Path) -> list[dict[str, Any]]:  # noqa: F811
    monkeypatch.setenv(yo.CLIENT_ID_ENV, "DUMMY-CLIENT-ID")
    monkeypatch.setenv(yo.CLIENT_SECRET_ENV, "DUMMY-CLIENT-SECRET")
    monkeypatch.setenv(yo.REDIRECT_ENV, "https://app.example.test" + CALLBACK)
    posts: list[dict[str, Any]] = []

    def google(request: httpx.Request) -> httpx.Response:
        posts.append(dict(parse_qs(request.content.decode())))
        return httpx.Response(200, json={"refresh_token": "DUMMY-REFRESH", "access_token": "x"})

    monkeypatch.setattr(
        yo, "CLIENT", lambda: httpx.AsyncClient(transport=httpx.MockTransport(google))
    )
    return posts


def _start(client: TestClient, who: dict[str, str]) -> str:
    res = client.post(CONNECT, headers=who)
    assert res.status_code == 200, res.text
    return parse_qs(urlparse(res.json()["data"]["url"]).query)["state"][0]


def test_admin_connects_through_callback_then_complete(
    client: TestClient,
    people: Any,
    app_keys: list[dict[str, Any]],  # noqa: F811
) -> None:
    assert client.post(CONNECT).status_code == 401
    assert client.post(CONNECT, headers=people["a-3"]).status_code == 403  # 회원은 못 한다
    res = client.post(CONNECT, headers=people["a-1"])
    url = res.json()["data"]["url"]
    query = parse_qs(urlparse(url).query)
    assert query["access_type"] == ["offline"] and "youtube.upload" in query["scope"][0]
    assert "DUMMY-CLIENT-SECRET" not in url
    state = query["state"][0]

    # 콜백은 코드를 바꾸지 않고 화면으로 넘기기만 한다 (코드는 # 뒤에만)
    back = client.get(CALLBACK, params={"state": state, "code": "c1"})
    assert back.status_code == 200 and "youtube=finish#" in back.text
    assert "youtube_code=c1" in back.text.split("#", 1)[1]
    assert app_keys == [] and get_secret(yo.TOKEN_NAME, tenant="catbooks") == ""

    done = client.post(COMPLETE, json={"code": "c1", "state": state}, headers=people["a-1"])
    assert done.status_code == 200, done.text
    assert app_keys[0]["code"] == ["c1"]
    assert get_secret(yo.TOKEN_NAME, tenant="catbooks") == "DUMMY-REFRESH"
    assert "DUMMY-REFRESH" not in done.text
    assert yo.connection("catbooks")["company_connected"] is True
    assert get_secret(yo.TOKEN_NAME, tenant="nubi") == ""  # 다른 회사는 따로

    out = client.post("/api/dashboard/marketing/youtube/disconnect", headers=people["a-1"])
    assert out.status_code == 200
    assert get_secret(yo.TOKEN_NAME, tenant="catbooks") == ""


def test_someone_else_cannot_finish_a_link_another_admin_started(
    client: TestClient,
    people: Any,
    app_keys: list[dict[str, Any]],  # noqa: F811
) -> None:
    """회사 관리자 민지가 받은 동의 주소를 다른 사람이 마쳐도 토큰이 들어가지 않는다."""
    state = _start(client, people["a-1"])
    other = client.post(COMPLETE, json={"code": "c", "state": state}, headers=people["a-2"])
    assert other.status_code == 403 and other.json()["error"]["code"] == "NOT_LINK_OWNER"
    member = client.post(COMPLETE, json={"code": "c", "state": state}, headers=people["a-3"])
    assert member.status_code == 403
    nobody = client.post(COMPLETE, json={"code": "c", "state": state})
    assert nobody.status_code == 401
    assert app_keys == []  # 구글에 코드를 보내지도 않았다
    assert get_secret(yo.TOKEN_NAME, tenant="catbooks") == ""


def test_state_from_another_company_or_provider_is_refused(
    client: TestClient,
    people: Any,
    app_keys: list[dict[str, Any]],  # noqa: F811
) -> None:
    nubi = oauth_link.make_state("youtube", "nubi", "a-1")
    res = client.post(COMPLETE, json={"code": "c", "state": nubi}, headers=people["a-1"])
    assert res.status_code == 403 and res.json()["error"]["code"] == "NOT_LINK_OWNER"
    cafe24 = oauth_link.make_state("cafe24", "catbooks", "a-1")
    res = client.post(COMPLETE, json={"code": "c", "state": cafe24}, headers=people["a-1"])
    assert res.status_code == 400 and res.json()["error"]["code"] == "BAD_STATE"
    assert "youtube=bad_state" in client.get(CALLBACK, params={"state": cafe24, "code": "c"}).text
    assert app_keys == []


def test_callback_refuses_forged_or_expired_state(
    client: TestClient,
    app_keys: list[dict[str, Any]],  # noqa: F811
) -> None:
    forged = oauth_link.make_state("youtube", "catbooks", "a-1")[:-3] + "AAA"
    res = client.get(CALLBACK, params={"state": forged, "code": "c"})
    assert "youtube=bad_state" in res.text
    old = oauth_link.make_state("youtube", "catbooks", "a-1", now=1.0)
    res = client.get(CALLBACK, params={"state": old, "code": "c"})
    assert "youtube=bad_state" in res.text
    denied = client.get(
        CALLBACK,
        params={"state": oauth_link.make_state("youtube", "catbooks", "a-1"), "error": "x"},
    )
    assert "youtube=denied" in denied.text
    assert app_keys == []


def test_callback_page_escapes_the_code(
    client: TestClient,
    app_keys: list[dict[str, Any]],  # noqa: F811
) -> None:
    state = oauth_link.make_state("youtube", "catbooks", "a-1")
    res = client.get(CALLBACK, params={"state": state, "code": '"><script>alert(1)</script>'})
    assert "<script>" not in res.text


def test_connect_without_app_settings_says_so(
    client: TestClient,
    people: Any,  # noqa: F811
) -> None:
    res = client.post(CONNECT, headers=people["a-1"])
    assert res.status_code == 409 and res.json()["error"]["code"] == "YOUTUBE_APP_MISSING"
