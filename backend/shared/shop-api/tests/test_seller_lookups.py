"""판매자 정보·가격 조회 (data fraud_score가 읽는다). 기존 테이블만 읽고 값을 지어내지 않는다."""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from ax_agent_template import events
from ax_contracts.data_access.shop_seller_prices import ShopSellerPricesQuery
from ax_contracts.data_access.shop_seller_profile import ShopSellerProfileQuery
from ax_shop_api.db import Db
from ax_shop_api.lookups import Lookups, median_price
from ax_shop_api.main import create_app

DATA = {"Authorization": "Bearer mock:data"}
ADMIN = {"Authorization": "Bearer mock:admin"}
FINANCE = {"Authorization": "Bearer mock:finance"}
NOW = datetime(2026, 10, 29, 12, 0, tzinfo=UTC)


def _iso(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


@pytest.fixture(autouse=True)
def _mock_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_TEMPLATE_MOCK", "1")
    monkeypatch.setenv("SHOP_MONITOR_INTERVAL_S", "0")
    events.reset_mock_bus()


@pytest.fixture
def db() -> Db:
    return Db(":memory:")


@pytest.fixture
def client(db: Db) -> TestClient:
    return TestClient(create_app(db))


def _seller(db: Db, seller_id: str, joined: datetime) -> None:
    with db.tx() as conn:
        conn.execute(
            "INSERT INTO sellers VALUES (?, ?, ?, ?, NULL, ?)",
            (seller_id, seller_id, "book", "shop-demo", _iso(joined)),
        )


def _product(
    db: Db,
    product_id: str,
    seller_id: str,
    price: int,
    created: datetime,
    *,
    category: str = "book",
    active: bool = True,
) -> None:
    with db.tx() as conn:
        conn.execute(
            "INSERT INTO products VALUES (?, ?, ?, ?, ?, 1, ?, ?, NULL, ?, ?)",
            (
                product_id,
                seller_id,
                product_id,
                category,
                price,
                int(active),
                "shop-demo",
                _iso(created),
                _iso(created),
            ),
        )


@pytest.mark.parametrize("path", ["profile", "prices"])
def test_seller_lookups_need_a_data_or_admin_token(client: TestClient, path: str) -> None:
    url = f"/api/shop/sellers/sel_0001/{path}"
    assert client.get(url).status_code == 401
    assert client.get(url, headers=FINANCE).status_code == 403
    assert client.get(url, headers=DATA).status_code == 200
    assert client.get(url, headers=ADMIN).status_code == 200


@pytest.mark.parametrize("path", ["profile", "prices"])
def test_unknown_seller_is_a_404(client: TestClient, path: str) -> None:
    response = client.get(f"/api/shop/sellers/sel_9999/{path}", headers=DATA)
    assert response.status_code == 404
    assert response.json()["error"]["code"] == "SELLER_NOT_FOUND"


def test_profile_of_a_seed_seller(client: TestClient) -> None:
    body = client.get("/api/shop/sellers/sel_0001/profile", headers=DATA).json()["data"]
    assert body["seller_id"] == "sel_0001"
    assert body["product_count"] == 3  # 시드: 책마루 상품 3개
    assert body["joined_at"].endswith("Z")
    # 사업자 해시는 테이블에 없어서 지어내지 않고 null이다
    assert body["business_hash"] is None
    assert body["business_hash_duplicated"] is None


def test_listings_last_24h_boundary() -> None:
    db = Db(":memory:")
    _seller(db, "sel_a", NOW - timedelta(days=30))
    _product(db, "prd_exact", "sel_a", 1000, NOW - timedelta(hours=24))  # 정확히 24시간 전: 센다
    _product(db, "prd_inside", "sel_a", 1000, NOW - timedelta(hours=23, minutes=59, seconds=59))
    _product(db, "prd_old", "sel_a", 1000, NOW - timedelta(hours=24, seconds=1))  # 안 센다
    _product(db, "prd_off", "sel_a", 1000, NOW - timedelta(hours=1), active=False)  # 등록은 센다
    _product(db, "prd_future", "sel_a", 1000, NOW + timedelta(seconds=1))  # 지금 뒤: 안 센다
    profile = Lookups(db, now=lambda: NOW).seller_profile(ShopSellerProfileQuery(seller_id="sel_a"))
    assert profile.listings_last_24h == 3
    assert profile.product_count == 4  # 판매 중 상품만 (prd_off 제외)
    assert profile.joined_at == NOW - timedelta(days=30)


def test_prices_use_the_category_median_of_active_products() -> None:
    db = Db(":memory:")
    _seller(db, "sel_a", NOW - timedelta(days=30))
    _seller(db, "sel_b", NOW - timedelta(days=30))
    old = NOW - timedelta(days=10)
    _product(db, "prd_a1", "sel_a", 4000, old)
    _product(db, "prd_a2", "sel_a", 9000, old, category="pen")  # 카테고리에 상품 1개
    _product(db, "prd_a3", "sel_a", 1, old, active=False)  # 판매 중지: 목록·중앙값 모두 제외
    for i, price in enumerate([10000, 20000, 30000]):
        _product(db, f"prd_b{i}", "sel_b", price, old)
    _product(db, "prd_b9", "sel_b", 50000, old, active=False)
    result = Lookups(db, now=lambda: NOW).seller_prices(ShopSellerPricesQuery(seller_id="sel_a"))
    items = {i.product_id: i for i in result.items}
    assert set(items) == {"prd_a1", "prd_a2"}
    # book 판매 중 가격: 4000, 10000, 20000, 30000 → (10000 + 20000) / 2
    assert items["prd_a1"].category_median_price == 15000
    assert items["prd_a1"].category == "book"
    # 카테고리에 상품이 하나뿐이면 그 가격이 중앙값이다
    assert items["prd_a2"].category_median_price == 9000


def test_seller_without_active_products_has_no_items(client: TestClient, db: Db) -> None:
    _seller(db, "sel_empty", NOW - timedelta(days=1))
    body = client.get("/api/shop/sellers/sel_empty/prices", headers=DATA).json()["data"]
    assert body == {"items": []}


def test_median_price_rounds_half_up() -> None:
    assert median_price([5]) == 5
    assert median_price([3, 1, 2]) == 2
    assert median_price([1, 2]) == 2  # 1.5 → 2
    assert median_price([1000, 2001]) == 1501  # 1500.5 → 1501
