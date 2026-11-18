"""네이버 검색광고 API 키워드 도구 커넥터 (연관 키워드·월간 검색량).

공식 문서로 확인한 것 (2026-11-18):
- 스펙 https://naver.github.io/searchad-apidoc/#/operations/GET/~2Fkeywordstool
  - `GET /keywordstool`, 쿼리 `hintKeywords`(쉼표로 최대 5개),
    `showDetail=1`(검색 수·클릭·경쟁 정도)
  - 항목: `relKeyword`, `monthlyPcQcCnt`, `monthlyMobileQcCnt`(최근 30일 합, 10 미만이면 "<10"),
    `compIdx`(low·mid·high). 그 밖의 칸(클릭 수·CTR·광고 깊이)은 쓰지 않는다
- 서명·헤더: 공식 샘플 https://github.com/naver/searchad-apidoc (python-sample/examples)
  - 기본 주소 `https://api.searchad.naver.com`
  - 헤더 `X-Timestamp`(밀리초) · `X-API-KEY` · `X-Customer` · `X-Signature`
  - 서명 = base64(HMAC-SHA256(secret, "{timestamp}.{method}.{uri}")), uri는 경로만(쿼리 제외)
- 오류 코드표 NaverSA_API_Error_Code_MAP.md: 429·1016 = 요청 과다

확인 필요(문서에 없음): 응답 맨 위 모양(`{"keywordList": [...]}`인지 배열인지) → 둘 다 받는다.
숫자 칸이 문자열이 아니라 숫자로 오는 경우 → 둘 다 받는다.
`compIdx`가 한글(낮음·중간·높음)로 오는 경우 → 같은 값으로 바꾼다.

- 키: `NAVER_SEARCHAD_API_KEY`·`NAVER_SEARCHAD_SECRET`·`NAVER_SEARCHAD_CUSTOMER_ID`
  (.env, 값은 로그·오류에 넣지 않는다). 없으면 `UNAUTHORIZED` + "키 등록 후 가능"
- 하루 캐시(한국 날짜, 같은 힌트 묶음), 하루 호출 상한 `NAVER_SEARCHAD_DAILY_LIMIT`(기본 300)
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import sqlite3
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Literal

import httpx

from data_agent.connectors import ConnectorUnavailable
from data_agent.stage_timing import stage

DEFAULT_URL = "https://api.searchad.naver.com"
URL_ENV = "AX_NAVER_SEARCHAD_URL"
KEY_ENV = "NAVER_SEARCHAD_API_KEY"
SECRET_ENV = "NAVER_SEARCHAD_SECRET"
CUSTOMER_ENV = "NAVER_SEARCHAD_CUSTOMER_ID"
KEY_ENVS = (KEY_ENV, SECRET_ENV, CUSTOMER_ENV)
DAILY_LIMIT_ENV = "NAVER_SEARCHAD_DAILY_LIMIT"
DEFAULT_DAILY_LIMIT = 300
CACHE_DB_ENV = "DATA_NAVER_SEARCHAD_CACHE_DB"
PATH = "/keywordstool"
MAX_HINTS = 5  # 문서: hintKeywords 최대 5개
TIMEOUT_SECONDS = 10.0
KST = timezone(timedelta(hours=9))
NO_KEY = "키 등록 후 가능"
_COMP = {"low": "low", "mid": "mid", "high": "high", "낮음": "low", "중간": "mid", "높음": "high"}

Competition = Literal["low", "mid", "high"]


class JsonDailyCache:
    """같은 요청의 응답(JSON)은 한국 날짜가 같은 동안 다시 부르지 않는다. 하루 호출 수도 센다.

    `path`가 있으면 SQLite(다시 켜도 남음), 없으면 프로세스 메모리. 지난 날 것은 지운다.
    """

    def __init__(self, path: str | Path | None = None,
                 today: Callable[[], date] | None = None) -> None:  # fmt: skip
        self._today = today or (lambda: datetime.now(UTC).astimezone(KST).date())
        self._day: date | None = None
        self._hits: dict[str, Any] = {}
        self.calls = 0
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
                )

    def _roll(self) -> None:
        today = self._today()
        if today == self._day:
            return
        self._day, self._hits, self.calls = today, {}, 0
        if self._conn is None:
            return
        day = today.isoformat()
        with self._lock, self._conn:
            for table in ("hits", "calls"):
                self._conn.execute(f"DELETE FROM {table} WHERE day < ?", (day,))  # noqa: S608
            row = self._conn.execute("SELECT n FROM calls WHERE day = ?", (day,)).fetchone()
        self.calls = int(row[0]) if row else 0

    @staticmethod
    def _hash(key: str) -> str:
        return hashlib.sha256(key.encode("utf-8")).hexdigest()

    def get(self, key: str) -> Any | None:
        self._roll()
        if key in self._hits or self._conn is None or self._day is None:
            return self._hits.get(key)
        with self._lock:
            row = self._conn.execute(
                "SELECT value FROM hits WHERE day = ? AND key = ?",
                (self._day.isoformat(), self._hash(key)),
            ).fetchone()
        if row is None:
            return None
        self._hits[key] = value = json.loads(row[0])
        return value

    def put(self, key: str, value: Any) -> None:
        self._roll()
        self._hits[key] = value
        if self._conn is not None and self._day is not None:
            with self._lock, self._conn:
                self._conn.execute(
                    "INSERT OR REPLACE INTO hits VALUES (?,?,?)",
                    (self._day.isoformat(), self._hash(key), json.dumps(value, ensure_ascii=False)),
                )

    def count_call(self, limit: int, what: str) -> None:
        """호출 하나를 센다. 상한이면 부르지 않고 `RATE_LIMITED`."""
        self._roll()
        if self.calls >= limit:
            raise ConnectorUnavailable("RATE_LIMITED", f"{what} 하루 호출 상한({limit}번)에 닿음")
        self.calls += 1
        if self._conn is not None and self._day is not None:
            with self._lock, self._conn:
                self._conn.execute(
                    "INSERT OR REPLACE INTO calls VALUES (?,?)", (self._day.isoformat(), self.calls)
                )

    def clear(self) -> None:
        self._day, self._hits, self.calls = None, {}, 0
        if self._conn is not None:
            with self._lock, self._conn:
                self._conn.execute("DELETE FROM hits")
                self._conn.execute("DELETE FROM calls")


_cache = JsonDailyCache(os.environ.get(CACHE_DB_ENV) or None)


def clear_cache() -> None:
    """캐시와 호출 수를 비운다 (테스트용)."""
    _cache.clear()


def limit_of(env: Mapping[str, str], name: str, default: int) -> int:
    try:
        value = int(str(env.get(name, "")).strip())
    except ValueError:
        return default
    return value if value > 0 else default


def has_keys(env: Mapping[str, str]) -> bool:
    return all(env.get(k) for k in KEY_ENVS)


@dataclass(frozen=True)
class KeywordStat:
    keyword: str
    pc: int | None  # "<10"이면 None + pc_below_10
    mobile: int | None
    pc_below_10: bool
    mobile_below_10: bool
    competition: Competition | None

    @property
    def total(self) -> int | None:
        return None if self.pc is None or self.mobile is None else self.pc + self.mobile


def _count(raw: Any) -> tuple[int | None, bool]:
    """(숫자, "<10"인지). 숫자로 못 읽으면 (None, False)."""
    if isinstance(raw, bool):
        return None, False
    if isinstance(raw, int):
        return max(0, raw), False
    if isinstance(raw, float):
        return max(0, int(raw)), False
    if isinstance(raw, str):
        text = raw.replace(" ", "").replace(",", "")
        if text.startswith("<"):
            return None, True
        try:
            return max(0, int(float(text))), False
        except ValueError:
            return None, False
    return None, False


def parse(payload: Any) -> list[KeywordStat]:
    """응답 → 키워드 값.

    맨 위가 `{"keywordList": [...]}`이든 배열이든 받는다(문서에 없어 확인 필요).
    """
    items = payload.get("keywordList") if isinstance(payload, dict) else payload
    if not isinstance(items, list):
        raise ConnectorUnavailable("BAD_RESPONSE", "검색광고 키워드 도구 응답 형식이 예상과 다르다")
    out: list[KeywordStat] = []
    for item in items:
        if not isinstance(item, dict) or not str(item.get("relKeyword") or "").strip():
            continue
        pc, pc_low = _count(item.get("monthlyPcQcCnt"))
        mobile, mobile_low = _count(item.get("monthlyMobileQcCnt"))
        raw = str(item.get("compIdx") or "").strip()
        comp = _COMP.get(raw.lower()) or _COMP.get(raw)
        out.append(KeywordStat(str(item["relKeyword"]).strip(), pc, mobile, pc_low, mobile_low,
                               comp))  # type: ignore[arg-type]  # fmt: skip
    return out


def signature(secret: str, timestamp: str, method: str, uri: str) -> str:
    """공식 샘플과 같은 서명: base64(HMAC-SHA256(secret, "{timestamp}.{method}.{uri}"))."""
    message = f"{timestamp}.{method}.{uri}"
    digest = hmac.new(secret.encode("utf-8"), message.encode("utf-8"), hashlib.sha256).digest()
    return base64.b64encode(digest).decode("ascii")


class NaverSearchAdConnector:
    """키워드 도구 읽기. 키·주소·한도는 `env`(기본 `os.environ`)."""

    def __init__(self, env: Mapping[str, str] | None = None, *,
                 client: httpx.AsyncClient | None = None, cache: JsonDailyCache | None = None,
                 clock: Callable[[], float] | None = None) -> None:  # fmt: skip
        env = os.environ if env is None else env
        self.url = (env.get(URL_ENV) or DEFAULT_URL).rstrip("/")
        self.daily_limit = limit_of(env, DAILY_LIMIT_ENV, DEFAULT_DAILY_LIMIT)
        self._key = env.get(KEY_ENV) or ""
        self._secret = env.get(SECRET_ENV) or ""
        self._customer = env.get(CUSTOMER_ENV) or ""
        self._client = client
        self._cache = cache or _cache
        self._clock = clock or time.time

    def __repr__(self) -> str:  # 키를 담지 않는다
        return f"NaverSearchAdConnector(url={self.url!r})"

    @property
    def configured(self) -> bool:
        return bool(self._key and self._secret and self._customer)

    @property
    def calls_today(self) -> int:
        self._cache.get("")
        return self._cache.calls

    def _headers(self) -> dict[str, str]:
        if not self.configured:
            why = f"{'·'.join(KEY_ENVS)}가 없어 검색광고 키워드 도구를 부를 수 없다 ({NO_KEY})"
            raise ConnectorUnavailable("UNAUTHORIZED", why)
        timestamp = str(round(self._clock() * 1000))
        return {
            "X-Timestamp": timestamp,
            "X-API-KEY": self._key,
            "X-Customer": self._customer,
            "X-Signature": signature(self._secret, timestamp, "GET", PATH),
        }

    async def keywords(self, hints: Sequence[str]) -> list[KeywordStat]:
        """힌트(최대 5개)의 연관 키워드와 월간 검색량. 못 읽으면 `ConnectorUnavailable`."""
        words = list(dict.fromkeys(h.strip() for h in hints if h and h.strip()))[:MAX_HINTS]
        if not words:
            return []
        key = f"{self.url}{PATH}|{','.join(words)}"
        cached = self._cache.get(key)
        if cached is not None:
            return parse(cached)
        headers = self._headers()
        self._cache.count_call(self.daily_limit, "검색광고 키워드 도구")
        payload = await self._get({"hintKeywords": ",".join(words), "showDetail": "1"}, headers)
        stats = parse(payload)
        self._cache.put(key, payload)
        return stats

    async def _get(self, params: dict[str, str], headers: dict[str, str]) -> Any:
        client = self._client or httpx.AsyncClient(timeout=TIMEOUT_SECONDS)
        try:
            with stage("external"):
                response = await client.get(f"{self.url}{PATH}", params=params, headers=headers)
        except httpx.HTTPError as exc:
            raise ConnectorUnavailable(
                "UNAVAILABLE", f"검색광고 API에 연결할 수 없다 ({type(exc).__name__})"
            ) from exc
        finally:
            if client is not self._client:
                await client.aclose()
        if response.status_code != 200:
            code = {400: "BAD_REQUEST", 401: "UNAUTHORIZED", 403: "UNAUTHORIZED",
                    429: "RATE_LIMITED"}.get(response.status_code, "UNAVAILABLE")  # fmt: skip
            why = f"검색광고 키워드 도구 응답 {response.status_code} ({code})"
            raise ConnectorUnavailable(code, why)
        try:
            return response.json()
        except ValueError as exc:
            raise ConnectorUnavailable("BAD_RESPONSE", "검색광고 응답이 JSON이 아니다") from exc
