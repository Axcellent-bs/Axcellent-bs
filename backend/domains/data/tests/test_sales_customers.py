"""고객 내보내기 수집·저장 (11/16): 고객 표, 속성 덮어쓰기, genre_code 칸(예전 DB 파일 포함),
고객 커서 이어 받기, 판매 서버에 고객 내보내기가 없을 때(404), 품질 보고의 장르·속성 매핑."""

import asyncio
import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import httpx
import pytest

from ax_contracts.data_access.shop_customers import CustomerRow, ShopCustomersExportResult
from ax_contracts.data_access.shop_sales import SalesOrderLine, SalesOrderRow
from ax_contracts.schemas.data import AttributeDef, CatalogSales
from data_agent import sales_collect
from data_agent.connectors import ConnectorUnavailable
from data_agent.connectors.shop_api import ShopApi, ShopApiConnector
from data_agent.sales_collect import (
    NO_CUSTOMERS_EXPORT,
    MockSalesSource,
    collect_once,
    mock_rows,
)
from data_agent.sales_quality import quality, window
from data_agent.sales_store import SalesStore, customers_source

TENANT = "shop-demo"
NOW = datetime(2026, 11, 16, 3, 0, tzinfo=UTC)
T0 = datetime(2026, 11, 10, 1, 0, tzinfo=UTC)


def run(coro):
    return asyncio.run(coro)


def customer(buyer: str, minutes: int = 0, **attributes: str) -> CustomerRow:
    at = T0 + timedelta(minutes=minutes)
    return CustomerRow(buyer_id=buyer, joined_at=at, updated_at=at, attributes=attributes)


def order_with_genre(order_id: str, genre: str | None, at: datetime = T0) -> SalesOrderRow:
    return SalesOrderRow(
        order_id=order_id, buyer_id="buy_1", status="paid", created_at=at, updated_at=at,
        items_amount=15000, payment_status="succeeded",
        lines=[SalesOrderLine(product_id="p1", category_code="book", qty=1, unit_price=15000,
                              genre_code=genre)],
    )  # fmt: skip


# ---------------------------------------------------------------- 저장소


def test_customer_upsert_replaces_that_buyers_attributes() -> None:
    store = SalesStore(":memory:")
    store.upsert_customers(TENANT, [customer("b1", age_band="20s", gender="female")], NOW)
    store.upsert_customers(TENANT, [customer("b2", occupation="student")], NOW)
    # b1이 성별을 지우고 직업을 골랐다 → 속성 줄을 지우고 다시 넣는다
    store.upsert_customers(TENANT, [customer("b1", 5, age_band="20s", occupation="employee")], NOW)
    found = {c.buyer_id: c for c in store.customers(TENANT)}
    assert store.customer_count(TENANT) == 2
    assert found["b1"].attributes == {"age_band": "20s", "occupation": "employee"}
    assert found["b1"].updated_at == T0 + timedelta(minutes=5)
    assert found["b2"].attributes == {"occupation": "student"}
    assert store.customers("other") == []


def test_genre_code_is_stored_and_read_back() -> None:
    store = SalesStore(":memory:")
    store.upsert(TENANT, [order_with_genre("o1", "novel"), order_with_genre("o2", None)], NOW)
    lines = {o.order_id: o.lines[0] for o in store.orders_created(TENANT, T0, NOW)}
    assert lines["o1"].genre_code == "novel" and lines["o2"].genre_code is None


