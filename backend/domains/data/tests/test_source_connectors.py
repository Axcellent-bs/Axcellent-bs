"""출처 커넥터 3개(네이버 쇼핑인사이트·도서관 정보나루·KOSIS) 테스트.

실제 API 없이 httpx MockTransport로 돈다. 응답은 공식 문서 형식으로 만든 테스트 전용 가짜 응답이다.
"""

import json
from collections.abc import Callable
from datetime import date
from pathlib import Path
from typing import Any

import httpx
import pytest

from data_agent.connectors import ConnectorUnavailable
from data_agent.connectors import data4library as d4l
from data_agent.connectors import kosis as ks
from data_agent.connectors import naver_shopping as ns
from data_agent.connectors.naver_shopping import DayCache

pytestmark = pytest.mark.asyncio

Handler = Callable[[httpx.Request], httpx.Response]
START, END = date(2026, 10, 1), date(2026, 10, 3)

NAVER_ENV = {"NAVER_HUB_CLIENT_ID": "test-id-1234", "NAVER_HUB_CLIENT_SECRET": "test-secret-5678"}
D4L_ENV = {"DATA4LIBRARY_API_KEY": "test-d4l-key-9999"}
KOSIS_ENV = {"KOSIS_API_KEY": "test-kosis-key-7777"}

# ---- 공식 문서 형식으로 만든 테스트 전용 가짜 응답

NAVER_CATEGORIES = {
    "startDate": "2026-10-01",
    "endDate": "2026-10-03",
    "timeUnit": "date",
    "results": [
        {
            "title": "패션의류",
            "category": ["50000000"],
            "data": [
                {"period": "2026-10-01", "ratio": 91.5},
                {"period": "2026-10-02", "ratio": 100.0},
            ],
        },
        {"title": "식품", "category": ["50000006"], "data": []},
    ],
}
NAVER_AGE = {
    "startDate": "2026-10-01",
    "endDate": "2026-10-03",
    "timeUnit": "date",
    "results": [
        {
            "title": "패션의류",
            "category": ["50000000"],
            "data": [
                {"period": "2026-10-01", "ratio": 50.0, "group": "20"},
                {"period": "2026-10-01", "ratio": 80.0, "group": "30"},
                {"period": "2026-10-02", "ratio": 100.0, "group": "30"},
            ],
        }
    ],
}
NAVER_KEYWORDS = {
    "startDate": "2026-10-01",
    "endDate": "2026-10-03",
    "timeUnit": "date",
    "results": [
        {
            "title": "원피스",
            "keyword": ["원피스"],
            "data": [{"period": "2026-10-01", "ratio": 70.25}],
        }
    ],
}
NAVER_GATEWAY_ERROR = {"error": {"errorCode": "200", "message": "Authentication Failed"}}
NAVER_ERRMSG = {"errMsg": "Bad Request", "errId": "E1", "body": {"secret": "echo"}}

D4L_POPULAR = {
    "response": {
        "request": {"startDt": "2026-10-01", "endDt": "2026-10-03", "pageNo": "1"},
        "resultNum": 2,
        "numFound": 1234,
        "docs": [
            {
                "doc": {
                    "no": 1,
                    "ranking": "1",
                    "bookname": "고양이 책",
                    "authors": "지은이",
                    "publisher": "출판사",
                    "publication_year": "2024",
                    "isbn13": "9788900000001",
                    "addition_symbol": "03810",
                    "vol": "",
                    "class_no": "818",
                    "class_nm": "문학 > 한국문학",
                    "loan_count": "321",
                    "bookImageURL": "http://example.invalid/a.jpg",
                }
            },
            {
                "doc": {
                    "no": 2,
                    "ranking": "2",
                    "bookname": "과학 책",
                    "isbn13": "9788900000002",
                    "class_no": "400",
                    "class_nm": "자연과학",
                    "loan_count": 120,
                }
            },
        ],
    }
}
D4L_EMPTY = {"response": {"resultNum": 0, "numFound": 0, "docs": []}}
D4L_ERROR = {"response": {"error": "인증키가 유효하지 않습니다"}}

