"""사이트 변환기 라우터: 짝 맞춘 읽기, 사이트에 없는 자료의 빈 응답, 접수 동기화 기록, 그 밖 404."""

import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from ax_agent_template import company_connection as cc
from ax_agent_template.config import agent_url
from ax_dashboard_api.main import app
from ax_dashboard_api.site_adapter import upstream as up

SITE = "http://10.0.0.9:3000"
BASE = "/site-adapter"
AGENT = {"authorization": "Bearer sup-tok"}


@pytest.fixture
def client(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, adapter_dir: Path, nubi: Any
) -> TestClient:
    path = tmp_path / "company_connection.json"
    path.write_text(
        json.dumps({"shop_api_url": SITE, "kind": "adapter", "adapter": {"base_url": SITE}}),
        encoding="utf-8",
    )
    monkeypatch.setenv(cc.CONNECTION_FILE_ENV, str(path))
    monkeypatch.setattr(cc, "_cache", None)
    monkeypatch.setenv("AX_AGENT_TOKENS", "support=sup-tok")
    pages = {
        "/api/v1/tours": {None: nubi("nubi_tours.json")},
        "/api/v1/bookings": {
            None: nubi("nubi_bookings_p1.json"),
            "MjAy": nubi("nubi_bookings_p2.json"),
        },
    }

    def handle(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/":
            return httpx.Response(200, json={})
        cursor = request.url.params.get("cursor")
        return httpx.Response(200, json=pages[request.url.path][cursor[:4] if cursor else None])

    monkeypatch.setattr(up, "TRANSPORT", httpx.MockTransport(handle))
    return TestClient(app)


def test_orders_filtered_and_paged(client: TestClient) -> None:
    q = "since=2026-11-10T00:00:00Z&until=2026-11-14T00:00:00Z&limit=1"
    first = client.get(f"{BASE}/api/shop/orders?{q}").json()
    assert [o["order_id"] for o in first["data"]] == ["NB-20261110-0001"]
    assert first["meta"]["next_cursor"] == "1"
    assert first["meta"]["source"] == "site_adapter"
    second = client.get(f"{BASE}/api/shop/orders?{q}&cursor=1").json()
    assert [o["order_id"] for o in second["data"]] == ["NB-20261111-0002"]
    assert second["meta"]["next_cursor"] is None


def test_orders_by_buyer(client: TestClient) -> None:
    body = client.get(f"{BASE}/api/shop/orders?buyer_id=usr_02").json()
    assert [o["order_id"] for o in body["data"]] == ["NB-20261115-0003"]


def test_order_detail_and_not_found(client: TestClient) -> None:
    ok = client.get(f"{BASE}/api/shop/orders/NB-20261110-0001")
    assert ok.status_code == 200 and ok.json()["data"]["status"] == "paid"
    missing = client.get(f"{BASE}/api/shop/orders/NB-NOPE")
    assert missing.status_code == 404 and missing.json()["error"]["code"] == "NOT_FOUND"


def test_products_categories_users(client: TestClient) -> None:
    products = client.get(f"{BASE}/api/shop/products").json()["data"]
    assert [p["product_id"] for p in products] == ["JP-OSA-4D", "VN-DAD-5D"]
    cats = client.get(f"{BASE}/api/shop/categories").json()["data"]
    assert [c["category_code"] for c in cats] == ["JP", "SEA"]
    users = client.get(f"{BASE}/api/shop/users").json()["data"]
    assert [u["buyer_id"] for u in users] == ["usr_01", "usr_02"]


def test_sales_summary(client: TestClient) -> None:
    body = client.get(f"{BASE}/api/shop/sales-summary?group_by=product").json()
    assert body["data"]["rows"] == [
        {"product_id": "JP-OSA-4D", "gross_qty": 3, "gross_amount": 2500000}
    ]


def test_sales_summary_uses_from_to_window(client: TestClient) -> None:
    q = "from=2026-11-12T00:00:00Z&to=2026-11-20T00:00:00Z&group_by=product"
    assert client.get(f"{BASE}/api/shop/sales-summary?{q}").json()["data"]["rows"] == []


def test_sales_export_shape(client: TestClient) -> None:
    from ax_contracts.data_access.shop_sales import ShopSalesExportResult

    body = client.get(f"{BASE}/api/shop/sales-export?limit=2").json()["data"]
    parsed = ShopSalesExportResult.model_validate(body)
    assert len(parsed.orders) == 2 and parsed.has_more is True and parsed.next_cursor
    # 커서는 마지막으로 준 (updated_at, order_id) 자리다. 이어 받으면 남은 주문만 온다
    rest = client.get(
        f"{BASE}/api/shop/sales-export", params={"limit": 2, "cursor": parsed.next_cursor}
    ).json()["data"]
    tail = ShopSalesExportResult.model_validate(rest)
    assert [o.order_id for o in tail.orders] == ["NB-20261115-0003"] and tail.has_more is False


def test_customers_export_shape(client: TestClient) -> None:
    from ax_contracts.data_access.shop_customers import ShopCustomersExportResult

    body = client.get(f"{BASE}/api/shop/customers-export").json()["data"]
    parsed = ShopCustomersExportResult.model_validate(body)
    assert [c.buyer_id for c in parsed.customers] == ["usr_01", "usr_02"] and not parsed.has_more


def test_metric_series_echoes_metric(client: TestClient) -> None:
    from ax_contracts.data_access.shop_metric_series import ShopMetricSeriesResult

    body = client.get(f"{BASE}/api/shop/metric-series?metric=order_count&bucket=5m").json()["data"]
    assert ShopMetricSeriesResult.model_validate(body).points == []


def test_truncated_upstream_fails_instead_of_partial(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(up.MAX_PAGES_ENV, "1")
    for path in [
        "/api/shop/orders",
        "/api/shop/orders/NB-NOPE",
        "/api/shop/users",
        "/api/shop/sales-summary",
    ]:
        resp = client.get(BASE + path)
        assert resp.status_code == 503 and resp.json()["error"]["code"] == "UPSTREAM_TRUNCATED", (
            path
        )


def test_health(client: TestClient) -> None:
    assert client.get(f"{BASE}/health").json()["data"]["status"] == "ok"


@pytest.mark.parametrize(
    ("path", "empty"),
    [
        ("/api/shop/failed-orders", {"orders": [], "truncated": False}),
        ("/api/shop/orders/attributed", {"orders": [], "truncated": False}),
        ("/api/shop/sellers", []),
        ("/api/shop/coupons", []),
        ("/api/shop/banners", []),
        ("/api/shop/admin/intake/stuck", []),
        ("/api/shop/admin/feedback", []),
        ("/api/shop/admin/ideas", []),
        ("/api/shop/admin/returns", []),
        ("/api/shop/admin/orchestrator/incidents", []),
        ("/api/shop/admin/marketing/briefs", []),
        ("/api/shop/sellers/s1/profile", {}),
        ("/api/shop/sellers/s1/prices", {}),
        ("/api/shop/admin/faults", {}),
        ("/api/shop/admin/payment-version", {}),
    ],
)
def test_empty_endpoints_return_shapes(client: TestClient, path: str, empty: Any) -> None:
    resp = client.get(BASE + path)
    assert resp.status_code == 200
    assert resp.json()["data"] == empty
    assert resp.json()["meta"]["source"] == "site_adapter"


def test_sync_write_records(client: TestClient, adapter_dir: Path) -> None:
    resp = client.post(
        f"{BASE}/api/shop/admin/inquiries/7/synced",
        json={"support_inquiry_id": "inq_1"},
        headers=AGENT,
    )
    assert resp.status_code == 200 and resp.json()["data"] == {"ok": True}
    lines = (adapter_dir / "bookkeeping.jsonl").read_text(encoding="utf-8").splitlines()
    line = json.loads(lines[-1])
    assert (line["kind"], line["key"], line["action"]) == ("inquiries", "7", "synced")
    assert line["fields"] == ["support_inquiry_id"]


def test_sync_write_needs_agent_token(client: TestClient) -> None:
    resp = client.post(f"{BASE}/api/shop/admin/inquiries/7/synced", json={})
    assert resp.status_code == 403


def test_unmapped_is_404(client: TestClient) -> None:
    resp = client.post(f"{BASE}/api/shop/coupons", json={}, headers=AGENT)
    assert resp.status_code == 404 and resp.json()["error"]["code"] == "NOT_MAPPED"


def test_not_configured_is_503(client: TestClient, adapter_dir: Path) -> None:
    (adapter_dir / "mapping.json").unlink()
    resp = client.get(f"{BASE}/api/shop/products")
    assert resp.status_code == 503 and resp.json()["error"]["code"] == "NOT_CONFIGURED"


def test_upstream_auth_maps_503(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(up, "TRANSPORT", httpx.MockTransport(lambda req: httpx.Response(401)))
    resp = client.get(f"{BASE}/api/shop/products")
    assert resp.status_code == 503 and resp.json()["error"]["code"] == "UPSTREAM_AUTH"


def test_mapping_broken_maps_502(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    broken = {"data": [{"name": "이름만"}], "next_cursor": None}
    monkeypatch.setattr(
        up, "TRANSPORT", httpx.MockTransport(lambda req: httpx.Response(200, json=broken))
    )
    resp = client.get(f"{BASE}/api/shop/products")
    assert resp.status_code == 502 and resp.json()["error"]["code"] == "MAPPING_BROKEN"


def test_agents_get_adapter_address(client: TestClient, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AX_DASHBOARD_API_URL", "http://dash:8020")
    assert agent_url("shop-api") == "http://dash:8020/site-adapter"
