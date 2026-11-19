"""공용 주소 전용 내려받기: 네트워크 없이 검증한다(가짜 resolver·가짜 전송·가짜 백엔드)."""

from __future__ import annotations

import httpcore
import httpx
import pytest

from marketing_agent.media import safe_fetch
from marketing_agent.media.base import MediaProviderError
from marketing_agent.media.safe_fetch import (
    PublicFetchError,
    PublicOnlyBackend,
    fetch_public,
    resolve_public,
)

PUBLIC = "93.184.216.34"


def _resolver(*ips: str):
    def resolve(host: str, port: int, **_kw: object) -> list[tuple]:
        return [(2, 1, 6, "", (ip, port)) for ip in ips]

    return resolve


def _no_dns(host: str, port: int, **_kw: object) -> list[tuple]:
    raise AssertionError("IP 리터럴은 이름 풀이를 하지 않는다")


@pytest.mark.parametrize(
    "host",
    [
        "127.0.0.1",
        "::1",
        "[::1]",
        "10.0.0.5",
        "172.16.0.1",
        "192.168.0.232",
        "169.254.169.254",
        "100.64.0.1",
        "0.0.0.0",
        "224.0.0.1",
        "240.0.0.1",
        "::ffff:127.0.0.1",
        "[::ffff:127.0.0.1]",
        "::ffff:169.254.169.254",
        "64:ff9b::7f00:1",
        "fe80::1",
        "fc00::1",
        "2130706433",
        "0x7f000001",
        "0x7f.1",
        "017700000001",
        "127.1",
    ],
)
def test_private_ip_literals_blocked(host: str) -> None:
    with pytest.raises(PublicFetchError, match="blocked_video_host"):
        resolve_public(host, 443, _no_dns)


def test_public_ip_literal_passes() -> None:
    assert resolve_public(PUBLIC, 443, _no_dns) == [PUBLIC]


@pytest.mark.parametrize("ip", ["10.1.2.3", "169.254.169.254", "127.0.0.1", "::1"])
def test_name_resolving_to_private_blocked(ip: str) -> None:
    with pytest.raises(PublicFetchError, match="blocked_video_host"):
        resolve_public("cdn.example.test", 443, _resolver(ip))


def test_one_private_among_many_blocks() -> None:
    with pytest.raises(PublicFetchError, match="blocked_video_host"):
        resolve_public("cdn.example.test", 443, _resolver(PUBLIC, "10.0.0.1"))


def test_all_public_passes_and_dedupes() -> None:
    ips = resolve_public("cdn.example.test", 443, _resolver(PUBLIC, "2606:4700::1111", PUBLIC))
    assert ips == [PUBLIC, "2606:4700::1111"]


def test_empty_or_failing_resolution_is_unresolved() -> None:
    with pytest.raises(PublicFetchError, match="video_host_unresolved"):
        resolve_public("cdn.example.test", 443, _resolver())

    def boom(host: str, port: int, **_kw: object) -> list[tuple]:
        raise OSError("nxdomain")

    with pytest.raises(PublicFetchError, match="video_host_unresolved"):
        resolve_public("cdn.example.test", 443, boom)


# --- 연결 고정(재바인딩) ---
class _Recording(httpcore.NetworkBackend):
    def __init__(self) -> None:
        self.connected: list[str] = []

    def connect_tcp(self, host, port, timeout=None, local_address=None, socket_options=None):
        self.connected.append(host)
        raise httpcore.ConnectError("stop")

    def connect_unix_socket(self, path, timeout=None, socket_options=None):
        raise AssertionError

    def sleep(self, seconds):
        return None


def test_connection_opens_only_to_checked_ip() -> None:
    inner = _Recording()
    calls = {"n": 0}

    def rebinding(host: str, port: int, **_kw: object) -> list[tuple]:
        calls["n"] += 1  # 두 번째 풀이는 사설로 바뀐다 - 연결은 첫(검사한) 답만 쓴다
        return [(2, 1, 6, "", (PUBLIC if calls["n"] == 1 else "169.254.169.254", port))]

    backend = PublicOnlyBackend(rebinding, inner)
    with pytest.raises(httpcore.ConnectError):
        backend.connect_tcp("cdn.example.test", 443)
    assert inner.connected == [PUBLIC] and calls["n"] == 1


def test_backend_refuses_private_without_connecting() -> None:
    inner = _Recording()
    backend = PublicOnlyBackend(_resolver("10.0.0.5"), inner)
    with pytest.raises(httpcore.ConnectError, match="blocked_video_host"):
        backend.connect_tcp("cdn.example.test", 443)
    assert inner.connected == []


