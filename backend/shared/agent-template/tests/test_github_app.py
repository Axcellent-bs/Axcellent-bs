"""공용 GitHub App 인증."""

from __future__ import annotations

import asyncio

import httpx
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa

from ax_agent_template.github_app import GithubAppAuth
from ax_agent_template.tool_connections import ToolConnectionError

PEM = (
    rsa.generate_private_key(public_exponent=65537, key_size=2048)
    .private_bytes(
        serialization.Encoding.PEM,
        serialization.PrivateFormat.PKCS8,
        serialization.NoEncryption(),
    )
    .decode()
)
BASE = "https://api.github.com"
NOW = 1_800_000_000.0  # 2027-01-15T08:00:00Z


def _client(calls: list[httpx.Request], status: int = 201) -> httpx.AsyncClient:
    def handler(req: httpx.Request) -> httpx.Response:
        calls.append(req)
        if status >= 400:
            return httpx.Response(status, json={"message": "Bad credentials ghs_leak"})
        return httpx.Response(
            201,
            json={
                "token": f"ghs_tok{len(calls)}",
                "expires_at": "2027-01-15T09:00:00Z",
                "permissions": {"contents": "write", "metadata": "read"},
            },
        )

    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.mark.parametrize(
    "app_id,inst,key",
    [("abc", "42", PEM), ("1", "", PEM), ("1", "42", "not a key"), ("1", "42", "")],
)
def test_bad_pem_raises_config(app_id: str, inst: str, key: str) -> None:
    with pytest.raises(ToolConnectionError) as e:
        GithubAppAuth(app_id, inst, key)
    assert e.value.code == "config"
    assert "PRIVATE KEY" not in e.value.message


def test_token_cached_until_refresh_window() -> None:
    now = {"t": NOW}
    auth = GithubAppAuth("1", "42", PEM, clock=lambda: now["t"])
    calls: list[httpx.Request] = []

    async def run() -> list[str]:
        async with _client(calls) as client:
            got = [await auth.token(client, BASE)]
            got.append(await auth.token(client, BASE))  # 캐시
            now["t"] = NOW + 3600 - 120  # 끝나기 2분 전: 새로 받는다
            got.append(await auth.token(client, BASE))
            return got

    got = asyncio.run(run())
    assert got == ["ghs_tok1", "ghs_tok1", "ghs_tok2"]
    assert len(calls) == 2
    assert auth.secrets() == ["ghs_tok2"]
    assert "ghs_" not in repr(auth) and "PRIVATE KEY" not in repr(auth)


def test_permissions_filled_after_token() -> None:
    auth = GithubAppAuth("1", "42", PEM, clock=lambda: NOW)
    assert auth.permissions == {}
    asyncio.run(auth.token(_client([]), BASE))
    assert auth.permissions == {"contents": "write", "metadata": "read"}


def test_failed_status_has_no_body() -> None:
    auth = GithubAppAuth("1", "42", PEM, clock=lambda: NOW)
    with pytest.raises(ToolConnectionError) as e:
        asyncio.run(auth.token(_client([], status=401), BASE))
    assert e.value.code == "unauthorized" and "ghs_leak" not in e.value.message