KOSIS_LIST = [
    {
        "VW_CD": "MT_ZTITLE",
        "VW_NM": "국내통계 주제별",
        "LIST_ID": "A",
        "LIST_NM": "인구",
        "ORG_ID": "101",
        "TBL_ID": "DT_1B040A3",
        "TBL_NM": "행정구역별 인구",
        "STAT_ID": "1962001",
        "SEND_DE": "202610",
        "REC_TBL_SE": "Y",
    }
]
KOSIS_DATA = [
    {
        "ORG_ID": "101",
        "TBL_ID": "DT_1B040A3",
        "TBL_NM": "행정구역별 인구",
        "C1": "00",
        "C1_NM": "전국",
        "ITM_ID": "T20",
        "ITM_NM": "총인구",
        "UNIT_NM": "명",
        "PRD_SE": "M",
        "PRD_DE": "202609",
        "DT": "51234567",
    },
    {
        "ORG_ID": "101",
        "TBL_ID": "DT_1B040A3",
        "TBL_NM": "행정구역별 인구",
        "C1": "00",
        "C1_NM": "전국",
        "ITM_ID": "T20",
        "ITM_NM": "총인구",
        "UNIT_NM": "명",
        "PRD_SE": "M",
        "PRD_DE": "202610",
        "DT": "-",
    },
]


class Recorder:
    def __init__(self, handler: Handler) -> None:
        self.handler = handler
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self.handler(request)


def _client(handler: Handler) -> tuple[httpx.AsyncClient, Recorder]:
    recorder = Recorder(handler)
    return httpx.AsyncClient(transport=httpx.MockTransport(recorder)), recorder


def _cache() -> DayCache:
    return DayCache(today=lambda: date(2026, 11, 2))


def _json(body: Any, status: int = 200) -> Handler:
    return lambda request: httpx.Response(status, json=body)


def _no_network(request: httpx.Request) -> httpx.Response:
    raise AssertionError("네트워크를 부르면 안 된다")


def _naver(handler: Handler, env: dict[str, str] | None = None):
    client, seen = _client(handler)
    conn = ns.NaverShoppingConnector(
        NAVER_ENV if env is None else env, client=client, cache=_cache()
    )
    return conn, seen


def _d4l(handler: Handler, env: dict[str, str] | None = None):
    client, seen = _client(handler)
    conn = d4l.Data4LibraryConnector(D4L_ENV if env is None else env, client=client, cache=_cache())
    return conn, seen


def _kosis(handler: Handler, env: dict[str, str] | None = None):
    client, seen = _client(handler)
    conn = ks.KosisConnector(KOSIS_ENV if env is None else env, client=client, cache=_cache())
    return conn, seen


def _replay_env(base: dict[str, str], mode: str, folder: Path) -> dict[str, str]:
    return {**base, "DATA_EXTERNAL_MODE": mode, "DATA_REPLAY_DIR": str(folder)}


# ================================================================ 네이버 쇼핑인사이트


async def test_constants_and_has_keys() -> None:
    assert ns.SOURCE_ID == "naver_shopping" and ns.KEY_ENVS[0] == "NAVER_HUB_CLIENT_ID"
    assert d4l.KEY_ENVS == ("DATA4LIBRARY_API_KEY",) and ks.KEY_ENVS == ("KOSIS_API_KEY",)
    assert ns.has_keys(NAVER_ENV) and not ns.has_keys({"NAVER_HUB_CLIENT_ID": "x"})
    assert d4l.has_keys(D4L_ENV) and not d4l.has_keys({})
    assert ks.has_keys(KOSIS_ENV) and not ks.has_keys({})
    assert (
        ns.DOCS_URL.startswith("https://") and d4l.DOCS_URL and ks.DOCS_URL.startswith("https://")
    )


