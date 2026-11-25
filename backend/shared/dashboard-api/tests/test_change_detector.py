"""변화 감지기 (설계 `docs/superpowers/specs/2026-11-19-customer-change-polling-design.md` PR 1).

가짜 판매 서버(httpx.MockTransport, 테스트 전용)가 `sales-export`를 `(updated_at, order_id)`
커서로 돌려준다. 이벤트 버스는 기록만 하는 가짜로 바꾼다.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from ax_agent_template import company_connection as cc
from ax_agent_template import tenant
from ax_contracts.schemas import EventEnvelope, EventType
from ax_contracts.schemas.event import OrderCancelledData, PaymentRefundedData
from ax_dashboard_api import change_detector as cd
from ax_dashboard_api.main import app

SHOP = "http://shop.test"
TOKEN = "detector-test-token"


def order(order_id: str, updated_at: str, **extra: Any) -> dict[str, Any]:
    row = {
        "order_id": order_id,
        "buyer_id": "buy_1",
        "status": "paid",
        "created_at": "2026-11-20T00:00:00Z",
        "updated_at": updated_at,
        "items_amount": 30000,
        "shipping_fee": 3000,
        "points_used": 1000,
        "coupon_discount": 2000,
        "payment_status": "succeeded",
        "lines": [],
        "refunds": [],
    }
    row.update(extra)
    return row


class FakeShop:
    """`sales-export`와 `orders/{id}`만 아는 가짜 판매 서버 (테스트 전용)."""

    def __init__(self) -> None:
        self.orders: dict[str, dict[str, Any]] = {}
        self.details: dict[str, dict[str, Any]] = {}
        self.calls: list[str] = []
        self.fail_status: int | None = None
        self.seen_tokens: set[str] = set()

    def put(self, row: dict[str, Any]) -> None:
        self.orders[row["order_id"]] = row

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.calls.append(request.url.path)
        self.seen_tokens.add(request.headers.get("authorization", ""))
        if self.fail_status is not None:
            return httpx.Response(
                self.fail_status, json={"error": {"code": "UPSTREAM_TRUNCATED", "message": "x"}}
            )
        path = request.url.path.removeprefix("/site-adapter")  # 변환기 고객사도 같은 주소
        if path == "/api/shop/sales-export":
            return self._export(request)
        if path.startswith("/api/shop/orders/"):
            order_id = path.rsplit("/", 1)[-1]
            detail = self.details.get(order_id)
            if detail is None:
                return httpx.Response(404, json={"error": {"code": "ORDER_NOT_FOUND"}})
            return httpx.Response(200, json={"data": detail, "meta": {}})
        return httpx.Response(404, json={})

    def _export(self, request: httpx.Request) -> httpx.Response:
        cursor = request.url.params.get("cursor") or ""
        limit = int(request.url.params.get("limit") or 500)
        rows = sorted(self.orders.values(), key=lambda r: (r["updated_at"], r["order_id"]))
        after = [r for r in rows if f"{r['updated_at']}|{r['order_id']}" > cursor]
        page = after[:limit]
        next_cursor = f"{page[-1]['updated_at']}|{page[-1]['order_id']}" if page else cursor
        body = {
            "orders": page,
            "next_cursor": next_cursor or None,
            "has_more": len(after) > limit,
        }
        return httpx.Response(200, json={"data": body, "meta": {}})


@pytest.fixture
def shop(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> FakeShop:
    monkeypatch.setenv(cd.DIR_ENV, str(tmp_path / "detector"))
    monkeypatch.setenv("AX_SHOP_API_URL", SHOP)
    monkeypatch.setenv("DASHBOARD_API_TOKEN", TOKEN)
    monkeypatch.setenv(tenant.TENANT_ENV, "catbooks")
    monkeypatch.delenv(tenant.TENANTS_DIR_ENV, raising=False)
    monkeypatch.delenv(cc.CONNECTION_FILE_ENV, raising=False)
    monkeypatch.delenv(cd.PUBLISH_ENV, raising=False)
    monkeypatch.delenv(cd.SECONDS_ENV, raising=False)
    monkeypatch.delenv(cd.MAX_CALLS_ENV, raising=False)
    monkeypatch.setattr(cc, "_cache", None)
    fake = FakeShop()
    monkeypatch.setattr(cd, "TRANSPORT", httpx.MockTransport(fake.handle))
    return fake


@pytest.fixture
def bus(monkeypatch: pytest.MonkeyPatch) -> list[EventEnvelope]:
    sent: list[EventEnvelope] = []

    async def publish(envelope: EventEnvelope) -> None:
        if all(e.event_id != envelope.event_id for e in sent):
            sent.append(envelope)

    monkeypatch.setattr(cd.events, "publish", publish)
    return sent


def refund(refund_id: str | None, amount: int, at: str) -> dict[str, Any]:
    return {"refund_id": refund_id, "amount": amount, "refunded_at": at}


# ------------------------------------------------------------------ 스위치 기본값


def test_switches_default_off(monkeypatch: pytest.MonkeyPatch) -> None:
    """기본은 꺼짐: 주기 0(돌지 않음), 그림자 모드(버스에 넣지 않음). 지금 동작이 바뀌지 않는다."""
    monkeypatch.delenv(cd.SECONDS_ENV, raising=False)
    monkeypatch.delenv(cd.PUBLISH_ENV, raising=False)
    assert cd.interval_seconds() == 0
    assert cd.publishing() is False
    monkeypatch.setenv(cd.SECONDS_ENV, "5")
    assert cd.interval_seconds() == cd.MIN_SECONDS  # 너무 짧으면 최소로
    monkeypatch.setenv(cd.SECONDS_ENV, "120")
    assert cd.interval_seconds() == 120
    monkeypatch.setenv(cd.SECONDS_ENV, "abc")
    assert cd.interval_seconds() == 0
    monkeypatch.setenv(cd.PUBLISH_ENV, "1")
    assert cd.publishing() is True


# ------------------------------------------------------------------ 처음 켤 때


@pytest.mark.asyncio
async def test_first_run_only_fills_state(shop: FakeShop, bus: list[EventEnvelope]) -> None:
    """처음 켤 때는 끝까지 읽어 상태 표만 채우고 이벤트를 내보내지 않는다."""
    shop.put(order("ord_1", "2026-11-20T01:00:00Z", status="cancelled",
                   cancel_reason_code="buyer"))  # fmt: skip
    shop.put(order("ord_2", "2026-11-20T02:00:00Z",
                   refunds=[refund("rfd_1", 5000, "2026-11-20T02:00:00Z")]))  # fmt: skip

    result = await cd.run_once()

    assert result.error is None and result.baseline is True
    assert result.events == [] and bus == []
    status = cd.status()
    assert status["baseline_at"] is not None
    assert status["cursor_at"] == "2026-11-20T02:00:00Z"
    assert status["orders_tracked"] == 2
    # 상태 표가 채워졌으니 다음 회차에 같은 줄을 다시 봐도 새 변화가 아니다
    again = await cd.run_once()
    assert again.events == [] and again.baseline is False


@pytest.mark.asyncio
async def test_uses_dashboard_api_token(shop: FakeShop, bus: list[EventEnvelope]) -> None:
    await cd.run_once()
    assert shop.seen_tokens == {f"Bearer {TOKEN}"}


# ------------------------------------------------------------------ 환불


@pytest.mark.asyncio
async def test_new_refund_is_shadowed_by_default(shop: FakeShop, bus: list[EventEnvelope]) -> None:
    """그림자 모드(기본): 버스에 넣지 않고 "내보냈을 이벤트"만 상태에 남긴다."""
    shop.put(order("ord_1", "2026-11-20T01:00:00Z"))
    await cd.run_once()

    shop.put(order("ord_1", "2026-11-20T03:00:00Z",
                   refunds=[refund("rfd_9", 5000, "2026-11-20T03:00:00Z")]))  # fmt: skip
    shop.details["ord_1"] = {
        "order_id": "ord_1",
        "payments": [{"payment_id": "pay_7", "status": "succeeded"}],
        "refunds": [{"refund_id": "rfd_9", "payment_id": "pay_7", "amount": 5000}],
    }
    result = await cd.run_once()

    assert bus == []
    assert [e.type for e in result.events] == [EventType.PAYMENT_REFUNDED]
    event = result.events[0]
    assert event.event_id == "chg:catbooks:payment.refunded:ord_1:rfd_9"
    assert event.idempotency_key == event.event_id
    assert event.source == "change-detector" and event.tenant_id == "catbooks"
    assert event.context_id is None
    data = PaymentRefundedData.model_validate(event.data)
    assert (data.payment_id, data.order_id, data.refund_id, data.refund_amount) == (
        "pay_7", "ord_1", "rfd_9", 5000,
    )  # fmt: skip
    status = cd.status()
    assert status["publish"] is False
    assert [s["event_id"] for s in status["shadow"]] == [event.event_id]


@pytest.mark.asyncio
async def test_publish_mode_sends_once(
    shop: FakeShop, bus: list[EventEnvelope], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(cd.PUBLISH_ENV, "1")
    shop.put(order("ord_1", "2026-11-20T01:00:00Z"))
    await cd.run_once()
    shop.put(order("ord_1", "2026-11-20T03:00:00Z",
                   refunds=[refund("rfd_9", 5000, "2026-11-20T03:00:00Z")]))  # fmt: skip
    shop.details["ord_1"] = {"order_id": "ord_1", "payments": [{"payment_id": "pay_7"}]}

    await cd.run_once()
    await cd.run_once()  # 커서 뒤에 바뀐 것이 없다

    assert [e.event_id for e in bus] == ["chg:catbooks:payment.refunded:ord_1:rfd_9"]
    assert cd.status()["published_total"] == 1


@pytest.mark.asyncio
async def test_refund_without_id_uses_cumulative_amount(
    shop: FakeShop, bus: list[EventEnvelope]
) -> None:
    """환불 id가 없으면 누적 금액 증가분으로 보고, 결제 id가 없으면 재무와 같은 규칙으로 만든다."""
    shop.put(order("ord_2", "2026-11-20T01:00:00Z",
                   refunds=[refund(None, 1000, "2026-11-20T01:00:00Z")]))  # fmt: skip
    await cd.run_once()
    shop.put(order("ord_2", "2026-11-20T04:00:00Z", refunds=[
        refund(None, 1000, "2026-11-20T01:00:00Z"),
        refund(None, 2500, "2026-11-20T04:00:00Z"),
    ]))  # fmt: skip
    shop.details["ord_2"] = {"order_id": "ord_2", "payments": []}

    result = await cd.run_once()

    assert len(result.events) == 1
    data = result.events[0].data
    assert data["refund_id"] == "rfd_of_ord_2_3500"
    assert data["refund_amount"] == 2500
    assert data["payment_id"] == "pay_of_ord_2"


# ------------------------------------------------------------------ 취소


@pytest.mark.asyncio
async def test_cancel_with_reason_makes_order_cancelled(
    shop: FakeShop, bus: list[EventEnvelope]
) -> None:
    shop.put(order("ord_3", "2026-11-20T01:00:00Z"))
    await cd.run_once()
    shop.put(order("ord_3", "2026-11-20T05:00:00Z", status="cancelled",
                   cancel_reason_code="seller",
                   refunds=[refund("rfd_3", 4000, "2026-11-20T05:00:00Z")]))  # fmt: skip
    shop.details["ord_3"] = {"order_id": "ord_3", "payments": [{"payment_id": "pay_3"}]}

    result = await cd.run_once()

    by_type = {e.type: e for e in result.events}
    cancelled = by_type[EventType.ORDER_CANCELLED]
    assert cancelled.event_id == "chg:catbooks:order.cancelled:ord_3"
    data = OrderCancelledData.model_validate(cancelled.data)
    # 결제 금액 30000 + 3000 - 1000 - 2000 = 30000, 이미 환불된 4000을 뺀다
    assert (data.order_id, data.reason_code, data.cancelled_amount) == ("ord_3", "seller", 26000)
    assert EventType.PAYMENT_REFUNDED in by_type


@pytest.mark.asyncio
async def test_cancel_without_reason_is_skipped_and_counted(
    shop: FakeShop, bus: list[EventEnvelope]
) -> None:
    """취소 사유가 없으면 이벤트를 만들지 않고 건너뜀 건수만 남긴다(지어내지 않는다)."""
    shop.put(order("ord_4", "2026-11-20T01:00:00Z"))
    await cd.run_once()
    shop.put(order("ord_4", "2026-11-20T06:00:00Z", status="cancelled"))

    result = await cd.run_once()

    assert result.events == []
    assert result.skipped_no_reason == 1
    assert cd.status()["skipped_no_reason"] == 1
    # 이미 취소로 본 주문이 다시 나와도 또 세지 않는다
    shop.put(order("ord_4", "2026-11-20T07:00:00Z", status="cancelled"))
    await cd.run_once()
    assert cd.status()["skipped_no_reason"] == 1


@pytest.mark.asyncio
async def test_order_new_after_baseline_already_cancelled(
    shop: FakeShop, bus: list[EventEnvelope]
) -> None:
    """처음 켠 뒤 새로 보인 주문은 직전 상태가 없으니 취소 아님에서 바뀐 것으로 본다."""
    shop.put(order("ord_1", "2026-11-20T01:00:00Z"))
    await cd.run_once()
    shop.put(order("ord_5", "2026-11-20T02:00:00Z", status="cancelled",
                   cancel_reason_code="system"))  # fmt: skip

    result = await cd.run_once()

    assert [e.event_id for e in result.events] == ["chg:catbooks:order.cancelled:ord_5"]


# ------------------------------------------------------------------ 실패·한도·연결 바뀜


@pytest.mark.asyncio
async def test_upstream_failure_keeps_cursor(shop: FakeShop, bus: list[EventEnvelope]) -> None:
    shop.put(order("ord_1", "2026-11-20T01:00:00Z"))
    await cd.run_once()
    before = cd.status()["cursor_at"]
    shop.put(
        order("ord_1", "2026-11-20T03:00:00Z", refunds=[refund("r", 1, "2026-11-20T03:00:00Z")])
    )
    shop.fail_status = 503

    result = await cd.run_once()

    assert result.error is not None and result.events == []
    status = cd.status()
    assert status["cursor_at"] == before
    assert status["last_error"] is not None
    # 다시 살아나면 같은 변화를 이어서 본다
    shop.fail_status = None
    shop.details["ord_1"] = {"order_id": "ord_1", "payments": [{"payment_id": "p"}]}
    again = await cd.run_once()
    assert [e.event_id for e in again.events] == ["chg:catbooks:payment.refunded:ord_1:r"]
    assert cd.status()["last_error"] is None


@pytest.mark.asyncio
async def test_publish_failure_does_not_advance(
    shop: FakeShop, monkeypatch: pytest.MonkeyPatch
) -> None:
    """버스에 못 넣으면 그 쪽의 커서·상태를 저장하지 않아 다음 회차에 같은 event_id로 다시 낸다."""
    monkeypatch.setenv(cd.PUBLISH_ENV, "1")
    sent: list[str] = []
    broken = {"on": True}

    async def publish(envelope: EventEnvelope) -> None:
        if broken["on"]:
            raise ConnectionError("redis down")
        sent.append(envelope.event_id)

    monkeypatch.setattr(cd.events, "publish", publish)
    shop.put(order("ord_1", "2026-11-20T01:00:00Z"))
    await cd.run_once()
    shop.put(order("ord_1", "2026-11-20T03:00:00Z", status="cancelled",
                   cancel_reason_code="buyer"))  # fmt: skip

    failed = await cd.run_once()
    assert failed.error is not None
    broken["on"] = False
    await cd.run_once()

    assert sent == ["chg:catbooks:order.cancelled:ord_1"]


@pytest.mark.asyncio
async def test_max_calls_stops_and_resumes(
    shop: FakeShop, bus: list[EventEnvelope], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv(cd.MAX_CALLS_ENV, "2")
    monkeypatch.setattr(cd, "PAGE_LIMIT", 1)
    for n in range(5):
        shop.put(order(f"ord_{n}", f"2026-11-20T0{n}:00:00Z"))

    first = await cd.run_once()
    assert first.calls == 2 and first.finished is False and first.error is None
    assert cd.status()["baseline_at"] is None  # 아직 끝까지 못 읽었다
    while not (await cd.run_once()).finished:
        pass
    status = cd.status()
    assert status["orders_tracked"] == 5 and status["baseline_at"] is not None
    assert bus == []


@pytest.mark.asyncio
async def test_connection_change_restarts_quietly(
    shop: FakeShop, bus: list[EventEnvelope], monkeypatch: pytest.MonkeyPatch
) -> None:
    """연결 주소가 바뀌면 상태를 새로 시작하고 처음 켤 때처럼 조용히 채운다."""
    shop.put(order("ord_1", "2026-11-20T01:00:00Z"))
    await cd.run_once()
    monkeypatch.setenv("AX_SHOP_API_URL", "http://other-shop.test")
    shop.put(order("ord_1", "2026-11-20T02:00:00Z", status="cancelled",
                   cancel_reason_code="buyer"))  # fmt: skip

    result = await cd.run_once()

    assert result.baseline is True and result.events == []


@pytest.mark.asyncio
async def test_adapter_connection_is_read_after_adapter_fix(
    shop: FakeShop, bus: list[EventEnvelope], monkeypatch: pytest.MonkeyPatch
) -> None:
    """변환기 보완(PR 2: 커서·`updated_since`·환불 줄) 뒤에는 변환기 고객사도 읽는다.

    변환기 주문 상세에는 결제 id가 없으니(사이트에 없음) 환불마다 상세를 다시 읽지 않고
    재무와 같은 규칙(`pay_of_<주문>`)으로 만든다. 상세 읽기는 변환기에서 고객사 전체 읽기다.
    """
    adapter = cc.Connection(
        shop_api_url="http://dashboard-api:8000/site-adapter",
        verified_at=None,
        updated_at=None,
        updated_by="tester",
        kind="adapter",
        adapter={"base_url": "https://nubi.example"},
        tenant="nubi",
    )
    monkeypatch.setattr(cc, "read_connection", lambda company_id=None: adapter)
    monkeypatch.setenv(tenant.TENANT_ENV, "nubi")
    shop.put(order("NB-1", "2026-11-20T01:00:00Z"))

    first = await cd.run_once()
    assert first.error is None and first.baseline is True
    assert shop.calls == ["/site-adapter/api/shop/sales-export"]

    shop.put(order("NB-1", "2026-11-20T03:00:00Z", status="cancelled", cancel_reason_code="buyer",
                   refunds=[refund(None, 4000, "2026-11-20T03:00:00Z")]))  # fmt: skip
    shop.calls.clear()
    result = await cd.run_once()

    assert result.error is None
    assert shop.calls == ["/site-adapter/api/shop/sales-export"]  # 주문 상세는 읽지 않는다
    assert [e.event_id for e in result.events] == [
        "chg:nubi:payment.refunded:NB-1:rfd_of_NB-1_4000",
        "chg:nubi:order.cancelled:NB-1",
    ]
    assert result.events[0].data["payment_id"] == "pay_of_NB-1"


@pytest.mark.asyncio
async def test_each_connected_company_is_read_on_its_own(
    shop: FakeShop, bus: list[EventEnvelope], monkeypatch: pytest.MonkeyPatch
) -> None:
    """회사별(로드맵 7단계): 캣북스는 예전 그대로, 연결된 다른 회사는 그 회사 변환기를 따로 읽는다.

    상태 파일·이벤트 회사 값·부르는 회사 머리글이 회사마다 따로다.
    한 회사가 실패해도 다른 회사는 돈다.
    """
    nubi = FakeShop()
    adapter = cc.Connection(
        shop_api_url="http://dashboard-api:8000/site-adapter/nubi",
        verified_at=None,
        updated_at=None,
        updated_by="tester",
        kind="adapter",
        adapter={"base_url": "https://nubi.example"},
        tenant="nubi",
    )
    monkeypatch.setattr(
        cc, "read_connection", lambda company_id=None: adapter if company_id == "nubi" else None
    )
    monkeypatch.setattr(cd, "company_ids", lambda: ["catbooks", "nubi", "studyon"])
    headers: list[tuple[str, str]] = []

    def route(request: httpx.Request) -> httpx.Response:
        if request.url.path.startswith("/site-adapter/nubi/"):
            headers.append(("nubi", request.headers.get(cc.CALLER_COMPANY_HEADER, "")))
            inner = request.copy_with(url=request.url.copy_with(
                path=request.url.path.removeprefix("/site-adapter/nubi")))  # fmt: skip
            return nubi.handle(inner)
        return shop.handle(request)

    monkeypatch.setattr(cd, "TRANSPORT", httpx.MockTransport(route))
    assert cd.watched_companies() == ["catbooks", "nubi"]  # 연결 없는 studyon은 읽지 않는다

    shop.put(order("ord_1", "2026-11-20T01:00:00Z"))
    nubi.put(order("NB-1", "2026-11-20T01:00:00Z"))
    first = await cd.run_all()
    assert first["catbooks"].baseline and first["nubi"].baseline
    assert (Path(cd._state_path("catbooks")).name, Path(cd._state_path("nubi")).name) == (
        "state.json",
        "state.nubi.json",
    )

    nubi.put(order("NB-1", "2026-11-20T03:00:00Z", status="cancelled", cancel_reason_code="buyer"))
    shop.fail_status = 503  # 캣북스가 실패해도 누비투어는 돈다
    result = await cd.run_all()
    assert result["catbooks"].error is not None
    assert [e.event_id for e in result["nubi"].events] == ["chg:nubi:order.cancelled:NB-1"]
    assert result["nubi"].events[0].tenant_id == "nubi"
    assert all(company == value == "nubi" for company, value in headers)
    status = cd.status()
    assert set(status["companies"]) == {"catbooks", "nubi"}
    assert status["companies"]["nubi"]["last_error"] is None
    assert status["companies"]["catbooks"]["last_error"] == status["last_error"]


@pytest.mark.asyncio
async def test_no_tenant_is_not_read(
    shop: FakeShop, bus: list[EventEnvelope], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(tenant.TENANT_ENV, raising=False)
    monkeypatch.setattr(tenant, "current", lambda: None)

    result = await cd.run_once()

    assert shop.calls == [] and result.error == cd.NO_TENANT


# ------------------------------------------------------------------ 상태 주소


def test_status_route_needs_human(shop: FakeShop) -> None:
    client = TestClient(app)
    res = client.get("/api/dashboard/change-detector/status")
    assert res.status_code == 200
    data = res.json()["data"]
    assert data["enabled"] is False and data["publish"] is False
    assert data["last_success_at"] is None and data["skipped_no_reason"] == 0
    anonymous = TestClient(app)
    anonymous.headers.pop("authorization")
    assert anonymous.get("/api/dashboard/change-detector/status").status_code == 401
