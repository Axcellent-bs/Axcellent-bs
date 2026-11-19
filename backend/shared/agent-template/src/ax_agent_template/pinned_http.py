"""검사한 IP로만 연결하는 httpx transport (DNS 재바인딩·리다이렉트 SSRF 차단).

`company_connection.check_host`는 이름을 한 번 풀어서 정책을 본다. 그런데 httpx가 연결할 때
이름을 다시 풀면, 그 사이 DNS 답을 바꿔(재바인딩) 메타데이터·내부 주소로 붙일 수 있다.
여기 transport는 연결 직전에 이름을 **한 번만** 풀고, 그 IP 목록을 같은 정책
(`company_connection.check_addrs`)으로 본 뒤, 검사한 IP로만 TCP를 연다.
Host 머리글과 TLS SNI·인증서 확인은 원래 이름 그대로다(httpcore가 origin 이름을 쓴다).

- 정책은 요청마다 본다. 리다이렉트를 따라가도(`follow_redirects=True`) 홉마다 새 요청이라
  다시 검사된다. 그래도 회사 주소를 부르는 곳은 리다이렉트를 끈다(httpx 기본 False).
- 정책을 볼 호스트는 `guard(host)`가 정한다.
  - `guarded_transport(exempt=...)`: 정해 둔 공용 API(예: api.github.com) 말고는 모두 검사
  - `company_transport()`: 회사 연결이 가리키는 호스트만 검사 (그 밖은 예전 그대로 연결)
- 로컬 개발 예외는 `check_host`와 같다: 사내 주소(localhost·10/8 등)는 받고, 사내 밖은
  `AX_COMPANY_SHOP_ALLOWED_HOSTS` + https.
- 이 transport를 쓰면 환경 변수 프록시(HTTP(S)_PROXY)는 쓰지 않는다(httpx 동작).
- 정책에 어긋나면 `httpx.ConnectError`(= `httpx.HTTPError`)가 난다. 이유가 문구에 들어간다.
"""

from __future__ import annotations

import asyncio
import contextvars
from collections.abc import Callable, Iterable
from typing import Any

import httpcore
import httpx

from ax_agent_template import company_connection

Guard = Callable[[str], bool]

# 지금 요청의 scheme (검사할 요청일 때만). 연결은 요청을 보낸 작업 안에서 열리므로 여기서 읽힌다
_SCHEME: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "ax_pinned_scheme", default=None
)


class PinnedBackend(httpcore.AsyncNetworkBackend):
    """연결 직전에 이름을 한 번 풀고 정책을 본 뒤, 그 IP로만 붙는 네트워크 백엔드."""

    def __init__(self, inner: httpcore.AsyncNetworkBackend | None = None) -> None:
        self._inner = inner or httpcore.AnyIOBackend()

    async def connect_tcp(
        self,
        host: str,
        port: int,
        timeout: float | None = None,
        local_address: str | None = None,
        socket_options: Iterable[Any] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        scheme = _SCHEME.get()
        if scheme is None:  # 검사하지 않는 호스트
            return await self._inner.connect_tcp(
                host,
                port,
                timeout=timeout,
                local_address=local_address,
                socket_options=socket_options,
            )
        try:
            # 모듈 속성으로 불러 시험이 이름 풀이를 바꿔 끼울 수 있게 한다
            addrs = await asyncio.to_thread(company_connection._resolve, host.lower())
            company_connection.check_addrs(host, scheme, addrs)
        except ValueError as exc:
            raise httpcore.ConnectError(f"주소 정책에 막혔습니다 ({host}): {exc}") from None
        last: httpcore.ConnectError | None = None
        for addr in addrs:
            try:
                return await self._inner.connect_tcp(
                    str(addr),
                    port,
                    timeout=timeout,
                    local_address=local_address,
                    socket_options=socket_options,
                )
            except httpcore.ConnectError as exc:  # 다음 IP를 해 본다
                last = exc
        raise last or httpcore.ConnectError(f"연결할 수 없습니다 ({host})")

    async def connect_unix_socket(
        self,
        path: str,
        timeout: float | None = None,
        socket_options: Iterable[Any] | None = None,
    ) -> httpcore.AsyncNetworkStream:
        raise httpcore.ConnectError("유닉스 소켓 연결은 쓰지 않습니다")

    async def sleep(self, seconds: float) -> None:
        await self._inner.sleep(seconds)


class PinnedTransport(httpx.AsyncHTTPTransport):
    """`guard(host)`가 참인 요청은 검사한 IP로만 연결한다."""

    def __init__(
        self,
        guard: Guard,
        *,
        network_backend: httpcore.AsyncNetworkBackend | None = None,
    ) -> None:
        ssl_context = httpx.create_ssl_context()
        super().__init__(verify=ssl_context)
        self._guard = guard
        self._pool = httpcore.AsyncConnectionPool(
            ssl_context=ssl_context,
            network_backend=PinnedBackend(network_backend),
        )

    async def handle_async_request(self, request: httpx.Request) -> httpx.Response:
        host = request.url.host.lower()
        scheme = request.url.scheme if self._guard(host) else None
        token = _SCHEME.set(scheme)
        try:
            return await super().handle_async_request(request)
        finally:
            _SCHEME.reset(token)


def guarded_transport(
    exempt: Iterable[str] = (), *, network_backend: httpcore.AsyncNetworkBackend | None = None
) -> PinnedTransport:
    """`exempt`(정해 둔 공용 API 호스트) 말고는 모든 요청을 검사하는 transport."""
    skip = frozenset(h.lower() for h in exempt)
    return PinnedTransport(lambda host: host not in skip, network_backend=network_backend)


def company_transport(
    *, network_backend: httpcore.AsyncNetworkBackend | None = None
) -> PinnedTransport:
    """회사 연결이 가리키는 호스트만 검사하는 transport (AI들의 판매 서버 호출용)."""
    return PinnedTransport(
        lambda host: host in company_connection.company_hosts(),
        network_backend=network_backend,
    )