async def test_naver_category_trend_request_and_parse() -> None:
    conn, seen = _naver(_json(NAVER_CATEGORIES))
    series = await conn.category_trend(
        [("패션의류", "50000000"), ("식품", "50000006")], START, END, "date"
    )
    request = seen.requests[0]
    assert request.method == "POST"
    assert str(request.url) == "https://naverapihub.apigw.ntruss.com/shopping/v1/categories"
    assert request.headers["X-NCP-APIGW-API-KEY-ID"] == "test-id-1234"
    assert request.headers["X-NCP-APIGW-API-KEY"] == "test-secret-5678"
    assert request.headers["Content-Type"] == "application/json"
    assert json.loads(request.content) == {
        "startDate": "2026-10-01",
        "endDate": "2026-10-03",
        "timeUnit": "date",
        "category": [
            {"name": "패션의류", "param": ["50000000"]},
            {"name": "식품", "param": ["50000006"]},
        ],
    }
    assert series[0].title == "패션의류" and series[0].codes == ("50000000",)
    assert series[0].points == ((date(2026, 10, 1), 91.5), (date(2026, 10, 2), 100.0))
    assert series[1].points == ()  # 0으로 채우지 않는다


async def test_naver_category_age_body_and_groups() -> None:
    conn, seen = _naver(_json(NAVER_AGE))
    groups = await conn.category_age("50000000", START, END)
    assert seen.requests[0].url.path == "/shopping/v1/category/age"
    assert json.loads(seen.requests[0].content)["category"] == "50000000"
    assert groups["30"] == [(date(2026, 10, 1), 80.0), (date(2026, 10, 2), 100.0)]
    assert groups["20"] == [(date(2026, 10, 1), 50.0)]  # 빠진 날은 그대로


async def test_naver_keyword_trend_and_age_bodies() -> None:
    conn, seen = _naver(_json(NAVER_KEYWORDS))
    series = await conn.keyword_trend("50000000", ["원피스"], START, END, "week")
    body = json.loads(seen.requests[0].content)
    assert seen.requests[0].url.path == "/shopping/v1/category/keywords"
    assert body["category"] == "50000000" and body["timeUnit"] == "week"
    assert body["keyword"] == [{"name": "원피스", "param": ["원피스"]}]
    assert series[0].codes == ("원피스",) and series[0].points == ((date(2026, 10, 1), 70.25),)

    conn2, seen2 = _naver(_json(NAVER_AGE))
    await conn2.keyword_age("50000000", "원피스", START, END)
    body2 = json.loads(seen2.requests[0].content)
    assert seen2.requests[0].url.path == "/shopping/v1/category/keyword/age"
    assert body2["keyword"] == "원피스"


async def test_naver_limits_on_batch_size() -> None:
    conn, seen = _naver(_json(NAVER_CATEGORIES))
    too_many = [(f"n{i}", str(i)) for i in range(4)]
    with pytest.raises(ConnectorUnavailable) as err:
        await conn.category_trend(too_many, START, END)
    assert err.value.code == "BAD_REQUEST" and not seen.requests


@pytest.mark.parametrize(
    ("status", "body", "code", "text"),
    [
        (401, NAVER_GATEWAY_ERROR, "UNAUTHORIZED", "Authentication Failed"),
        (403, NAVER_GATEWAY_ERROR, "UNAUTHORIZED", "Authentication Failed"),
        (429, NAVER_GATEWAY_ERROR, "RATE_LIMITED", "Authentication Failed"),
        (400, NAVER_ERRMSG, "BAD_REQUEST", "Bad Request"),
        (500, NAVER_ERRMSG, "UNAVAILABLE", "Bad Request"),
    ],
)
async def test_naver_error_mapping(status: int, body: dict, code: str, text: str) -> None:
    conn, _ = _naver(_json(body, status))
    with pytest.raises(ConnectorUnavailable) as err:
        await conn.category_trend([("패션의류", "50000000")], START, END)
    assert err.value.code == code
    message = str(err.value)
    assert text in message and "echo" not in message and "test-secret" not in message


async def test_naver_missing_key_no_network() -> None:
    conn, _ = _naver(_no_network, env={})
    with pytest.raises(ConnectorUnavailable) as err:
        await conn.category_trend([("패션의류", "50000000")], START, END)
    assert err.value.code == "UNAUTHORIZED" and "키 등록 후 가능" in str(err.value)


