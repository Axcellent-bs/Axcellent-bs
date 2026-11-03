"""지표 시계열의 주문 금액·쿠폰 지표: order_amount, coupon_order_count, coupon_discount_amount."""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient

from ax_agent_template import events
from ax_shop_api.db import Db
from ax_shop_api.main import create_app

DATA = {"Authorization": "Bearer mock:data"}
MARKETING = {"Authorization": "Bearer mock:marketing"}
SUPPORT = {"Authorization": "Bearer mock:support"}
BUYER = "buy_0001"
T0 = datetime(2026, 10, 22, 6, 0, tzinfo=UTC)  # 5분 경계


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


def _coupon(client: TestClient) -> str:
    body = {
        "idempotency_key": "c-1",
        "purpose_code": "campaign",
        "campaign_id": "cmp_0001",
        "buyer_id": BUYER,
        "count": 6,
        "unit_value": 2000,
    }
    response = client.post("/api/shop/coupons", json=body, headers=MARKETING)
    assert response.status_code == 201, response.text
    return response.json()["data"]["coupon_id"]


def _order(
    client: TestClient, db: Db, at: datetime, coupon_id: str | None = None, units: int = 1
) -> dict:
    """18,000원 상품 한 개 주문(배송비 3,000원)을 만들고 접수 시각을 `at`으로 맞춘다."""
    body: dict = {"buyer_id": BUYER, "items": [{"product_id": "prd_0001", "qty": 1}]}
    if coupon_id:
        body |= {"coupon_id": coupon_id, "coupon_units": units}
    order = client.post("/api/shop/orders", json=body).json()["data"]
    with db.tx() as conn:
        conn.execute(
            "UPDATE orders SET created_at = ? WHERE order_id = ?", (_iso(at), order["order_id"])
        )
    return order


def _pay(client: TestClient, order: dict) -> dict:
    paid = client.post(f"/api/shop/orders/{order['order_id']}/payments", json={"simulate": None})
    assert paid.status_code == 200, paid.text
    return paid.json()["data"]


def _series(client: TestClient, metric: str) -> list[tuple[str, float]]:
    params = {
        "metric": metric,
        "from": _iso(T0),
        "to": _iso(T0 + timedelta(minutes=20)),
    }
    response = client.get("/api/shop/metric-series", params=params, headers=DATA)
    assert response.status_code == 200, response.text
    return [(p["ts"][11:16], p["value"]) for p in response.json()["data"]["points"]]


def _fill(client: TestClient, db: Db) -> None:
    coupon = _coupon(client)
    _pay(client, _order(client, db, T0 + timedelta(minutes=1), coupon, 2))  # 17,000원, 할인 4,000
    _pay(client, _order(client, db, T0 + timedelta(minutes=2)))  # 21,000원
    cancelled = _order(client, db, T0 + timedelta(minutes=3), coupon, 1)
    cancel = client.post(f"/api/shop/orders/{cancelled['order_id']}/cancel", json={})
    assert cancel.status_code == 200
    _order(client, db, T0 + timedelta(minutes=4))  # 결제 전 (pending_payment)
    refunded = _pay(client, _order(client, db, T0 + timedelta(minutes=12), coupon, 1))  # 19,000원
    full = client.post(
        f"/api/shop/payments/{refunded['payments'][0]['payment_id']}/refund",
        json={"refund_amount": 19000, "idempotency_key": "r-1"},
        headers=SUPPORT,
    )
    assert full.status_code == 201, full.text


def test_order_amount_sums_paid_orders_per_bucket(client: TestClient, db: Db) -> None:
    _fill(client, db)
    # 결제 전·취소 주문은 빼고, 환불은 빼지 않는다(06:10 버킷의 전액 환불 주문도 센다)
    assert _series(client, "order_amount") == [
        ("06:00", 38000.0), ("06:05", 0.0), ("06:10", 19000.0), ("06:15", 0.0),
    ]  # fmt: skip


def test_coupon_metrics_count_orders_that_still_hold_their_units(
    client: TestClient, db: Db
) -> None:
    _fill(client, db)
    # 취소된 주문(06:03)과 전액 환불된 주문(06:12)은 장수를 돌려받으므로 세지 않는다
    assert _series(client, "coupon_order_count") == [
        ("06:00", 1.0), ("06:05", 0.0), ("06:10", 0.0), ("06:15", 0.0),
    ]  # fmt: skip
    assert _series(client, "coupon_discount_amount") == [
        ("06:00", 4000.0), ("06:05", 0.0), ("06:10", 0.0), ("06:15", 0.0),
    ]  # fmt: skip


def test_a_pending_coupon_order_still_counts_because_it_holds_its_units(
    client: TestClient, db: Db
) -> None:
    _order(client, db, T0 + timedelta(minutes=1), _coupon(client), 2)  # 결제 전
    assert _series(client, "coupon_order_count")[0] == ("06:00", 1.0)
    assert _series(client, "coupon_discount_amount")[0] == ("06:00", 4000.0)
    assert _series(client, "order_amount")[0] == ("06:00", 0.0)  # 아직 매출이 아니다


def test_a_partial_refund_keeps_the_coupon_order(client: TestClient, db: Db) -> None:
    paid = _pay(client, _order(client, db, T0 + timedelta(minutes=1), _coupon(client), 1))
    client.post(
        f"/api/shop/payments/{paid['payments'][0]['payment_id']}/refund",
        json={"refund_amount": 5000, "idempotency_key": "r-1"},
        headers=SUPPORT,
    )
    assert _series(client, "coupon_order_count")[0] == ("06:00", 1.0)
    assert _series(client, "coupon_discount_amount")[0] == ("06:00", 2000.0)


def test_empty_range_gives_zero_filled_buckets(client: TestClient) -> None:
    for metric in ("order_amount", "coupon_order_count", "coupon_discount_amount"):
        assert [value for _, value in _series(client, metric)] == [0.0, 0.0, 0.0, 0.0]


def test_the_coupons_response_fits_the_data_access_contract(client: TestClient) -> None:
    from ax_contracts.data_access.shop_coupons import ShopCoupon

    _coupon(client)
    got = client.get("/api/shop/coupons", params={"campaign_id": "cmp_0001"}, headers=DATA)
    assert got.status_code == 200, got.text
    coupons = [ShopCoupon.model_validate(item) for item in got.json()["data"]]
    assert [c.campaign_id for c in coupons] == ["cmp_0001"]
    assert (coupons[0].count, coupons[0].remaining_units, coupons[0].expired) == (6, 6, False)
