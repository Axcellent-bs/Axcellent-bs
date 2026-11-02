"""쿠폰 사용: 주문에 본인 쿠폰 한 건과 장수를 쓰고, 남은 장수는 주문에서 계산한다."""

import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ax_agent_template import events
from ax_shop_api import service
from ax_shop_api.db import Db
from ax_shop_api.main import create_app

MARKETING = {"Authorization": "Bearer mock:marketing"}
SUPPORT = {"Authorization": "Bearer mock:support"}
DATA = {"Authorization": "Bearer mock:data"}
BUYER = "buy_0001"


@pytest.fixture(autouse=True)
def _mock_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_TEMPLATE_MOCK", "1")
    events.reset_mock_bus()


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app(Db(":memory:")))


def _coupon(
    client: TestClient,
    key: str = "c-1",
    buyer: str = BUYER,
    count: int = 3,
    unit_value: int = 2000,
    purpose: str = "campaign",
    **extra: object,
) -> dict:
    body: dict = {
        "idempotency_key": key,
        "purpose_code": purpose,
        "buyer_id": buyer,
        "count": count,
        "unit_value": unit_value,
        **({"campaign_id": "cmp_0001"} if purpose == "campaign" else {}),
        **extra,
    }
    response = client.post("/api/shop/coupons", json=body, headers=MARKETING)
    assert response.status_code in (200, 201), response.text
    return response.json()["data"]


def _order(client: TestClient, coupon_id: str | None = None, units: int | None = None, **body):
    payload: dict = {"buyer_id": BUYER, "items": [{"product_id": "prd_0001", "qty": 1}]}  # 18,000원
    if coupon_id is not None:
        payload["coupon_id"] = coupon_id
    if units is not None:
        payload["coupon_units"] = units
    payload.update(body)
    return client.post("/api/shop/orders", json=payload)


def _remaining(client: TestClient, coupon_id: str) -> int:
    got = client.get(f"/api/shop/coupons/{coupon_id}", headers=MARKETING).json()["data"]
    return got["remaining_units"]


def _error(response) -> str:  # noqa: ANN001
    return response.json()["error"]["code"]


# --- 할인과 분배 -------------------------------------------------------------


def test_an_order_without_a_coupon_is_unchanged(client: TestClient) -> None:
    order = _order(client).json()["data"]
    assert (order["coupon_id"], order["coupon_units"], order["coupon_discount"]) == (None, 0, 0)
    assert order["total_amount"] == 18000 + 3000
    assert order["items"][0]["coupon_share"] == 0


def test_the_coupon_discount_is_applied_and_split_over_the_items(client: TestClient) -> None:
    coupon = _coupon(client)
    body = {
        "buyer_id": BUYER,
        "items": [{"product_id": "prd_0001", "qty": 1}, {"product_id": "prd_0002", "qty": 2}],
        "coupon_id": coupon["coupon_id"],
        "coupon_units": 2,
    }  # 18,000 + 48,000 = 66,000원, 배송비 0원
    order = client.post("/api/shop/orders", json=body).json()["data"]
    assert (order["coupon_id"], order["coupon_units"]) == (coupon["coupon_id"], 2)
    assert order["coupon_discount"] == 4000
    assert order["total_amount"] == 66000 - 4000
    shares = [item["coupon_share"] for item in order["items"]]
    assert sum(shares) == 4000
    paid = sum(item["paid_amount"] for item in order["items"])
    assert order["total_amount"] == paid + order["shipping_fee"]  # 항상 Σ paid + 배송비


def test_units_default_to_one(client: TestClient) -> None:
    coupon = _coupon(client)
    order = _order(client, coupon["coupon_id"]).json()["data"]
    assert (order["coupon_units"], order["coupon_discount"]) == (1, 2000)
    assert _remaining(client, coupon["coupon_id"]) == 2


