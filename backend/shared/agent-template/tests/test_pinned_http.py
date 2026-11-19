"""검사한 IP로만 연결하는 transport (DNS 재바인딩·리다이렉트 SSRF). 네트워크 없이 돈다."""

from __future__ import annotations

import ipaddress
import json
from collections.abc import Iterable
from typing import Any

import httpcore
import httpx
import pytest

from ax_agent_template import company_connection as cc
from ax_agent_template import pinned_http

PUBLIC = "93.184.216.34"
METADATA = "169.254.169.254"

OK = [b"HTTP/1.1 200 OK\r\n", b"Content-Length: 2\r\n", b"\r\n", b"ok"]


def _redirect(to: str) -> list[bytes]:
    return [
        b"HTTP/1.1 302 Found\r\n",
        f"Location: {to}\r\n".encode(),
        b"Content-Length: 0\r\n",
        b"\r\n",
    ]


class _Recording(httpcore.AsyncNetworkBackend):
    """실제 소켓 대신 정해 둔 응답을 주고, 어디로 붙었는지 적는다."""

    def __init__(self, *responses: list[bytes]) -> None:
        self.responses = iter(responses)
        self.connected: list[tuple[str, int]] = []

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Iterable[Any] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        self.connected.append((host, port))
        return httpcore.AsyncMockStream(list(next(self.responses)))  # 읽으면 비워지므로 복사

    async def sleep(self, seconds: float) -> None:
        return None


def _answers(monkeypatch: pytest.MonkeyPatch, *hosts: tuple[Any, ...]) -> list[str]:
    """이름 풀이를 바꿔 끼운다. `(이름, 첫 답, 둘째 답, ...)`: 다시 물으면 다음 답."""
    table = dict((h[0], h[1:]) for h in hosts)
    asked: list[str] = []

    def fake(host: str) -> list[cc.IpAddress]:
        try:
            return [ipaddress.ip_address(host)]
        except ValueError:
            pass
        answers = table[host]
        found = answers[min(asked.count(host), len(answers) - 1)]
        asked.append(host)
        return [ipaddress.ip_address(a) for a in found]

    monkeypatch.setattr(cc, "_resolve", fake)
    return asked


async def _get(backend: _Recording, url: str, **kwargs: Any) -> httpx.Response:
    transport = pinned_http.guarded_transport(network_backend=backend)
    async with httpx.AsyncClient(transport=transport, **kwargs) as client:
        return await client.get(url)


@pytest.mark.asyncio
async def test_a_public_allowed_host_connects_to_the_checked_ip(monkeypatch) -> None:
    monkeypatch.setenv(cc.ALLOWED_HOSTS_ENV, "shop.example.com")
    _answers(monkeypatch, ("shop.example.com", [PUBLIC]))
    backend = _Recording(OK)
    res = await _get(backend, "https://shop.example.com/health")
    assert res.status_code == 200
    # 이름이 아니라 검사한 IP로 붙는다 (Host·SNI는 httpcore가 원래 이름으로 둔다)
    assert backend.connected == [(PUBLIC, 443)]
    assert res.request.headers["host"] == "shop.example.com"


@pytest.mark.asyncio
async def test_dns_rebinding_between_check_and_connect_is_refused(monkeypatch) -> None:
    monkeypatch.setenv(cc.ALLOWED_HOSTS_ENV, "shop.example.com")
    # 첫 번째 풀이(검사)는 공인 주소, 두 번째(연결)는 메타데이터 주소
    asked = _answers(monkeypatch, ("shop.example.com", [PUBLIC], [METADATA]))
    cc.check_host("https://shop.example.com")  # 검사 때는 통과한다
    backend = _Recording(OK)
    with pytest.raises(httpx.ConnectError, match="링크 로컬"):
        await _get(backend, "https://shop.example.com/health")
    assert backend.connected == []  # 메타데이터 주소로는 아예 붙지 않는다
    assert asked == ["shop.example.com", "shop.example.com"]


@pytest.mark.asyncio
async def test_a_mixed_answer_outside_the_list_is_refused(monkeypatch) -> None:
    # 허용 목록에 없는 이름이 연결 때 섞인 답(사내+공인)을 주면 사내 밖으로 보고 막는다
    monkeypatch.delenv(cc.ALLOWED_HOSTS_ENV, raising=False)
    _answers(monkeypatch, ("evil.example.com", ["10.0.0.5", PUBLIC]))
    backend = _Recording(OK)
    with pytest.raises(httpx.ConnectError, match="허용 목록"):
        await _get(backend, "https://evil.example.com/")
    assert backend.connected == []


