"""네이버 검색어 트렌드 커넥터 (NAVER API HUB `search-trend`).

외부 검색 트렌드를 읽는 커넥터다. 회사 지표 커넥터(`connectors.Connector`, 5분 지표·실패 이벤트)와
모양이 달라 그 인터페이스를 구현하지 않고 `search_trend` 하나만 둔다.

- 주소: `AX_NAVER_DATALAB_URL`(없으면 `DEFAULT_URL`). 예전 `openapi.naver.com/v1/datalab/search`가
  아니라 API HUB 주소다.
- 키: `NAVER_HUB_CLIENT_ID`·`NAVER_HUB_CLIENT_SECRET` 환경변수(`.env`)에서만 읽는다.
  헤더 `X-NCP-APIGW-API-KEY-ID`·`X-NCP-APIGW-API-KEY`로 보낸다.
  키 값은 로그·오류 문장에 넣지 않는다.
- 값은 검색 건수가 아니라 "그 요청 안의 최댓값 = 100"인 상대값이다. 요청이 다르면 배수가 달라지므로
  기준선과 판정 구간은 한 요청으로 받아야 한다.
- 네이버가 자료를 주지 않은 그룹(검색량이 적은 키워드)은 빈 시계열이다. 0으로 채우지 않는다.
  빠진 날도 그대로 빠진 채로 둔다.
- 응답 없음·4xx·5xx·형식 오류는 `ConnectorUnavailable`이다.
- 하루 호출 한도: 같은 요청은 한국 날짜가 바뀔 때까지 캐시하고(네이버는 하루 한 번 채워진다),
  하루 호출 수가 `NAVER_DATALAB_DAILY_LIMIT`(기본 1000)에 닿으면 부르지 않고 `RATE_LIMITED`다.
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta, timezone
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ax_contracts.schemas.data import TrendScanInput, TrendTimeUnit
from data_agent.connectors import ConnectorUnavailable

DEFAULT_URL = "https://naverapihub.apigw.ntruss.com/search-trend/v1/search"
URL_ENV = "AX_NAVER_DATALAB_URL"
CLIENT_ID_ENV = "NAVER_HUB_CLIENT_ID"
CLIENT_SECRET_ENV = "NAVER_HUB_CLIENT_SECRET"
DAILY_LIMIT_ENV = "NAVER_DATALAB_DAILY_LIMIT"
DEFAULT_DAILY_LIMIT = 1000
TIMEOUT_SECONDS = 10.0
SOURCE = "naver_datalab"
KST = timezone(timedelta(hours=9))

_client: httpx.AsyncClient | None = None  # 테스트가 `use_client`로 바꿔 끼운다


def use_client(client: httpx.AsyncClient | None) -> None:
    """HTTP 클라이언트를 바꿔 끼운다 (테스트용). None이면 호출마다 새로 만든다."""
    global _client
    _client = client


# ---------------------------------------------------------------- 표준 형식


@dataclass(frozen=True)
class TrendSeries:
    """키워드 그룹 하나의 시계열. `points`가 비면 네이버가 자료를 주지 않은 것이다(0이 아님)."""

    group_name: str
    keywords: tuple[str, ...]
    points: tuple[tuple[date, float], ...]


@dataclass(frozen=True)
class TrendResponse:
    start_date: date
    end_date: date
    time_unit: TrendTimeUnit
    series: tuple[TrendSeries, ...]  # 요청한 그룹 순서

    def by_group(self, group_name: str) -> TrendSeries:
        return next(s for s in self.series if s.group_name == group_name)


# ---------------------------------------------------------------- 네이버 응답 형식 (여기서만 쓴다)


class _Point(BaseModel):
    model_config = ConfigDict(extra="ignore")
    period: date
    ratio: float = Field(ge=0)


class _Result(BaseModel):
    model_config = ConfigDict(extra="ignore")
    title: str
    keywords: list[str] = Field(default_factory=list)
    data: list[_Point]


class _Body(BaseModel):
    model_config = ConfigDict(extra="ignore")
    results: list[_Result]


# ---------------------------------------------------------------- 캐시·호출 수


class DailyCache:
    """같은 요청은 한국 날짜가 같은 동안 다시 부르지 않는다. 하루 호출 수도 센다."""

    def __init__(self, today: Callable[[], date] | None = None) -> None:
        self._today = today or (lambda: datetime.now(UTC).astimezone(KST).date())
        self._day: date | None = None
        self._hits: dict[str, TrendResponse] = {}
        self.calls = 0

    def _roll(self) -> None:
        today = self._today()
        if today != self._day:
            self._day, self._hits, self.calls = today, {}, 0

    def get(self, key: str) -> TrendResponse | None:
        self._roll()
        return self._hits.get(key)

    def put(self, key: str, value: TrendResponse) -> None:
        self._roll()
        self._hits[key] = value

    def count_call(self, limit: int) -> None:
        """호출 하나를 센다. 한도에 닿았으면 부르지 않고 `RATE_LIMITED`."""
        self._roll()
        if self.calls >= limit:
            raise ConnectorUnavailable(
                "RATE_LIMITED",
                f"네이버 검색어 트렌드 하루 호출 한도({limit}회)에 닿아 부르지 않았다",
            )
        self.calls += 1

    def clear(self) -> None:
        self._day, self._hits, self.calls = None, {}, 0


_cache = DailyCache()  # 요청마다 커넥터를 새로 만들어도 캐시·호출 수는 이어진다


def clear_cache() -> None:
    """캐시와 호출 수를 비운다 (테스트용)."""
    _cache.clear()


# ---------------------------------------------------------------- 커넥터


def _request_body(query: TrendScanInput) -> dict[str, Any]:
    return {
        "startDate": query.start_date.isoformat(),
        "endDate": query.end_date.isoformat(),
        "timeUnit": query.time_unit,
        "keywordGroups": [
            {"groupName": g.group_name, "keywords": list(g.keywords)} for g in query.keyword_groups
        ],
    }


def _limit(env: Mapping[str, str]) -> int:
    try:
        value = int(str(env.get(DAILY_LIMIT_ENV, "")).strip())
    except ValueError:
        return DEFAULT_DAILY_LIMIT
    return value if value > 0 else DEFAULT_DAILY_LIMIT


class NaverDatalabConnector:
    """네이버 검색어 트렌드 읽기. 키·주소·한도는 `env`(기본 `os.environ`)에서 읽는다."""

    def __init__(
        self,
        env: Mapping[str, str] | None = None,
        *,
        client: httpx.AsyncClient | None = None,
        cache: DailyCache | None = None,
    ) -> None:
        env = os.environ if env is None else env
        self.url = env.get(URL_ENV) or DEFAULT_URL
        self.daily_limit = _limit(env)
        self._client_id = env.get(CLIENT_ID_ENV) or ""
        self._client_secret = env.get(CLIENT_SECRET_ENV) or ""
        self._client = client
        self._cache = cache or _cache

    def __repr__(self) -> str:  # 키를 담지 않는다
        return f"NaverDatalabConnector(url={self.url!r})"

    def _headers(self) -> dict[str, str]:
        if not self._client_id or not self._client_secret:
            raise ConnectorUnavailable(
                "UNAUTHORIZED",
                f"{CLIENT_ID_ENV}·{CLIENT_SECRET_ENV}가 없어 네이버 검색어 트렌드를 부를 수 없다",
            )
        return {
            "X-NCP-APIGW-API-KEY-ID": self._client_id,
            "X-NCP-APIGW-API-KEY": self._client_secret,
            "Content-Type": "application/json",
        }

    async def search_trend(self, query: TrendScanInput) -> TrendResponse:
        """키워드 그룹별 상대값 시계열. 못 읽으면 `ConnectorUnavailable`."""
        body = _request_body(query)
        key = f"{self.url}|{json.dumps(body, ensure_ascii=False, sort_keys=True)}"
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        headers = self._headers()
        self._cache.count_call(self.daily_limit)
        payload = await self._post(body, headers)
        result = _parse(payload, query)
        self._cache.put(key, result)
        return result

    async def _post(self, body: dict[str, Any], headers: dict[str, str]) -> Any:
        client = self._client or _client or httpx.AsyncClient(timeout=TIMEOUT_SECONDS)
        try:
            response = await client.post(
                self.url, content=json.dumps(body, ensure_ascii=False), headers=headers
            )
        except httpx.HTTPError as exc:
            raise ConnectorUnavailable(
                "UNAVAILABLE", f"네이버 검색어 트렌드에 연결할 수 없다 ({type(exc).__name__})"
            ) from exc
        finally:
            if client is not self._client and client is not _client:
                await client.aclose()
        if response.status_code != 200:
            raise _error(response)
        try:
            return response.json()
        except ValueError as exc:
            raise ConnectorUnavailable(
                "BAD_RESPONSE", "네이버 검색어 트렌드 응답이 JSON이 아니다"
            ) from exc


def _error(response: httpx.Response) -> ConnectorUnavailable:
    status = response.status_code
    code = {400: "BAD_REQUEST", 401: "UNAUTHORIZED", 403: "UNAUTHORIZED", 429: "RATE_LIMITED"}.get(
        status, "UNAVAILABLE"
    )
    detail = ""
    try:  # 네이버 오류 문장 (요청 본문 echo `body`는 넣지 않는다)
        message = response.json().get("errMsg")
        if isinstance(message, str):
            detail = f": {message[:200]}"
    except (ValueError, AttributeError):
        pass
    return ConnectorUnavailable(code, f"네이버 검색어 트렌드 응답 {status} ({code}){detail}")


def _parse(payload: Any, query: TrendScanInput) -> TrendResponse:
    try:
        body = _Body.model_validate(payload)
    except ValidationError as exc:
        raise ConnectorUnavailable(
            "BAD_RESPONSE", "네이버 검색어 트렌드 응답 형식이 예상과 다르다"
        ) from exc
    by_title = {r.title: r for r in body.results}
    wanted = [g.group_name for g in query.keyword_groups]
    if len(by_title) != len(body.results) or set(by_title) != set(wanted):
        raise ConnectorUnavailable(
            "BAD_RESPONSE", "네이버 검색어 트렌드 응답의 그룹이 요청한 그룹과 다르다"
        )
    series = []
    for group in query.keyword_groups:
        result = by_title[group.group_name]
        points = {p.period: p.ratio for p in result.data}
        series.append(
            TrendSeries(group.group_name, tuple(group.keywords), tuple(sorted(points.items())))
        )
    return TrendResponse(query.start_date, query.end_date, query.time_unit, tuple(series))
