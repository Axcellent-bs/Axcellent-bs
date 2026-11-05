"""네이버 쇼핑인사이트 커넥터 (NAVER API HUB `shopping`).

분야(카테고리)·키워드의 클릭 추이를 읽는다. 검색어 트렌드(`naver_datalab`)와 같은 키를 쓴다.

공식 문서
- 개요: https://api.ncloud-docs.com/docs/naver-api-hub-overview
- 분야별 추이: https://api.ncloud-docs.com/docs/naver-api-hub-shopping-insight-categories.md
- 분야 안 연령별: ...-shopping-insight-age.md
- 분야 안 키워드별: ...-shopping-insight-keywords.md
- 분야 안 키워드 연령별: ...-shopping-insight-keyword-age.md
- 예시: https://api.ncloud-docs.com/docs/naver-api-hub-shopping-insight-examples.md

문서로 확인한 것
- 주소 `https://naverapihub.apigw.ntruss.com` (`AX_NAVER_SHOPPING_URL`로 바꾼다). 모두 POST.
  `/shopping/v1/categories`, `/category/age`, `/category/keywords`, `/category/keyword/age`
- 헤더 `X-NCP-APIGW-API-KEY-ID`·`X-NCP-APIGW-API-KEY`, `Content-Type: application/json`
- 본문 `startDate`·`endDate`(yyyy-mm-dd)·`timeUnit`(date|week|month)·`category`
  (`/categories`는 `[{name, param:[cid]}]` 최대 3개, 나머지는 cid 문자열)·`keyword`
  (`/keywords`는 `[{name, param:[단어]}]` 최대 5개, `/keyword/age`는 문자열)
- 응답 `{startDate, endDate, timeUnit, results:[{title, category|keyword,
  data:[{period, ratio}]}]}`,
  연령별은 data 항목에 `group`("10"~"60")이 붙는다. ratio는 요청 안의 상대값(최댓값 100)
- 오류: `{"error":{"errorCode","message"}}`(게이트웨이) 또는 `{"errMsg","errId"}`.
  오류 응답은 요청 본문을 `body`로 되돌려 줄 수 있어 문장만 쓴다
- 429는 하루 허용량 초과(개요 문서)

미확인
- 하루 호출 한도의 정확한 수치 (`NAVER_SHOPPING_DAILY_LIMIT`, 기본 1000은 가정)
- 분야 목록을 주는 공식 API (없다고 안내됨. `CATEGORY_CANDIDATES` 참고)
- 데이터가 적은 분야·키워드의 응답 모양 (빈 `data`로 온다고 가정, 0으로 채우지 않는다)

키는 환경변수에서만 읽고 로그·오류·repr에 넣지 않는다. `DATA_EXTERNAL_MODE`는 `naver_datalab`과
같다(live·record·replay).
"""

from __future__ import annotations

import json
import os
from collections import defaultdict
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, date, datetime, timedelta, timezone
from typing import Any

import httpx

from data_agent.connectors import ConnectorUnavailable, naver_datalab, replay

SOURCE_ID = "naver_shopping"
KEY_ENVS = ("NAVER_HUB_CLIENT_ID", "NAVER_HUB_CLIENT_SECRET")
DOCS_URL = "https://api.ncloud-docs.com/docs/naver-api-hub-overview"
DEFAULT_URL = "https://naverapihub.apigw.ntruss.com"
URL_ENV = "AX_NAVER_SHOPPING_URL"
DAILY_LIMIT_ENV = "NAVER_SHOPPING_DAILY_LIMIT"
DEFAULT_DAILY_LIMIT = 1000  # 공식 수치 미확인
TIMEOUT_SECONDS = 10.0
KST = timezone(timedelta(hours=9))

PATH_CATEGORIES = "/shopping/v1/categories"
PATH_CATEGORY_AGE = "/shopping/v1/category/age"
PATH_KEYWORDS = "/shopping/v1/category/keywords"
PATH_KEYWORD_AGE = "/shopping/v1/category/keyword/age"
BATCH_CATEGORIES = 3  # /categories 한 번에 최대 3개
BATCH_KEYWORDS = 5  # /keywords 한 번에 최대 5개

