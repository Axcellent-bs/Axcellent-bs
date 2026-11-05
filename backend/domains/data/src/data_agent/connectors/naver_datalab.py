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

import hashlib
import json
import os
import sqlite3
import threading
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import httpx
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ax_contracts.schemas.data import TrendScanInput, TrendTimeUnit
from data_agent.connectors import ConnectorUnavailable, replay
from data_agent.stage_timing import stage

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


CACHE_DB_ENV = "DATA_NAVER_CACHE_DB"


def _dump(value: TrendResponse) -> str:
    return json.dumps(
        {
            "start": value.start_date.isoformat(), "end": value.end_date.isoformat(),
            "unit": value.time_unit,
            "series": [[s.group_name, list(s.keywords), [[d.isoformat(), v] for d, v in s.points]]
                       for s in value.series],
        },
        ensure_ascii=False,
    )  # fmt: skip


def _load(raw: str) -> TrendResponse:
    body = json.loads(raw)
    return TrendResponse(
        date.fromisoformat(body["start"]), date.fromisoformat(body["end"]), body["unit"],
        tuple(TrendSeries(name, tuple(kws), tuple((date.fromisoformat(d), v) for d, v in pts))
              for name, kws, pts in body["series"]),
    )  # fmt: skip


class DailyCache:
    """같은 요청은 한국 날짜가 같은 동안 다시 부르지 않는다. 하루 호출 수도 센다.

    `path`가 있으면 SQLite에 둬 서버를 다시 켜도 그날 응답·호출 수가 남는다(한도도 이어서 센다).
    없으면 프로세스 메모리. 지난 날 것은 날짜가 바뀌면 지운다.
    """

    def __init__(
        self, today: Callable[[], date] | None = None, path: str | Path | None = None
    ) -> None:
        self._today = today or (lambda: datetime.now(UTC).astimezone(KST).date())
        self._day: date | None = None
        self._hits: dict[str, TrendResponse] = {}
        self.calls = 0
        self.by_caller: dict[str, int] = {}
        self._lock = threading.Lock()
        self._conn: sqlite3.Connection | None = None
        if path is not None:
            if str(path) != ":memory:":
                Path(path).parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(str(path), check_same_thread=False, timeout=5.0)
            with self._lock, self._conn:
                self._conn.executescript(
                    "CREATE TABLE IF NOT EXISTS hits (day TEXT NOT NULL, key TEXT NOT NULL,"
                    " value TEXT NOT NULL, PRIMARY KEY (day, key));"
                    "CREATE TABLE IF NOT EXISTS calls (day TEXT PRIMARY KEY, n INTEGER NOT NULL);"
                    "CREATE TABLE IF NOT EXISTS calls_by (day TEXT NOT NULL, caller TEXT NOT NULL,"
                    " n INTEGER NOT NULL, PRIMARY KEY (day, caller));"
                )

    def _roll(self) -> None:
        today = self._today()
        if today == self._day:
            return
        self._day, self._hits, self.calls, self.by_caller = today, {}, 0, {}
        if self._conn is None:
            return
        day = today.isoformat()
        with self._lock, self._conn:
            for table in ("hits", "calls", "calls_by"):
                self._conn.execute(f"DELETE FROM {table} WHERE day < ?", (day,))  # noqa: S608
            row = self._conn.execute("SELECT n FROM calls WHERE day = ?", (day,)).fetchone()
            by = self._conn.execute(
                "SELECT caller, n FROM calls_by WHERE day = ?", (day,)
            ).fetchall()
        self.calls = int(row[0]) if row else 0
        self.by_caller = {c: int(n) for c, n in by}

    @staticmethod
    def _hash(key: str) -> str:
        return hashlib.sha256(key.encode("utf-8")).hexdigest()

    def get(self, key: str) -> TrendResponse | None:
        self._roll()
        if (hit := self._hits.get(key)) is not None or self._conn is None or self._day is None:
            return hit
        with self._lock:
            row = self._conn.execute(
                "SELECT value FROM hits WHERE day = ? AND key = ?",
                (self._day.isoformat(), self._hash(key)),
            ).fetchone()
        if row is None:
            return None
        self._hits[key] = value = _load(row[0])
        return value

    def put(self, key: str, value: TrendResponse) -> None:
        self._roll()
        self._hits[key] = value
        if self._conn is not None and self._day is not None:
            with self._lock, self._conn:
                self._conn.execute(
                    "INSERT OR REPLACE INTO hits VALUES (?,?,?)",
                    (self._day.isoformat(), self._hash(key), _dump(value)),
                )

    def count_call(self, limit: int, caller: str = "", what: str = "네이버 검색어 트렌드") -> None:
        """호출 하나를 센다. 한도에 닿았으면 부르지 않고 `RATE_LIMITED`. 호출자별로도 센다."""
        self._roll()
        if self.calls >= limit:
            raise ConnectorUnavailable(
                "RATE_LIMITED",
                f"{what} 하루 호출 한도(네이버 전체 {limit}회)에 닿아 부르지 않았다",
            )
        self.calls += 1
        if caller:
            self.by_caller[caller] = self.by_caller.get(caller, 0) + 1
        if self._conn is not None and self._day is not None:
            day = self._day.isoformat()
            with self._lock, self._conn:
                self._conn.execute("INSERT OR REPLACE INTO calls VALUES (?,?)", (day, self.calls))
                if caller:
                    self._conn.execute(
                        "INSERT OR REPLACE INTO calls_by VALUES (?,?,?)",
                        (day, caller, self.by_caller[caller]),
                    )

    def used_by(self, caller: str) -> int:
        """오늘 `caller`가 센 호출 수."""
        self._roll()
        return self.by_caller.get(caller, 0)

    def clear(self) -> None:
        self._day, self._hits, self.calls, self.by_caller = None, {}, 0, {}
        if self._conn is not None:
            with self._lock, self._conn:
                self._conn.execute("DELETE FROM hits")
                self._conn.execute("DELETE FROM calls")
                self._conn.execute("DELETE FROM calls_by")