OLD_SCHEMA = """
CREATE TABLE sales_orders (tenant_id TEXT NOT NULL, order_id TEXT NOT NULL, buyer_id TEXT NOT NULL,
  status TEXT NOT NULL, payment_status TEXT, created_at TEXT NOT NULL, updated_at TEXT NOT NULL,
  items_amount INTEGER NOT NULL, shipping_fee INTEGER NOT NULL, points_used INTEGER NOT NULL,
  coupon_discount INTEGER NOT NULL, src_source TEXT, src_medium TEXT, src_campaign TEXT,
  synced_at TEXT NOT NULL, PRIMARY KEY (tenant_id, order_id));
CREATE TABLE sales_order_lines (tenant_id TEXT NOT NULL, order_id TEXT NOT NULL,
  line_no INTEGER NOT NULL, product_id TEXT NOT NULL, category_code TEXT, title TEXT,
  qty INTEGER NOT NULL, unit_price INTEGER NOT NULL, coupon_share INTEGER NOT NULL,
  points_share INTEGER NOT NULL, PRIMARY KEY (tenant_id, order_id, line_no));
INSERT INTO sales_orders VALUES ('shop-demo','old_1','buy_9','paid','succeeded',
  '2026-11-10T01:00:00Z','2026-11-10T01:00:00Z',9000,0,0,0,NULL,NULL,NULL,'2026-11-10T01:00:00Z');
INSERT INTO sales_order_lines VALUES ('shop-demo','old_1',0,'p_old','book','옛 책',1,9000,0,0);
"""


def test_existing_db_file_gets_genre_code_column(tmp_path: Path) -> None:
    path = tmp_path / "sales.db"
    conn = sqlite3.connect(path)
    conn.executescript(OLD_SCHEMA)
    conn.close()
    store = SalesStore(path)  # 열 때 genre_code 칸을 더한다
    store.upsert(TENANT, [order_with_genre("o_new", "essay")], NOW)
    lines = {o.order_id: o.lines[0] for o in store.orders_created(TENANT, T0, NOW)}
    assert lines["old_1"].genre_code is None and lines["old_1"].title == "옛 책"
    assert lines["o_new"].genre_code == "essay" and lines["o_new"].unit_price == 15000
    store.close()
    SalesStore(path).close()  # 두 번 열어도(칸이 이미 있어도) 괜찮다


def test_sync_state_without_source_ignores_customers_source() -> None:
    store = SalesStore(":memory:")
    store.finish_sync(TENANT, "shop-api", T0, None)
    store.finish_sync(TENANT, customers_source("shop-api"), NOW, None)  # 더 최근이어도
    state = store.sync_state(TENANT)
    assert state.source == "shop-api" and state.last_synced_at == T0


# ---------------------------------------------------------------- 수집


class WithCustomers(MockSalesSource):
    """판매 기록(목 표본) + 고객 내보내기(커서 `c:<위치>`)."""

    def __init__(self, customers: list[CustomerRow], fail: ConnectorUnavailable | None = None):
        super().__init__(mock_rows(4))
        self.customers = customers
        self.customer_calls: list[str | None] = []
        self.fail = fail

    async def customers_export(self, cursor=None, limit=500) -> ShopCustomersExportResult:
        self.customer_calls.append(cursor)
        if self.fail is not None:
            raise self.fail
        start = int(cursor.removeprefix("c:")) if cursor else 0
        page = self.customers[start : start + limit]
        end = start + len(page)
        return ShopCustomersExportResult(
            customers=page, next_cursor=f"c:{end}" if page else cursor,
            has_more=end < len(self.customers),
        )  # fmt: skip


def test_customers_follow_their_own_cursor_and_continue() -> None:
    store = SalesStore(":memory:")
    people = [customer(f"b{i}", i, age_band="20s") for i in range(5)]
    first = WithCustomers(people[:3])
    result = run(collect_once(first, store, now=NOW, customers_limit=2))
    assert result.errors == [] and result.notes == [] and result.customers_saved == 3
    assert first.customer_calls == [None, "c:2"]
    state = store.sync_state(TENANT, "shop-api:customers")
    assert state.cursor == "c:3" and state.last_synced_at == NOW and state.last_error is None
    assert store.sync_state(TENANT, "shop-api").cursor == "mock:4"  # 판매 커서와 따로
    second = WithCustomers(people)  # 새 고객 2명
    again = run(collect_once(second, store, now=NOW + timedelta(minutes=5), customers_limit=2))
    assert second.customer_calls == ["c:3"]  # 처음부터가 아니라 이어서
    assert store.customer_count(TENANT) == 5
    assert again.customers_saved == 2 and "고객 2명" in again.line()


