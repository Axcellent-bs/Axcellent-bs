"""연결 대장 (FR-AG-012 연결 시 보안 조건): 서버 종류·버전·토큰 권한을 읽고 패치 기준과 맞춘다."""

from __future__ import annotations

import asyncio

import httpx
import pytest
from fastapi.testclient import TestClient

from dev_agent import vcs
from dev_agent.main import app
from dev_agent.vcs import inventory, vuln_watch
from dev_agent.vcs.github import GithubVcsAdapter

pytestmark = pytest.mark.role("dev")

GHES = "https://ghe.acme.local/api/v3"


def setup_function() -> None:
    vcs._screen = None
    vcs.set_vcs(None)
    inventory.reset()

    # 취약점 소식은 부르지 않는다 (KEV·NVD 빈 소식)
    async def empty(url, params):
        return {"vulnerabilities": [], "totalResults": 0}

    vuln_watch.watch.feed = vuln_watch.Feed(empty, nvd_gap=0)


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


def test_other_connection_kind_registers_and_is_judged(monkeypatch: pytest.MonkeyPatch) -> None:
    """배포 도구 같은 다른 연결 종류도 같은 인터페이스로 대장에 들어온다."""
    monkeypatch.setenv("PLANNING_VCS_MIN_VERSIONS", "jenkins=2.462.3")

    async def probe() -> vcs.base.ServerInfo:
        return vcs.base.ServerInfo(product="jenkins", hosted=False, version="2.440.1")

    inventory.register_source(
        "deploy",
        lambda: [
            inventory.Target(
                id="dep-1", category="deploy", name="ci.acme.local", kind="jenkins", probe=probe
            )
        ],
    )
    try:
        e = _check("dep-1")
        assert (e.category, e.status, e.min_version, e.policy_source) == (
            "deploy", "outdated", "2.462.3", "env",
        )  # fmt: skip
        assert [r["connection_id"] for r in inventory.entries()] == ["dep-1"]
    finally:
        inventory.unregister_source("deploy")
    assert inventory.entries() == []


def test_ghes_on_kev_is_emergency_and_hold_waits_for_safety() -> None:
    """사내 설치형 GHES 버전이 KEV 취약 범위면 긴급, 안전 서비스 창구가 없으면 알림은 대기."""
    cpe = "cpe:2.3:a:github:enterprise_server"

    async def feed(url, params):
        if url == vuln_watch.KEV_URL:
            return {"vulnerabilities": [{"cveID": "CVE-2026-7", "vendorProject": "GitHub",
                                         "product": "Enterprise Server"}]}  # fmt: skip
        if "cveId" in params:
            m = {"criteria": f"{cpe}:*:*", "versionEndExcluding": "3.14.0"}
            return {
                "vulnerabilities": [{"cve": {"configurations": [{"nodes": [{"cpeMatch": [m]}]}]}}]
            }
        return {"totalResults": 1, "vulnerabilities": [{"cve": {"id": "CVE-2026-7"}}]}

    async def no_safety(notice):
        raise httpx.ConnectError("창구 없음")

    vuln_watch.watch.feed = vuln_watch.Feed(feed, nvd_gap=0)
    vuln_watch.watch.send = no_safety
    try:
        _connect(
            _server({"installed_version": "3.12.1"}, {"x-oauth-scopes": "repo"}), base_url=GHES
        )
        asyncio.run(inventory.check_all())
        row = inventory.entries()[0]
        assert row["vuln"] == "emergency" and row["kev_cves"] == ["CVE-2026-7"]
        assert row["warnings"][0].startswith("긴급: 실제 악용 중인 취약점 CVE-2026-7")
        assert row["safety_hold"]["hold"] and not row["safety_hold"]["sent"]
        assert "못 보냄" in row["safety_hold"]["error"]
    finally:
        vuln_watch.watch.send = vuln_watch.send_to_safety
