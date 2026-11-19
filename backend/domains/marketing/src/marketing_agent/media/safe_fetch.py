"""공용 인터넷 주소로만 영상을 내려받는 도우미 (SSRF 차단, 동기).

공급자 응답의 영상 URL이 사설·루프백·링크로컬(169.254.169.254 메타데이터 포함) 주소를 가리켜도
서버가 그대로 요청하지 않도록 한다. 정책은 `ax_agent_template.pinned_http`와 반대(공용만 허용)이고
그쪽은 async라 재사용하지 않고 방식만 따른다.

- `resolve_public(host, port)`: IP 리터럴이면 그대로, 이름이면 모든 A/AAAA를 풀어 하나라도
  `is_global`이 아니면 막는다(`blocked_video_host`). 풀리지 않으면 `video_host_unresolved`.
  IPv4-mapped·NAT64·6to4 IPv6는 안의 IPv4로 풀어 본다. `0x7f.1`·`2130706433` 같은 옛 표기도 푼다.
- `PublicOnlyTransport`: 연결 직전에 이름을 한 번 풀고 검사한 IP로만 TCP를 연다. Host 머리글·SNI·
  인증서는 원래 이름 그대로다. 요청(=리다이렉트 홉)마다 검사되므로 DNS 재바인딩 창이 닫힌다.
  이 transport를 쓰면 HTTP(S)_PROXY 환경 변수는 쓰지 않는다(httpx 동작).
- `fetch_public(...)`: https만, 홉마다 재검사, 크기·총 시간 상한을 지켜 받는다. 오류는 사유 코드만.
"""

from __future__ import annotations

import ipaddress
import socket
import time
from collections.abc import Callable, Iterable
from typing import Any
from urllib.parse import urljoin, urlsplit

import httpcore
import httpx

from marketing_agent.media.base import MediaProviderError

MAX_VIDEO_BYTES = 64 * 1024 * 1024
MAX_REDIRECTS = 3
DOWNLOAD_DEADLINE_S = 120.0
REDIRECT_STATUSES = (301, 302, 303, 307, 308)
BLOCKED = "blocked_video_host"
UNRESOLVED = "video_host_unresolved"

# socket.getaddrinfo 호환: (host, port, type=...) -> [(family, type, proto, canon, (ip, ...))]
Resolver = Callable[..., Any]

_NAT64 = ipaddress.ip_network("64:ff9b::/96")
_SIX_TO_FOUR = ipaddress.ip_network("2002::/16")


class PublicFetchError(MediaProviderError):
    """공용 주소 검사·내려받기 실패. 메시지는 사유 코드뿐이다."""


def _embedded_v4(addr: ipaddress.IPv6Address) -> ipaddress.IPv4Address | None:
    if addr.ipv4_mapped is not None:
        return addr.ipv4_mapped
    if addr in _NAT64:
        return ipaddress.IPv4Address(int(addr) & 0xFFFFFFFF)
    if addr in _SIX_TO_FOUR:
        return ipaddress.IPv4Address((int(addr) >> 80) & 0xFFFFFFFF)
    return None