def test_customers_export_missing_is_not_a_failure() -> None:
    store = SalesStore(":memory:")
    source = WithCustomers([], fail=ConnectorUnavailable("NOT_FOUND", "shop-api 응답 404"))
    result = run(collect_once(source, store, now=NOW))
    assert result.errors == [] and result.notes == [NO_CUSTOMERS_EXPORT]
    assert result.finished and result.saved == 4  # 판매 기록은 그대로 성공
    sales = store.sync_state(TENANT, "shop-api")
    assert sales.last_error is None and sales.last_synced_at == NOW
    customers = store.sync_state(TENANT, "shop-api:customers")
    assert customers.last_error == NO_CUSTOMERS_EXPORT and customers.last_synced_at is None
    assert NO_CUSTOMERS_EXPORT in result.line() and "실패" not in result.line()


def test_source_without_customers_export_is_noted() -> None:
    store = SalesStore(":memory:")
    result = run(collect_once(MockSalesSource(mock_rows(2)), store, now=NOW))
    assert result.errors == [] and result.notes == [NO_CUSTOMERS_EXPORT]
    assert store.sync_state(TENANT, "shop-api:customers").last_error == NO_CUSTOMERS_EXPORT


def test_other_customers_failure_is_an_error_but_sales_are_kept() -> None:
    store = SalesStore(":memory:")
    source = WithCustomers([], fail=ConnectorUnavailable("UNAVAILABLE", "shop-api 응답 없음"))
    result = run(collect_once(source, store, now=NOW))
    assert result.errors == ["고객 기록 [UNAVAILABLE]"] and result.saved == 4
    assert store.sync_state(TENANT, "shop-api").last_error is None
    assert store.sync_state(TENANT, "shop-api:customers").last_error == "고객 기록 [UNAVAILABLE]"


def _shop(customers_status: int, customers_body: dict) -> ShopApiConnector:
    sales = {"orders": [mock_rows(1)[0].model_dump(mode="json")], "next_cursor": "s1",
             "has_more": False}  # fmt: skip

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/shop/sales-export":
            return httpx.Response(200, json={"data": sales, "meta": {}})
        return httpx.Response(customers_status, json=customers_body)

    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    return ShopApiConnector(ShopApi("http://shop", token="mock:data", client=client))