async def test_naver_daily_limit_and_cache() -> None:
    env = {**NAVER_ENV, "NAVER_SHOPPING_DAILY_LIMIT": "1"}
    conn, seen = _naver(_json(NAVER_CATEGORIES), env=env)
    await conn.category_trend([("패션의류", "50000000")], START, END)
    await conn.category_trend([("패션의류", "50000000")], START, END)  # 캐시
    assert len(seen.requests) == 1
    with pytest.raises(ConnectorUnavailable) as err:
        await conn.category_age("50000000", START, END)
    assert err.value.code == "RATE_LIMITED"


async def test_naver_record_then_replay(tmp_path: Path) -> None:
    rec_env = _replay_env(NAVER_ENV, "record", tmp_path)
    conn, _ = _naver(_json(NAVER_CATEGORIES), env=rec_env)
    await conn.category_trend([("패션의류", "50000000")], START, END)
    saved = "".join(p.read_text(encoding="utf-8") for p in tmp_path.glob("*.json"))
    assert "패션의류" in saved and "test-secret" not in saved and "test-id" not in saved
    assert "test-secret" not in repr(conn)

    rep_env = _replay_env(NAVER_ENV, "replay", tmp_path)
    conn2, _ = _naver(_no_network, env=rep_env)
    series = await conn2.category_trend([("패션의류", "50000000")], START, END)
    assert series[0].points[0] == (date(2026, 10, 1), 91.5)
    with pytest.raises(ConnectorUnavailable) as err:
        await conn2.category_age("50000000", START, END)
    assert err.value.code == "REPLAY_MISS"


async def test_naver_verify_categories_drops_without_data() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        names = [c["name"] for c in json.loads(request.content)["category"]]
        results = [
            {
                "title": n,
                "category": [n],
                "data": [] if n == "식품" else [{"period": "2026-10-01", "ratio": 1.0}],
            }
            for n in names
            if n != "도서"  # 응답에서 빠진 분야
        ]
        return httpx.Response(200, json={"results": results})

    conn, seen = _naver(handler)
    cands = [("패션의류", "50000000"), ("식품", "50000006"), ("도서", "50005542"), ("생활", "1")]
    verified, dropped = await conn.verify_categories(cands, START, END)
    assert len(seen.requests) == 2  # 3개씩 묶음
    assert verified == [("패션의류", "50000000"), ("생활", "1")]
    assert [(n, r) for n, _, r in dropped] == [("식품", "데이터 없음"), ("도서", "응답에 없음")]


async def test_naver_candidates_cover_official_examples() -> None:
    cids = dict(ns.CATEGORY_CANDIDATES)
    assert cids["패션의류"] == "50000000" and cids["화장품/미용"] == "50000002"
    assert len(ns.CATEGORY_CANDIDATES) == 11


async def test_naver_probe_true_and_false() -> None:
    conn, seen = _naver(_json(NAVER_CATEGORIES))
    ok, why = await conn.probe()
    body = json.loads(seen.requests[0].content)
    assert ok and why
    assert body["timeUnit"] == "month" and body["category"][0]["param"] == ["50000000"]

    conn2, _ = _naver(_json(NAVER_GATEWAY_ERROR, 401))
    ok2, why2 = await conn2.probe()
    assert not ok2 and "UNAUTHORIZED" in why2

    conn3, _ = _naver(_json({"results": []}))
    ok3, _ = await conn3.probe()
    assert not ok3


# ================================================================ 도서관 정보나루


async def test_d4l_request_and_parse() -> None:
    conn, seen = _d4l(_json(D4L_POPULAR))
    result = await conn.popular_loans(START, END, kdc="8", from_age=20, to_age=29, gender="1")
    request = seen.requests[0]
    assert request.method == "GET"
    assert f"{request.url.scheme}://{request.url.host}{request.url.path}" == (
        "http://data4library.kr/api/loanItemSrch"
    )
    params = dict(request.url.params)
    assert params["authKey"] == "test-d4l-key-9999"
    assert params["startDt"] == "2026-10-01" and params["endDt"] == "2026-10-03"
    assert params["format"] == "json" and params["pageSize"] == "50" and params["kdc"] == "8"
    assert params["from_age"] == "20" and params["to_age"] == "29" and params["gender"] == "1"
    assert result.numFound == 1234
    first = result.items[0]
    assert (first.ranking, first.title, first.isbn13) == (1, "고양이 책", "9788900000001")
    assert (first.class_no, first.class_nm, first.loan_count) == ("818", "문학 > 한국문학", 321)
    assert result.items[1].loan_count == 120


