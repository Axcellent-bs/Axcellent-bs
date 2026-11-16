"""data 평가의 가짜 판매 서버(eval/fake_shop.py)가 판매 서버 계약과 규칙을 따르는지.

판매 서버는 `Catbooks-demo/catbooks-demo`로 분리됐다. data(코드·평가·시험)는 판매 서버 코드를
import하지 않고, 평가 생성기와 시험은 가짜 판매 서버를 HTTP로 읽는다. 여기서 재는 것:
- data 어디에도 `ax_shop_api` import가 없다
- data 커넥터·판매자 클라이언트가 가짜에 보내는 요청이 판매 서버 OpenAPI 고정본에 있는 GET 경로·
  쿼리이고(필수 쿼리 포함), 토큰을 요구하는 경로가 고정본과 같다
- 가짜의 응답이 `{data, meta}` 모양이고 data 계약 모델로 읽힌다
- 쓰기·조회 규칙이 판매 서버와 같다(ID·금액 나누기·배송비·실패 코드·시각·지표 계산)
"""

from __future__ import annotations

import asyncio
import importlib.util
import re
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest

from data_agent.clients.shop_client import HttpSellerClient
from data_agent.connectors import shop_api
from data_agent.connectors.shop_api import ShopApi, ShopApiConnector, ShopUnavailable

DATA = Path(__file__).resolve().parents[1]
EVAL = DATA / "eval"
_NAME = "data_eval_fake_shop"  # eval_world와 같은 이름 (재무의 같은 이름 파일과 섞이지 않게)
if _NAME not in sys.modules:
    _spec = importlib.util.spec_from_file_location(_NAME, EVAL / "fake_shop.py")
    assert _spec and _spec.loader
    sys.modules[_NAME] = importlib.util.module_from_spec(_spec)
    _spec.loader.exec_module(sys.modules[_NAME])
fake_shop = sys.modules[_NAME]

T0 = datetime(2026, 10, 5, 1, 0, tzinfo=UTC)
METRICS = [
    "order_count",
    "order_fail_count",
    "payment_fail_rate",
    "payment_count",
    "order_amount",
    "coupon_order_count",
    "coupon_discount_amount",
]


def _world() -> Any:
    """주문 넷: 쿠폰·유입 표시·결제 성공 / 장애 실패 뒤 재결제 / 포인트·카드 거절 / 결제 전."""
    clock = [T0]
    shop = fake_shop.FakeShop(fake_shop.FakeShopDb(), now=lambda: clock[0])
    shop.seed()
    coupon, replayed = shop.issue_coupon("k-1", "campaign", "buy_0001", 2, 3000, "cmp_eval")
    assert not replayed
    a = shop.place_order(
        "buy_0001",
        [("prd_0001", 1), ("prd_0004", 2)],
        coupon_id=coupon["coupon_id"],
        src_source="youtube",
        src_medium="video",
        src_campaign="cmp_eval",
    )
    shop.pay(a["order_id"], "card", None)
    clock[0] = T0 + timedelta(minutes=2)
    b = shop.create_order("buy_0002", [("prd_0007", 2)])
    shop.inject_fault("failure")
    assert shop.pay(b["order_id"], "card", None)["status"] == "payment_failed"
    shop.release_fault()
    assert shop.pay(b["order_id"], "card", None)["status"] == "paid"
    clock[0] = T0 + timedelta(minutes=6)
    c = shop.place_order("buy_0003", [("prd_0002", 1)], points_used=1000, src_source="bad tag!")
    assert shop.pay(c["order_id"], "card", "failure")["status"] == "payment_failed"
    clock[0] = T0 + timedelta(minutes=8)
    shop.create_order("buy_0004", [("prd_0003", 1)])
    return shop


def _iso(moment: datetime) -> str:
    return moment.strftime("%Y-%m-%dT%H:%M:%SZ")


def test_data_does_not_import_the_shop_server() -> None:
    """data 코드·평가·시험 어디에도 판매 서버 코드 import가 없다(레포 분리 뒤에도 돈다)."""
    found = []
    for path in DATA.rglob("*.py"):
        if "__pycache__" in path.parts or ".venv" in path.parts:
            continue
        text = path.read_text("utf-8")
        if re.search(r"^\s*(from|import)\s+ax_shop_api", text, re.M):
            found.append(path.relative_to(DATA).as_posix())
    assert found == []
    assert "ax-shop-api" not in (DATA / "pyproject.toml").read_text("utf-8")


