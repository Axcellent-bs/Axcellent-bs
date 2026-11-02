"""네이버 검색어 트렌드 커넥터 테스트. 실제 API 없이 httpx MockTransport 메모리 응답으로 돈다.

응답 모양은 2026-11-02 실제 호출(NAVER API HUB `search-trend/v1/search`)에서 본 것과 같다:
`{startDate, endDate, timeUnit, results: [{title, keywords, data: [{period, ratio}]}]}`.
"""

import json
from collections.abc import Callable
from datetime import date

import httpx
import pytest

from ax_contracts.schemas import TrendScanInput
from data_agent.connectors import ConnectorUnavailable
from data_agent.connectors.naver_datalab import (
    DEFAULT_URL,
    DailyCache,
    NaverDatalabConnector,
)

pytestmark = pytest.mark.asyncio

KEY_ID = "test-key-id-1234"
KEY = "test-secret-5678"
ENV = {"NAVER_HUB_CLIENT_ID": KEY_ID, "NAVER_HUB_CLIENT_SECRET": KEY}
Handler = Callable[[httpx.Request], httpx.Response]


def _query(*names: str, end: date = date(2026, 11, 1)) -> TrendScanInput:
    return TrendScanInput.model_validate(
        {
            "keyword_groups": [{"group_name": n, "keywords": [n]} for n in names or ("고양이",)],
            "start_date": "2026-10-30",
            "end_date": end.isoformat(),
        }
    )


def _naver(results: list[dict]) -> dict:
    return {"startDate": "2026-10-30", "endDate": "2026-11-01", "timeUnit": "date",
            "results": results}  # fmt: skip


def _series(title: str, values: list[float]) -> dict:
    days = ["2026-10-30", "2026-10-31", "2026-11-01"]
    data = [{"period": d, "ratio": v} for d, v in zip(days, values, strict=False)]
    return {"title": title, "keywords": [title], "data": data}


class Recorder:
    def __init__(self, handler: Handler) -> None:
        self.handler = handler
        self.requests: list[httpx.Request] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        return self.handler(request)


def _connector(
    handler: Handler, env: dict[str, str] | None = None
) -> tuple[NaverDatalabConnector, Recorder]:
    recorder = Recorder(handler)
    client = httpx.AsyncClient(transport=httpx.MockTransport(recorder))
    cache = DailyCache(today=lambda: date(2026, 11, 2))
    return NaverDatalabConnector(ENV if env is None else env, client=client, cache=cache), recorder


def _ok(body: dict) -> Handler:
    return lambda request: httpx.Response(200, json=body)


# ---------------------------------------------------------------- 정상


async def test_reads_series_with_hub_url_and_headers() -> None:
    body = _naver([_series("고양이", [94.04861, 92.24643, 100]), _series("밈", [10.5, 8.5, 9.0])])
    connector, seen = _connector(_ok(body))
    result = await connector.search_trend(_query("고양이", "밈"))

    request = seen.requests[0]
    assert str(request.url) == DEFAULT_URL
    assert request.method == "POST"
    assert request.headers["X-NCP-APIGW-API-KEY-ID"] == KEY_ID
    assert request.headers["X-NCP-APIGW-API-KEY"] == KEY
    sent = json.loads(request.content)
    assert sent["timeUnit"] == "date"
    assert sent["keywordGroups"][1] == {"groupName": "밈", "keywords": ["밈"]}
    cat = result.by_group("고양이")
    assert cat.points[-1] == (date(2026, 11, 1), 100.0)
    assert [s.group_name for s in result.series] == ["고양이", "밈"]  # 요청 순서


async def test_url_can_be_changed_by_env() -> None:
    body = _naver([_series("고양이", [1, 2, 3])])
    connector, seen = _connector(_ok(body), {**ENV, "AX_NAVER_DATALAB_URL": "http://naver.test/x"})
    await connector.search_trend(_query())
    assert str(seen.requests[0].url) == "http://naver.test/x"


# ---------------------------------------------------------------- 캐시·한도


async def test_same_request_is_cached_for_the_day() -> None:
    connector, seen = _connector(_ok(_naver([_series("고양이", [1, 2, 3])])))
    first = await connector.search_trend(_query())
    second = await connector.search_trend(_query())
    assert first == second
    assert len(seen.requests) == 1


async def test_different_request_is_not_cached() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        names = [g["groupName"] for g in json.loads(request.content)["keywordGroups"]]
        return httpx.Response(200, json=_naver([_series(n, [1, 2, 3]) for n in names]))

    connector, seen = _connector(handler)
    await connector.search_trend(_query("고양이"))
    await connector.search_trend(_query("밈"))
    assert len(seen.requests) == 2


async def test_cache_resets_when_korean_day_changes() -> None:
    today = [date(2026, 11, 2)]
    recorder = Recorder(_ok(_naver([_series("고양이", [1, 2, 3])])))
    client = httpx.AsyncClient(transport=httpx.MockTransport(recorder))
    connector = NaverDatalabConnector(ENV, client=client, cache=DailyCache(today=lambda: today[0]))
    await connector.search_trend(_query())
    today[0] = date(2026, 11, 3)  # 한국 날짜가 바뀌면 네이버 값도 새로 채워진다
    await connector.search_trend(_query())
    assert len(recorder.requests) == 2


