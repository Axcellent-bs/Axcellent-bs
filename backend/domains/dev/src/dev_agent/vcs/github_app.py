"""GitHub App 인증: 공용 구현(`ax_agent_template.github_app`)을 다시 내보낸다.

공용 쪽은 `ToolConnectionError`를 던진다. 개발 AI의 연결 부품은 `VcsError`로 다루므로
같은 코드·문구로 바꿔 던지는 얇은 감싸기만 둔다.
"""

from __future__ import annotations

import time
from collections.abc import Callable

import httpx
from cryptography.hazmat.primitives.asymmetric import rsa

from ax_agent_template import github_app as _shared
from ax_agent_template.tool_connections import ToolConnectionError
from dev_agent.vcs.base import VcsError

JWT_BACK = _shared.JWT_BACK
JWT_LIFE = _shared.JWT_LIFE
REFRESH_BEFORE = _shared.REFRESH_BEFORE


def load_key(pem: str) -> rsa.RSAPrivateKey:
    """PEM 개인 키를 읽는다. 틀리면 `VcsError`(config)."""
    try:
        return _shared.load_key(pem)
    except ToolConnectionError as exc:
        raise VcsError(exc.code, exc.message) from None


def check_id(raw: str, what: str) -> str:
    try:
        return _shared.check_id(raw, what)
    except ToolConnectionError as exc:
        raise VcsError(exc.code, exc.message) from None


class GithubAppAuth(_shared.GithubAppAuth):
    """공용 `GithubAppAuth`와 같고, 오류만 `VcsError`로 바꿔 던진다."""

    def __init__(
        self,
        app_id: str,
        installation_id: str,
        private_key: str,
        *,
        clock: Callable[[], float] | None = None,
    ):
        try:
            super().__init__(app_id, installation_id, private_key, clock=clock or time.time)
        except ToolConnectionError as exc:
            raise VcsError(exc.code, exc.message) from None

    async def token(self, client: httpx.AsyncClient, base_url: str) -> str:
        try:
            return await super().token(client, base_url)
        except ToolConnectionError as exc:
            raise VcsError(exc.code, exc.message) from None
