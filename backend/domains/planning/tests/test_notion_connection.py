"""연결 페이지 › 문서 › 노션: 토큰 넣기·확인·지우기 (관리자). 실제 노션은 부르지 않는다."""

from __future__ import annotations

import json

import pytest
from fastapi.testclient import TestClient

from planning_agent import notion_import, notion_store
from planning_agent.main import app

BASE = "/api/planning/notion/connection"
ADMIN = {"Authorization": "Bearer admin-test-token"}
HUMAN = {"Authorization": "Bearer human-test-token"}
TOKEN = "ntn_test_1234567890abcd"


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch, tmp_path):
    monkeypatch.setenv("AX_AGENT_TOKENS", "admin=admin-test-token")
    monkeypatch.setenv("SAFETY_HUMAN_TOKENS", "operator:human-test-token")
    monkeypatch.setenv("PLANNING_STATE_DIR", str(tmp_path))
    monkeypatch.delenv("PLANNING_NOTION_TOKEN", raising=False)
    monkeypatch.delenv("NOTION_TOKEN", raising=False)
    notion_store.reset_for_tests()
    yield
    notion_store.reset_for_tests()


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def _fake_check(ok: bool):
    async def check(key: str, client=None) -> str:
        if not ok:
            raise notion_import.NotionError("NOTION_UNAUTHORIZED", "노션 토큰이 맞지 않습니다")
        return "캣북스 워크스페이스"

    return check


