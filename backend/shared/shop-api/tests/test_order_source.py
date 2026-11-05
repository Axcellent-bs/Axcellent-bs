"""주문 유입 출처(#647): 저장·비움·멱등·옛 DB, 그리고 data만 읽는 귀속 주문 조회."""

import sqlite3
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ax_agent_template import events
from ax_shop_api.db import Db
from ax_shop_api.main import create_app
from ax_shop_api.service import clean_source_tag

DATA = {"Authorization": "Bearer mock:data"}
ADMIN = {"Authorization": "Bearer mock:admin"}
MARKETING = {"Authorization": "Bearer mock:marketing"}
SUPPORT = {"Authorization": "Bearer mock:support"}
BUYER = "buy_0001"
TAGS = {"src_source": "banner", "src_medium": "onsite", "src_campaign": "cmp_PROP-1"}


@pytest.fixture(autouse=True)
def _mock_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_TEMPLATE_MOCK", "1")
    events.reset_mock_bus()


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app(Db(":memory:")))


def _order(client: TestClient, **body: object):  # noqa: ANN202
    payload: dict = {"buyer_id": BUYER, "items": [{"product_id": "prd_0001", "qty": 1}]}
    payload.update(body)
    return client.post("/api/shop/orders", json=payload)


def _window() -> dict[str, str]:
    now = datetime.now(UTC)
    return {
        "from": (now - timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "to": (now + timedelta(days=1)).strftime("%Y-%m-%dT%H:%M:%SZ"),
    }


def _attributed(client: TestClient, headers: dict = DATA, **params: object):  # noqa: ANN202
    return client.get(
        "/api/shop/orders/attributed", params={**_window(), **params}, headers=headers
    )


def _stored(client: TestClient, order_id: str) -> tuple:
    row = client.app.state.shop.db._conn.execute(  # type: ignore[attr-defined]
        "SELECT src_source, src_medium, src_campaign FROM orders WHERE order_id = ?", (order_id,)
    ).fetchone()
    return tuple(row)


# --- 저장·비움·하위 호환 ---------------------------------------------------------


def test_source_tags_are_stored_with_the_order(client: TestClient) -> None:
    order = _order(client, **TAGS)
    assert order.status_code == 201, order.text
    assert _stored(client, order.json()["data"]["order_id"]) == ("banner", "onsite", "cmp_PROP-1")


def test_an_order_without_tags_works_as_before(client: TestClient) -> None:
    order = _order(client)
    assert order.status_code == 201
    assert _stored(client, order.json()["data"]["order_id"]) == (None, None, None)


@pytest.mark.parametrize(
    "value", ["x" * 65, "a b", "한글", "a/b", "a;drop", "<b>", "a\nb", "", "   "]
)
def test_a_bad_tag_is_dropped_but_the_order_still_goes_through(
    client: TestClient, value: str
) -> None:
    order = _order(client, src_source=value, src_medium="social", src_campaign="cmp_PROP-2")
    assert order.status_code == 201, order.text
    assert _stored(client, order.json()["data"]["order_id"]) == (None, "social", "cmp_PROP-2")


def test_tag_validation_rule() -> None:
    assert clean_source_tag("a.B-c_9") == "a.B-c_9"
    assert clean_source_tag("x" * 64) == "x" * 64
    assert clean_source_tag("x" * 65) is None
    assert clean_source_tag(" banner ") == "banner"
    assert clean_source_tag(None) is None


# --- 멱등 -------------------------------------------------------------------------


def test_retry_with_other_tags_replays_the_first_order_and_keeps_its_tags(
    client: TestClient,
) -> None:
    first = _order(client, idempotency_key="k-1", **TAGS)
    assert first.status_code == 201
    retry = _order(client, idempotency_key="k-1", src_source="google", src_campaign="cmp_PROP-9")
    assert retry.status_code == 200  # 409가 아니라 처음 주문을 돌려준다
    order_id = first.json()["data"]["order_id"]
    assert retry.json()["data"]["order_id"] == order_id
    assert _stored(client, order_id) == ("banner", "onsite", "cmp_PROP-1")


# --- 조회 -------------------------------------------------------------------------


def test_attributed_orders_return_items_and_tags_without_personal_fields(
    client: TestClient,
) -> None:
    _order(client, **TAGS)
    _order(client)  # 출처 없는 주문은 나오지 않는다
    response = _attributed(client)
    body = response.json()["data"]
    assert body["order_count"] == 1 and body["truncated"] is False
    order = body["orders"][0]
    assert order["items"] == [{"product_id": "prd_0001", "qty": 1, "unit_price": 18000}]
    assert order["total_amount"] == 21000 and body["total_amount"] == 21000
    assert (order["src_source"], order["src_medium"], order["src_campaign"]) == (
        "banner",
        "onsite",
        "cmp_PROP-1",
    )
    assert {"order_id", "created_at", "status"} <= set(order)
    assert BUYER not in response.text and "buyer_id" not in response.text
    assert "seller_id" not in response.text


def test_filters_by_campaign_and_source(client: TestClient) -> None:
    _order(client, **TAGS)
    _order(client, src_source="google", src_medium="cpc", src_campaign="cmp_PROP-2")
    only = _attributed(client, campaign_id="cmp_PROP-2").json()["data"]
    assert [o["src_source"] for o in only["orders"]] == ["google"]
    by_source = _attributed(client, source="banner").json()["data"]
    assert [o["src_campaign"] for o in by_source["orders"]] == ["cmp_PROP-1"]
    assert _attributed(client, campaign_id="cmp_NONE").json()["data"]["orders"] == []


def test_cancelled_and_fully_refunded_orders_are_left_out(client: TestClient) -> None:
    keep = _order(client, **TAGS).json()["data"]
    gone = _order(client, **TAGS).json()["data"]
    client.post(f"/api/shop/orders/{gone['order_id']}/cancel", json={})
    refunded = _order(client, **TAGS).json()["data"]
    paid = client.post(
        f"/api/shop/orders/{refunded['order_id']}/payments", json={"simulate": None}
    ).json()["data"]
    done = client.post(
        f"/api/shop/payments/{paid['payments'][0]['payment_id']}/refund",
        json={"refund_amount": paid["total_amount"], "idempotency_key": "r-1"},
        headers=SUPPORT,
    )
    assert done.status_code == 201, done.text
    ids = [o["order_id"] for o in _attributed(client).json()["data"]["orders"]]
    assert ids == [keep["order_id"]]


def test_period_filter_and_result_limit(client: TestClient) -> None:
    _order(client, **TAGS)
    _order(client, **TAGS)
    past = {"from": "2020-01-01T00:00:00Z", "to": "2020-01-02T00:00:00Z"}
    empty = client.get("/api/shop/orders/attributed", params=past, headers=DATA)
    assert empty.json()["data"]["orders"] == []
    cut = _attributed(client, limit=1).json()["data"]
    assert cut["order_count"] == 1 and cut["truncated"] is True


def test_range_must_be_ordered_short_and_have_a_timezone(client: TestClient) -> None:
    def get(**params: str):  # noqa: ANN202
        return client.get("/api/shop/orders/attributed", params=params, headers=DATA)

    assert get(**{"from": "2026-01-02T00:00:00Z", "to": "2026-01-01T00:00:00Z"}).status_code == 400
    too_long = get(**{"from": "2026-01-01T00:00:00Z", "to": "2026-02-02T00:00:01Z"})
    assert too_long.status_code == 400 and too_long.json()["error"]["code"] == "RANGE_TOO_LONG"
    ok = get(**{"from": "2026-01-01T00:00:00Z", "to": "2026-02-01T00:00:00Z"})
    assert ok.status_code == 200  # 31일은 허용
    naive = get(**{"from": "2026-01-01T00:00:00", "to": "2026-01-02T00:00:00"})
    assert naive.status_code == 400


def test_only_the_data_token_may_read(client: TestClient) -> None:
    assert _attributed(client, headers={}).status_code == 401
    for headers in (ADMIN, MARKETING, SUPPORT):
        assert _attributed(client, headers=headers).status_code == 403
    assert _attributed(client, headers=DATA).status_code == 200


# --- 옛 DB 파일 -------------------------------------------------------------------


def test_an_old_database_file_gets_the_source_columns(tmp_path: Path) -> None:
    path = str(tmp_path / "shop.db")
    client = TestClient(create_app(Db(path)))
    order = _order(client, **TAGS).json()["data"]
    client.app.state.shop.db._conn.close()  # type: ignore[attr-defined]
    old = sqlite3.connect(path)  # 이 열들이 생기기 전의 파일을 흉내 낸다
    old.execute("DROP INDEX idx_orders_src_campaign")
    for column in ("src_source", "src_medium", "src_campaign"):
        old.execute(f"ALTER TABLE orders DROP COLUMN {column}")
    old.commit()
    old.close()

    reopened = TestClient(create_app(Db(path)))
    kept = reopened.get(f"/api/shop/orders/{order['order_id']}").json()["data"]
    assert kept["total_amount"] == order["total_amount"]  # 옛 주문은 그대로
    assert _attributed(reopened).json()["data"]["orders"] == []  # 출처가 비어 있다
    assert _order(reopened, **TAGS).status_code == 201
    assert _attributed(reopened).json()["data"]["order_count"] == 1
