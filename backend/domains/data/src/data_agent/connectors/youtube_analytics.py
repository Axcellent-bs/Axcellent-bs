"""YouTube Analytics·Reporting API 커넥터 (채널 주인 OAuth, 읽기만, 11/26).

공식 문서로 확인한 것 (2026-11-26):
- 접근 토큰: `POST https://oauth2.googleapis.com/token` grant_type=refresh_token (client_id·
  client_secret·refresh_token). 접근 토큰은 메모리에만 두고 저장하지 않는다
- Analytics: `GET https://youtubeanalytics.googleapis.com/v2/reports` ids=channel==MINE,
  startDate·endDate(YYYY-MM-DD), metrics, dimensions, filters(video==id). 응답
  `{"columnHeaders": [{"name"}], "rows": [[...]]}` (값이 없으면 rows가 없다)
  https://developers.google.com/youtube/analytics/reference/reports/query
- Reporting: `GET https://youtubereporting.googleapis.com/v1/jobs` → `jobs[]{id, reportTypeId}`,
  `GET /v1/jobs/{jobId}/reports` → `reports[]{id, startTime, endTime, createTime, downloadUrl}`.
  리포트 CSV는 downloadUrl을 같은 OAuth 토큰(Authorization)으로 받는다. 코드는 job을 만들지
  않는다(상태 변경). channel_reach_basic_a1: date, channel_id, video_id,
  video_thumbnail_impressions, video_thumbnail_impressions_ctr. channel_basic_a3: date, channel_id,
  video_id, …, views, watch_time_minutes, average_view_duration_seconds, …
  https://developers.google.com/youtube/reporting/v1/reports/channel_reports
- 토큰 채널: Data API `GET https://www.googleapis.com/youtube/v3/channels?part=snippet&mine=true`
  (1단위, YouTube 장부 `youtube_quota`에 같이 적는다) → id, snippet.customUrl(@handle)
- 스코프: https://www.googleapis.com/auth/yt-analytics.readonly (+ youtube.readonly)

env (정식 이름이 먼저, 비어 있으면 옛 이름):
`DATA_YT_OAUTH_CLIENT_ID`(옛 `YT_CLIENT_ID`) ·
`DATA_YT_OAUTH_CLIENT_SECRET`(옛 `YT_CLIENT_SECRET`) ·
`DATA_YT_REFRESH_TOKEN`(옛 `YT_REFRESH_TOKEN`).
하루 호출 상한 `DATA_YT_ANALYTICS_DAILY_LIMIT`(기본 200, 한국 날짜, 인스타 연결과 같은 방식).
토큰·주소는 로그·오류·응답에 넣지 않는다.
이 커넥터는 GET만 보낸다(토큰 갱신 POST 하나 빼고). 쓰기 경로는 없다(시험으로 확인).
"""

from __future__ import annotations

import csv
import io
import os
import time
from collections.abc import Mapping, Sequence
from typing import Any

import httpx

from data_agent import youtube_quota
from data_agent.connectors import ConnectorUnavailable
from data_agent.connectors.naver_searchad import JsonDailyCache, limit_of
from data_agent.stage_timing import stage

# (정식 이름, 옛 이름)
CLIENT_ID_ENV = ("DATA_YT_OAUTH_CLIENT_ID", "YT_CLIENT_ID")
CLIENT_SECRET_ENV = ("DATA_YT_OAUTH_CLIENT_SECRET", "YT_CLIENT_SECRET")
REFRESH_TOKEN_ENV = ("DATA_YT_REFRESH_TOKEN", "YT_REFRESH_TOKEN")
KEY_ENVS = (CLIENT_ID_ENV, CLIENT_SECRET_ENV, REFRESH_TOKEN_ENV)
DAILY_LIMIT_ENV = "DATA_YT_ANALYTICS_DAILY_LIMIT"
CACHE_DB_ENV = "DATA_YT_ANALYTICS_CACHE_DB"
DEFAULT_DAILY_LIMIT = 200
TOKEN_URL = "https://oauth2.googleapis.com/token"  # noqa: S105 - 주소 (비밀값 아님)
ANALYTICS_URL = "https://youtubeanalytics.googleapis.com/v2/reports"
REPORTING_URL = "https://youtubereporting.googleapis.com/v1"
CHANNELS_URL = "https://www.googleapis.com/youtube/v3/channels"
CALLER = "video_insights"  # YouTube 장부의 부른 곳 (channels.list 1단위)
TIMEOUT_SECONDS = 20.0
TOKEN_MARGIN_SECONDS = 60