@pytest.mark.role("planning")
def test_save_checks_with_notion_then_shows_only_hint(client, monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(notion_import, "check", _fake_check(True))
    assert client.get(BASE).json()["data"]["connected"] is False
    res = client.post(BASE, json={"token": TOKEN}, headers=ADMIN)
    assert res.status_code == 200
    data = res.json()["data"]
    assert data == {
        "connected": True,
        "source": "saved",
        "hint": "••••abcd",
        "workspace": "캣북스 워크스페이스",
        "checked_at": data["checked_at"],
        "oauth_ready": False,
    }
    assert TOKEN not in res.text and TOKEN not in client.get(BASE).text
    saved = json.loads((tmp_path / "notion_token.json").read_text(encoding="utf-8"))
    assert saved["token"] == TOKEN
    assert notion_import.token() == TOKEN  # 가져오기가 저장한 토큰을 쓴다


@pytest.mark.role("planning")
def test_wrong_token_is_not_saved(client, monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(notion_import, "check", _fake_check(False))
    res = client.post(BASE, json={"token": TOKEN}, headers=ADMIN)
    assert res.status_code == 400 and res.json()["error"]["code"] == "NOTION_UNAUTHORIZED"
    assert not (tmp_path / "notion_token.json").exists()
    bad = client.post(BASE, json={"token": "한글 토큰"}, headers=ADMIN)
    assert bad.status_code == 400 and bad.json()["error"]["code"] == "INVALID_INPUT"


@pytest.mark.role("planning")
def test_only_admin_writes(client, monkeypatch) -> None:
    monkeypatch.setattr(notion_import, "check", _fake_check(True))
    assert client.post(BASE, json={"token": TOKEN}, headers=HUMAN).status_code == 403
    assert client.post(f"{BASE}/delete", headers=HUMAN).status_code == 403


@pytest.mark.role("planning")
def test_delete_falls_back_to_env_and_check(client, monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(notion_import, "check", _fake_check(True))
    client.post(BASE, json={"token": TOKEN}, headers=ADMIN)
    monkeypatch.setenv("PLANNING_NOTION_TOKEN", "ntn_env_token_00009999")
    res = client.post(f"{BASE}/delete", headers=ADMIN).json()["data"]
    assert res["source"] == "env" and res["hint"] == "••••9999"
    assert not (tmp_path / "notion_token.json").exists()
    assert notion_import.token() == "ntn_env_token_00009999"
    checked = client.post(f"{BASE}/check", headers=ADMIN).json()["data"]
    assert checked["workspace"] == "캣북스 워크스페이스"
    monkeypatch.delenv("PLANNING_NOTION_TOKEN")
    none = client.post(f"{BASE}/check", headers=ADMIN)
    assert none.status_code == 400 and none.json()["error"]["code"] == "NOTION_NOT_CONFIGURED"


@pytest.mark.role("planning")
def test_search_by_title_needs_human_and_connection(client, monkeypatch) -> None:
    import httpx

    calls: list[dict] = []

    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(json.loads(req.content))
        title = {"t": {"type": "title", "title": [{"plain_text": "11월 기획전 회의록"}]}}
        page = {
            "object": "page", "id": "1a2b3c4d-5e6f-7a8b-9c0d-1e2f3a4b5c6d",
            "url": "https://notion.so/x", "last_edited_time": "2026-11-04T01:00:00Z",
            "properties": title,
        }  # fmt: skip
        return httpx.Response(200, json={"results": [page, {**page, "archived": True}]})

    real = httpx.AsyncClient
    monkeypatch.setattr(
        notion_import.httpx,
        "AsyncClient",
        lambda **kw: real(transport=httpx.MockTransport(handler), **kw),
    )
    no = client.post("/api/planning/notion/search", json={"query": "기획전"}, headers=HUMAN)
    assert no.status_code == 400 and no.json()["error"]["code"] == "NOTION_NOT_CONFIGURED"
    monkeypatch.setenv("PLANNING_NOTION_TOKEN", "ntn_env_token_00009999")
    none = {"Authorization": ""}  # conftest가 목 토큰을 붙이지 않게
    assert (
        client.post("/api/planning/notion/search", json={"query": "x"}, headers=none).status_code
        == 401
    )
    res = client.post("/api/planning/notion/search", json={"query": "기획전"}, headers=HUMAN)
    rows = res.json()["data"]
    assert rows == [
        {
            "id": "1a2b3c4d5e6f7a8b9c0d1e2f3a4b5c6d",
            "title": "11월 기획전 회의록",
            "url": "https://notion.so/x",
            "edited": "2026-11-04T01:00:00Z",
        }
    ]
    assert calls[-1]["query"] == "기획전" and calls[-1]["filter"]["value"] == "page"


# ---- 노션 계정 연결 (OAuth)
from urllib.parse import parse_qs, urlparse  # noqa: E402

from planning_agent import notion_oauth  # noqa: E402

CB = "/api/planning/notion/oauth/callback"


@pytest.fixture
def oauth(monkeypatch):
    monkeypatch.setenv("PLANNING_NOTION_CLIENT_ID", "cid-test")
    monkeypatch.setenv("PLANNING_NOTION_CLIENT_SECRET", "secret-test")
    notion_oauth.reset_for_tests()
    seen: list[str] = []

    async def exchange(code: str, client=None):
        seen.append(code)
        if code == "bad":
            raise notion_import.NotionError("NOTION_OAUTH_FAILED", "x")
        return "ntn_oauth_token_abcd1234", "캣북스 워크스페이스"

    monkeypatch.setattr(notion_oauth, "exchange", exchange)
    yield seen
    notion_oauth.reset_for_tests()


def _start(client) -> str:
    url = client.post("/api/planning/notion/oauth/start", headers=ADMIN).json()["data"]["url"]
    q = parse_qs(urlparse(url).query)
    assert url.startswith("https://api.notion.com/v1/oauth/authorize?")
    assert q["client_id"] == ["cid-test"] and q["owner"] == ["user"]
    assert "secret" not in url
    return q["state"][0]


@pytest.mark.role("planning")
def test_oauth_off_without_client_and_admin_only(client, monkeypatch) -> None:
    assert client.get(BASE).json()["data"]["oauth_ready"] is False
    off = client.post("/api/planning/notion/oauth/start", headers=ADMIN)
    assert off.status_code == 400 and off.json()["error"]["code"] == "NOTION_OAUTH_OFF"
    monkeypatch.setenv("PLANNING_NOTION_CLIENT_ID", "cid")
    monkeypatch.setenv("PLANNING_NOTION_CLIENT_SECRET", "sec")
    assert client.get(BASE).json()["data"]["oauth_ready"] is True
    assert client.post("/api/planning/notion/oauth/start", headers=HUMAN).status_code == 403
    monkeypatch.setenv("PLANNING_NOTION_REDIRECT_URI", "http://example.com/cb")  # 실서버 http
    assert client.get(BASE).json()["data"]["oauth_ready"] is False


@pytest.mark.role("planning")
def test_oauth_callback_stores_token_once_and_redirects_fixed(client, oauth, tmp_path) -> None:
    state = _start(client)
    res = client.get(CB, params={"code": "c1", "state": state}, follow_redirects=False)
    assert res.status_code == 302
    assert res.headers["location"] == "http://localhost:3090/connections?notion=ok"
    assert "ntn_oauth" not in res.headers["location"]
    data = client.get(BASE).json()["data"]
    assert data["source"] == "saved" and data["workspace"] == "캣북스 워크스페이스"
    assert data["hint"] == "••••1234" and "ntn_oauth_token" not in client.get(BASE).text
    assert notion_import.token() == "ntn_oauth_token_abcd1234"
    again = client.get(CB, params={"code": "c2", "state": state}, follow_redirects=False)
    assert again.headers["location"].endswith("notion=bad_state") and oauth == ["c1"]


@pytest.mark.role("planning")
def test_oauth_rejects_forged_state_denied_and_failed(client, oauth) -> None:
    forged = client.get(CB, params={"code": "c", "state": "made-up"}, follow_redirects=False)
    assert forged.headers["location"].endswith("notion=bad_state") and oauth == []
    state = _start(client)
    denied = client.get(
        CB, params={"error": "access_denied", "state": state}, follow_redirects=False
    )
    assert denied.headers["location"].endswith("notion=denied")
    assert (
        client.get(CB, params={"code": "c", "state": state}, follow_redirects=False)
        .headers["location"]
        .endswith("notion=bad_state")
    )  # 거절된 state는 다시 못 쓴다
    state = _start(client)
    failed = client.get(CB, params={"code": "bad", "state": state}, follow_redirects=False)
    assert failed.headers["location"].endswith("notion=failed")
    assert client.get(BASE).json()["data"]["connected"] is False
    evil = client.get(
        CB,
        params={"code": "c", "state": "x", "next": "https://evil.example"},
        follow_redirects=False,
    )
    assert evil.headers["location"].startswith("http://localhost:3090/connections")


@pytest.mark.role("planning")
def test_oauth_exchange_error_code_reaches_screen_without_secrets(client, monkeypatch) -> None:
    import httpx

    monkeypatch.setenv("PLANNING_NOTION_CLIENT_ID", "cid-test")
    monkeypatch.setenv("PLANNING_NOTION_CLIENT_SECRET", "secret-test")
    notion_oauth.reset_for_tests()

    def handler(req: httpx.Request) -> httpx.Response:
        body = {"error": "invalid_client", "error_description": "bad <script> secret-test"}
        return httpx.Response(401, json=body)

    real = httpx.AsyncClient
    monkeypatch.setattr(
        notion_oauth.httpx,
        "AsyncClient",
        lambda **kw: real(transport=httpx.MockTransport(handler), **kw),
    )
    state = _start(client)
    res = client.get(CB, params={"code": "c", "state": state}, follow_redirects=False)
    loc = res.headers["location"]
    assert loc.endswith("notion=failed&reason=invalid_client") and "secret" not in loc