async def test_d4l_optional_params_omitted_and_empty_not_filled() -> None:
    conn, seen = _d4l(_json(D4L_EMPTY))
    result = await conn.popular_loans(START, END)
    params = dict(seen.requests[0].url.params)
    assert "kdc" not in params and "gender" not in params and "from_age" not in params
    assert result.numFound == 0 and result.items == []


async def test_d4l_kdc_classes() -> None:
    assert [c for c, _ in d4l.KDC_CLASSES] == list("0123456789")
    assert dict(d4l.KDC_CLASSES)["8"] == "문학"


@pytest.mark.parametrize(
    ("status", "code"), [(401, "UNAUTHORIZED"), (403, "UNAUTHORIZED"), (429, "RATE_LIMITED")]
)
async def test_d4l_http_error_mapping(status: int, code: str) -> None:
    conn, _ = _d4l(_json(D4L_ERROR, status))
    with pytest.raises(ConnectorUnavailable) as err:
        await conn.popular_loans(START, END)
    assert err.value.code == code and "test-d4l-key" not in str(err.value)


async def test_d4l_error_json_in_200() -> None:
    conn, _ = _d4l(_json(D4L_ERROR))
    with pytest.raises(ConnectorUnavailable) as err:
        await conn.popular_loans(START, END)
    assert err.value.code == "UNAUTHORIZED" and "인증키" in str(err.value)


async def test_d4l_missing_key_no_network() -> None:
    conn, _ = _d4l(_no_network, env={})
    with pytest.raises(ConnectorUnavailable) as err:
        await conn.popular_loans(START, END)
    assert err.value.code == "UNAUTHORIZED" and "키 등록 후 가능" in str(err.value)


async def test_d4l_daily_limit() -> None:
    conn, seen = _d4l(_json(D4L_POPULAR), env={**D4L_ENV, "DATA4LIBRARY_DAILY_LIMIT": "1"})
    await conn.popular_loans(START, END)
    with pytest.raises(ConnectorUnavailable) as err:
        await conn.popular_loans(START, END, kdc="8")
    assert err.value.code == "RATE_LIMITED" and len(seen.requests) == 1


async def test_d4l_record_then_replay(tmp_path: Path) -> None:
    conn, _ = _d4l(_json(D4L_POPULAR), env=_replay_env(D4L_ENV, "record", tmp_path))
    await conn.popular_loans(START, END, kdc="8")
    saved = "".join(p.read_text(encoding="utf-8") for p in tmp_path.glob("*.json"))
    assert "고양이 책" in saved and "test-d4l-key" not in saved
    assert "test-d4l-key" not in repr(conn)

    conn2, _ = _d4l(_no_network, env=_replay_env(D4L_ENV, "replay", tmp_path))
    assert (await conn2.popular_loans(START, END, kdc="8")).numFound == 1234
    with pytest.raises(ConnectorUnavailable) as err:
        await conn2.popular_loans(START, END, kdc="9")
    assert err.value.code == "REPLAY_MISS"


# ================================================================ KOSIS


async def test_kosis_statistics_list_request_and_parse() -> None:
    conn, seen = _kosis(_json(KOSIS_LIST))
    items = await conn.statistics_list()
    request = seen.requests[0]
    assert request.method == "GET" and request.url.scheme == "https"
    assert str(request.url).startswith("https://kosis.kr/openapi/statisticsList.do?")
    params = dict(request.url.params)
    assert params == {
        "apiKey": "test-kosis-key-7777",
        "method": "getList",
        "vwCd": "MT_ZTITLE",
        "parentListId": "",
        "format": "json",
        "jsonVD": "Y",
    }
    assert items[0].tbl_id == "DT_1B040A3" and items[0].list_nm == "인구"


