"""구글 애즈 광고 성과 커넥터 (REST `googleAds:search` + GAQL, 읽기만, 11/26).

공식 문서로 확인한 것 (2026-11-26):
- 최신 버전 v26 (2026-10-14, v26.2 2026-11-11) https://developers.google.com/google-ads/api/docs/release-notes
- `POST https://googleads.googleapis.com/v26/customers/{고객 번호}/googleAds:search`
  본문 {"query": GAQL, "pageToken"}, 머리글 Authorization: Bearer · developer-token ·
  login-customer-id(관리자 계정으로 볼 때) https://developers.google.com/google-ads/api/rest/examples
  `search`는 조회(GAQL SELECT)만 한다. 계정을 바꾸는 경로는 부르지 않는다(시험으로 확인)
- 지표 이름 (v26 fields): metrics.impressions·clicks·ctr(비율)·cost_micros·conversions,
  영상 조회는 metrics.video_trueview_views·video_trueview_view_rate
  (v26에 metrics.video_views·video_view_rate는 없다)
  https://developers.google.com/google-ads/api/fields/v26/metrics
- 접근 토큰: `POST https://oauth2.googleapis.com/token` (refresh_token, 메모리에만)

env (정식 이름이 먼저, 비어 있으면 옛 이름):
`DATA_GADS_DEVELOPER_TOKEN`(옛 `GOOGLE_ADS_DEVELOPER_TOKEN`) ·
`DATA_GADS_OAUTH_CLIENT_ID`(옛 `GOOGLE_ADS_CLIENT_ID`) ·
`DATA_GADS_OAUTH_CLIENT_SECRET`(옛 `GOOGLE_ADS_CLIENT_SECRET`) ·
`DATA_GADS_REFRESH_TOKEN`(옛 `GOOGLE_ADS_REFRESH_TOKEN`) ·
`DATA_GADS_LOGIN_CUSTOMER_ID`(옛 `GOOGLE_ADS_LOGIN_CUSTOMER_ID`, 선택) ·
`DATA_GADS_CUSTOMER_ID`(옛 `GOOGLE_ADS_CUSTOMER_ID`, 회사 설정 [ads] google_customer_id가 먼저).
버전 `DATA_GADS_API_VERSION`(기본 v26). 하루 상한 `DATA_GADS_DAILY_MAX`(기본 500, Explorer 하루
2,880번 안). 토큰·주소는 로그·오류·응답에 넣지 않는다.
"""

from __future__ import annotations

import os
import re
import time
from collections.abc import Mapping
from typing import Any

import httpx

from data_agent.connectors import ConnectorUnavailable
from data_agent.connectors.naver_searchad import JsonDailyCache, limit_of
from data_agent.stage_timing import stage

DEVELOPER_TOKEN_ENV = ("DATA_GADS_DEVELOPER_TOKEN", "GOOGLE_ADS_DEVELOPER_TOKEN")
CLIENT_ID_ENV = ("DATA_GADS_OAUTH_CLIENT_ID", "GOOGLE_ADS_CLIENT_ID")
CLIENT_SECRET_ENV = ("DATA_GADS_OAUTH_CLIENT_SECRET", "GOOGLE_ADS_CLIENT_SECRET")
REFRESH_TOKEN_ENV = ("DATA_GADS_REFRESH_TOKEN", "GOOGLE_ADS_REFRESH_TOKEN")
LOGIN_CUSTOMER_ENV = ("DATA_GADS_LOGIN_CUSTOMER_ID", "GOOGLE_ADS_LOGIN_CUSTOMER_ID")
CUSTOMER_ENV = ("DATA_GADS_CUSTOMER_ID", "GOOGLE_ADS_CUSTOMER_ID")
KEY_ENVS = (DEVELOPER_TOKEN_ENV, CLIENT_ID_ENV, CLIENT_SECRET_ENV, REFRESH_TOKEN_ENV)
VERSION_ENV, DEFAULT_VERSION = "DATA_GADS_API_VERSION", "v26"
DAILY_MAX_ENV, DEFAULT_DAILY_MAX = "DATA_GADS_DAILY_MAX", 500
CACHE_DB_ENV = "DATA_GADS_CACHE_DB"
TOKEN_URL = "https://oauth2.googleapis.com/token"  # noqa: S105 - 주소 (비밀값 아님)
HOST = "https://googleads.googleapis.com"
TIMEOUT_SECONDS = 20.0
MAX_PAGES = 3
TOKEN_MARGIN_SECONDS = 60
_ID = re.compile(r"^\d{10}$")

_calls = JsonDailyCache(os.environ.get(CACHE_DB_ENV) or None)


def clear_calls() -> None:
    _calls.clear()


def env_value(env: Mapping[str, str], names: tuple[str, str]) -> str:
    """정식 이름이 먼저, 비어 있으면 옛 이름."""
    return (env.get(names[0]) or "").strip() or (env.get(names[1]) or "").strip()


def missing(env: Mapping[str, str]) -> list[str]:
    return [names[0] for names in KEY_ENVS if not env_value(env, names)]


def customer_id(raw: str) -> str:
    """고객 번호 "123-456-7890" → "1234567890". 10자리가 아니면 ""."""
    digits = re.sub(r"[\s-]", "", raw or "")
    return digits if _ID.match(digits) else ""


class GoogleAdsError(ConnectorUnavailable):
    """code: KEY_MISSING · TOKEN_FAILED · PERMISSION · RATE_LIMITED · NOT_FOUND · UNAVAILABLE ·
    BAD_RESPONSE."""


