"""GitHub App 인증: 조직에 설치된 앱으로 레포에 붙는다 (사람 토큰 없이).

1. 앱 개인 키로 JWT(RS256, 10분 미만)를 만든다
2. `POST /app/installations/{설치 id}/access_tokens`로 설치 토큰(1시간)을 받는다
3. 끝나기 5분 전까지 같은 토큰을 쓰고, 그 뒤에는 다시 받는다

- 조직 관리자가 앱을 설치하면서 레포와 권한을 고르므로, 개인 계정 권한·퇴사와 상관없다.
- 개인 키·JWT·설치 토큰은 메모리에만 두고 repr·로그·오류 문구·응답에 넣지 않는다.
"""

from __future__ import annotations

import asyncio
import base64
import json
import time
from collections.abc import Callable
from datetime import datetime

import httpx
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from planning_agent.vcs.base import VcsError

# JWT는 GitHub가 10분까지 받는다. 시계 차이를 생각해 1분 앞에서 시작해 9분 뒤에 끝낸다
JWT_BACK = 60
JWT_LIFE = 540
# 설치 토큰이 이만큼(초) 남으면 새로 받는다
REFRESH_BEFORE = 300


def _b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")


def load_key(pem: str) -> rsa.RSAPrivateKey:
    """PEM 개인 키를 읽는다. 틀리면 `VcsError`(config). 키 내용은 문구에 넣지 않는다."""
    text = (pem or "").strip()
    if "PRIVATE KEY" not in text:
        raise VcsError("config", "GitHub App 개인 키(.pem) 형식이 아니다")
    try:
        key = serialization.load_pem_private_key(text.encode("ascii"), password=None)
    except (ValueError, TypeError, UnicodeEncodeError):
        raise VcsError("config", "GitHub App 개인 키를 읽지 못했다") from None
    if not isinstance(key, rsa.RSAPrivateKey):
        raise VcsError("config", "GitHub App 개인 키는 RSA 키여야 한다")
    return key


def check_id(raw: str, what: str) -> str:
    value = (raw or "").strip()
    if not value.isdigit():
        raise VcsError("config", f"{what}는 숫자다")
    return value


class GithubAppAuth:
    """설치 토큰을 받아 두고 나눠 준다. 연결(레포)마다 하나."""

    def __init__(
        self,
        app_id: str,
        installation_id: str,
        private_key: str,
        *,
        clock: Callable[[], float] | None = None,
    ):
        self.app_id = check_id(app_id, "앱 ID")
        self.installation_id = check_id(installation_id, "설치 ID")
        self.__key = load_key(private_key)
        self._clock = clock or time.time
        self.__token = ""
        self._expires = 0.0
        self._lock = asyncio.Lock()

    def __repr__(self) -> str:
        return f"GithubAppAuth(app_id={self.app_id!r}, installation_id={self.installation_id!r})"

    def jwt(self) -> str:
        now = int(self._clock())
        head = _b64(json.dumps({"alg": "RS256", "typ": "JWT"}, separators=(",", ":")).encode())
        body = _b64(
            json.dumps(
                {"iat": now - JWT_BACK, "exp": now + JWT_LIFE, "iss": self.app_id},
                separators=(",", ":"),
            ).encode()
        )
        signed = f"{head}.{body}".encode("ascii")
        sig = self.__key.sign(signed, padding.PKCS1v15(), hashes.SHA256())
        return f"{head}.{body}.{_b64(sig)}"

    def secrets(self) -> list[str]:
        """오류 문구에서 가릴 값 (지금 쓰는 설치 토큰)."""
        return [self.__token] if self.__token else []

    async def token(self, client: httpx.AsyncClient, base_url: str) -> str:
        """설치 토큰. 남은 시간이 넉넉하면 받아 둔 것을 준다."""
        if self.__token and self._expires - self._clock() > REFRESH_BEFORE:
            return self.__token
        async with self._lock:
            if self.__token and self._expires - self._clock() > REFRESH_BEFORE:
                return self.__token
            where = f"POST /app/installations/{self.installation_id}/access_tokens"
            try:
                res = await client.post(
                    f"{base_url}/app/installations/{self.installation_id}/access_tokens",
                    headers={
                        "Authorization": f"Bearer {self.jwt()}",
                        "Accept": "application/vnd.github+json",
                        "X-GitHub-Api-Version": "2022-11-28",
                    },
                )
            except httpx.HTTPError as exc:
                raise VcsError("unavailable", f"{where} 연결 실패 ({type(exc).__name__})") from None
            if res.status_code >= 400:
                code = {401: "unauthorized", 403: "forbidden", 404: "not_found"}.get(
                    res.status_code, "unavailable"
                )
                # 응답 본문은 넣지 않는다 (앱·설치 정보만 남긴다)
                raise VcsError(code, f"{where} → {res.status_code} (앱 ID·설치 ID·키를 확인)")
            try:
                got = res.json()
                token = str(got["token"])
                expires = datetime.fromisoformat(str(got["expires_at"]).replace("Z", "+00:00"))
            except (ValueError, KeyError, TypeError):
                raise VcsError("unavailable", f"{where} 응답 형식이 다르다") from None
            self.__token = token
            self._expires = expires.timestamp()
            return token
