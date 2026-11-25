"""사이트 변환기 지표 시계열 (11/25): 고객사 주문 원본에서 5분 버킷 점을 센다.

누비투어 녹화 예약 3건: 0001 deposit_paid(11/10 01:00, 받은 금액 900000) · 0002 cancelled(접수 11/11
01:00, 바뀜 11/12 01:00) · 0003 pending_deposit(11/15 01:00). 사이트에 없는 지표는 빈 점.
"""

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from ax_agent_template import company_connection as cc
from ax_contracts.data_access.shop_metric_series import ShopMetricSeriesResult
from ax_dashboard_api.main import app
from ax_dashboard_api.site_adapter import series
from ax_dashboard_api.site_adapter import upstream as up

SITE = "http://10.0.0.9:3000"
URL = "/site-adapter/api/shop/metric-series"
WEEK = {"from": "2026-11-09T00:00:00Z", "to": "2026-11-16T00:00:00Z", "bucket": "5m"}


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
    pages = {
        None: nubi("nubi_bookings_p1.json"),
        "MjAy": nubi("nubi_bookings_p2.json"),
    }

    def handle(request: httpx.Request) -> httpx.Response:
        cursor = request.url.params.get("cursor")
        return httpx.Response(200, json=pages[cursor[:4] if cursor else None])

    monkeypatch.setattr(up, "TRANSPORT", httpx.MockTransport(handle))
    return TestClient(app)


def points(client: TestClient, metric: str, **window: str) -> list[tuple[str, float]]:
    res = client.get(URL, params={**WEEK, **window, "metric": metric})
    assert res.status_code == 200, res.text
    parsed = ShopMetricSeriesResult.model_validate(res.json()["data"])
    assert parsed.metric == metric
    return [(p.ts.strftime("%m-%d %H:%M"), p.value) for p in parsed.points]


def test_order_count_by_created_at(client: TestClient) -> None:
    assert points(client, "order_count") == [
        ("11-10 01:00", 1.0),
        ("11-11 01:00", 1.0),
        ("11-15 01:00", 1.0),
    ]


def test_order_amount_counts_only_paid_orders(client: TestClient) -> None:
    got = points(client, "order_amount")
    assert [ts for ts, _ in got] == ["11-10 01:00"]  # 취소·결제 전 예약은 빠진다


def test_cancel_count_at_last_change(client: TestClient) -> None:
    assert points(client, "cancel_count") == [("11-12 01:00", 1.0)]


def test_window_limits_points(client: TestClient) -> None:
    got = points(
        client, "order_count", **{"from": "2026-11-11T00:00:00Z", "to": "2026-11-12T00:00:00Z"}
    )
    assert got == [("11-11 01:00", 1.0)]


@pytest.mark.parametrize("metric", ["payment_fail_rate", "coupon_order_count", "inquiry_count"])
def test_site_does_not_have_these_yet(client: TestClient, metric: str) -> None:
    """사이트에 없는 지표·문의 받은함 짝이 없으면 빈 점 (지어내지 않는다)."""
    assert points(client, metric) == []


def test_without_window_stays_empty(client: TestClient) -> None:
    body = client.get(URL, params={"metric": "order_count"}).json()["data"]
    assert body["points"] == []


def test_bucketize_sums_and_floors() -> None:
    t = datetime(2026, 11, 10, 1, 7, 30, tzinfo=UTC)
    got, truncated = series.bucketize(
        [(t, 2.0), (t, 3.0), (None, 9.0), (datetime(2026, 11, 20, tzinfo=UTC), 1.0)],
        datetime(2026, 11, 10, tzinfo=UTC),
        datetime(2026, 11, 11, tzinfo=UTC),
    )
    assert got == [{"ts": "2026-11-10T01:05:00Z", "value": 5.0}] and truncated is False


def test_parse_time_reads_site_formats() -> None:
    assert series.parse_time("2026-11-10T01:00:00.000Z") == datetime(2026, 11, 10, 1, tzinfo=UTC)
    assert series.parse_time("2026-11-10T10:00:00+09:00") == datetime(2026, 11, 10, 1, tzinfo=UTC)
    assert series.parse_time("어제") is None and series.parse_time(None) is None