def test_snapshot_is_pinned_to_a_shop_repo_commit() -> None:
    spec = fake_shop.openapi()
    assert spec["info"]["title"] == "ax-shop-api"
    assert re.search(r"Catbooks-demo/catbooks-demo@[0-9a-f]{40} ", fake_shop.OPENAPI_SOURCE)


def _read_all(monkeypatch: pytest.MonkeyPatch) -> Any:
    """data 커넥터·판매자 클라이언트로 가짜 판매 서버의 모든 조회를 data 토큰으로 부른다."""
    monkeypatch.setenv("AGENT_TEMPLATE_MOCK", "1")
    monkeypatch.setattr(shop_api, "ORDERS_PAGE", 2)  # 주문 페이지 넘기기(next_cursor)도 지나게
    shop = _world()
    transport = fake_shop.FakeShopTransport(shop, now=lambda: T0 + timedelta(hours=1),
                                            check_tokens=True)  # fmt: skip

    async def go() -> dict[str, Any]:
        async with httpx.AsyncClient(transport=transport) as client:
            api = ShopApi("http://shop", token="mock:data", client=client)
            connector, sellers = ShopApiConnector(api), HttpSellerClient(api)
            end = T0 + timedelta(minutes=15)
            out: dict[str, Any] = {
                m: await connector.metric_series(m, T0, end) for m in METRICS
            }
            out["events"] = await connector.failure_events(T0, end)
            out["coupons"] = await connector.campaign_coupons("cmp_eval")
            out["orders"] = await connector.orders(T0, end)
            out["attributed"] = await connector.attributed_orders(T0, end, "cmp_eval", "youtube")
            out["products"] = await connector.products()
            out["profile"] = await sellers.seller_profile("sel_0001")
            out["prices"] = await sellers.seller_prices("sel_0003")
            return out

    return asyncio.run(go()), transport


def test_data_requests_are_in_the_shop_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    """data가 보내는 요청은 모두 고정본에 있는 GET 경로·쿼리이고 필수 쿼리를 빠뜨리지 않는다."""
    _, transport = _read_all(monkeypatch)
    paths = fake_shop.openapi()["paths"]
    templates = {
        re.compile("^" + re.sub(r"\{[^}]+\}", "[^/]+", path) + "$"): path for path in paths
    }
    used = set()
    for method, path, query in transport.seen:
        matches = [t for pattern, t in templates.items() if pattern.match(path)]
        assert matches, path
        template = min(matches, key=lambda t: t.count("{"))  # 고정 경로가 먼저
        op = paths[template][method.lower()]
        params = op.get("parameters", [])
        declared = {p["name"] for p in params if p["in"] == "query"}
        required = {p["name"] for p in params if p["in"] == "query" and p.get("required")}
        assert set(query) <= declared, (path, query)
        assert required <= set(query), (path, query)
        used.add(template)
    assert used == {
        "/api/shop/metric-series",
        "/api/shop/failed-orders",
        "/api/shop/coupons",
        "/api/shop/orders",
        "/api/shop/orders/attributed",
        "/api/shop/products",
        "/api/shop/sellers/{seller_id}/profile",
        "/api/shop/sellers/{seller_id}/prices",
    }
    assert any("cursor" in q for _, p, q in transport.seen if p == "/api/shop/orders")


GETS = {
    "/api/shop/metric-series": {"metric": "order_count", "from": _iso(T0), "to": _iso(T0)},
    "/api/shop/failed-orders": {"from": _iso(T0), "to": _iso(T0 + timedelta(hours=1))},
    "/api/shop/coupons": {},
    "/api/shop/orders": {},
    "/api/shop/orders/attributed": {"from": _iso(T0), "to": _iso(T0 + timedelta(hours=1))},
    "/api/shop/products": {},
    "/api/shop/sellers/{seller_id}/profile": {},
    "/api/shop/sellers/{seller_id}/prices": {},
}


