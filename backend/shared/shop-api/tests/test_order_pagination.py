"""주문 목록 페이지(`GET /api/shop/orders` limit·cursor).

25만 건을 한 번에 읽다 서버가 멈춘 일(2026-11-10) 뒤에 넣었다.
"""

import inspect
import sqlite3

import pytest
from fastapi.testclient import TestClient

from ax_agent_template import events
from ax_shop_api.db import Db
from ax_shop_api.main import create_app
from ax_shop_api.service import ORDER_PAGE_DEFAULT, ORDER_PAGE_MAX, Shop


@pytest.fixture(autouse=True)
def _mock_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_TEMPLATE_MOCK", "1")
    events.reset_mock_bus()


def _fill(db: Db, count: int) -> list[str]:
    """주문 `count`건(구매자 둘이 번갈아, 1분 간격, 품목·결제 하나씩)을 바로 넣는다."""
    ids = [f"ord_t{n:05d}" for n in range(count)]
    with db.tx() as conn:
        for buyer in ("buy_t1", "buy_t2"):
            conn.execute(
                "INSERT INTO buyers(buyer_id, tenant_id, created_at) VALUES (?, 't', 'x')", (buyer,)
            )
        for n, order_id in enumerate(ids):
            at = f"2026-11-{1 + n // 1440:02d}T{n // 60 % 24:02d}:{n % 60:02d}:00Z"
            conn.execute(
                "INSERT INTO orders(order_id, buyer_id, status, total_amount, tenant_id,"
                " created_at, updated_at) VALUES (?, ?, 'paid', 1000, 't', ?, ?)",
                (order_id, f"buy_t{1 + n % 2}", at, at),
            )
            conn.execute(
                "INSERT INTO order_items(order_id, product_id, seller_id, qty, unit_price,"
                " tenant_id) VALUES (?, 'prd_0001', 'sel_0001', 1, 1000, 't')",
                (order_id,),
            )
            conn.execute(
                "INSERT INTO payments(payment_id, order_id, amount, method_code, status,"
                " tenant_id, created_at) VALUES (?, ?, 1000, 'card', 'succeeded', 't', ?)",
                (f"pay_t{n:05d}", order_id, at),
            )
    return ids


@pytest.fixture
def filled() -> tuple[TestClient, Db, list[str]]:
    db = Db(":memory:")
    client = TestClient(create_app(db))
    ids = _fill(db, 2500)
    return client, db, ids


def _walk(client: TestClient, **params: object) -> tuple[list[str], int]:
    """cursor를 끝까지 넘겨 주문 번호를 모은다. (번호, 요청 수)"""
    seen: list[str] = []
    calls = 0
    query: dict[str, object] = dict(params)
    while True:
        body = client.get("/api/shop/orders", params=query).json()
        calls += 1
        seen += [o["order_id"] for o in body["data"]]
        if body["meta"]["next_cursor"] is None:
            return seen, calls
        query["cursor"] = body["meta"]["next_cursor"]


def _seeded(client: TestClient) -> list[str]:
    """시드가 넣은 주문(이 시험이 넣은 것 말고)."""
    rows = _walk(client, limit=ORDER_PAGE_MAX)[0]
    return [order_id for order_id in rows if not order_id.startswith("ord_t")]


def test_default_page_is_200_and_says_where_to_continue(filled) -> None:
    client, _, _ = filled
    body = client.get("/api/shop/orders").json()
    assert len(body["data"]) == ORDER_PAGE_DEFAULT == 200
    assert body["meta"]["next_cursor"] == body["data"][-1]["order_id"]


def test_limit_is_capped_at_1000(filled) -> None:
    client, _, _ = filled
    assert len(client.get("/api/shop/orders?limit=1000").json()["data"]) == 1000
    assert client.get("/api/shop/orders?limit=1001").status_code == 422
    assert client.get("/api/shop/orders?limit=0").status_code == 422