def test_the_discount_never_exceeds_the_items_and_shipping_is_not_discounted(
    client: TestClient,
) -> None:
    coupon = _coupon(client, count=1, unit_value=50000)
    order = _order(client, coupon["coupon_id"]).json()["data"]
    assert order["coupon_discount"] == 18000  # 상품 합계까지만
    assert order["total_amount"] == 3000  # 배송비는 그대로 낸다
    assert order["items"][0]["paid_amount"] == 0


def test_a_compensation_coupon_can_be_used_too(client: TestClient) -> None:
    coupon = _coupon(client, purpose="compensation", count=1, unit_value=500)
    assert _order(client, coupon["coupon_id"]).json()["data"]["coupon_discount"] == 500


def test_points_and_coupon_together_stay_within_the_items(client: TestClient) -> None:
    coupon = _coupon(client, count=1, unit_value=4000)
    items = [{"product_id": "prd_0001", "qty": 1}, {"product_id": "prd_0002", "qty": 1}]
    base = {"buyer_id": BUYER, "items": items, "coupon_id": coupon["coupon_id"]}  # 42,000원
    too_much = client.post("/api/shop/orders", json={**base, "points_used": 38001})
    assert too_much.status_code == 400 and _error(too_much) == "POINTS_EXCEED_ITEMS"
    assert _remaining(client, coupon["coupon_id"]) == 1  # 거절된 주문은 쿠폰을 쓰지 않는다
    ok = client.post("/api/shop/orders", json={**base, "points_used": 38000}).json()["data"]
    assert ok["coupon_discount"] == 4000 and ok["points_used"] == 38000
    assert all(item["paid_amount"] >= 0 for item in ok["items"])
    assert ok["total_amount"] == sum(i["paid_amount"] for i in ok["items"]) + ok["shipping_fee"]


# --- 거절 ----------------------------------------------------------------------


def test_unknown_coupon_and_other_buyers_coupon_are_rejected(client: TestClient) -> None:
    stock = client.get("/api/shop/products/prd_0001").json()["data"]["stock"]
    missing = _order(client, "cpn_9999")
    assert missing.status_code == 404 and _error(missing) == "COUPON_NOT_FOUND"
    theirs = _coupon(client, key="c-2", buyer="buy_0002")
    stolen = _order(client, theirs["coupon_id"])
    assert stolen.status_code == 403 and _error(stolen) == "COUPON_NOT_YOURS"
    assert client.get("/api/shop/products/prd_0001").json()["data"]["stock"] == stock


def test_more_units_than_left_are_rejected_across_orders(client: TestClient) -> None:
    coupon = _coupon(client, count=3)
    assert _order(client, coupon["coupon_id"], 2).status_code == 201
    again = _order(client, coupon["coupon_id"], 2)
    assert again.status_code == 409 and _error(again) == "COUPON_UNITS_EXCEEDED"
    assert "1장" in again.json()["error"]["message"]
    assert _order(client, coupon["coupon_id"], 1).status_code == 201
    assert _remaining(client, coupon["coupon_id"]) == 0