def test_token_rules_match_the_contract(monkeypatch: pytest.MonkeyPatch) -> None:
    """고정본이 토큰(authorization 헤더)을 받는 경로만 토큰을 요구한다. 판매 서버 `_caller`처럼
    토큰이 없으면 401, 받지 않는 부서(finance가 data 조회)면 403이다."""
    monkeypatch.setenv("AGENT_TEMPLATE_MOCK", "1")
    shop = _world()
    transport = fake_shop.FakeShopTransport(shop, check_tokens=True)
    paths = fake_shop.openapi()["paths"]

    def get(path: str, params: dict[str, str], token: str | None) -> httpx.Response:
        headers = {"Authorization": f"Bearer {token}"} if token else {}
        with httpx.Client(transport=transport, base_url="http://shop") as client:
            return client.get(path.replace("{seller_id}", "sel_0001"), params=params,
                              headers=headers)  # fmt: skip

    for path, params in GETS.items():
        guarded = any(p["name"] == "authorization" for p in paths[path]["get"]["parameters"])
        anonymous = get(path, params, None)
        assert (anonymous.status_code == 401) == guarded, path
        assert get(path, params, "mock:data").status_code in (200, 400), path
    assert get("/api/shop/sellers/{seller_id}/profile", {}, "mock:finance").status_code == 403
    assert get("/api/shop/coupons", {}, "mock:finance").status_code == 200  # 쿠폰은 재무도 읽는다
    assert get("/api/shop/orders/attributed", GETS["/api/shop/orders/attributed"],
               "mock:marketing").status_code == 403  # fmt: skip
    assert get("/api/shop/unknown", {}, "mock:data").status_code == 404


def test_responses_are_read_by_the_data_contracts(monkeypatch: pytest.MonkeyPatch) -> None:
    out, _ = _read_all(monkeypatch)
    assert [p[1] for p in out["order_count"]] == [2.0, 2.0, 0.0]  # 빈 5분도 0으로 채운다
    assert [p[1] for p in out["payment_count"]] == [3.0, 1.0, 0.0]
    # 실패율은 결제가 없는 5분을 뺀다 (0%와 '측정 못 함'을 섞지 않는다)
    assert [(p[0].minute, p[1]) for p in out["payment_fail_rate"]] == [(0, 1 / 3), (5, 1.0)]
    assert [p[1] for p in out["order_fail_count"]] == [1.0, 1.0, 0.0]
    # 매출은 결제가 끝난 주문만 (결제 실패·결제 대기 제외)
    assert [p[1] for p in out["order_amount"]] == [33000.0 + 62000.0, 0.0, 0.0]
    assert [p[1] for p in out["coupon_order_count"]] == [1.0, 0.0, 0.0]
    assert [p[1] for p in out["coupon_discount_amount"]] == [3000.0, 0.0, 0.0]
    assert len(out["events"].events) == 2 and not out["events"].truncated  # 장애 1·카드 거절 1
    assert [(c.used_units, c.count) for c in out["coupons"]] == [(1, 2)]
    assert sorted(o.order_id for o in out["orders"]) == ["ord_0001", "ord_0002", "ord_0003",
                                                         "ord_0004"]  # fmt: skip
    assert [o.order_id for o in out["attributed"]] == ["ord_0001"]  # 어긋난 유입 표시는 비운다
    assert [p.product_id for p in out["products"]][:2] == ["prd_0001", "prd_0002"]
    assert out["profile"].seller_id == "sel_0001" and out["profile"].product_count == 3
    assert out["profile"].business_hash is None  # 판매 서버에 없는 값은 지어내지 않는다
    medians = {i.category: i.category_median_price for i in out["prices"].items}
    # 같은 카테고리 판매 중 상품(모든 판매자). 4개씩이라 가운데 두 값의 평균
    # book 13,000·18,000·24,000·31,000 → 21,000 / stationery 7,500·9,800·15,000·29,000 → 12,400
    assert medians == {"book": 21000, "stationery": 12400}


