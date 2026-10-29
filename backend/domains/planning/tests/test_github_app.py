"""GitHub App 연결: 개인 키로 JWT를 만들어 설치 토큰을 받고, 그 토큰으로 레포를 부른다.

실제 GitHub는 부르지 않는다(`httpx.MockTransport`). 키는 테스트 안에서 새로 만든다.
"""

from __future__ import annotations

import asyncio
import base64
import json

import httpx
import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from fastapi.testclient import TestClient

from planning_agent import vcs
from planning_agent.main import app
from planning_agent.vcs import VcsError
from planning_agent.vcs.github import GithubVcsAdapter
from planning_agent.vcs.github_app import GithubAppAuth

KEY = rsa.generate_private_key(public_exponent=65537, key_size=2048)
PEM = KEY.private_bytes(
    serialization.Encoding.PEM,
    serialization.PrivateFormat.PKCS8,
    serialization.NoEncryption(),
).decode()
REPO = "acme/shop"
BASE = "https://api.github.com"
NOW = 1_800_000_000.0


def run(coro):
    return asyncio.run(coro)


def _unb64(part: str) -> bytes:
    return base64.urlsafe_b64decode(part + "=" * (-len(part) % 4))


class Fake:
    """설치 토큰 발급과 PR 목록만 아는 가짜 GitHub."""

    def __init__(self, status: int = 201) -> None:
        self.status = status
        self.expires = "2027-01-15T09:00:00Z"
        self.minted = 0
        self.jwts: list[str] = []
        self.used: list[str] = []

    def handler(self, req: httpx.Request) -> httpx.Response:
        auth = req.headers["Authorization"].removeprefix("Bearer ")
        if req.url.path == "/app/installations/42/access_tokens":
            self.jwts.append(auth)
            if self.status >= 400:
                return httpx.Response(self.status, json={"message": "Bad credentials"})
            self.minted += 1
            return httpx.Response(
                201,
                json={"token": f"ghs_install{self.minted}", "expires_at": self.expires},
            )
        self.used.append(auth)
        if req.url.path == f"/repos/{REPO}/pulls":
            return httpx.Response(200, json=[])
        if req.url.path == f"/repos/{REPO}/issues":
            return httpx.Response(200, json=[])
        return httpx.Response(404, json={"message": "Not Found"})


def _adapter(fake: Fake, clock=lambda: NOW) -> GithubVcsAdapter:
    return GithubVcsAdapter(
        REPO,
        app=GithubAppAuth("1234", "42", PEM, clock=clock),
        base_url=BASE,
        client=httpx.AsyncClient(transport=httpx.MockTransport(fake.handler)),
    )


def test_jwt_is_rs256_signed_by_the_app_key() -> None:
    token = GithubAppAuth("1234", "42", PEM, clock=lambda: NOW).jwt()
    head, body, sig = token.split(".")
    assert json.loads(_unb64(head)) == {"alg": "RS256", "typ": "JWT"}
    claims = json.loads(_unb64(body))
    assert claims == {"iat": int(NOW) - 60, "exp": int(NOW) + 540, "iss": "1234"}
    KEY.public_key().verify(
        _unb64(sig), f"{head}.{body}".encode(), padding.PKCS1v15(), hashes.SHA256()
    )


def test_adapter_uses_installation_token_and_reuses_it() -> None:
    fake = Fake()
    gh = _adapter(fake)
    run(gh.list_changes("open"))
    run(gh.list_issues("open"))
    assert fake.minted == 1  # 아직 넉넉히 남아 있어 다시 받지 않는다
    assert fake.used and set(fake.used) == {"ghs_install1"}
    assert "PRIVATE KEY" not in repr(gh) and "ghs_" not in repr(gh)


def test_token_is_refreshed_near_expiry() -> None:
    fake = Fake()
    now = {"t": NOW}
    gh = _adapter(fake, clock=lambda: now["t"])
    run(gh.list_changes("open"))
    # 2027-01-15T09:00:00Z 5분 전 안쪽이면 새로 받는다
    now["t"] = NOW + 3600 - 120  # 끝나기 2분 전
    fake.expires = "2027-01-15T10:00:00Z"
    run(gh.list_changes("open"))
    assert fake.minted == 2 and fake.used[-1] == "ghs_install2"


def test_bad_app_credentials_are_reported_without_secrets() -> None:
    gh = _adapter(Fake(status=401))
    with pytest.raises(VcsError) as e:
        run(gh.list_changes("open"))
    assert e.value.code == "unauthorized"
    assert "PRIVATE KEY" not in e.value.message and "eyJ" not in e.value.message


@pytest.mark.parametrize(
    "app_id,installation_id,key",
    [("abc", "42", PEM), ("1234", "", PEM), ("1234", "42", "not a key"), ("1234", "42", "")],
)
def test_bad_app_config_is_config_error(app_id, installation_id, key) -> None:
    with pytest.raises(VcsError) as e:
        GithubAppAuth(app_id, installation_id, key)
    assert e.value.code == "config"


@pytest.fixture
def client():
    vcs._screen = None
    vcs.set_vcs(None)
    yield TestClient(app)
    vcs._screen = None
    vcs.set_vcs(None)


def test_connection_api_takes_app_and_never_returns_key(client) -> None:
    r = client.post(
        "/api/planning/vcs/connections",
        json={
            "kind": "github",
            "repo": REPO,
            "auth": "app",
            "app_id": "1234",
            "installation_id": "42",
            "private_key": PEM,
        },
    )
    assert r.status_code == 200
    assert "PRIVATE KEY" not in r.text
    row = r.json()["data"][0]
    assert (row["auth"], row["app_id"], row["installation_id"], row["key_set"]) == (
        "app",
        "1234",
        "42",
        True,
    )
    assert row["token_set"] is False
    # 키를 비워 다시 보내면 이전 키를 그대로 쓴다
    again = client.post(
        "/api/planning/vcs/connections",
        json={"id": row["id"], "kind": "github", "repo": REPO, "auth": "app", "app_id": "1234",
              "installation_id": "42"},
    )  # fmt: skip
    assert again.status_code == 200 and again.json()["data"][0]["key_set"] is True
    assert "PRIVATE KEY" not in client.get("/api/planning/vcs/connections").text
    bad = client.post(
        "/api/planning/vcs/connections",
        json={"kind": "github", "repo": REPO, "auth": "app", "app_id": "x", "installation_id": "42",
              "private_key": PEM},
    )  # fmt: skip
    assert bad.status_code == 400 and bad.json()["error"]["code"] == "INVALID_VCS"
    bad = client.post(
        "/api/planning/vcs/connections",
        json={"kind": "github", "repo": REPO, "auth": "sso"},
    )
    assert bad.status_code == 400
