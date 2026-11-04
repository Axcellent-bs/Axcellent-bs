"""GitHub 계정 연결(앱 설치 하나) → 레포 자동으로 찾기 → 사람이 켠 레포만 개발 AI가 쓴다.

실제 GitHub는 부르지 않는다 (`httpx.MockTransport`). 키는 시험 안에서 새로 만든다.
"""

from __future__ import annotations

import asyncio
import os
import stat

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from dev_agent import vcs
from dev_agent.vcs import github_account as ga

pytestmark = pytest.mark.role("dev")

KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
PEM = KEY.private_bytes(
    serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()
).decode()


def run(coro):
    return asyncio.run(coro)


class Fake:
    def __init__(self, selection: str = "selected", perms: dict | None = None) -> None:
        self.selection = selection
        self.perms = perms or {"contents": "write", "pull_requests": "write", "metadata": "read"}
        self.repos = [
            {"full_name": "catbooks/shop-web", "default_branch": "dev", "private": True},
            {"full_name": "catbooks/shop-api", "default_branch": "main", "private": True},
        ]

    def handler(self, req: httpx.Request) -> httpx.Response:
        if req.url.path == "/app/installations/42/access_tokens":
            return httpx.Response(201, json={
                "token": "ghs_test", "expires_at": "2099-01-01T00:00:00Z",
                "permissions": self.perms,
            })  # fmt: skip
        if req.url.path == "/installation/repositories":
            assert req.headers["Authorization"] == "Bearer ghs_test"
            return httpx.Response(200, json={
                "total_count": len(self.repos), "repository_selection": self.selection,
                "repositories": self.repos,
            })  # fmt: skip
        return httpx.Response(404, json={})


@pytest.fixture(autouse=True)
def _clean(monkeypatch, tmp_path):
    monkeypatch.setenv("PLANNING_VCS_ACCOUNT_STORE", str(tmp_path / "vcs_account.json"))
    monkeypatch.delenv("PLANNING_VCS_STORE", raising=False)
    ga._account = None
    yield
    ga.clear_account()
    ga.set_client(None)


def _connect(fake: Fake) -> None:
    ga.set_client(httpx.AsyncClient(transport=httpx.MockTransport(fake.handler)))
    ga.set_account("1234", "42", PEM)
    run(ga.refresh())


def test_repos_are_found_but_not_used_until_a_human_enables_them() -> None:
    _connect(Fake())
    pub = ga.public()
    assert [(r["full_name"], r["default_branch"], r["enabled"]) for r in pub["repos"]] == [
        ("catbooks/shop-web", "dev", False),
        ("catbooks/shop-api", "main", False),
    ]
    assert vcs.find_connection(ga.connection_id("catbooks/shop-web")) is None
    ga.enable_repo("catbooks/shop-web", True)
    conn = vcs.find_connection(ga.connection_id("catbooks/shop-web"))
    assert conn is not None and (conn.auth, conn.base_branch, conn.repo) == (
        "app",
        "dev",
        "catbooks/shop-web",
    )
    ga.enable_repo("catbooks/shop-web", False)
    assert vcs.find_connection(ga.connection_id("catbooks/shop-web")) is None


def test_key_is_never_shown_and_store_is_owner_only() -> None:
    _connect(Fake())
    pub = ga.public()
    assert pub["key_set"] is True and "PRIVATE KEY" not in str(pub)
    assert "PRIVATE KEY" not in repr(ga.account())
    if os.name != "nt":
        mode = stat.S_IMODE(os.stat(os.environ["PLANNING_VCS_ACCOUNT_STORE"]).st_mode)
        assert mode == 0o600


def test_warns_on_all_repositories_and_risky_permissions() -> None:
    _connect(
        Fake(
            selection="all",
            perms={"contents": "write", "administration": "write", "workflows": "write"},
        )
    )
    warns = ga.public()["warnings"]
    assert any("모든 레포" in w for w in warns)
    assert any("administration" in w for w in warns) and any("workflows" in w for w in warns)


def test_repo_removed_from_install_turns_its_connection_off() -> None:
    fake = Fake()
    _connect(fake)
    ga.enable_repo("catbooks/shop-api", True)
    fake.repos = fake.repos[:1]  # GitHub 설치 범위에서 shop-api를 뺌
    run(ga.refresh())
    assert vcs.find_connection(ga.connection_id("catbooks/shop-api")) is None
    with pytest.raises(vcs.VcsError):
        ga.enable_repo("catbooks/shop-api", True)
