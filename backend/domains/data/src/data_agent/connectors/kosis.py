"""통계청 KOSIS 공유서비스 커넥터 (통계목록·통계자료 파라미터 방식).

공식 문서
- 통계목록: https://kosis.kr/openapi/devGuide/devGuide_0101List.do
- 통계자료(파라미터): https://kosis.kr/openapi/devGuide/devGuide_0201List.do

문서로 확인한 것
- 통계목록 `GET https://kosis.kr/openapi/statisticsList.do?method=getList&apiKey=..&vwCd=..
  &parentListId=..&format=json&jsonVD=Y` -> 배열 `{VW_CD, VW_NM, LIST_ID, LIST_NM, ORG_ID, TBL_ID,
  TBL_NM, STAT_ID, SEND_DE, REC_TBL_SE}`
- 통계자료 `GET https://kosis.kr/openapi/Param/statisticsParameterData.do?method=getList&apiKey=..
  &orgId=..&tblId=..&itmId=..&objL1=..&prdSe=..&startPrdDe=..&endPrdDe=..&newEstPrdCnt=..
  &format=json&jsonVD=Y` -> 배열 `{ORG_ID, TBL_ID, TBL_NM, C1, C1_NM, ITM_ID, ITM_NM, UNIT_NM,
  PRD_SE, PRD_DE, DT, ...}`. DT는 문자열 숫자
- 키는 쿼리 `apiKey`. HTTPS만 쓴다

미확인
- 오류 응답 모양: `{"err": "20", "errMsg": "..."}` 형태로 알려졌으나 문서로 확인 못 함.
  코드 의미도 가정(30·31 키 문제, 22 호출 제한, 나머지는 요청 오류)
- 2026 공지: HTTP 종료, 분당 호출 제한 (수치 미확인)
- 하루 호출 한도 (`KOSIS_DAILY_LIMIT`, 기본 1000은 가정)
- DT가 숫자가 아닌 값("-" 등)의 정확한 의미 (None으로 두고 0으로 채우지 않는다)

키는 환경변수에서만 읽고 로그·오류·repr·재생 파일에 넣지 않는다.
`DATA_EXTERNAL_MODE`(live·record·replay)는 `naver_datalab`과 같다.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

import httpx

from data_agent.connectors import ConnectorUnavailable, replay
from data_agent.connectors.naver_shopping import DayCache

SOURCE_ID = "kosis"
KEY_ENVS = ("KOSIS_API_KEY",)
DOCS_URL = "https://kosis.kr/openapi/devGuide/devGuide_0101List.do"
LIST_URL = "https://kosis.kr/openapi/statisticsList.do"
DATA_URL = "https://kosis.kr/openapi/Param/statisticsParameterData.do"
DAILY_LIMIT_ENV = "KOSIS_DAILY_LIMIT"
DEFAULT_DAILY_LIMIT = 1000  # 공식 수치 미확인
TIMEOUT_SECONDS = 10.0
LABEL = "KOSIS"

_client: httpx.AsyncClient | None = None


def use_client(client: httpx.AsyncClient | None) -> None:
    """HTTP 클라이언트를 바꿔 끼운다 (테스트용)."""
    global _client
    _client = client


def has_keys(env: Mapping[str, str] | None = None) -> bool:
    env = os.environ if env is None else env
    return all(env.get(name) for name in KEY_ENVS)


@dataclass(frozen=True)
class KosisListItem:
    vw_cd: str
    list_id: str
    list_nm: str
    org_id: str
    tbl_id: str
    tbl_nm: str
    stat_id: str
    send_de: str
    rec_tbl_se: str


@dataclass(frozen=True)
class KosisDataPoint:
    org_id: str
    tbl_id: str
    tbl_nm: str
    c1: str
    c1_nm: str
    itm_id: str
    itm_nm: str
    unit_nm: str
    prd_se: str
    prd_de: str
    value: float | None  # DT가 숫자가 아니면 None (0이 아님)


_cache = DayCache(label=LABEL)


def clear_cache() -> None:
    _cache.clear()


def _limit(env: Mapping[str, str]) -> int:
    try:
        value = int(str(env.get(DAILY_LIMIT_ENV, "")).strip())
    except ValueError:
        return DEFAULT_DAILY_LIMIT
    return value if value > 0 else DEFAULT_DAILY_LIMIT


class KosisConnector:
    def __init__(
        self,
        env: Mapping[str, str] | None = None,
        *,
        client: httpx.AsyncClient | None = None,
        cache: DayCache | None = None,
    ) -> None:
        env = os.environ if env is None else env
        self.daily_limit = _limit(env)
        self._key = env.get(KEY_ENVS[0]) or ""
        self._client = client
        self._cache = cache or _cache
        self._env = env
        self.mode = replay.mode(env)

    def __repr__(self) -> str:  # 키를 담지 않는다
        return "KosisConnector()"

    async def statistics_list(
        self, vw_cd: str = "MT_ZTITLE", parent_list_id: str = ""
    ) -> list[KosisListItem]:
        """통계목록 한 단계."""
        query = {
            "method": "getList",
            "vwCd": vw_cd,
            "parentListId": parent_list_id,
            "format": "json",
            "jsonVD": "Y",
        }
        rows = await self._call(LIST_URL, query)
        try:
            return [
                KosisListItem(
                    vw_cd=str(r.get("VW_CD", "")),
                    list_id=str(r.get("LIST_ID", "")),
                    list_nm=str(r.get("LIST_NM", "")),
                    org_id=str(r.get("ORG_ID", "")),
                    tbl_id=str(r.get("TBL_ID", "")),
                    tbl_nm=str(r.get("TBL_NM", "")),
                    stat_id=str(r.get("STAT_ID", "")),
                    send_de=str(r.get("SEND_DE", "")),
                    rec_tbl_se=str(r.get("REC_TBL_SE", "")),
                )
                for r in rows
            ]
        except AttributeError as exc:
            raise ConnectorUnavailable(
                "BAD_RESPONSE", f"{LABEL} 응답 형식이 예상과 다르다"
            ) from exc

    async def statistics_data(
        self,
        org_id: str,
        tbl_id: str,
        itm_id: str,
        obj_l1: str,
        prd_se: str,
        start_prd_de: str | None = None,
        end_prd_de: str | None = None,
        new_est_prd_cnt: int | None = None,
    ) -> list[KosisDataPoint]:
        """통계표 자료. 기간은 시작·끝 또는 최근 n개 시점."""
        query: dict[str, Any] = {
            "method": "getList",
            "orgId": org_id,
            "tblId": tbl_id,
            "itmId": itm_id,
            "objL1": obj_l1,
            "prdSe": prd_se,
            "format": "json",
            "jsonVD": "Y",
        }
        for name, value in (
            ("startPrdDe", start_prd_de),
            ("endPrdDe", end_prd_de),
            ("newEstPrdCnt", new_est_prd_cnt),
        ):
            if value is not None:
                query[name] = value
        rows = await self._call(DATA_URL, query)
        try:
            return [
                KosisDataPoint(
                    org_id=str(r.get("ORG_ID", "")),
                    tbl_id=str(r.get("TBL_ID", "")),
                    tbl_nm=str(r.get("TBL_NM", "")),
                    c1=str(r.get("C1", "")),
                    c1_nm=str(r.get("C1_NM", "")),
                    itm_id=str(r.get("ITM_ID", "")),
                    itm_nm=str(r.get("ITM_NM", "")),
                    unit_nm=str(r.get("UNIT_NM", "")),
                    prd_se=str(r.get("PRD_SE", "")),
                    prd_de=str(r.get("PRD_DE", "")),
                    value=_number(r.get("DT")),
                )
                for r in rows
            ]
        except AttributeError as exc:
            raise ConnectorUnavailable(
                "BAD_RESPONSE", f"{LABEL} 응답 형식이 예상과 다르다"
            ) from exc

    async def _call(self, url: str, query: dict[str, Any]) -> list[Any]:
        key = f"{url}|{sorted(query.items())}"
        cached: list[Any] | None = self._cache.get(key)
        if cached is not None:
            return cached
        if self.mode == "replay":
            payload = replay.load(SOURCE_ID, url, query, self._env)
        else:
            if not self._key:
                raise ConnectorUnavailable(
                    "UNAUTHORIZED",
                    f"{KEY_ENVS[0]} 키가 없어 KOSIS를 부를 수 없다 (키 등록 후 가능)",
                )
            self._cache.count_call(self.daily_limit, LABEL)
            payload = await self._get(url, query)
            if self.mode == "record":
                replay.save(SOURCE_ID, url, query, payload, self._env)
        rows = _rows(payload)
        self._cache.put(key, rows)
        return rows

    async def _get(self, url: str, query: dict[str, Any]) -> Any:
        client = self._client or _client or httpx.AsyncClient(timeout=TIMEOUT_SECONDS)
        try:
            response = await client.get(url, params={"apiKey": self._key, **query})
        except httpx.HTTPError as exc:
            raise ConnectorUnavailable(
                "UNAVAILABLE", f"{LABEL}에 연결할 수 없다 ({type(exc).__name__})"
            ) from exc
        finally:
            if client is not self._client and client is not _client:
                await client.aclose()
        if response.status_code != 200:
            status = response.status_code
            code = {
                400: "BAD_REQUEST",
                401: "UNAUTHORIZED",
                403: "UNAUTHORIZED",
                429: "RATE_LIMITED",
            }
            raise ConnectorUnavailable(
                code.get(status, "UNAVAILABLE"),
                f"{LABEL} 응답 {status} ({code.get(status, 'UNAVAILABLE')})",
            )
        try:
            return response.json()
        except ValueError as exc:
            raise ConnectorUnavailable("BAD_RESPONSE", f"{LABEL} 응답이 JSON이 아니다") from exc


def _number(value: Any) -> float | None:
    try:
        return float(str(value).replace(",", "").strip())
    except ValueError:
        return None


def _rows(payload: Any) -> list[Any]:
    if isinstance(payload, list):
        return payload
    if isinstance(payload, dict) and ("err" in payload or "errMsg" in payload):
        err = str(payload.get("err", "")).strip()
        message = str(payload.get("errMsg", ""))[:200]
        code = {"30": "UNAUTHORIZED", "31": "UNAUTHORIZED", "22": "RATE_LIMITED"}.get(
            err, "BAD_REQUEST"
        )
        raise ConnectorUnavailable(code, f"{LABEL} 오류 {err}: {message}")
    raise ConnectorUnavailable("BAD_RESPONSE", f"{LABEL} 응답 형식이 예상과 다르다")