_calls = JsonDailyCache(os.environ.get(CACHE_DB_ENV) or None)  # 하루 호출 수 (응답은 저장하지 않음)


def clear_calls() -> None:
    _calls.clear()


def env_value(env: Mapping[str, str], names: tuple[str, str]) -> str:
    """정식 이름이 먼저, 비어 있으면 옛 이름."""
    return (env.get(names[0]) or "").strip() or (env.get(names[1]) or "").strip()


def missing(env: Mapping[str, str]) -> list[str]:
    """비어 있는 env 정식 이름 (옛 이름도 비었을 때만)."""
    return [names[0] for names in KEY_ENVS if not env_value(env, names)]


class YtaError(ConnectorUnavailable):
    """code: KEY_MISSING · TOKEN_FAILED · PERMISSION · RATE_LIMITED · QUOTA_EXCEEDED · NOT_FOUND ·
    UNAVAILABLE · BAD_RESPONSE."""


def _error(status: int, what: str) -> YtaError:
    if status in (401, 403):
        return YtaError("PERMISSION", f"{what} 권한 없음 (HTTP {status}, 스코프·채널 주인 확인)")
    if status == 404:
        return YtaError("NOT_FOUND", f"{what} 없음 (HTTP 404)")
    if status == 429:
        return YtaError("RATE_LIMITED", f"{what} 호출 제한 (HTTP 429)")
    return YtaError("UNAVAILABLE", f"{what} 응답 {status}")