def test_once_mode_exit_code_with_and_without_customers_export(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    env = {"DATA_SALES_DB": str(tmp_path / "sales.db")}
    monkeypatch.setattr(sales_collect, "get_source", lambda e: _shop(404, {"detail": "Not Found"}))
    assert run(sales_collect.run(None, env)) == 0  # 고객 내보내기 없음은 실패가 아니다
    store = SalesStore(tmp_path / "sales.db")
    assert store.sync_state(TENANT, "shop-api:customers").last_error == NO_CUSTOMERS_EXPORT
    assert store.sync_state(TENANT, "shop-api").last_error is None
    store.close()
    monkeypatch.setattr(sales_collect, "get_source", lambda e: _shop(503, {}))
    assert run(sales_collect.run(None, env)) == 1  # 다른 실패는 종료 코드 1


def test_connector_customers_export_request_and_404() -> None:
    seen: list[httpx.Request] = []
    body = {"customers": [customer("b1", age_band="30s").model_dump(mode="json")],
            "next_cursor": "c9", "has_more": True}  # fmt: skip

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        if request.url.params.get("cursor") == "gone":
            return httpx.Response(404, json={"detail": "Not Found"})
        if request.url.params.get("cursor") == "coded":
            return httpx.Response(404, json={"error": {"code": "SELLER_NOT_FOUND"}})
        return httpx.Response(200, json={"data": body, "meta": {}})

    async def go(cursor: str) -> ShopCustomersExportResult:
        async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as client:
            api = ShopApi("http://shop", token="mock:data", client=client)
            return await ShopApiConnector(api).customers_export(cursor, 200)

    result = run(go("c1"))
    assert result.customers[0].attributes == {"age_band": "30s"} and result.has_more
    assert seen[0].url.path == "/api/shop/customers-export"
    assert seen[0].url.params["cursor"] == "c1" and seen[0].url.params["limit"] == "200"
    with pytest.raises(ConnectorUnavailable) as missing:
        run(go("gone"))
    assert missing.value.code == "NOT_FOUND"
    with pytest.raises(ConnectorUnavailable) as coded:  # 서버 오류 코드가 있으면 그대로
        run(go("coded"))
    assert coded.value.code == "SELLER_NOT_FOUND"


@pytest.mark.parametrize("sales_source", [None, "", "shop-api"])
def test_real_mode_reads_customers_from_the_shop_server(
    monkeypatch: pytest.MonkeyPatch, sales_source: str | None
) -> None:
    """실제 모드(`AX_SHOP_API_URL` 있음, `DATA_SALES_SOURCE`가 fake 아님, 목 아님)면 수집기 출처가
    shop-api 커넥터이고 `GET /api/shop/customers-export`를 그 주소로 부른다 (네트워크 없음)."""
    from ax_agent_template.company_connection import CONNECTION_FILE_ENV
    from data_agent import catalog
    from data_agent.connectors import shop_api

    monkeypatch.delenv(CONNECTION_FILE_ENV, raising=False)  # 화면의 회사 연결 주소가 없을 때
    monkeypatch.setenv("DATA_TOKEN", "mock:data")  # 테스트용 가짜 토큰
    env = {"AX_SHOP_API_URL": "http://shop.test:8010"}
    if sales_source is not None:
        env["DATA_SALES_SOURCE"] = sales_source
    seen: list[httpx.Request] = []
    sales = {"orders": [mock_rows(1)[0].model_dump(mode="json")], "next_cursor": "s1",
             "has_more": False}  # fmt: skip
    people = {"customers": [customer("b1", age_band="20s").model_dump(mode="json")],
              "next_cursor": "c1", "has_more": False}  # fmt: skip

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        body = sales if request.url.path.endswith("/sales-export") else people
        return httpx.Response(200, json={"data": body, "meta": {}})

    catalog.use_catalog(catalog.load_catalog("catbooks"))
    shop_api.use_client(httpx.AsyncClient(transport=httpx.MockTransport(handler)))
    try:
        source = sales_collect.get_source(env)
        assert isinstance(source, ShopApiConnector)
        assert sales_collect.source_name(env) == "shop-api"
        store = SalesStore(":memory:")
        result = run(collect_once(source, store, now=NOW))
    finally:
        shop_api.use_client(None)
        catalog.use_catalog(None)
    assert [(r.url.host, r.url.port, r.url.path) for r in seen] == [
        ("shop.test", 8010, "/api/shop/sales-export"),
        ("shop.test", 8010, "/api/shop/customers-export"),
    ]
    assert all(r.headers["authorization"] == "Bearer mock:data" for r in seen)
    assert result.errors == [] and result.notes == [] and result.customers_saved == 1
    assert store.customers(TENANT)[0].attributes == {"age_band": "20s"}


# ---------------------------------------------------------------- 품질 보고

CFG = CatalogSales(
    categories={"book": "도서"},
    genres={"novel": "소설", "essay": "에세이"},
    attributes=[
        AttributeDef(key="age_band", label="연령대", values={"20s": "20대", "30s": "30대"}),
        AttributeDef(key="gender", label="성별", values={"female": "여성", "male": "남성"}),
    ],
)


def test_quality_reports_unknown_genres_and_unmapped_attribute_values() -> None:
    store = SalesStore(":memory:")
    at = NOW - timedelta(hours=1)
    rows = [order_with_genre("o1", "novel", at), order_with_genre("o2", "poetry", at),
            order_with_genre("o3", None, at), order_with_genre("o4", "cooking", at)]  # fmt: skip
    store.upsert(TENANT, rows, NOW)
    store.upsert_customers(TENANT, [
        customer("b1", age_band="20s", gender="female"),
        customer("b2", age_band="60s", gender="x"),
        customer("b3", hobby="cats"),
    ], NOW)  # fmt: skip
    w = window("today", NOW)
    report = quality(store, TENANT, w, store.orders_created(TENANT, w.start, w.end), CFG)
    assert report.unknown_genres == ["cooking", "poetry"]
    assert report.unmapped_attribute_values == {
        "age_band": ["60s"], "gender": ["x"], "hobby": ["cats"]
    }  # fmt: skip
    clean = CatalogSales(genres={"novel": "소설"})
    empty = SalesStore(":memory:")
    assert quality(empty, TENANT, w, [], clean).unmapped_attribute_values == {}


# ---------------------------------------------------------------- 캣북스 카탈로그 [sales]


def test_catbooks_catalog_customer_market_settings_validate() -> None:
    from data_agent.catalog import load_catalog

    sales = load_catalog("catbooks").sales
    assert sales is not None  # CatalogSales 교차 확인(없는 장르·속성·값을 가리키지 않음)을 통과
    assert list(sales.genres) == [
        "novel", "essay", "business", "self_help", "humanities", "society", "history",
        "science", "it", "language", "kids", "comics", "hobby", "writing", "notebook", "deco",
    ]  # fmt: skip
    assert set(sales.genre_keywords) == set(sales.genres)
    attrs = {a.key: list(a.values) for a in sales.attributes}
    assert attrs == {
        "age_band": ["10s", "20s", "30s", "40s", "50s_plus"],
        "gender": ["female", "male", "no_answer"],
        "occupation": ["student", "employee", "self_employed", "other", "no_answer"],
    }
    assert sales.min_group_size == 5 and sales.active_days == 365
    assert 3 <= len(sales.quick_questions) <= 4
    assert {"bundle_discount", "genre_coupon"} <= {e.code for e in sales.event_kinds}
    assert all(f.source_id == "data4library" for f in sales.external_filters)
    # 정보나루 매뉴얼 v20260620으로 확인한 조건만 (연령 10~11쪽, 성별 코드 58쪽, KDC 대주제 59쪽)
    by_value = {(f.attribute_key, f.value_code): f.params for f in sales.external_filters}
    assert by_value == {
        ("age_band", "10s"): {"from_age": 10, "to_age": 19},
        ("age_band", "20s"): {"from_age": 20, "to_age": 29},
        ("age_band", "30s"): {"from_age": 30, "to_age": 39},
        ("age_band", "40s"): {"from_age": 40, "to_age": 49},
        ("age_band", "50s_plus"): {"from_age": 50},  # to_age는 100세로 자동 설정
        ("gender", "male"): {"gender": "0"},
        ("gender", "female"): {"gender": "1"},
    }  # no_answer(응답 안 함)는 2 = 미상과 뜻이 달라 넣지 않는다
    assert {g.genre_code: g.params for g in sales.genre_external} == {
        "novel": {"kdc": "8"}, "essay": {"kdc": "8"}, "business": {"kdc": "3"},
        "society": {"kdc": "3"}, "history": {"kdc": "9"}, "science": {"kdc": "4"},
        "language": {"kdc": "7"},
    }  # fmt: skip
    ref = sales.population[0].table_ref  # KOSIS 행정구역(읍면동)별/5세별 주민등록인구, 전국
    assert (ref["org_id"], ref["tbl_id"], ref["obj_l1"], ref["prd_se"]) == (
        "101", "DT_1B04005N", "00", "M")
    assert ref["dims"] == {"age_band": "c2", "gender": "itm"}
    assert ref["codes"]["gender"] == {"female": "T4", "male": "T3", "no_answer": []}
    assert ref["codes"]["age_band"]["20s"] == ["25", "30"]
    assert [p.attribute_keys for p in sales.population] == [["age_band", "gender"]]
