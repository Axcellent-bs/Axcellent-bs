"""판매 기록 수집: 커서 이어 받기, 같은 주문 두 번(upsert), 목 모드, 실패, 잠금, 커넥터."""

import asyncio
from datetime import UTC, datetime, timedelta

import httpx
import pytest

from ax_contracts.data_access.shop_sales import SalesOrderRow, ShopSalesExportResult
from data_agent import sales_collect
from data_agent.connectors import ConnectorUnavailable
from data_agent.connectors.shop_api import ShopApi, ShopApiConnector
from data_agent.sales_collect import MockSalesSource, collect_once, get_source, mock_rows
from data_agent.sales_store import SalesStore

NOW = datetime(2026, 11, 16, 3, 0, tzinfo=UTC)
TENANT = "shop-demo"


def run(coro):
    return asyncio.run(coro)


def test_collect_follows_cursor_to_the_end() -> None:
    store = SalesStore(":memory:")
    source = MockSalesSource(mock_rows(12))
    result = run(collect_once(source, store, limit=5, now=NOW))
    assert result.finished and result.pages == 3 and result.saved == 12
    assert source.calls == [None, "mock:5", "mock:10"]
    assert store.order_count(TENANT) == 12
    state = store.sync_state(TENANT, "shop-api")
    assert state.cursor == "mock:12" and state.last_synced_at == NOW and state.last_error is None


def test_next_run_continues_from_saved_cursor() -> None:
    store = SalesStore(":memory:")
    rows = mock_rows(6)
    run(collect_once(MockSalesSource(rows[:4]), store, limit=10, now=NOW))
    source = MockSalesSource(rows)  # 새 주문 2건이 뒤에 생김
    run(collect_once(source, store, limit=10, now=NOW + timedelta(minutes=5)))
    assert source.calls == ["mock:4"]  # 처음부터가 아니라 이어서
    assert store.order_count(TENANT) == 6


def test_same_order_received_twice_is_overwritten() -> None:
    store = SalesStore(":memory:")
    first = mock_rows(3)
    run(collect_once(MockSalesSource(first), store, now=NOW))
    changed = first[0].model_copy(
        update={"status": "cancelled", "updated_at": first[-1].updated_at + timedelta(hours=1)}
    )
    # 판매 서버가 바뀐 주문을 커서 뒤에 다시 준다
    source = MockSalesSource([*first, changed])
    source.rows = [*first, changed]
    run(collect_once(source, store, now=NOW))
    assert store.order_count(TENANT) == 3
    orders = {o.order_id: o for o in store.orders_created(TENANT, NOW - timedelta(days=60), NOW)}
    assert orders[first[0].order_id].status == "cancelled"


def test_max_pages_stops_and_resumes_without_marking_synced() -> None:
    store = SalesStore(":memory:")
    result = run(collect_once(MockSalesSource(mock_rows(10)), store, limit=2, max_pages=2, now=NOW))
    assert not result.finished and result.saved == 4
    state = store.sync_state(TENANT, "shop-api")
    assert state.last_synced_at is None and state.cursor == "mock:4"
    run(collect_once(MockSalesSource(mock_rows(10)), store, limit=20, now=NOW))
    assert store.sync_state(TENANT, "shop-api").last_synced_at == NOW


class Broken:
    def __init__(self, after: int = 0) -> None:
        self.inner = MockSalesSource(mock_rows(6))
        self.after = after
        self.n = 0

    async def sales_export(self, cursor=None, limit=500) -> ShopSalesExportResult:
        self.n += 1
        if self.n > self.after:
            raise ConnectorUnavailable("UNAVAILABLE", "shop-api 응답 없음")
        return await self.inner.sales_export(cursor, limit)