# 공식 목록 API가 없다(문서: 네이버쇼핑 URL의 cat_id). 공식 예시로 확인된 것은 50000000·50000002뿐,
# 나머지는 후보이고 `verify_categories`로 실제 호출해 데이터가 오는 것만 쓴다.
CATEGORY_CANDIDATES: tuple[tuple[str, str], ...] = (
    ("패션의류", "50000000"),
    ("패션잡화", "50000001"),
    ("화장품/미용", "50000002"),
    ("디지털/가전", "50000003"),
    ("가구/인테리어", "50000004"),
    ("출산/육아", "50000005"),
    ("식품", "50000006"),
    ("스포츠/레저", "50000007"),
    ("생활/건강", "50000008"),
    ("여가/생활편의", "50000009"),
    ("도서", "50005542"),
)

_client: httpx.AsyncClient | None = None  # 테스트가 `use_client`로 바꿔 끼운다


def use_client(client: httpx.AsyncClient | None) -> None:
    """HTTP 클라이언트를 바꿔 끼운다 (테스트용). None이면 호출마다 새로 만든다."""
    global _client
    _client = client


def has_keys(env: Mapping[str, str] | None = None) -> bool:
    env = os.environ if env is None else env
    return all(env.get(name) for name in KEY_ENVS)


# ---------------------------------------------------------------- 표준 형식


@dataclass(frozen=True)
class ShoppingSeries:
    """분야·키워드 하나의 시계열. `points`가 비면 자료가 없는 것이다(0이 아님)."""

    title: str
    codes: tuple[str, ...]
    points: tuple[tuple[date, float], ...]


AgeSeries = dict[str, list[tuple[date, float]]]  # 연령 구간("10"~"60") → [(날짜, 값)]


# ---------------------------------------------------------------- 캐시·호출 수


class DayCache:
    """같은 요청은 한국 날짜가 같은 동안 다시 부르지 않는다. 하루 호출 수도 센다."""

    def __init__(self, today: Callable[[], date] | None = None, label: str = "") -> None:
        self._today = today or (lambda: datetime.now(UTC).astimezone(KST).date())
        self._label = label
        self._day: date | None = None
        self._hits: dict[str, Any] = {}
        self.calls = 0

    def _roll(self) -> None:
        today = self._today()
        if today != self._day:
            self._day, self._hits, self.calls = today, {}, 0

    def get(self, key: str) -> Any | None:
        self._roll()
        return self._hits.get(key)

    def put(self, key: str, value: Any) -> None:
        self._roll()
        self._hits[key] = value

    def count_call(self, limit: int, label: str = "") -> None:
        """호출 하나를 센다. 한도에 닿았으면 부르지 않고 `RATE_LIMITED`."""
        self._roll()
        if self.calls >= limit:
            raise ConnectorUnavailable(
                "RATE_LIMITED",
                f"{label or self._label} 하루 호출 한도({limit}회)에 닿아 부르지 않았다",
            )
        self.calls += 1

    def clear(self) -> None:
        self._day, self._hits, self.calls = None, {}, 0


_cache = DayCache(label="네이버 쇼핑인사이트")


def clear_cache() -> None:
    """캐시와 호출 수를 비운다 (테스트용)."""
    _cache.clear()


def _limit(env: Mapping[str, str]) -> int:
    try:
        value = int(str(env.get(DAILY_LIMIT_ENV, "")).strip())
    except ValueError:
        return DEFAULT_DAILY_LIMIT
    return value if value > 0 else DEFAULT_DAILY_LIMIT


# ---------------------------------------------------------------- 커넥터