def test_fake_shop_uses_the_shop_rules() -> None:
    shop = _world()
    a, b, c, d = (shop.get_order(f"ord_000{n}") for n in range(1, 5))
    # 18,000 + 7,500×2 = 33,000 − 쿠폰 3,000(1장) + 배송비 3,000 (5만 원 미만)
    assert (a["total_amount"], a["coupon_discount"], a["shipping_fee"]) == (33000, 3000, 3000)
    assert [i["coupon_share"] for i in a["items"]] == [1636, 1364]  # 금액 비율, 남는 몫은 뒤부터
    assert [i["order_item_id"] for i in a["items"]] == ["oit_0001", "oit_0002"]
    assert a["promised_delivery_at"] == "2026-10-08T01:00:00Z"  # 결제 시각 + 3일
    assert b["shipping_fee"] == 0  # 62,000원: 무료 배송
    assert [(p["payment_id"], p["failure_code"]) for p in b["payments"]] == [
        ("pay_0002", "PG_UNAVAILABLE"),
        ("pay_0003", None),
    ]
    # 포인트 1,000 + 배송비: 24,000 − 1,000 + 3,000
    assert (c["total_amount"], c["items"][0]["points_share"]) == (26000, 1000)
    assert c["payments"][0]["failure_code"] == "PG_DECLINED" and c["promised_delivery_at"] is None
    assert d["status"] == "pending_payment" and d["created_at"] == "2026-10-05T01:08:00Z"
    with shop.db.tx() as conn:
        row = conn.execute("SELECT src_source, src_medium FROM orders WHERE order_id = 'ord_0003'")
        assert tuple(row.fetchone()) == (None, None)
        stock = conn.execute("SELECT stock FROM products WHERE product_id = 'prd_0004'")
        assert stock.fetchone()[0] == 500 - 2
    assert shop.create_product("sel_0001", "새 책", "book", 9000, 5)["product_id"] == "prd_0009"
    again, replayed = shop.issue_coupon("k-1", "campaign", "buy_0001", 2, 3000, "cmp_eval")
    assert replayed and again["used_units"] == 1 and again["remaining_units"] == 1
    for bad, code in (
        (lambda: shop.place_order("buy_0002", [("prd_0001", 1)], coupon_id="cpn_0001"),
         "COUPON_NOT_YOURS"),
        (lambda: shop.place_order("buy_0001", [("prd_0001", 1)], coupon_id="cpn_0001",
                                  coupon_units=2), "COUPON_UNITS_EXCEEDED"),
        (lambda: shop.pay("ord_0001", "card", None), "ORDER_NOT_PAYABLE"),
        (lambda: shop.place_order("buy_9999", [("prd_0001", 1)]), "BUYER_NOT_FOUND"),
        (lambda: shop.place_order("buy_0001", [("prd_0001", 10_000)]), "OUT_OF_STOCK"),
    ):  # fmt: skip
        with pytest.raises(fake_shop.FakeShopError) as caught:
            bad()
        assert caught.value.code == code
    assert fake_shop.split_by_amount(10, [10, 10, 10]) == [3, 3, 4]
    assert fake_shop.median_price([1, 2]) == 2 and fake_shop.median_price([5]) == 5


def test_orders_page_and_cursor() -> None:
    transport = fake_shop.FakeShopTransport(_world())
    with httpx.Client(transport=transport, base_url="http://shop") as client:
        first = client.get("/api/shop/orders", params={"limit": 3}).json()
        assert [o["order_id"] for o in first["data"]] == ["ord_0001", "ord_0002", "ord_0003"]
        assert first["meta"] == {"next_cursor": "ord_0003"}
        rest = client.get("/api/shop/orders", params={"limit": 3, "cursor": "ord_0003"}).json()
        assert [o["order_id"] for o in rest["data"]] == ["ord_0004"]
        assert rest["meta"] == {"next_cursor": None}
        since = client.get("/api/shop/orders", params={"since": "2026-10-05T10:05:00+09:00"})
        assert [o["order_id"] for o in since.json()["data"]] == ["ord_0003", "ord_0004"]
        long = {"metric": "order_count", "from": _iso(T0), "to": _iso(T0 + timedelta(days=2))}
        too_long = client.get("/api/shop/metric-series", params=long)
        assert (too_long.status_code, too_long.json()["error"]["code"]) == (422, "RANGE_TOO_LONG")
        failed = client.get("/api/shop/failed-orders", params=GETS["/api/shop/failed-orders"]
                            | {"limit": 1}).json()["data"]  # fmt: skip
        assert failed["truncated"] is True and failed["orders"][0]["reason"] == "PG_UNAVAILABLE"


def test_unavailable_when_token_is_rejected(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_TEMPLATE_MOCK", "1")
    transport = fake_shop.FakeShopTransport(_world(), check_tokens=True)

    async def go() -> None:
        async with httpx.AsyncClient(transport=transport) as client:
            api = ShopApi("http://shop", token="mock:finance", client=client)
            await ShopApiConnector(api).metric_series("order_count", T0, T0 + timedelta(hours=1))

    with pytest.raises(ShopUnavailable) as caught:
        asyncio.run(go())
    assert caught.value.code == "FORBIDDEN"