async def test_daily_limit_stops_before_calling() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        names = [g["groupName"] for g in json.loads(request.content)["keywordGroups"]]
        return httpx.Response(200, json=_naver([_series(n, [1, 2, 3]) for n in names]))

    connector, seen = _connector(handler, {**ENV, "NAVER_DATALAB_DAILY_LIMIT": "1"})
    await connector.search_trend(_query("고양이"))
    await connector.search_trend(_query("고양이"))  # 캐시: 호출 수를 쓰지 않는다
    with pytest.raises(ConnectorUnavailable) as caught:
        await connector.search_trend(_query("밈"))
    assert caught.value.code == "RATE_LIMITED"
    assert len(seen.requests) == 1


# ---------------------------------------------------------------- 실패


@pytest.mark.parametrize(
    ("status", "code"),
    [(400, "BAD_REQUEST"), (401, "UNAUTHORIZED"), (403, "UNAUTHORIZED"),
     (429, "RATE_LIMITED"), (500, "UNAVAILABLE"), (503, "UNAVAILABLE")],
)  # fmt: skip
async def test_http_errors_are_unavailable(status: int, code: str) -> None:
    body = {"errMsg": "TypeError: .timeUnit -> should be equal to one of the allowed values",
            "body": "{\"echo\": \"요청 본문\"}"}  # fmt: skip
    connector, _ = _connector(lambda request: httpx.Response(status, json=body))
    with pytest.raises(ConnectorUnavailable) as caught:
        await connector.search_trend(_query())
    assert caught.value.code == code
    assert str(status) in str(caught.value)
    assert "요청 본문" not in str(caught.value)


async def test_network_error_is_unavailable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused", request=request)

    connector, _ = _connector(handler)
    with pytest.raises(ConnectorUnavailable) as caught:
        await connector.search_trend(_query())
    assert caught.value.code == "UNAVAILABLE"


async def test_failure_is_not_cached() -> None:
    answers = iter(
        [httpx.Response(500), httpx.Response(200, json=_naver([_series("고양이", [1, 2, 3])]))]
    )
    connector, seen = _connector(lambda request: next(answers))
    with pytest.raises(ConnectorUnavailable):
        await connector.search_trend(_query())
    result = await connector.search_trend(_query())
    assert result.by_group("고양이").points
    assert len(seen.requests) == 2


async def test_missing_keys_do_not_call() -> None:
    connector, seen = _connector(_ok(_naver([])), {})
    with pytest.raises(ConnectorUnavailable) as caught:
        await connector.search_trend(_query())
    assert caught.value.code == "UNAUTHORIZED"
    assert seen.requests == []


async def test_keys_never_appear_in_errors_or_repr() -> None:
    connector, _ = _connector(lambda request: httpx.Response(401, text=f"bad key {KEY_ID} {KEY}"))
    with pytest.raises(ConnectorUnavailable) as caught:
        await connector.search_trend(_query())
    for text in (str(caught.value), repr(connector)):
        assert KEY_ID not in text
        assert KEY not in text


# ---------------------------------------------------------------- 빈 응답


async def test_group_without_data_is_empty_not_zero() -> None:
    body = _naver(
        [_series("고양이", [1, 2, 3]), {"title": "캣북스", "keywords": ["캣북스"], "data": []}]
    )
    connector, _ = _connector(_ok(body))
    result = await connector.search_trend(_query("고양이", "캣북스"))
    assert result.by_group("캣북스").points == ()


async def test_missing_day_stays_missing() -> None:
    body = _naver([_series("날씨", [40.0, 41.0])])  # 11-01 빠짐 (실제 응답에서 본 모양)
    connector, _ = _connector(_ok(body))
    result = await connector.search_trend(_query("날씨"))
    assert [d for d, _ in result.by_group("날씨").points] == [
        date(2026, 10, 30),
        date(2026, 10, 31),
    ]


@pytest.mark.parametrize("response", [
    httpx.Response(200, content=b""),
    httpx.Response(200, json={}),
    httpx.Response(200, json={"results": []}),
])  # fmt: skip
async def test_empty_response_is_bad_response(response: httpx.Response) -> None:
    connector, _ = _connector(lambda request: response)
    with pytest.raises(ConnectorUnavailable) as caught:
        await connector.search_trend(_query())
    assert caught.value.code == "BAD_RESPONSE"


# ---------------------------------------------------------------- 형식 오류


@pytest.mark.parametrize("result", [
    {"title": "고양이", "data": [{"period": "2026-11-01", "ratio": "많음"}]},
    {"title": "고양이", "data": [{"period": "어제", "ratio": 1.0}]},
    {"title": "고양이", "data": [{"period": "2026-11-01", "ratio": -1}]},
    {"title": "고양이"},
    {"keywords": ["고양이"], "data": []},
    {"title": "다른 그룹", "data": []},
])  # fmt: skip
async def test_malformed_response_is_bad_response(result: dict) -> None:
    connector, _ = _connector(_ok(_naver([result])))
    with pytest.raises(ConnectorUnavailable) as caught:
        await connector.search_trend(_query())
    assert caught.value.code == "BAD_RESPONSE"


async def test_not_json_is_bad_response() -> None:
    connector, _ = _connector(lambda request: httpx.Response(200, text="<html>gateway</html>"))
    with pytest.raises(ConnectorUnavailable) as caught:
        await connector.search_trend(_query())
    assert caught.value.code == "BAD_RESPONSE"
