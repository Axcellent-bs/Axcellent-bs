"""판매 기록 내보내기(`sales-export`)를 변화 감지기·데이터 수집기가 이어 읽을 수 있게.

설계 `docs/superpowers/specs/2026-11-19-customer-change-polling-design.md` "변환기 보완"(PR 2).

- 커서는 마지막으로 준 `(updated_at, order_id)`. 중간 주문이 바뀌어도 빠지거나 두 번 나오지 않는다
- 커서 시각을 고객사 `updated_since`(짝의 `paging.since_param`)로 2분 앞당겨 넘겨 바뀐 것만 읽는다
- 환불 줄·취소 주체·결제 실패는 짝(고객사 칸)에 근거가 있을 때만 채운다(지어내지 않는다)

고객사는 누비투어 녹화 응답(테스트 전용)을 `updated_since`를 지키는 가짜 API로 돌려준다.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from ax_agent_template import company_connection as cc
from ax_contracts.data_access.shop_sales import SalesOrderRow, ShopSalesExportResult
from ax_dashboard_api.main import app
from ax_dashboard_api.site_adapter import resources
from ax_dashboard_api.site_adapter import upstream as up
from ax_dashboard_api.site_adapter.mapping import ResourceMap

SITE = "http://10.0.0.9:3000"
EXPORT = "/site-adapter/api/shop/sales-export"


def _at(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))


class FakeSite:
    """누비투어 예약 API 흉내 (테스트 전용): `updated_since` 이상만, `updated_at` 순, 쪽 나눔."""

    def __init__(self, bookings: list[dict[str, Any]], page_size: int = 2) -> None:
        self.bookings = {b["code"]: dict(b) for b in bookings}
        self.page_size = page_size
        self.requests: list[dict[str, str]] = []

    def touch(self, code: str, updated_at: str, **changes: Any) -> None:
        self.bookings[code].update(updated_at=updated_at, **changes)

    def handle(self, request: httpx.Request) -> httpx.Response:
        if request.url.path != "/api/v1/bookings":
            return httpx.Response(404, json={})
        params = dict(request.url.params)
        self.requests.append(params)
        rows = sorted(self.bookings.values(), key=lambda b: (b["updated_at"], b["code"]))
        since = params.get("updated_since")
        if since:
            rows = [b for b in rows if _at(b["updated_at"]) >= _at(since)]
        start = int(params.get("cursor") or 0)
        chunk = rows[start : start + self.page_size]
        nxt = str(start + self.page_size) if start + self.page_size < len(rows) else None
        return httpx.Response(200, json={"data": chunk, "next_cursor": nxt})


@pytest.fixture
def bookings(nubi: Any) -> list[dict[str, Any]]:
    return nubi("nubi_bookings_p1.json")["data"] + nubi("nubi_bookings_p2.json")["data"]


@pytest.fixture
def site(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    adapter_dir: Path,
    bookings: list[dict[str, Any]],
) -> FakeSite:
    path = tmp_path / "company_connection.json"
    path.write_text(
        json.dumps({"shop_api_url": SITE, "kind": "adapter", "adapter": {"base_url": SITE}}),
        encoding="utf-8",
    )
    monkeypatch.setenv(cc.CONNECTION_FILE_ENV, str(path))
    monkeypatch.setattr(cc, "_cache", None)
    monkeypatch.setenv(up.CACHE_SECONDS_ENV, "0")  # 고객사 값이 바뀌는 것을 시험한다
    fake = FakeSite(bookings)
    monkeypatch.setattr(up, "TRANSPORT", httpx.MockTransport(fake.handle))
    return fake


@pytest.fixture
def client(site: FakeSite) -> TestClient:
    return TestClient(app)


def _export(client: TestClient, cursor: str | None = None, limit: int = 1) -> ShopSalesExportResult:
    params: dict[str, Any] = {"limit": limit}
    if cursor is not None:
        params["cursor"] = cursor
    res = client.get(EXPORT, params=params)
    assert res.status_code == 200, res.text
    return ShopSalesExportResult.model_validate(res.json()["data"])


def _walk(client: TestClient, cursor: str | None = None, limit: int = 1) -> tuple[list[str], str]:
    """`has_more=false`까지 이어 읽는다 → (받은 주문 번호 순서, 마지막 커서)."""
    seen: list[str] = []
    for _ in range(20):
        page = _export(client, cursor, limit)
        seen += [o.order_id for o in page.orders]
        assert page.next_cursor
        cursor = page.next_cursor
        if not page.has_more:
            return seen, cursor
    raise AssertionError("끝나지 않는다")


# ------------------------------------------------------------------ 커서


def test_walk_gives_every_order_once_in_updated_order(client: TestClient) -> None:
    seen, _ = _walk(client)
    assert seen == ["NB-20261110-0001", "NB-20261111-0002", "NB-20261115-0003"]


def test_changed_order_is_not_skipped_and_comes_again(client: TestClient, site: FakeSite) -> None:
    """읽는 도중 앞 주문이 바뀌어도 뒤 주문이 빠지지 않고, 바뀐 주문은 뒤에 다시 나온다.

    목록 순번 커서였을 때는 순번이 밀려 두 번째 주문이 빠졌다.
    """
    first = _export(client)
    assert [o.order_id for o in first.orders] == ["NB-20261110-0001"]
    site.touch("NB-20261110-0001", "2026-11-16T00:00:00.000Z", status="cancelled")

    seen, _ = _walk(client, first.next_cursor)

    assert seen == ["NB-20261111-0002", "NB-20261115-0003", "NB-20261110-0001"]


def test_nothing_new_keeps_cursor(client: TestClient) -> None:
    _, cursor = _walk(client, limit=500)
    again = _export(client, cursor, limit=500)
    assert again.orders == [] and again.has_more is False and again.next_cursor == cursor


def test_same_time_orders_split_across_pages(client: TestClient, site: FakeSite) -> None:
    """같은 시각에 바뀐 주문도 `order_id`로 이어서 빠지지 않는다."""
    same = "2026-11-17T00:00:00.000Z"
    site.touch("NB-20261111-0002", same)
    site.touch("NB-20261115-0003", same)
    seen, _ = _walk(client)
    assert seen == ["NB-20261110-0001", "NB-20261111-0002", "NB-20261115-0003"]


def test_cursor_time_goes_to_site_two_minutes_early(client: TestClient, site: FakeSite) -> None:
    """처음은 `updated_since` 없이, 이어 읽을 때는 커서 시각 2분 전부터 고객사에 묻는다."""
    first = _export(client)
    assert "updated_since" not in site.requests[0]

    site.requests.clear()
    _export(client, first.next_cursor)

    asked = {r.get("updated_since") for r in site.requests}
    assert len(asked) == 1
    since = _at(asked.pop() or "")
    assert since == _at("2026-11-10T02:00:00.000Z") - timedelta(minutes=2)
    assert since.tzinfo is not None and since.utcoffset() == UTC.utcoffset(None)


def test_only_changed_orders_are_read_from_site(client: TestClient, site: FakeSite) -> None:
    """이어 읽을 때 고객사에서 받는 것은 바뀐 주문뿐이다(매번 전체를 읽지 않는다)."""
    _, cursor = _walk(client, limit=500)
    site.requests.clear()
    site.touch("NB-20261115-0003", "2026-11-18T00:00:00.000Z")

    page = _export(client, cursor, limit=500)

    assert [o.order_id for o in page.orders] == ["NB-20261115-0003"]
    assert len(site.requests) == 1  # 바뀐 것만 오니 한 쪽이면 된다


def test_old_position_cursor_starts_over(client: TestClient) -> None:
    """예전 목록 순번 커서(숫자)를 저장해 둔 수집기는 처음부터 다시 받는다(덮어쓰기라 안전)."""
    seen, _ = _walk(client, cursor="2")
    assert seen == ["NB-20261110-0001", "NB-20261111-0002", "NB-20261115-0003"]


def test_broken_cursor_is_422(client: TestClient) -> None:
    res = client.get(EXPORT, params={"cursor": "not-a-cursor"})
    assert res.status_code == 422 and res.json()["error"]["code"] == "INVALID_REQUEST"


def test_truncated_site_fails_instead_of_partial(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(up.MAX_PAGES_ENV, "1")
    res = client.get(EXPORT)
    assert res.status_code == 503 and res.json()["error"]["code"] == "UPSTREAM_TRUNCATED"


# ------------------------------------------------------------------ 줄 내용 (누비투어 짝)


def test_nubi_rows_have_no_made_up_refund_or_reason(client: TestClient) -> None:
    """누비투어 짝에는 환불·취소 주체 칸이 없다 → 비워 둔다."""
    page = _export(client, limit=500)
    by_id = {o.order_id: o for o in page.orders}
    cancelled = by_id["NB-20261111-0002"]
    assert cancelled.status == "cancelled"
    assert cancelled.cancel_reason_code is None
    assert all(o.refunds == [] for o in page.orders)


def test_reason_from_mapping_reaches_export(
    client: TestClient, site: FakeSite, adapter_dir: Path
) -> None:
    """짝에 취소 주체 칸과 값 표가 있으면 판매 기록에 실린다."""
    path = adapter_dir / "mapping.json"
    mapping = json.loads(path.read_text(encoding="utf-8"))
    orders = mapping["resources"]["orders"]
    orders["fields"]["cancel_reason_code"] = "cancelled_by"
    orders["values"]["cancel_reason_code"] = {"customer": "buyer", "staff": "seller"}
    path.write_text(json.dumps(mapping), encoding="utf-8")
    site.bookings["NB-20261111-0002"]["cancelled_by"] = "customer"
    for b in site.bookings.values():
        b.setdefault("cancelled_by", None)

    page = _export(client, limit=500)

    reasons = {o.order_id: o.cancel_reason_code for o in page.orders}
    assert reasons == {
        "NB-20261110-0001": None,
        "NB-20261111-0002": "buyer",
        "NB-20261115-0003": None,
    }


# ------------------------------------------------------------------ 줄 변환 (순수 함수)

BASE_FIELDS = {
    "order_id": "code",
    "status": "status",
    "created_at": "created_at",
    "updated_at": "updated_at",
    "buyer_id": "member_id",
    "product_id": "product_code",
    "qty": "const:1",
    "total_amount": "total",
    "paid_amount": "paid",
}
RAW = {
    "code": "O-1",
    "status": "refunded_part",
    "created_at": "2026-11-18T01:00:00Z",
    "updated_at": "2026-11-18T05:00:00Z",
    "member_id": "m_1",
    "product_code": "P-1",
    "total": 90000,
    "paid": 90000,
    "refund_total": 30000,
    "refund_time": "2026-11-18T04:00:00Z",
    "who": "auto",
}


def _row(fields: dict[str, str], values: dict[str, dict[str, str]] | None = None, **raw: Any):
    m = ResourceMap.model_validate(
        {"path": "/o", "fields": {**BASE_FIELDS, **fields}, "values": values or {}}
    )
    return SalesOrderRow.model_validate(resources.sales_row({**RAW, **raw}, m))


def test_refund_line_from_refunded_amount() -> None:
    row = _row({"refunded_amount": "refund_total", "refunded_at": "refund_time"})
    assert [(r.refund_id, r.amount) for r in row.refunds] == [(None, 30000)]
    assert row.refunds[0].refunded_at == _at("2026-11-18T04:00:00Z")


def test_refund_time_defaults_to_order_change_time() -> None:
    """환불 시각 칸이 없는 사이트는 주문이 마지막으로 바뀐 시각(고객사 값)을 쓴다."""
    row = _row({"refunded_amount": "refund_total"})
    assert row.refunds[0].refunded_at == _at("2026-11-18T05:00:00Z")


def test_no_refund_line_without_site_value() -> None:
    assert _row({}).refunds == []
    assert _row({"refunded_amount": "refund_total"}, refund_total=0).refunds == []


def test_cancel_reason_only_for_cancelled_and_known_values() -> None:
    fields = {"cancel_reason_code": "who"}
    table = {"status": {"gone": "cancelled"}, "cancel_reason_code": {"auto": "system"}}
    assert _row(fields, table, status="gone").cancel_reason_code == "system"
    # 취소가 아니면 비운다
    assert _row(fields, table).cancel_reason_code is None
    # 값 표에 없는 값은 지어내지 않고 비운다
    assert _row(fields, table, status="gone", who="mystery").cancel_reason_code is None
    # 짝에 칸이 없으면 비운다
    assert _row({}, {"status": {"gone": "cancelled"}}, status="gone").cancel_reason_code is None


def test_payment_failed_only_when_status_table_says_so() -> None:
    table = {"status": {"pay_error": "payment_failed"}}
    assert _row({}, table, status="pay_error", paid=0).payment_status == "failed"
    assert _row({}, table, status="waiting", paid=0).payment_status is None
    assert _row({}, table).payment_status == "succeeded"