def _error(status: int) -> GoogleAdsError:
    if status in (401, 403):
        why = f"권한 없음 (HTTP {status}, 개발자 토큰·계정 접근 확인)"
        return GoogleAdsError("PERMISSION", why)
    if status == 404:
        return GoogleAdsError("NOT_FOUND", "고객 계정을 찾을 수 없음 (HTTP 404)")
    if status == 429:
        return GoogleAdsError("RATE_LIMITED", "구글 애즈 호출 제한 (HTTP 429)")
    return GoogleAdsError("UNAVAILABLE", f"구글 애즈 응답 {status}")


class GoogleAds:
    """GAQL 조회만 (googleAds:search)."""

    def __init__(self, env: Mapping[str, str] | None = None, *,
                 client: httpx.AsyncClient | None = None,
                 calls: JsonDailyCache | None = None) -> None:  # fmt: skip
        env = os.environ if env is None else env
        self._env = env
        self._developer = env_value(env, DEVELOPER_TOKEN_ENV)
        self._client_id = env_value(env, CLIENT_ID_ENV)
        self._secret = env_value(env, CLIENT_SECRET_ENV)
        self._refresh = env_value(env, REFRESH_TOKEN_ENV)
        self.login_customer = customer_id(env_value(env, LOGIN_CUSTOMER_ENV))
        self.default_customer = customer_id(env_value(env, CUSTOMER_ENV))
        self.version = (env.get(VERSION_ENV) or "").strip() or DEFAULT_VERSION
        self.daily_max = limit_of(env, DAILY_MAX_ENV, DEFAULT_DAILY_MAX)
        self._client = client
        self._calls = calls or _calls
        self._token = ""
        self._expires = 0.0

    def __repr__(self) -> str:  # 토큰을 담지 않는다
        return f"GoogleAds(version={self.version!r}, configured={self.configured})"

    @property
    def configured(self) -> bool:
        return bool(self._developer and self._client_id and self._secret and self._refresh)

    async def _send(self, url: str, **kw: Any) -> httpx.Response:
        client = self._client or httpx.AsyncClient(timeout=TIMEOUT_SECONDS)
        try:
            with stage("external"):
                return await client.request("POST", url, **kw)
        except httpx.HTTPError as exc:  # 주소·토큰을 오류 문장에 넣지 않는다
            why = f"구글에 연결할 수 없다 ({type(exc).__name__})"
            raise GoogleAdsError("UNAVAILABLE", why) from None
        finally:
            if client is not self._client:
                await client.aclose()

    async def _access_token(self) -> str:
        if not self.configured:
            why = f"{', '.join(missing(self._env))}가 없음 (연결 대기)"
            raise GoogleAdsError("KEY_MISSING", why)
        if self._token and time.monotonic() < self._expires:
            return self._token
        self._calls.count_call(self.daily_max, "구글 애즈 API")
        response = await self._send(TOKEN_URL, data={
            "grant_type": "refresh_token", "client_id": self._client_id,
            "client_secret": self._secret, "refresh_token": self._refresh,
        })  # fmt: skip
        try:
            payload = response.json()
        except ValueError:
            payload = None
        token = payload.get("access_token") if isinstance(payload, dict) else None
        if response.status_code != 200 or not isinstance(token, str) or not token:
            err = payload.get("error") if isinstance(payload, dict) else None
            code = err if isinstance(err, str) and err.isascii() and len(err) < 40 else "error"
            why = f"접근 토큰을 받지 못함 (HTTP {response.status_code}, {code})"
            raise GoogleAdsError("TOKEN_FAILED", why)
        life = payload.get("expires_in")
        seconds = life if isinstance(life, int) and life > 0 else 3600
        self._token = token
        self._expires = time.monotonic() + max(seconds - TOKEN_MARGIN_SECONDS, 0)
        return token

    async def search(self, customer: str, query: str) -> list[dict[str, Any]]:
        """GAQL 조회 결과 줄 (페이지 최대 3). 조회(SELECT)만 받는다."""
        if not query.lstrip().upper().startswith("SELECT"):
            raise GoogleAdsError("BAD_RESPONSE", "GAQL 조회(SELECT)만 보낸다")
        cid = customer_id(customer)
        if not cid:
            raise GoogleAdsError("NOT_FOUND", "구글 애즈 고객 번호가 10자리가 아니다")
        token = await self._access_token()
        headers = {"Authorization": f"Bearer {token}", "developer-token": self._developer}
        if self.login_customer:
            headers["login-customer-id"] = self.login_customer
        url = f"{HOST}/{self.version}/customers/{cid}/googleAds:search"
        body: dict[str, str] = {"query": query}
        rows: list[dict[str, Any]] = []
        for _ in range(MAX_PAGES):
            self._calls.count_call(self.daily_max, "구글 애즈 API")
            response = await self._send(url, json=body, headers=headers)
            if response.status_code != 200:
                raise _error(response.status_code)
            try:
                payload = response.json()
            except ValueError:
                raise GoogleAdsError("BAD_RESPONSE", "구글 애즈 응답이 JSON이 아니다") from None
            rows += [r for r in (payload.get("results") or []) if isinstance(r, dict)]
            nxt = payload.get("nextPageToken")
            if not isinstance(nxt, str) or not nxt:
                break
            body = {"query": query, "pageToken": nxt}
        return rows
