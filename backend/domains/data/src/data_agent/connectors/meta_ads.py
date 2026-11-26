"""메타 광고 성과 커넥터 (Marketing API `act_<id>/insights`, 읽기만, 11/26).

공식 문서로 확인한 것 (2026-11-26):
- `GET https://graph.facebook.com/{버전}/act_{광고 계정 번호}/insights` level=campaign,
  time_range={"since":"YYYY-MM-DD","until":"YYYY-MM-DD"}, fields, breakdowns(age·gender)
  https://developers.facebook.com/docs/marketing-api/reference/ad-account/insights/
- 필드: campaign_id·campaign_name·impressions·reach·clicks·ctr(%)·spend·account_currency·
  video_play_actions·video_thruplay_watched_actions·video_p25/p50/p75/p100_watched_actions
  (영상 값은 [{action_type, value}] 목록)
- 최신 Graph·Marketing API 버전 v27.0 (2026-11-04,
  https://developers.facebook.com/documentation/ads-commerce/marketing-api/marketing-api-changelog)

env: `DATA_META_ADS_TOKEN`(ads_read 권한 토큰). 버전 `DATA_META_API_VERSION` → 없으면
`DATA_IG_API_VERSION` → 기본 v27.0. 하루 호출 상한 `DATA_META_ADS_DAILY_LIMIT`(기본 50, 인스타
연결과 같은 방식). 토큰은 쿼리로만 보내고 로그·오류·응답에 넣지 않는다.
**GET만 보낸다**: 광고 만들기·예산·켜기/끄기 경로는 없다(시험으로 확인).
"""

from __future__ import annotations

import json
import os
from collections.abc import Mapping, Sequence
from typing import Any

import httpx

from data_agent.connectors import ConnectorUnavailable
from data_agent.connectors.naver_searchad import JsonDailyCache, limit_of
from data_agent.stage_timing import stage

TOKEN_ENV = "DATA_META_ADS_TOKEN"
VERSION_ENV, IG_VERSION_ENV = "DATA_META_API_VERSION", "DATA_IG_API_VERSION"
DEFAULT_VERSION = "v27.0"
DAILY_LIMIT_ENV = "DATA_META_ADS_DAILY_LIMIT"
CACHE_DB_ENV = "DATA_META_ADS_CACHE_DB"
DEFAULT_DAILY_LIMIT = 50
HOST = "https://graph.facebook.com"
TIMEOUT_SECONDS = 20.0
MAX_PAGES = 3
CAMPAIGN_FIELDS = (
    "campaign_id", "campaign_name", "impressions", "reach", "clicks", "ctr", "spend",
    "account_currency", "video_play_actions", "video_thruplay_watched_actions",
    "video_p25_watched_actions", "video_p50_watched_actions", "video_p75_watched_actions",
    "video_p100_watched_actions",
)  # fmt: skip
AUDIENCE_FIELDS = ("impressions", "video_play_actions")
RATE_CODES = frozenset({4, 17, 32, 613, 80000, 80004})

_calls = JsonDailyCache(os.environ.get(CACHE_DB_ENV) or None)


def clear_calls() -> None:
    _calls.clear()


class MetaAdsError(ConnectorUnavailable):
    """code: KEY_MISSING · PERMISSION · RATE_LIMITED · TOKEN_EXPIRED · NOT_FOUND · UNAVAILABLE ·
    BAD_RESPONSE."""


def _error(status: int, payload: Any) -> MetaAdsError:
    err = payload.get("error") if isinstance(payload, dict) else None
    code = err.get("code") if isinstance(err, dict) else None
    if code in RATE_CODES:
        return MetaAdsError("RATE_LIMITED", f"한도: 메타 광고 호출 제한 (code {code})")
    if code == 190:
        return MetaAdsError("TOKEN_EXPIRED", "토큰 만료: 다시 발급 필요 (code 190)")
    if code == 10 or (isinstance(code, int) and 200 <= code <= 299):
        why = f"권한 없음: ads_read 권한·광고 계정 접근 확인 (code {code})"
        return MetaAdsError("PERMISSION", why)
    if code == 100:
        why = "광고 계정을 찾을 수 없거나 필드를 지원하지 않음 (code 100)"
        return MetaAdsError("NOT_FOUND", why)
    return MetaAdsError("UNAVAILABLE", f"메타 광고 응답 {status} (code {code})")


class MetaAds:
    """graph.facebook.com 광고 인사이트 읽기 전용."""

    def __init__(self, env: Mapping[str, str] | None = None, *,
                 client: httpx.AsyncClient | None = None,
                 calls: JsonDailyCache | None = None) -> None:  # fmt: skip
        env = os.environ if env is None else env
        self._token = (env.get(TOKEN_ENV) or "").strip()
        self.version = ((env.get(VERSION_ENV) or "").strip() or (env.get(IG_VERSION_ENV) or "")
                        .strip() or DEFAULT_VERSION)  # fmt: skip
        self.daily_limit = limit_of(env, DAILY_LIMIT_ENV, DEFAULT_DAILY_LIMIT)
        self._client = client
        self._calls = calls or _calls

    def __repr__(self) -> str:  # 토큰을 담지 않는다
        return f"MetaAds(version={self.version!r})"

    @property
    def configured(self) -> bool:
        return bool(self._token)

    async def _get(self, path: str, params: Mapping[str, str]) -> Any:
        if not self.configured:
            raise MetaAdsError("KEY_MISSING", f"{TOKEN_ENV}가 없음 (연결 대기)")
        self._calls.count_call(self.daily_limit, "메타 광고 API")
        client = self._client or httpx.AsyncClient(timeout=TIMEOUT_SECONDS)
        try:
            with stage("external"):
                response = await client.get(f"{HOST}/{self.version}/{path}",
                                            params={**params, "access_token": self._token})
        except httpx.HTTPError as exc:  # 주소(토큰 포함)를 오류 문장에 넣지 않는다
            raise MetaAdsError("UNAVAILABLE", f"메타 광고에 연결할 수 없다 ({type(exc).__name__})"
                               ) from None  # fmt: skip
        finally:
            if client is not self._client:
                await client.aclose()
        try:
            payload = response.json()
        except ValueError:
            payload = None
        if response.status_code != 200 or (isinstance(payload, dict) and "error" in payload):
            raise _error(response.status_code, payload)
        if not isinstance(payload, dict):
            raise MetaAdsError("BAD_RESPONSE", "메타 광고 응답이 JSON 객체가 아니다")
        return payload

    async def insights(self, account_id: str, since: str, until: str, fields: Sequence[str], *,
                       breakdowns: Sequence[str] = ()) -> list[dict[str, Any]]:  # fmt: skip
        """캠페인 단위 인사이트 줄 (페이지 최대 3, 다음 페이지는 after 커서만 쓴다)."""
        params = {"level": "campaign", "fields": ",".join(fields),
                  "time_range": json.dumps({"since": since, "until": until}), "limit": "200"}
        if breakdowns:
            params["breakdowns"] = ",".join(breakdowns)
        rows: list[dict[str, Any]] = []
        account = account_id.strip().removeprefix("act_")
        for _ in range(MAX_PAGES):
            payload = await self._get(f"act_{account}/insights", params)
            rows += [r for r in payload.get("data") or [] if isinstance(r, dict)]
            paging = payload.get("paging") if isinstance(payload.get("paging"), dict) else {}
            cursors = paging.get("cursors") if isinstance(paging.get("cursors"), dict) else {}
            after = cursors.get("after")
            if not paging.get("next") or not isinstance(after, str) or not after:
                break
            params = {**params, "after": after}
        return rows