async def test_kosis_statistics_data_request_and_parse() -> None:
    conn, seen = _kosis(_json(KOSIS_DATA))
    points = await conn.statistics_data(
        "101", "DT_1B040A3", "T20", "ALL", "M", start_prd_de="202609", end_prd_de="202610"
    )
    request = seen.requests[0]
    assert request.url.path == "/openapi/Param/statisticsParameterData.do"
    params = dict(request.url.params)
    assert params["orgId"] == "101" and params["tblId"] == "DT_1B040A3"
    assert params["itmId"] == "T20" and params["objL1"] == "ALL" and params["prdSe"] == "M"
    assert params["startPrdDe"] == "202609" and params["endPrdDe"] == "202610"
    assert "newEstPrdCnt" not in params and params["jsonVD"] == "Y"
    assert points[0].value == 51234567.0 and points[0].prd_de == "202609"
    assert points[0].unit_nm == "명" and points[0].c1_nm == "전국"
    assert points[1].value is None  # "-"는 0이 아니라 None


async def test_kosis_new_est_prd_cnt_param() -> None:
    conn, seen = _kosis(_json([]))
    assert await conn.statistics_data("101", "T", "I", "ALL", "Y", new_est_prd_cnt=3) == []
    assert dict(seen.requests[0].url.params)["newEstPrdCnt"] == "3"


@pytest.mark.parametrize(
    ("body", "code"),
    [
        ({"err": "30", "errMsg": "인증키 오류"}, "UNAUTHORIZED"),
        ({"err": "31", "errMsg": "인증키 기간 만료"}, "UNAUTHORIZED"),
        ({"err": "22", "errMsg": "호출 제한 초과"}, "RATE_LIMITED"),
        ({"err": "20", "errMsg": "필수 요청변수 누락"}, "BAD_REQUEST"),
    ],
)
async def test_kosis_error_json(body: dict, code: str) -> None:
    conn, _ = _kosis(_json(body))
    with pytest.raises(ConnectorUnavailable) as err:
        await conn.statistics_list()
    assert err.value.code == code and body["errMsg"] in str(err.value)
    assert "test-kosis-key" not in str(err.value)


@pytest.mark.parametrize(
    ("status", "code"), [(401, "UNAUTHORIZED"), (429, "RATE_LIMITED"), (500, "UNAVAILABLE")]
)
async def test_kosis_http_error_mapping(status: int, code: str) -> None:
    conn, _ = _kosis(_json({}, status))
    with pytest.raises(ConnectorUnavailable) as err:
        await conn.statistics_list()
    assert err.value.code == code


async def test_kosis_missing_key_no_network() -> None:
    conn, _ = _kosis(_no_network, env={})
    with pytest.raises(ConnectorUnavailable) as err:
        await conn.statistics_list()
    assert err.value.code == "UNAUTHORIZED" and "키 등록 후 가능" in str(err.value)


async def test_kosis_daily_limit() -> None:
    conn, seen = _kosis(_json(KOSIS_LIST), env={**KOSIS_ENV, "KOSIS_DAILY_LIMIT": "1"})
    await conn.statistics_list()
    with pytest.raises(ConnectorUnavailable) as err:
        await conn.statistics_list(parent_list_id="A")
    assert err.value.code == "RATE_LIMITED" and len(seen.requests) == 1


async def test_kosis_record_then_replay(tmp_path: Path) -> None:
    conn, _ = _kosis(_json(KOSIS_DATA), env=_replay_env(KOSIS_ENV, "record", tmp_path))
    await conn.statistics_data("101", "DT_1B040A3", "T20", "ALL", "M")
    saved = "".join(p.read_text(encoding="utf-8") for p in tmp_path.glob("*.json"))
    assert "DT_1B040A3" in saved and "test-kosis-key" not in saved
    assert "test-kosis-key" not in repr(conn)

    conn2, _ = _kosis(_no_network, env=_replay_env(KOSIS_ENV, "replay", tmp_path))
    points = await conn2.statistics_data("101", "DT_1B040A3", "T20", "ALL", "M")
    assert points[0].value == 51234567.0
    with pytest.raises(ConnectorUnavailable) as err:
        await conn2.statistics_data("101", "OTHER", "T20", "ALL", "M")
    assert err.value.code == "REPLAY_MISS"