def test_failure_keeps_saved_pages_and_last_sync() -> None:
    store = SalesStore(":memory:")
    run(collect_once(MockSalesSource(mock_rows(2)), store, now=NOW))
    result = run(collect_once(Broken(after=1), store, limit=3, now=NOW + timedelta(minutes=5)))
    assert result.errors == ["판매 기록 [UNAVAILABLE]"] and result.saved == 3
    state = store.sync_state(TENANT, "shop-api")
    assert state.last_synced_at == NOW  # 실패한 수집은 마지막 수집 시각을 바꾸지 않는다
    assert state.last_error == "판매 기록 [UNAVAILABLE]"


def test_mock_mode_source(monkeypatch: pytest.MonkeyPatch) -> None:
    assert isinstance(get_source({"DATA_MOCK": "1"}), MockSalesSource)
    assert isinstance(get_source({"AGENT_TEMPLATE_MOCK": "1"}), MockSalesSource)
    real = get_source({"AX_SHOP_API_URL": "http://127.0.0.1:9"})
    assert isinstance(real, ShopApiConnector)
    store = SalesStore(":memory:")
    run(collect_once(get_source({"DATA_MOCK": "1"}), store, now=NOW))
    assert store.order_count(TENANT) == len(mock_rows())


def test_mock_rows_are_valid_contract_rows() -> None:
    rows = mock_rows()
    assert all(isinstance(r, SalesOrderRow) for r in rows)
    assert any(r.refunds for r in rows) and any(r.status == "cancelled" for r in rows)
    assert any(r.coupon_discount for r in rows) and any(r.points_used for r in rows)


def test_every_seconds_bounds() -> None:
    assert sales_collect.every_seconds({}, None) == 300
    assert sales_collect.every_seconds({"DATA_SALES_SYNC_SECONDS": "120"}, None) == 120
    assert sales_collect.every_seconds({"DATA_SALES_SYNC_SECONDS": "5"}, None) == 60
    assert sales_collect.every_seconds({"DATA_SALES_SYNC_SECONDS": "x"}, None) == 300
    assert sales_collect.every_seconds({}, 900) == 900


def test_lock_path_next_to_db(tmp_path) -> None:
    env = {"DATA_SALES_DB": str(tmp_path / "s.db")}
    assert sales_collect.lock_path(env) == tmp_path / "sales_collect.lock"


def test_second_collector_exits_when_lock_held(tmp_path, capsys) -> None:
    from data_agent import collector_lock

    env_db = tmp_path / "s.db"
    lock = collector_lock.acquire(tmp_path / "sales_collect.lock", "test")
    assert lock is not None
    try:
        with pytest.MonkeyPatch.context() as mp:
            mp.setenv("DATA_SALES_DB", str(env_db))
            mp.setenv("DATA_MOCK", "1")
            assert sales_collect.main([]) == 0
        assert "이미 다른 수집기가 돌고 있음" in capsys.readouterr().out
    finally:
        lock.close()


def test_connector_sales_export_calls_shop_api_with_cursor() -> None:
    seen: list[httpx.Request] = []
    body = {"orders": [mock_rows(1)[0].model_dump(mode="json")], "next_cursor": "c2",
            "has_more": False}  # fmt: skip

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={"data": body, "meta": {}})

    async def go() -> ShopSalesExportResult:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            api = ShopApi("http://shop", token="mock:data", client=client)
            return await ShopApiConnector(api).sales_export("c1", 100)

    result = run(go())
    assert result.next_cursor == "c2" and len(result.orders) == 1
    assert seen[0].url.path == "/api/shop/sales-export"
    assert seen[0].url.params["cursor"] == "c1" and seen[0].url.params["limit"] == "100"
    assert seen[0].headers["authorization"] == "Bearer mock:data"


def test_connector_bad_shape_is_unavailable() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"data": {"orders": [{"order_id": 1}]}})

    async def go() -> None:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            api = ShopApi("http://shop", token="t", client=client)
            await ShopApiConnector(api).sales_export()

    with pytest.raises(ConnectorUnavailable):
        run(go())