# 요청마다 커넥터를 새로 만들어도 캐시·호출 수는 이어진다 (`DATA_NAVER_CACHE_DB`면 다시 켜도)
_cache = DailyCache(path=os.environ.get(CACHE_DB_ENV) or None)


def count_shared(env: Mapping[str, str], caller: str, what: str) -> None:
    """다른 네이버 API(쇼핑인사이트) 호출도 네이버 전체 하루 상한(`NAVER_DATALAB_DAILY_LIMIT`)에."""
    _cache.count_call(_limit(env), caller, what)


def used_today(caller: str) -> int:
    """오늘 네이버 전체 상한 중 `caller`가 쓴 호출 수 (skill별 몫 확인용)."""
    return _cache.used_by(caller)


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
        self._env = env
        self.mode = replay.mode(env)  # live · record · replay (README "외부 호출 없이 확인하기")

    def __repr__(self) -> str:  # 키를 담지 않는다
        return f"NaverDatalabConnector(url={self.url!r})"

    @property
    def calls_today(self) -> int:
        """오늘(한국 날짜) 네이버를 실제로 부른 수 (캐시에서 돌려준 요청은 세지 않는다)."""
        self._cache.get("")  # 날짜가 바뀌었으면 비운다
        return self._cache.calls

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

    async def search_trend(self, query: TrendScanInput, ages: Sequence[str] = ()) -> TrendResponse:
        """키워드 그룹별 상대값 시계열. 못 읽으면 `ConnectorUnavailable`.

        `ages`(네이버 연령 코드 1~11)를 주면 그 연령대만 센 값이다. 값은 그 요청 안에서만 비교한다.
        """
        body = _request_body(query)
        if ages:
            body["ages"] = list(ages)
        key = f"{self.url}|{json.dumps(body, ensure_ascii=False, sort_keys=True)}"
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        if self.mode == "replay":  # 저장된 응답만 (할당량·하루 호출 수를 세지 않는다)
            payload = replay.load("naver", self.url, body, self._env)
        else:
            headers = self._headers()
            self._cache.count_call(self.daily_limit)
            payload = await self._post(body, headers)
            if self.mode == "record":
                replay.save("naver", self.url, body, payload, self._env)
        result = _parse(payload, query)
        self._cache.put(key, result)
        return result

    async def _post(self, body: dict[str, Any], headers: dict[str, str]) -> Any:
        client = self._client or _client or httpx.AsyncClient(timeout=TIMEOUT_SECONDS)
        try:
            with stage("external"):
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