def is_public(addr: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    if isinstance(addr, ipaddress.IPv6Address):
        inner = _embedded_v4(addr)
        if inner is not None:
            return is_public(inner)
    return addr.is_global and not addr.is_multicast


def _literal(host: str) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    host = host.strip("[]").split("%", 1)[0]
    try:
        return ipaddress.ip_address(host)
    except ValueError:
        pass
    try:  # 정수·16진·8진·축약 표기(inet_aton)
        return ipaddress.IPv4Address(socket.inet_aton(host))
    except (OSError, ValueError):
        return None


def resolve_public(
    host: str, port: int = 443, resolver: Resolver = socket.getaddrinfo
) -> list[str]:
    """검사를 통과한 IP 문자열 목록. 막히면 PublicFetchError(사유 코드)."""
    literal = _literal(host)
    if literal is not None:
        if not is_public(literal):
            raise PublicFetchError(BLOCKED)
        return [str(literal)]
    try:
        infos = resolver(host, port, type=socket.SOCK_STREAM)
    except (OSError, UnicodeError):
        raise PublicFetchError(UNRESOLVED) from None
    ips: list[str] = []
    for info in infos:
        try:
            addr = ipaddress.ip_address(str(info[4][0]).split("%", 1)[0])
        except (ValueError, IndexError, TypeError):
            raise PublicFetchError(BLOCKED) from None
        if not is_public(addr):
            raise PublicFetchError(BLOCKED)
        if str(addr) not in ips:
            ips.append(str(addr))
    if not ips:
        raise PublicFetchError(UNRESOLVED)
    return ips


class PublicOnlyBackend(httpcore.NetworkBackend):
    """연결 직전에 이름을 한 번 풀고 검사한 뒤, 그 IP로만 붙는 네트워크 백엔드."""

    def __init__(
        self, resolver: Resolver = socket.getaddrinfo, inner: httpcore.NetworkBackend | None = None
    ) -> None:
        self._resolver = resolver
        self._inner = inner or httpcore.SyncBackend()

    def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Iterable[Any] | None = None,
    ) -> httpcore.NetworkStream:
        try:
            ips = resolve_public(host, port, self._resolver)
        except PublicFetchError as error:
            raise httpcore.ConnectError(str(error)) from None
        last: httpcore.ConnectError | None = None
        for ip in ips:
            try:
                return self._inner.connect_tcp(
                    ip,
                    port,
                    timeout=timeout,
                    local_address=local_address,
                    socket_options=socket_options,
                )
            except httpcore.ConnectError as error:  # 다음 IP를 해 본다
                last = error
        raise last or httpcore.ConnectError("video_connect_failed")

    def connect_unix_socket(
        self,
        path: str,
        timeout: float | None = None,
        socket_options: Iterable[Any] | None = None,
    ) -> httpcore.NetworkStream:
        raise httpcore.ConnectError(BLOCKED)

    def sleep(self, seconds: float) -> None:
        self._inner.sleep(seconds)


class PublicOnlyTransport(httpx.HTTPTransport):
    """공용 주소로만 연결하는 httpx transport."""

    def __init__(
        self,
        resolver: Resolver = socket.getaddrinfo,
        *,
        network_backend: httpcore.NetworkBackend | None = None,
    ) -> None:
        ssl_context = httpx.create_ssl_context()
        super().__init__(verify=ssl_context)
        self._pool = httpcore.ConnectionPool(
            ssl_context=ssl_context,
            network_backend=PublicOnlyBackend(resolver, network_backend),
        )


def _target(url: str) -> tuple[str, str, int] | None:
    try:
        parts = urlsplit(url)
        host = parts.hostname
        port = parts.port or 443
    except ValueError:
        return None
    if not host:
        return None
    return parts.scheme, host, port


def fetch_public(
    client: httpx.Client,
    url: str,
    *,
    resolver: Resolver = socket.getaddrinfo,
    headers_for: Callable[[str], dict[str, str]] | None = None,
    non_https_code: str = "unexpected_video_uri",
    error: type[MediaProviderError] = PublicFetchError,
    max_bytes: int = MAX_VIDEO_BYTES,
    deadline_s: float = DOWNLOAD_DEADLINE_S,
    monotonic: Callable[[], float] = time.monotonic,
) -> bytes:
    """https·공용 주소만 따라가며 영상을 받는다. 리다이렉트는 직접 따라가며 홉마다 재검사한다."""
    started = monotonic()
    for _ in range(MAX_REDIRECTS + 1):
        target = _target(url)
        if target is None or target[0] != "https":
            raise error(non_https_code)
        try:
            resolve_public(target[1], target[2], resolver)
        except PublicFetchError as blocked:
            raise error(str(blocked)) from None
        headers = headers_for(url) if headers_for else {}
        try:
            with client.stream("GET", url, headers=headers) as response:
                if response.status_code in REDIRECT_STATUSES:
                    location = response.headers.get("location")
                    if not location:
                        raise error("invalid_redirect")
                    url = urljoin(url, location)
                    continue
                if response.status_code != 200:
                    raise error(f"http_{response.status_code}")
                declared = response.headers.get("content-length", "")
                if declared.isdigit() and int(declared) > max_bytes:
                    raise error("invalid_video_size")
                chunks: list[bytes] = []
                total = 0
                for chunk in response.iter_bytes():
                    total += len(chunk)
                    if total > max_bytes:
                        raise error("invalid_video_size")
                    if monotonic() - started > deadline_s:
                        raise error("download_timeout")
                    chunks.append(chunk)
                return b"".join(chunks)
        except httpx.ConnectError as exc:
            if str(exc) in (BLOCKED, UNRESOLVED):  # 연결 시점 검사(재바인딩 포함)에 막힘
                raise error(str(exc)) from None
            raise
    raise error("too_many_redirects")
