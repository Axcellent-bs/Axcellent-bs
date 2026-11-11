"""판매 서버 HTTP 클라이언트 재사용과 지표 동시 읽기 (#909).

판정 한 번이 판매 서버를 약 40번 부른다. 요청마다 클라이언트를 새로 만들면 그 비용만 한 번에
약 0.3초라 판정이 20초 가까이 걸렸다. 프로세스에 클라이언트 하나를 두고, 지표 시계열은 같이 읽는다.
"""

import asyncio
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from ax_contracts.data_access.shop_metric_series import ShopMetricSeriesResult
from data_agent import detect
from data_agent.connectors import ConnectorUnavailable, shop_api
from data_agent.connectors.shop_api import ShopApi, ShopApiConnector, close_shared_client
from data_agent.metrics.judge import Settings

pytestmark = pytest.mark.asyncio

W_FROM = datetime(2026, 10, 21, 5, 0, tzinfo=UTC)
W_TO = datetime(2026, 10, 21, 5, 30, tzinfo=UTC)
METRICS = ["payment_fail_rate", "order_fail_count"]  # 인프라 복구 확인이 묻는 지표


def _series(request: httpx.Request) -> httpx.Response:
    result = ShopMetricSeriesResult(
        metric=request.url.params["metric"],
        bucket="5m",
        points=[],
        truncated=False,
        collected_at=datetime.now(UTC),
    )
    return httpx.Response(200, json={"data": result.model_dump(mode="json"), "meta": {}})


@pytest.fixture
def created(monkeypatch: pytest.MonkeyPatch) -> list[httpx.AsyncClient]:
    """공용 클라이언트를 만들 때마다 적는다(네트워크 없이 MockTransport)."""
    made: list[httpx.AsyncClient] = []

    def new_client() -> httpx.AsyncClient:
        client = httpx.AsyncClient(transport=httpx.MockTransport(_series))
        made.append(client)
        return client

    monkeypatch.setattr(shop_api, "_new_client", new_client)
    monkeypatch.setattr(shop_api, "_shared", None)
    shop_api.use_client(None)
    return made


async def test_many_requests_share_one_client(created: list[httpx.AsyncClient]) -> None:
    connector = ShopApiConnector(ShopApi("http://shop.test", token="mock:data"))
    for metric in ("order_count", "payment_count"):
        # 사흘 = 하루씩 3번
        await connector.metric_series(metric, W_FROM - timedelta(days=3), W_FROM)
    await connector.metric_series("order_count", W_FROM, W_TO)

    assert len(created) == 1
    assert not created[0].is_closed

    await close_shared_client()
    assert created[0].is_closed
    await connector.metric_series("order_count", W_FROM, W_TO)  # 닫은 뒤에는 새로 만든다
    assert len(created) == 2
    await close_shared_client()


async def test_a_new_event_loop_gets_its_own_client(created: list[httpx.AsyncClient]) -> None:
    """다른 이벤트 루프에서 만든 클라이언트는 쓰지 않는다(루프에 묶여 있다)."""
    connector = ShopApiConnector(ShopApi("http://shop.test", token="mock:data"))
    await connector.metric_series("order_count", W_FROM, W_TO)

    def other_loop() -> None:
        asyncio.run(connector.metric_series("order_count", W_FROM, W_TO))

    await asyncio.to_thread(other_loop)
    assert len(created) == 2
    await connector.metric_series("order_count", W_FROM, W_TO)  # 원래 루프로 돌아오면 또 새로
    assert len(created) == 3
    await close_shared_client()


class _SlowSource:
    """지표를 읽는 동안 동시에 몇 개가 읽히는지 센다 (예전 공급자 `fetch`)."""

    def __init__(self, fail: str | None = None) -> None:
        self.fail = fail
        self.running = 0
        self.peak = 0
        self.read: list[str] = []
        self.cancelled: list[str] = []

    async def fetch(self, metric: str, window_from: datetime, window_to: datetime) -> list:
        self.running += 1
        self.peak = max(self.peak, self.running)
        try:
            if metric == self.fail:
                raise ConnectorUnavailable("UNAVAILABLE", "down")
            await asyncio.sleep(0.05)
            self.read.append(metric)
            return []
        except asyncio.CancelledError:
            self.cancelled.append(metric)
            raise
        finally:
            self.running -= 1


async def test_judge_reads_metric_series_at_the_same_time() -> None:
    source = _SlowSource()
    judged = await detect.judge_window(source, W_FROM, W_TO, METRICS, Settings())

    assert len(judged.queried) == 4  # 지표 2개 + 주문 수 + 결제 수(분모)
    assert sorted(source.read) == sorted(judged.queried)
    assert source.peak == 4


async def test_judge_reads_no_more_than_the_limit_at_once(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(detect, "SERIES_CONCURRENCY", 2)
    source = _SlowSource()
    await detect.judge_window(source, W_FROM, W_TO, METRICS, Settings())

    assert source.peak == 2
    assert len(source.read) == 4


async def test_one_failed_read_stops_the_rest_and_raises_the_same_error() -> None:
    """차례로 읽을 때처럼 `ConnectorUnavailable`이 그대로 나온다(묶음 오류로 바뀌지 않는다)."""
    source = _SlowSource(fail="payment_fail_rate")
    with pytest.raises(ConnectorUnavailable):
        await detect.judge_window(source, W_FROM, W_TO, METRICS, Settings())

    assert source.running == 0
    assert source.read == []  # 나머지는 끝나기 전에 멈췄다
    assert source.cancelled
