"""도서관 정보나루 Open API 커넥터 (인기대출도서 `loanItemSrch`).

공식 문서: https://data4library.kr/apiUtilization (상세 매뉴얼은 `/downloadApiManual`)

문서로 확인한 것
- 예시 주소 `http://data4library.kr/api/loanItemSrch?authKey=..&startDt=..&endDt=..&gender=0
  &from_age=6&to_age=10&region=11&addCode=0&kdc=8` (기간·성별·연령·지역·ISBN부가기호·KDC 조건)
- 제공 항목: 인기대출 순위, 도서명, 저자명, 출판사, 출판년도, ISBN, 대출횟수
- 키는 쿼리 `authKey`

미확인 (매뉴얼 본문을 읽지 못해 흔히 알려진 형식으로 짠 가정)
- `format=json` 응답 모양 `{"response": {"numFound", "resultNum", "docs": [{"doc": {ranking,
  bookname, authors, publisher, publication_year, isbn13, class_no, class_nm, loan_count, ...}}]}}`
  (loan_count·ranking이 문자열일 수도 있어 둘 다 받는다)
- 오류 응답 `{"response": {"error": "..."}}`와 HTTP 상태
- 하루 호출 한도: 공식 수치 미확인(비공식 안내 기본 500회/일) -> `DATA4LIBRARY_DAILY_LIMIT`
- `pageSize` 최댓값, 정렬 기준, 빈 결과 모양

주소는 `AX_DATA4LIBRARY_URL`로 바꾼다. 키는 환경변수에서만 읽고 로그·오류·repr·재생 파일에
넣지 않는다. `DATA_EXTERNAL_MODE`(live·record·replay)는 `naver_datalab`과 같다.
숫자(대출 횟수)는 응답에서 읽은 것만 쓴다.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass, field
from datetime import date
from typing import Any

import httpx

from data_agent.connectors import ConnectorUnavailable, replay
from data_agent.connectors.naver_shopping import DayCache

SOURCE_ID = "data4library"
KEY_ENVS = ("DATA4LIBRARY_API_KEY",)
DOCS_URL = "https://data4library.kr/apiUtilization"
DEFAULT_URL = "http://data4library.kr/api"
URL_ENV = "AX_DATA4LIBRARY_URL"
DAILY_LIMIT_ENV = "DATA4LIBRARY_DAILY_LIMIT"
DEFAULT_DAILY_LIMIT = 500  # 공식 수치 미확인; 비공식 안내 기본 500회/일
TIMEOUT_SECONDS = 10.0
LABEL = "도서관 정보나루"

# KDC(한국십진분류) 주제 대분류. 고정된 공공 분류라 카테고리 출처는 "fixed"다.
KDC_CLASSES: tuple[tuple[str, str], ...] = (
    ("0", "총류"),
    ("1", "철학"),
    ("2", "종교"),
    ("3", "사회과학"),
    ("4", "자연과학"),
    ("5", "기술과학"),
    ("6", "예술"),
    ("7", "언어"),
    ("8", "문학"),
    ("9", "역사"),
)

_client: httpx.AsyncClient | None = None


def use_client(client: httpx.AsyncClient | None) -> None:
    """HTTP 클라이언트를 바꿔 끼운다 (테스트용)."""
    global _client
    _client = client


def has_keys(env: Mapping[str, str] | None = None) -> bool:
    env = os.environ if env is None else env
    return all(env.get(name) for name in KEY_ENVS)


@dataclass(frozen=True)
class LoanItem:
    ranking: int
    title: str
    isbn13: str
    class_no: str
    class_nm: str
    loan_count: int


@dataclass(frozen=True)
class LoanResult:
    numFound: int  # noqa: N815  (응답 칸 이름 그대로)
    items: list[LoanItem] = field(default_factory=list)


_cache = DayCache(label=LABEL)


def clear_cache() -> None:
    _cache.clear()


def _limit(env: Mapping[str, str]) -> int:
    try:
        value = int(str(env.get(DAILY_LIMIT_ENV, "")).strip())
    except ValueError:
        return DEFAULT_DAILY_LIMIT
    return value if value > 0 else DEFAULT_DAILY_LIMIT


class Data4LibraryConnector:
    def __init__(
        self,
        env: Mapping[str, str] | None = None,
        *,
        client: httpx.AsyncClient | None = None,
        cache: DayCache | None = None,
    ) -> None:
        env = os.environ if env is None else env
        self.url = (env.get(URL_ENV) or DEFAULT_URL).rstrip("/")
        self.daily_limit = _limit(env)
        self._key = env.get(KEY_ENVS[0]) or ""
        self._client = client
        self._cache = cache or _cache
        self._env = env
        self.mode = replay.mode(env)

    def __repr__(self) -> str:  # 키를 담지 않는다
        return f"Data4LibraryConnector(url={self.url!r})"

    async def popular_loans(
        self,
        start: date,
        end: date,
        *,
        kdc: str | None = None,
        from_age: int | None = None,
        to_age: int | None = None,
        gender: str | None = None,
        page_size: int = 50,
    ) -> LoanResult:
        """기간 안 인기대출도서. 못 읽으면 `ConnectorUnavailable`."""
        url = f"{self.url}/loanItemSrch"
        query: dict[str, Any] = {
            "startDt": start.isoformat(),
            "endDt": end.isoformat(),
            "pageNo": 1,
            "pageSize": page_size,
            "format": "json",
        }
        for name, value in (
            ("kdc", kdc),
            ("from_age", from_age),
            ("to_age", to_age),
            ("gender", gender),
        ):
            if value is not None:
                query[name] = value
        key = f"{url}|{sorted(query.items())}"
        cached: LoanResult | None = self._cache.get(key)
        if cached is not None:
            return cached
        if self.mode == "replay":
            payload = replay.load(SOURCE_ID, url, query, self._env)
        else:
            if not self._key:
                raise ConnectorUnavailable(
                    "UNAUTHORIZED",
                    f"{KEY_ENVS[0]} 키가 없어 도서관 정보나루를 부를 수 없다 (키 등록 후 가능)",
                )
            self._cache.count_call(self.daily_limit, LABEL)
            payload = await self._get(url, query)
            if self.mode == "record":
                replay.save(SOURCE_ID, url, query, payload, self._env)
        result = _parse(payload)
        self._cache.put(key, result)
        return result

    async def _get(self, url: str, query: dict[str, Any]) -> Any:
        client = self._client or _client or httpx.AsyncClient(timeout=TIMEOUT_SECONDS)
        try:
            response = await client.get(url, params={"authKey": self._key, **query})
        except httpx.HTTPError as exc:
            raise ConnectorUnavailable(
                "UNAVAILABLE", f"{LABEL}에 연결할 수 없다 ({type(exc).__name__})"
            ) from exc
        finally:
            if client is not self._client and client is not _client:
                await client.aclose()
        if response.status_code != 200:
            raise _http_error(response)
        try:
            return response.json()
        except ValueError as exc:
            raise ConnectorUnavailable("BAD_RESPONSE", f"{LABEL} 응답이 JSON이 아니다") from exc


def _message(data: Any) -> str:
    """오류 문장 (`{"response": {"error": ".."}}`). 없으면 빈 문자열."""
    body = data.get("response") if isinstance(data, dict) else None
    error = body.get("error") if isinstance(body, dict) else None
    return f": {error[:200]}" if isinstance(error, str) else ""


def _http_error(response: httpx.Response) -> ConnectorUnavailable:
    status = response.status_code
    code = {400: "BAD_REQUEST", 401: "UNAUTHORIZED", 403: "UNAUTHORIZED", 429: "RATE_LIMITED"}.get(
        status, "UNAVAILABLE"
    )
    try:
        detail = _message(response.json())
    except ValueError:
        detail = ""
    return ConnectorUnavailable(code, f"{LABEL} 응답 {status} ({code}){detail}")


def _int(value: Any) -> int:
    return int(str(value).replace(",", "").strip())


def _parse(payload: Any) -> LoanResult:
    body = payload.get("response") if isinstance(payload, dict) else None
    if not isinstance(body, dict):
        raise ConnectorUnavailable("BAD_RESPONSE", f"{LABEL} 응답 형식이 예상과 다르다")
    error = body.get("error")
    if isinstance(error, str):  # 키 오류 등은 200으로 올 수 있다 (미확인)
        text = error[:200]
        code = "UNAUTHORIZED" if "인증" in text or "authKey" in text else "BAD_REQUEST"
        raise ConnectorUnavailable(code, f"{LABEL} 오류: {text}")
    docs = body.get("docs", [])
    if not isinstance(docs, list):
        raise ConnectorUnavailable("BAD_RESPONSE", f"{LABEL} 응답 형식이 예상과 다르다 (docs)")
    items = []
    try:
        for entry in docs:
            doc = entry["doc"]
            items.append(
                LoanItem(
                    ranking=_int(doc["ranking"]),
                    title=str(doc["bookname"]).strip(),
                    isbn13=str(doc.get("isbn13", "")),
                    class_no=str(doc.get("class_no", "")),
                    class_nm=str(doc.get("class_nm", "")),
                    loan_count=_int(doc["loan_count"]),
                )
            )
        found = _int(body["numFound"]) if "numFound" in body else len(items)
    except (KeyError, TypeError, ValueError) as exc:
        raise ConnectorUnavailable(
            "BAD_RESPONSE", f"{LABEL} 응답 형식이 예상과 다르다 (doc)"
        ) from exc
    return LoanResult(found, items)
