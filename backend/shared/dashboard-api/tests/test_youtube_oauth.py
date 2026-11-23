"""회사마다 유튜브 채널 연결(구글 OAuth). 가짜 구글·더미 값만 쓴다."""

# ruff: noqa: F811 - 회사 AI 경로 테스트의 준비(fixture)를 그대로 쓴다

from __future__ import annotations

from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
from fastapi.testclient import TestClient

from ax_dashboard_api import youtube_oauth as yo
from ax_secret_store import get_secret

from .test_company_ai_routes import ROWS, _people, client, env, people  # noqa: F401

CONNECT = "/api/dashboard/marketing/youtube/connect"
CALLBACK = "/api/dashboard/marketing/youtube/callback"


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


def test_admin_gets_consent_url_and_callback_stores_token_for_that_company(
    client: TestClient,
    people: Any,
    app_keys: list[dict[str, Any]],  # noqa: F811
) -> None:
    assert client.post(CONNECT).status_code == 401
    assert client.post(CONNECT, headers=people["a-3"]).status_code == 403  # 회원은 못 한다
    res = client.post(CONNECT, headers=people["a-1"])
    assert res.status_code == 200
    url = res.json()["data"]["url"]
    query = parse_qs(urlparse(url).query)
    assert query["access_type"] == ["offline"] and "youtube.upload" in query["scope"][0]
    assert "DUMMY-CLIENT-SECRET" not in url

    back = client.get(CALLBACK, params={"state": query["state"][0], "code": "c1"})
    assert back.status_code == 200 and "youtube=connected" in back.text
    assert app_keys[0]["code"] == ["c1"]
    assert get_secret(yo.TOKEN_NAME, tenant="catbooks") == "DUMMY-REFRESH"
    assert yo.connection("catbooks")["company_connected"] is True
    assert get_secret(yo.TOKEN_NAME, tenant="nubi") == ""  # 다른 회사는 따로

    out = client.post("/api/dashboard/marketing/youtube/disconnect", headers=people["a-1"])
    assert out.status_code == 200
    assert get_secret(yo.TOKEN_NAME, tenant="catbooks") == ""


def test_callback_refuses_forged_or_expired_state(
    client: TestClient,
    app_keys: list[dict[str, Any]],  # noqa: F811
) -> None:
    forged = yo.make_state("catbooks")[:-3] + "AAA"
    res = client.get(CALLBACK, params={"state": forged, "code": "c"})
    assert "youtube=bad_state" in res.text
    old = yo.make_state("catbooks", now=1.0)
    res = client.get(CALLBACK, params={"state": old, "code": "c"})
    assert "youtube=bad_state" in res.text
    denied = client.get(
        CALLBACK,
        params={"state": yo.make_state("catbooks"), "error": "access_denied"},
    )
    assert "youtube=denied" in denied.text
    assert app_keys == []


def test_connect_without_app_settings_says_so(
    client: TestClient,
    people: Any,  # noqa: F811
) -> None:
    res = client.post(CONNECT, headers=people["a-1"])
    assert res.status_code == 409 and res.json()["error"]["code"] == "YOUTUBE_APP_MISSING"