@pytest.mark.asyncio
async def test_private_addresses_still_work_for_local_development(monkeypatch) -> None:
    monkeypatch.delenv(cc.ALLOWED_HOSTS_ENV, raising=False)
    _answers(monkeypatch, ("localhost", ["127.0.0.1"]))
    backend = _Recording(OK)
    res = await _get(backend, "http://localhost:8010/health")
    assert res.status_code == 200
    assert backend.connected == [("127.0.0.1", 8010)]


@pytest.mark.asyncio
async def test_http_to_a_public_host_is_refused_at_connect_time(monkeypatch) -> None:
    monkeypatch.setenv(cc.ALLOWED_HOSTS_ENV, "shop.example.com")
    _answers(monkeypatch, ("shop.example.com", [PUBLIC]))
    backend = _Recording(OK)
    with pytest.raises(httpx.ConnectError, match="https"):
        await _get(backend, "http://shop.example.com/")
    assert backend.connected == []


@pytest.mark.asyncio
async def test_a_redirect_is_not_followed_by_default(monkeypatch) -> None:
    monkeypatch.setenv(cc.ALLOWED_HOSTS_ENV, "shop.example.com")
    _answers(monkeypatch, ("shop.example.com", [PUBLIC]))
    backend = _Recording(_redirect(f"http://{METADATA}/latest/meta-data/"))
    res = await _get(backend, "https://shop.example.com/health")
    assert res.status_code == 302
    assert backend.connected == [(PUBLIC, 443)]


@pytest.mark.asyncio
async def test_a_followed_redirect_to_an_internal_name_is_refused(monkeypatch) -> None:
    monkeypatch.setenv(cc.ALLOWED_HOSTS_ENV, "shop.example.com")
    _answers(monkeypatch, ("shop.example.com", [PUBLIC]), ("inner.example.com", [METADATA]))
    backend = _Recording(_redirect("http://inner.example.com/"), OK)
    with pytest.raises(httpx.ConnectError, match="링크 로컬"):
        await _get(backend, "https://shop.example.com/health", follow_redirects=True)
    assert backend.connected == [(PUBLIC, 443)]  # 두 번째 홉은 붙기 전에 막힌다


@pytest.mark.asyncio
async def test_a_followed_redirect_to_a_metadata_ip_is_refused(monkeypatch) -> None:
    monkeypatch.setenv(cc.ALLOWED_HOSTS_ENV, "shop.example.com")
    _answers(monkeypatch, ("shop.example.com", [PUBLIC]))
    backend = _Recording(_redirect(f"http://{METADATA}/"), OK)
    with pytest.raises(httpx.ConnectError, match="링크 로컬"):
        await _get(backend, "https://shop.example.com/", follow_redirects=True)
    assert backend.connected == [(PUBLIC, 443)]


@pytest.mark.asyncio
async def test_exempt_hosts_connect_as_before(monkeypatch) -> None:
    asked = _answers(monkeypatch)
    backend = _Recording(OK)
    transport = pinned_http.guarded_transport(exempt=["api.example.org"], network_backend=backend)
    async with httpx.AsyncClient(transport=transport) as client:
        res = await client.get("https://api.example.org/app")
    assert res.status_code == 200
    assert backend.connected == [("api.example.org", 443)]
    assert asked == []


@pytest.mark.asyncio
async def test_company_transport_checks_only_the_company_host(monkeypatch, tmp_path) -> None:
    path = tmp_path / "company.json"
    path.write_text(json.dumps(dict(shop_api_url="http://shop.corp.example:8080")), "utf-8")
    monkeypatch.setenv(cc.CONNECTION_FILE_ENV, str(path))
    monkeypatch.setattr(cc, "_cache", None)
    # 파일 읽기(검사) 때는 사내 주소, 연결 때는 메타데이터 주소
    _answers(monkeypatch, ("shop.corp.example", ["10.1.2.3"], [METADATA]))
    assert cc.company_hosts() == frozenset(["shop.corp.example"])
    backend = _Recording(OK)
    transport = pinned_http.company_transport(network_backend=backend)
    async with httpx.AsyncClient(transport=transport) as client:
        with pytest.raises(httpx.ConnectError, match="링크 로컬"):
            await client.get("http://shop.corp.example:8080/api/shop/products")
        res = await client.get("http://dashboard-api:8100/site-adapter/health")
    assert res.status_code == 200
    assert backend.connected == [("dashboard-api", 8100)]