class YouTubeAnalytics:
    """채널 주인 OAuth로 읽기만. 키·한도는 `env`(기본 `os.environ`)."""

    def __init__(self, env: Mapping[str, str] | None = None, *,
                 client: httpx.AsyncClient | None = None,
                 calls: JsonDailyCache | None = None) -> None:  # fmt: skip
        env = os.environ if env is None else env
        self._env = env
        self._client_id = env_value(env, CLIENT_ID_ENV)
        self._secret = env_value(env, CLIENT_SECRET_ENV)
        self._refresh = env_value(env, REFRESH_TOKEN_ENV)
        self.daily_limit = limit_of(env, DAILY_LIMIT_ENV, DEFAULT_DAILY_LIMIT)
        self._client = client
        self._calls = calls or _calls
        self._token = ""
        self._expires = 0.0

    def __repr__(self) -> str:  # 토큰을 담지 않는다
        return f"YouTubeAnalytics(configured={self.configured})"

    @property
    def configured(self) -> bool:
        return bool(self._client_id and self._secret and self._refresh)

    async def _send(self, method: str, url: str, **kw: Any) -> httpx.Response:
        client = self._client or httpx.AsyncClient(timeout=TIMEOUT_SECONDS)
        try:
            with stage("external"):
                return await client.request(method, url, **kw)
        except httpx.HTTPError as exc:  # 주소·토큰을 오류 문장에 넣지 않는다
            why = f"YouTube에 연결할 수 없다 ({type(exc).__name__})"
            raise YtaError("UNAVAILABLE", why) from None
        finally:
            if client is not self._client:
                await client.aclose()

    async def _access_token(self) -> str:
        if not self.configured:
            raise YtaError("KEY_MISSING", f"{', '.join(missing(self._env))}가 없음 (연결 대기)")
        if self._token and time.monotonic() < self._expires:
            return self._token
        self._calls.count_call(self.daily_limit, "YouTube Analytics API")
        response = await self._send("POST", TOKEN_URL, data={
            "grant_type": "refresh_token", "client_id": self._client_id,
            "client_secret": self._secret, "refresh_token": self._refresh,
        })  # fmt: skip
        try:
            payload = response.json()
        except ValueError:
            payload = None
        token = payload.get("access_token") if isinstance(payload, dict) else None
        if response.status_code != 200 or not isinstance(token, str) or not token:
            # 오류 본문(error_description)에도 값이 섞일 수 있어 코드만 남긴다
            err = payload.get("error") if isinstance(payload, dict) else None
            code = err if isinstance(err, str) and err.isascii() and len(err) < 40 else "error"
            why = f"접근 토큰을 받지 못함 (HTTP {response.status_code}, {code})"
            raise YtaError("TOKEN_FAILED", why)
        life = payload.get("expires_in")
        seconds = life if isinstance(life, int) and life > 0 else 3600
        self._token = token
        self._expires = time.monotonic() + max(seconds - TOKEN_MARGIN_SECONDS, 0)
        return token

    async def _get(self, url: str, params: Mapping[str, str] | None, what: str,
                   *, raw: bool = False) -> Any:  # fmt: skip
        token = await self._access_token()
        self._calls.count_call(self.daily_limit, "YouTube Analytics API")
        response = await self._send("GET", url, params=dict(params or {}),
                                    headers={"Authorization": f"Bearer {token}"})  # fmt: skip
        if response.status_code != 200:
            raise _error(response.status_code, what)
        if raw:
            return response.text
        try:
            return response.json()
        except ValueError:
            raise YtaError("BAD_RESPONSE", f"{what} 응답이 JSON이 아니다") from None

    # ------------------------------------------------------------ 읽기

    async def my_channel(self) -> dict[str, str]:
        """토큰 채널 {id, handle}. Data API 1단위(YouTube 장부)."""
        why = youtube_quota.LEDGER.take(youtube_quota.current_caller(CALLER), 1, env=self._env)
        if why is not None:
            raise YtaError("QUOTA_EXCEEDED", why)
        got = await self._get(CHANNELS_URL, {"part": "snippet", "mine": "true"}, "채널 정보")
        items = got.get("items") if isinstance(got, dict) else None
        first = items[0] if isinstance(items, list) and items and isinstance(items[0], dict) else {}
        snippet = first.get("snippet") if isinstance(first.get("snippet"), dict) else {}
        cid = first.get("id")
        if not isinstance(cid, str) or not cid:
            raise YtaError("NOT_FOUND", "토큰에 연결된 채널이 없음")
        handle = snippet.get("customUrl") if isinstance(snippet, dict) else None
        return {"id": cid, "handle": handle if isinstance(handle, str) else ""}

    async def report(self, start: str, end: str, metrics: Sequence[str], *,
                     dimensions: Sequence[str] = (), video_id: str | None = None,
                     sort: str | None = None) -> list[dict[str, Any]]:  # fmt: skip
        """Analytics reports.query → 줄마다 {칸 이름: 값}. 값이 없으면 []."""
        params = {"ids": "channel==MINE", "startDate": start, "endDate": end,
                  "metrics": ",".join(metrics)}  # fmt: skip
        if dimensions:
            params["dimensions"] = ",".join(dimensions)
        if video_id:
            params["filters"] = f"video=={video_id}"
        if sort:
            params["sort"] = sort
        got = await self._get(ANALYTICS_URL, params, "YouTube Analytics")
        if not isinstance(got, dict):
            raise YtaError("BAD_RESPONSE", "YouTube Analytics 응답 모양이 다르다")
        heads = [h.get("name") for h in got.get("columnHeaders") or [] if isinstance(h, dict)]
        rows = got.get("rows") or []
        return [dict(zip(heads, r, strict=False)) for r in rows if isinstance(r, list)]

    async def jobs(self) -> list[dict[str, str]]:
        """Reporting 예약(job) 목록 [{id, reportTypeId}]. 만들지 않는다."""
        got = await self._get(f"{REPORTING_URL}/jobs", None, "YouTube Reporting job")
        rows = got.get("jobs") if isinstance(got, dict) else None
        return [{"id": str(j.get("id")), "reportTypeId": str(j.get("reportTypeId"))}
                for j in rows or [] if isinstance(j, dict) and j.get("id")]  # fmt: skip

    async def reports(self, job_id: str) -> list[dict[str, str]]:
        """그 job의 리포트 [{id, startTime, endTime, createTime, downloadUrl}]."""
        got = await self._get(f"{REPORTING_URL}/jobs/{job_id}/reports", None,
                              "YouTube Reporting 리포트")  # fmt: skip
        rows = got.get("reports") if isinstance(got, dict) else None
        keys = ("id", "startTime", "endTime", "createTime", "downloadUrl")
        return [{k: str(r.get(k) or "") for k in keys}
                for r in rows or [] if isinstance(r, dict) and r.get("downloadUrl")]  # fmt: skip

    async def download_csv(self, url: str) -> list[dict[str, str]]:
        """리포트 CSV를 같은 토큰으로 받아 줄마다 {칸: 글}. 리포트 주소만 받는다(다른 주소 거절)."""
        if not url.startswith(("https://youtubereporting.googleapis.com/",)):
            raise YtaError("BAD_RESPONSE", "리포트 주소가 YouTube Reporting이 아니다")
        text = await self._get(url, None, "YouTube Reporting CSV", raw=True)
        return list(csv.DictReader(io.StringIO(text)))