def test_transport_connect_block_maps_to_reason_code() -> None:
    transport = safe_fetch.PublicOnlyTransport(_resolver("10.0.0.5"))
    with httpx.Client(transport=transport) as client:
        with pytest.raises(httpx.ConnectError, match="blocked_video_host"):
            client.get("https://cdn.example.test/x.mp4")


# --- fetch_public ---
def _fetch(handler, url="https://cdn.example.test/a.mp4", resolver=None, **kw) -> bytes:
    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        return fetch_public(client, url, resolver=resolver or _resolver(PUBLIC), **kw)


def _redirect(location: str):
    return lambda _r: httpx.Response(302, headers={"location": location})


def test_success_returns_body() -> None:
    assert _fetch(lambda _r: httpx.Response(200, content=b"abc")) == b"abc"


def test_non_https_and_hostless_refused() -> None:
    for url in ("http://cdn.example.test/a.mp4", "https:///a.mp4", "ftp://x/a"):
        with pytest.raises(PublicFetchError, match="unexpected_video_uri"):
            _fetch(lambda _r: httpx.Response(200), url)


def test_redirect_public_to_private_refused_before_request() -> None:
    seen: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req.url.host)
        return httpx.Response(302, headers={"location": "https://meta.internal.test/x"})

    def resolver(host: str, port: int, **_kw: object) -> list[tuple]:
        return [(2, 1, 6, "", ("169.254.169.254" if host.startswith("meta") else PUBLIC, port))]

    with pytest.raises(PublicFetchError, match="blocked_video_host"):
        _fetch(handler, resolver=resolver)
    assert seen == ["cdn.example.test"]


def test_redirect_to_ip_literal_private_refused() -> None:
    with pytest.raises(PublicFetchError, match="blocked_video_host"):
        _fetch(_redirect("https://169.254.169.254/x"))


def test_redirect_https_to_http_refused() -> None:
    with pytest.raises(PublicFetchError, match="unexpected_video_uri"):
        _fetch(_redirect("http://cdn.example.test/a"))


def test_too_many_redirects() -> None:
    with pytest.raises(PublicFetchError, match="too_many_redirects"):
        _fetch(_redirect("/again"))


def test_redirect_without_location_and_http_error() -> None:
    with pytest.raises(PublicFetchError, match="invalid_redirect"):
        _fetch(lambda _r: httpx.Response(302))
    with pytest.raises(PublicFetchError, match="http_404"):
        _fetch(lambda _r: httpx.Response(404))


def test_content_length_over_cap_refused_without_reading_body() -> None:
    class Boom(httpx.SyncByteStream):
        def __iter__(self):
            raise AssertionError("본문을 읽으면 안 된다")

    def handler(_r: httpx.Request) -> httpx.Response:
        return httpx.Response(200, headers={"content-length": "100"}, stream=Boom())

    with pytest.raises(PublicFetchError, match="invalid_video_size"):
        _fetch(handler, max_bytes=10)


def test_stream_over_cap_aborts() -> None:
    read: list[int] = []

    class Chunks(httpx.SyncByteStream):
        def __iter__(self):
            for _ in range(100):
                read.append(1)
                yield b"x" * 4

    with pytest.raises(PublicFetchError, match="invalid_video_size"):
        _fetch(lambda _r: httpx.Response(200, stream=Chunks()), max_bytes=10)
    assert len(read) == 3  # 12바이트째에서 멈춘다


def test_exact_cap_passes() -> None:
    assert _fetch(lambda _r: httpx.Response(200, content=b"x" * 10), max_bytes=10) == b"x" * 10


def test_deadline_aborts() -> None:
    ticks = iter([0.0, 1.0, 999.0, 999.0])

    class Chunks(httpx.SyncByteStream):
        def __iter__(self):
            yield b"a"
            yield b"b"

    with pytest.raises(PublicFetchError, match="download_timeout"):
        _fetch(
            lambda _r: httpx.Response(200, stream=Chunks()),
            deadline_s=10,
            monotonic=lambda: next(ticks),
        )


def test_header_hook_and_custom_error_type() -> None:
    class MyError(MediaProviderError):
        pass

    seen: list[httpx.Request] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req)
        return httpx.Response(200, content=b"ok")

    _fetch(handler, headers_for=lambda _u: {"x-test": "1"})
    assert seen[0].headers["x-test"] == "1"
    with pytest.raises(MyError, match="blocked_video_host"):
        _fetch(handler, resolver=_resolver("127.0.0.1"), error=MyError)