class NaverShoppingConnector:
    """쇼핑인사이트 읽기. 키·주소·한도는 `env`(기본 `os.environ`)에서 읽는다."""

    def __init__(
        self,
        env: Mapping[str, str] | None = None,
        *,
        client: httpx.AsyncClient | None = None,
        cache: DayCache | None = None,
        caller: str = "",
    ) -> None:
        env = os.environ if env is None else env
        # 네이버 전체 하루 상한에 셀 때의 호출자 이름 (skill별 몫, 예: shopping:domain_opportunity)
        self.caller = f"shopping:{caller or 'other'}"
        self.url = (env.get(URL_ENV) or DEFAULT_URL).rstrip("/")
        self.daily_limit = _limit(env)
        self._client_id = env.get(KEY_ENVS[0]) or ""
        self._client_secret = env.get(KEY_ENVS[1]) or ""
        self._client = client
        self._cache = cache or _cache
        self._env = env
        self.mode = replay.mode(env)

    def __repr__(self) -> str:  # 키를 담지 않는다
        return f"NaverShoppingConnector(url={self.url!r})"

    @property
    def calls_today(self) -> int:
        self._cache.get("")
        return self._cache.calls

    def _headers(self) -> dict[str, str]:
        if not self._client_id or not self._client_secret:
            raise ConnectorUnavailable(
                "UNAUTHORIZED",
                f"{KEY_ENVS[0]}·{KEY_ENVS[1]} 키가 없어 네이버 쇼핑인사이트를 부를 수 없다"
                " (키 등록 후 가능)",
            )
        return {
            "X-NCP-APIGW-API-KEY-ID": self._client_id,
            "X-NCP-APIGW-API-KEY": self._client_secret,
            "Content-Type": "application/json",
        }

    # ---- 공개 메서드

    async def category_trend(
        self, cids: list[tuple[str, str]], start: date, end: date, time_unit: str = "date"
    ) -> list[ShoppingSeries]:
        """분야별 클릭 추이. `cids`는 (이름, cid), 최대 3개."""
        if not 1 <= len(cids) <= BATCH_CATEGORIES:
            raise ConnectorUnavailable("BAD_REQUEST", "분야는 한 번에 1~3개만 물을 수 있다")
        body = _base(start, end, time_unit)
        body["category"] = [{"name": n, "param": [c]} for n, c in cids]
        payload = await self._call(PATH_CATEGORIES, body)
        return _series(payload, "category")

    async def category_age(
        self, cid: str, start: date, end: date, time_unit: str = "date"
    ) -> AgeSeries:
        """분야 안 연령별 추이."""
        body = _base(start, end, time_unit)
        body["category"] = cid
        return _by_group(await self._call(PATH_CATEGORY_AGE, body))

    async def keyword_trend(
        self, cid: str, keywords: list[str], start: date, end: date, time_unit: str = "date"
    ) -> list[ShoppingSeries]:
        """분야 안 키워드별 추이. 키워드는 최대 5개."""
        if not 1 <= len(keywords) <= BATCH_KEYWORDS:
            raise ConnectorUnavailable("BAD_REQUEST", "키워드는 한 번에 1~5개만 물을 수 있다")
        body = _base(start, end, time_unit)
        body["category"] = cid
        body["keyword"] = [{"name": k, "param": [k]} for k in keywords]
        return _series(await self._call(PATH_KEYWORDS, body), "keyword")

    async def keyword_age(
        self, cid: str, keyword: str, start: date, end: date, time_unit: str = "date"
    ) -> AgeSeries:
        """분야 안 키워드 하나의 연령별 추이."""
        body = _base(start, end, time_unit)
        body["category"] = cid
        body["keyword"] = keyword
        return _by_group(await self._call(PATH_KEYWORD_AGE, body))

    async def verify_categories(
        self, cids: list[tuple[str, str]], start: date, end: date
    ) -> tuple[list[tuple[str, str]], list[tuple[str, str, str]]]:
        """후보 분야를 3개씩 실제로 불러 데이터가 한 점이라도 오는 것만 남긴다.

        반환: (확인된 [(이름, cid)], 뺀 [(이름, cid, 이유)]).
        """
        verified: list[tuple[str, str]] = []
        dropped: list[tuple[str, str, str]] = []
        for i in range(0, len(cids), BATCH_CATEGORIES):
            batch = cids[i : i + BATCH_CATEGORIES]
            try:
                series = await self.category_trend(batch, start, end, "month")
            except ConnectorUnavailable as exc:
                if exc.code in ("UNAUTHORIZED", "RATE_LIMITED", "REPLAY_MISS"):
                    raise
                dropped += [(n, c, f"{exc.code}: {exc}") for n, c in batch]
                continue
            by_title = {s.title: s for s in series}
            for name, cid in batch:
                found = by_title.get(name)
                if found is None:
                    dropped.append((name, cid, "응답에 없음"))
                elif not found.points:
                    dropped.append((name, cid, "데이터 없음"))
                else:
                    verified.append((name, cid))
        return verified, dropped

    async def probe(self) -> tuple[bool, str]:
        """키가 쇼핑인사이트에 쓸 수 있는지 한 번 확인한다 (패션의류, 최근 30일, 월 단위)."""
        end = datetime.now(UTC).astimezone(KST).date()
        try:
            series = await self.category_trend(
                [CATEGORY_CANDIDATES[0]], end - timedelta(days=30), end, "month"
            )
        except ConnectorUnavailable as exc:
            return False, f"{exc.code}: {exc}"
        if not series:
            return False, "BAD_RESPONSE: 결과가 비어 있다"
        return True, "쇼핑인사이트 호출 성공"

    # ---- 호출

    async def _call(self, path: str, body: dict[str, Any]) -> Any:
        url = f"{self.url}{path}"
        key = f"{url}|{json.dumps(body, ensure_ascii=False, sort_keys=True)}"
        cached = self._cache.get(key)
        if cached is not None:
            return cached
        if self.mode == "replay":
            payload = replay.load(SOURCE_ID, url, body, self._env)
        else:
            headers = self._headers()
            self._cache.count_call(self.daily_limit, "네이버 쇼핑인사이트")
            # 같은 API HUB 앱이라 네이버 전체 하루 상한(검색어 트렌드와 같이 1,000)에도 센다
            naver_datalab.count_shared(self._env, self.caller, "네이버 쇼핑인사이트")
            payload = await self._post(url, body, headers)
            if self.mode == "record":
                replay.save(SOURCE_ID, url, body, payload, self._env)
        self._cache.put(key, payload)
        return payload

    async def _post(self, url: str, body: dict[str, Any], headers: dict[str, str]) -> Any:
        client = self._client or _client or httpx.AsyncClient(timeout=TIMEOUT_SECONDS)
        try:
            response = await client.post(
                url, content=json.dumps(body, ensure_ascii=False), headers=headers
            )
        except httpx.HTTPError as exc:
            raise ConnectorUnavailable(
                "UNAVAILABLE", f"네이버 쇼핑인사이트에 연결할 수 없다 ({type(exc).__name__})"
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
                "BAD_RESPONSE", "네이버 쇼핑인사이트 응답이 JSON이 아니다"
            ) from exc