def test_cursor_walks_every_order_once(filled) -> None:
    client, _, ids = filled
    seeded = _seeded(client)
    seen, calls = _walk(client, limit=300)
    assert len(seen) == len(set(seen))  # 중복 없음
    assert sorted(seen) == sorted([*ids, *seeded])  # 빠짐 없음
    assert seen == sorted(seen)  # order_id 순
    assert calls == -(-len(seen) // 300)  # 마지막 페이지에서 바로 끝난다(빈 페이지를 더 묻지 않음)


def test_buyer_and_period_filters_page_too(filled) -> None:
    client, _, ids = filled
    mine, _ = _walk(client, buyer_id="buy_t2", limit=100)
    assert mine == [i for n, i in enumerate(ids) if n % 2 == 1]
    # 11월 1일 하루(1440건)만, 기간 경계는 since 이상 until 미만
    day, _ = _walk(
        client, since="2026-11-01T00:00:00Z", until="2026-11-02T00:00:00Z", limit=ORDER_PAGE_MAX
    )
    assert [i for i in day if i.startswith("ord_t")] == ids[:1440]


def test_last_page_has_no_cursor(filled) -> None:
    client, _, ids = filled
    body = client.get(
        "/api/shop/orders", params={"buyer_id": "buy_t1", "cursor": ids[-3], "limit": 5}
    ).json()
    # buy_t1은 짝수 번째 주문. ids[-3](홀수 번째) 뒤로는 ids[-2] 하나만 남는다
    assert [o["order_id"] for o in body["data"]] == [ids[-2]]
    assert body["meta"]["next_cursor"] is None


def test_bad_cursor_is_rejected(filled) -> None:
    client, _, _ = filled
    assert client.get("/api/shop/orders?cursor=ord_1%27%20OR%201=1").status_code == 422


def test_page_reads_items_and_payments_in_a_few_queries(filled) -> None:
    """페이지 안 주문의 품목·결제·환불은 주문마다가 아니라 표마다 묶어 읽는다."""
    _, db, _ = filled
    shop = Shop(db)
    statements: list[str] = []
    with db.tx() as conn:
        conn.set_trace_callback(statements.append)
    try:
        orders, _ = shop.list_orders(limit=ORDER_PAGE_MAX)
    finally:
        with db.tx() as conn:
            conn.set_trace_callback(None)
    assert len(orders) == ORDER_PAGE_MAX
    selects = [s for s in statements if s.lstrip().upper().startswith("SELECT")]
    # 주문 1 + (품목·결제·환불) x 500건 묶음 2개 = 7
    assert len(selects) <= 7, selects
    assert all(o["items"] and o["payments"] for o in orders)
    assert set(orders[0]["payments"][0]) == {
        "payment_id",
        "amount",
        "method_code",
        "status",
        "failure_code",
        "created_at",
    }


def test_single_order_view_is_unchanged(filled) -> None:
    client, _, ids = filled
    one = client.get(f"/api/shop/orders/{ids[0]}").json()["data"]
    listed = client.get("/api/shop/orders", params={"buyer_id": "buy_t1", "limit": 1}).json()
    assert listed["data"][0] == one


def test_handler_runs_off_the_event_loop() -> None:
    """동기 핸들러라 FastAPI가 스레드에서 돌린다(DB를 읽는 동안 /health가 막히지 않는다)."""
    app = create_app(Db(":memory:"))
    route = next(
        r
        for r in app.routes
        if getattr(r, "path", None) == "/api/shop/orders" and "GET" in getattr(r, "methods", ())
    )
    assert not inspect.iscoroutinefunction(route.endpoint)


@pytest.mark.parametrize(
    ("sql", "index"),
    [
        # 기간 페이지는 order_id 순이라 sqlite가 기본 키 순서로 훑기도 한다(25만 건에서 약 60ms).
        # 기간으로 거르는 조회(건수·기간 목록)는 이 인덱스를 쓴다
        (
            "SELECT order_id FROM orders WHERE created_at >= ? AND created_at < '9'",
            "idx_orders_created_at",
        ),
        (
            "SELECT * FROM orders WHERE buyer_id = ? ORDER BY order_id LIMIT 201",
            "idx_orders_buyer_id",
        ),
    ],
)
def test_page_queries_can_use_an_index(sql: str, index: str) -> None:
    db = Db(":memory:")
    with db.tx() as conn:
        names = {r["name"] for r in conn.execute("PRAGMA index_list(orders)")}
        plan = " | ".join(
            str(r["detail"]) for r in conn.execute("EXPLAIN QUERY PLAN " + sql, ("x",))
        )
    assert index in names
    assert index in plan, plan


def test_reads_from_another_thread_are_safe(tmp_path) -> None:
    """Db는 연결 하나를 잠금으로 감싸 스레드에서 같이 읽고 써도 된다."""
    import threading

    db = Db(str(tmp_path / "shop.db"))
    _fill(db, 50)
    shop = Shop(db)
    errors: list[BaseException] = []

    def read() -> None:
        try:
            for _ in range(20):
                assert len(shop.list_orders(limit=50)[0]) == 50
        except BaseException as error:  # noqa: BLE001 - 스레드 밖에서 확인한다
            errors.append(error)

    threads = [threading.Thread(target=read) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    assert errors == []
    assert sqlite3.threadsafety in (1, 3)