def test_an_expired_coupon_is_rejected(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    coupon = _coupon(client, expires_at="2099-01-01T00:00:00Z")
    assert _order(client, coupon["coupon_id"]).status_code == 201  # 아직 유효
    monkeypatch.setattr(service, "_now", lambda: "2100-01-01T00:00:00Z")
    late = _order(client, coupon["coupon_id"])
    assert late.status_code == 409 and _error(late) == "COUPON_EXPIRED"
    view = client.get(f"/api/shop/coupons/{coupon['coupon_id']}", headers=MARKETING).json()["data"]
    assert view["expires_at"] == "2099-01-01T00:00:00Z"


# --- 남은 장수는 주문에서 계산한다 ---------------------------------------------------


def test_a_cancelled_order_gives_the_units_back(client: TestClient) -> None:
    coupon = _coupon(client, count=3)
    order = _order(client, coupon["coupon_id"], 3).json()["data"]
    assert _remaining(client, coupon["coupon_id"]) == 0
    cancelled = client.post(f"/api/shop/orders/{order['order_id']}/cancel", json={})
    assert cancelled.status_code == 200, cancelled.text
    assert _remaining(client, coupon["coupon_id"]) == 3
    assert _order(client, coupon["coupon_id"], 3).status_code == 201


def _paid_order(client: TestClient, coupon_id: str) -> dict:
    order = _order(client, coupon_id, 1).json()["data"]
    paid = client.post(f"/api/shop/orders/{order['order_id']}/payments", json={"simulate": None})
    return paid.json()["data"]


def _refund(client: TestClient, order: dict, amount: int, key: str) -> None:
    response = client.post(
        f"/api/shop/payments/{order['payments'][0]['payment_id']}/refund",
        json={"refund_amount": amount, "idempotency_key": key},
        headers=SUPPORT,
    )
    assert response.status_code == 201, response.text


def test_a_full_refund_gives_the_units_back_but_a_partial_refund_does_not(
    client: TestClient,
) -> None:
    coupon = _coupon(client, count=2)
    order = _paid_order(client, coupon["coupon_id"])  # 결제 금액 = 18,000 − 2,000 + 3,000
    assert order["total_amount"] == 19000 and _remaining(client, coupon["coupon_id"]) == 1
    _refund(client, order, 5000, "r-1")
    assert _remaining(client, coupon["coupon_id"]) == 1  # 부분 환불은 복원하지 않는다
    _refund(client, order, 14000, "r-2")  # 합계 19,000원 = 전액
    assert _remaining(client, coupon["coupon_id"]) == 2


# --- 쿠폰 읽기 ------------------------------------------------------------------


def test_coupon_reads_show_usage_and_filter_by_campaign(client: TestClient) -> None:
    first = _coupon(client, key="c-a", count=3)
    other = _coupon(client, key="c-b", count=1, campaign_id="cmp_0002")
    _order(client, first["coupon_id"], 2)
    listing = client.get("/api/shop/coupons", headers=DATA).json()["data"]
    mine = next(c for c in listing if c["coupon_id"] == first["coupon_id"])
    assert (mine["used_units"], mine["remaining_units"], mine["expired"]) == (2, 1, False)
    assert mine["expires_at"] is None
    only = client.get("/api/shop/coupons?campaign_id=cmp_0002", headers=DATA).json()["data"]
    assert [c["coupon_id"] for c in only] == [other["coupon_id"]]
    assert only[0]["used_units"] == 0 and only[0]["remaining_units"] == 1


# --- 발급의 만료일 ---------------------------------------------------------------


def test_issuing_with_an_expiry(client: TestClient) -> None:
    body = {
        "idempotency_key": "e-1",
        "purpose_code": "compensation",
        "buyer_id": BUYER,
        "count": 1,
        "unit_value": 500,
    }
    past = client.post(
        "/api/shop/coupons", json={**body, "expires_at": "2000-01-01T00:00:00Z"}, headers=MARKETING
    )
    assert past.status_code == 400 and _error(past) == "INVALID_COUPON"
    naive = client.post(
        "/api/shop/coupons", json={**body, "expires_at": "2099-01-01T00:00:00"}, headers=MARKETING
    )
    assert naive.status_code == 422  # 시간대가 없는 시각은 받지 않는다
    sent = {**body, "expires_at": "2099-01-01T00:00:00Z"}
    first = client.post("/api/shop/coupons", json=sent, headers=MARKETING)
    again = client.post("/api/shop/coupons", json=sent, headers=MARKETING)
    assert first.status_code == 201 and again.status_code == 200
    assert again.json()["data"] == first.json()["data"]
    different = client.post(
        "/api/shop/coupons",
        json={**body, "expires_at": "2099-06-01T00:00:00Z"},
        headers=MARKETING,
    )
    assert different.status_code == 409 and _error(different) == "IDEMPOTENCY_MISMATCH"


# --- 멱등 키와 견적 --------------------------------------------------------------


def test_replaying_an_order_with_a_coupon_uses_the_units_once(client: TestClient) -> None:
    coupon = _coupon(client, count=3)
    first = _order(client, coupon["coupon_id"], 2, idempotency_key="o-1")
    again = _order(client, coupon["coupon_id"], 2, idempotency_key="o-1")
    assert again.status_code == 200 and again.json()["data"] == first.json()["data"]
    assert _remaining(client, coupon["coupon_id"]) == 1
    other_units = _order(client, coupon["coupon_id"], 1, idempotency_key="o-1")
    assert other_units.status_code == 409 and _error(other_units) == "IDEMPOTENCY_MISMATCH"
    no_coupon = _order(client, idempotency_key="o-1")
    assert no_coupon.status_code == 409


def test_the_cart_price_matches_the_order_total(client: TestClient) -> None:
    coupon = _coupon(client)
    body = {
        "items": [{"product_id": "prd_0002", "qty": 2}],  # 48,000원
        "points_used": 1000,
        "coupon_id": coupon["coupon_id"],
        "coupon_units": 2,
        "buyer_id": BUYER,
    }
    quote = client.post("/api/shop/cart/price", json=body).json()["data"]
    assert quote["coupon_discount"] == 4000 and quote["coupon_unknown_reason"] is None
    assert quote["payable_amount"] == 48000 + 3000 - 4000 - 1000
    order = client.post(
        "/api/shop/orders",
        json={
            "buyer_id": BUYER,
            "items": body["items"],
            "points_used": 1000,
            "coupon_id": coupon["coupon_id"],
            "coupon_units": 2,
        },
    ).json()["data"]
    assert order["total_amount"] == quote["payable_amount"]
    assert _remaining(client, coupon["coupon_id"]) == 1  # 견적은 장수를 쓰지 않는다


def test_the_cart_price_needs_a_buyer_for_a_coupon(client: TestClient) -> None:
    coupon = _coupon(client)
    body = {"items": [{"product_id": "prd_0001", "qty": 1}], "coupon_id": coupon["coupon_id"]}
    response = client.post("/api/shop/cart/price", json=body)
    assert response.status_code == 400 and _error(response) == "BUYER_REQUIRED"
    assert _remaining(client, coupon["coupon_id"]) == 3


# --- 옛 DB 파일 --------------------------------------------------------------------


def test_an_old_database_file_gets_the_new_columns(tmp_path: Path) -> None:
    path = str(tmp_path / "shop.db")
    client = TestClient(create_app(Db(path)))
    coupon = _coupon(client, count=2)
    order = _order(client).json()["data"]
    client.app.state.shop.db._conn.close()  # type: ignore[attr-defined]
    old = sqlite3.connect(path)  # 이 열들이 생기기 전의 파일을 흉내 낸다
    old.execute("DROP INDEX idx_orders_coupon_id")
    for table, column in (
        ("orders", "coupon_id"),
        ("orders", "coupon_units"),
        ("orders", "coupon_discount"),
        ("order_items", "coupon_share"),
        ("coupons", "expires_at"),
    ):
        old.execute(f"ALTER TABLE {table} DROP COLUMN {column}")
    old.commit()
    old.close()

    reopened = TestClient(create_app(Db(path)))
    got = reopened.get(f"/api/shop/coupons/{coupon['coupon_id']}", headers=MARKETING).json()["data"]
    assert got["expires_at"] is None and got["remaining_units"] == 2  # 옛 쿠폰은 무기한
    kept = reopened.get(f"/api/shop/orders/{order['order_id']}").json()["data"]
    assert kept["coupon_discount"] == 0 and kept["total_amount"] == order["total_amount"]
    assert _order(reopened, coupon["coupon_id"]).status_code == 201