def _base(start: date, end: date, time_unit: str) -> dict[str, Any]:
    if time_unit not in ("date", "week", "month"):
        raise ConnectorUnavailable("BAD_REQUEST", "timeUnit은 date·week·month 중 하나다")
    return {"startDate": start.isoformat(), "endDate": end.isoformat(), "timeUnit": time_unit}


def _error(response: httpx.Response) -> ConnectorUnavailable:
    status = response.status_code
    code = {400: "BAD_REQUEST", 401: "UNAUTHORIZED", 403: "UNAUTHORIZED", 429: "RATE_LIMITED"}.get(
        status, "UNAVAILABLE"
    )
    detail = ""
    try:  # 오류 문장만 (요청 본문 echo `body`는 넣지 않는다)
        data = response.json()
        message: Any = None
        if isinstance(data, dict):
            err = data.get("error")
            message = err.get("message") if isinstance(err, dict) else data.get("errMsg")
        if isinstance(message, str):
            detail = f": {message[:200]}"
    except (ValueError, AttributeError):
        pass
    return ConnectorUnavailable(code, f"네이버 쇼핑인사이트 응답 {status} ({code}){detail}")


def _bad(what: str) -> ConnectorUnavailable:
    return ConnectorUnavailable(
        "BAD_RESPONSE", f"네이버 쇼핑인사이트 응답 형식이 예상과 다르다 ({what})"
    )


def _results(payload: Any) -> list[dict[str, Any]]:
    results = payload.get("results") if isinstance(payload, dict) else None
    if not isinstance(results, list) or not all(isinstance(r, dict) for r in results):
        raise _bad("results")
    return results


def _point(item: Any) -> tuple[date, float]:
    try:
        return date.fromisoformat(str(item["period"])), float(item["ratio"])
    except (KeyError, TypeError, ValueError) as exc:
        raise _bad("data") from exc


def _data(result: dict[str, Any]) -> list[Any]:
    data = result.get("data", [])
    if not isinstance(data, list):
        raise _bad("data")
    return data


def _series(payload: Any, code_field: str) -> list[ShoppingSeries]:
    out = []
    for result in _results(payload):
        codes = result.get(code_field) or []
        if not isinstance(codes, list):
            raise _bad(code_field)
        points = dict(_point(i) for i in _data(result))  # 같은 날은 마지막 값, 빠진 날은 그대로
        out.append(
            ShoppingSeries(
                str(result.get("title", "")),
                tuple(str(c) for c in codes),
                tuple(sorted(points.items())),
            )
        )
    return out


def _by_group(payload: Any) -> AgeSeries:
    groups: dict[str, dict[date, float]] = defaultdict(dict)
    for result in _results(payload):
        for item in _data(result):
            day, ratio = _point(item)
            group = item.get("group") if isinstance(item, dict) else None
            if not isinstance(group, str):
                raise _bad("group")
            groups[group][day] = ratio
    return {g: sorted(points.items()) for g, points in sorted(groups.items())}
