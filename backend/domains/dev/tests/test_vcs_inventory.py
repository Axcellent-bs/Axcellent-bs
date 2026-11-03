"""연결 대장 (FR-AG-012 연결 시 보안 조건): 서버 종류·버전·토큰 권한을 읽고 패치 기준과 맞춘다."""

from __future__ import annotations

import asyncio

import httpx
import pytest
from fastapi.testclient import TestClient

from dev_agent import vcs
from dev_agent.main import app
from dev_agent.vcs import inventory
from dev_agent.vcs.github import GithubVcsAdapter

pytestmark = pytest.mark.role("dev")

GHES = "https://ghe.acme.local/api/v3"


def setup_function() -> None:
    vcs._screen = None
    vcs.set_vcs(None)
    inventory.reset()


def teardown_function() -> None:
    setup_function()


def _server(meta: dict, headers: dict | None = None, status: int = 200):
    def handle(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET" and request.url.path.endswith("/meta")
        return httpx.Response(status, json=meta, headers=headers or {})

    return handle


def _connect(handle, *, base_url: str = "", cid: str = "conn-1") -> vcs.Connection:
    """화면 연결 하나를 가짜 서버 부품으로 붙인다."""
    conn = vcs.Connection(
        id=cid, kind="github", repo="acme/shop", base_url=base_url, token="t" * 12
    )
    adapter = GithubVcsAdapter(
        "acme/shop",
        "t" * 12,
        base_url=base_url or "https://api.github.com",
        client=httpx.AsyncClient(transport=httpx.MockTransport(handle)),
    )
    vcs._screen = {cid: conn}
    vcs._adapters[cid] = (conn, adapter)
    return conn


def _check(cid: str = "conn-1") -> inventory.Entry:
    entry = asyncio.run(inventory.check(cid))
    assert entry is not None
    return entry


def test_version_compare() -> None:
    assert inventory.older("3.12.1", "3.14.0") is True
    assert inventory.older("18.11.13-ee", "18.11.13") is False
    assert inventory.older("19.5", "19.5.1") is True
    assert inventory.older("", "1.0") is None


def test_github_com_is_hosted_and_flags_broad_scopes() -> None:
    _connect(
        _server(
            {"verifiable_password_authentication": False},
            {
                "x-oauth-scopes": "repo, admin:org",
                "github-authentication-token-expiration": "2026-12-01 00:00:00 UTC",
            },
        )
    )
    e = _check()
    assert e.status == "hosted" and e.product == "github.com" and e.version == ""
    assert e.scopes == ["repo", "admin:org"]
    assert e.token_expires.startswith("2026-12-01")
    assert e.warnings == ["넓은 토큰 범위: admin:org"]


def test_ghes_below_patch_policy_is_outdated(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PLANNING_VCS_MIN_VERSIONS", "ghes=3.14.0, gitlab=18.11.13")
    _connect(_server({"installed_version": "3.12.1"}, {"x-oauth-scopes": "repo"}), base_url=GHES)
    e = _check()
    assert e.status == "outdated" and e.product == "ghes" and e.version == "3.12.1"
    assert e.min_version == "3.14.0"
    assert e.warnings[0].startswith("보안 패치 기준(3.14.0)보다 낮은 버전")


def test_ghes_at_or_above_policy_is_ok(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("PLANNING_VCS_MIN_VERSIONS", "ghes=3.14.0")
    _connect(_server({"installed_version": "3.15.2"}, {"x-oauth-scopes": "repo"}), base_url=GHES)
    assert _check().status == "ok"


def test_ghes_without_policy_records_version() -> None:
    _connect(_server({"installed_version": "3.15.2"}), base_url=GHES)
    e = _check()
    assert e.status == "no_policy" and e.version == "3.15.2"
    # 범위 머리글이 없으면 세분화 토큰으로 본다
    assert e.scopes == [] and "세분화 토큰" in e.scope_note


def test_unauthorized_is_error_without_token() -> None:
    _connect(_server({"message": "Bad credentials"}, status=401))
    e = _check()
    assert e.status == "error" and e.error_code == "unauthorized"
    assert "t" * 12 not in e.error


def test_app_permissions_flag_unneeded_writes() -> None:
    info = vcs.base.ServerInfo(
        product="github.com",
        hosted=True,
        scopes=("contents:write", "administration:write", "members:read"),
    )
    assert inventory.scope_warnings(info, app=True) == ["필요 없는 App write 권한: administration"]


def test_inventory_api_lists_checks_and_forgets() -> None:
    conn = vcs.Connection(id="conn-1", kind="mock", repo="acme/shop")
    vcs._screen = {"conn-1": conn}
    c = TestClient(app)
    rows = c.get("/api/planning/vcs/inventory").json()["data"]
    assert [r["status"] for r in rows] == ["unknown"]  # 아직 안 읽음
    # 연결 확인을 부르면 대장도 같이 갱신된다
    t = c.post("/api/planning/vcs/connections/conn-1/test").json()["data"]
    assert t["inventory"]["status"] == "hosted" and t["inventory"]["product"] == "mock"
    rows = c.post("/api/planning/vcs/inventory/check").json()["data"]
    assert rows[0]["status"] == "hosted" and rows[0]["checked_at"]
    c.post("/api/planning/vcs/connections/conn-1/delete")
    assert c.get("/api/planning/vcs/inventory").json()["data"] == []
