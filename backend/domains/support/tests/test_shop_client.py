"""shop-api 연결: 주문 조회로 클레임을 판정하고, 지급 뒤 현금 환불 API를 부른다."""

import json
from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from ax_contracts.schemas import ExecuteDecision
from support_agent import claim_handlers, handlers, shop_client
from support_agent.claims import ClaimStore
from support_agent.main import app
from support_agent.store import InquiryStore

ORDER_ID = "ord_0001"
PAYMENT_ID = "pay_0001"
SHOP_SKILLS = {"create_claim": "shop-api"}  # 나머지는 대시보드가 부른다


def shop_order(**changes: Any) -> dict[str, Any]:
    """shop-api `GET /orders/{id}` 응답의 `data` (배송 전, 품목 1개)."""
    order = {
        "order_id": ORDER_ID,
        "buyer_id": "buy_0009",
        "status": "paid",
        "total_amount": 23000,
        "refunded_amount": 0,
        "shipping_fee": 3000,
        "points_used": 0,
        "delivered_at": None,
        "created_at": "2026-10-20T01:00:00Z",
        "items": [
            {
                "order_item_id": "oit_0001",
                "product_id": "prd_0001",
                "seller_id": "sel_0001",
                "title": "고양이의 하루",
                "qty": 1,
                "unit_price": 20000,
                "points_share": 0,
                "paid_amount": 20000,
            }
        ],
        "payments": [
            {
                "payment_id": PAYMENT_ID,
                "amount": 23000,
                "method_code": "card",
                "status": "succeeded",
                "failure_code": None,
                "created_at": "2026-10-20T01:00:05Z",
            }
        ],
    }
    return {**order, **changes}


class FakeShop:
    """shop-api 대역. 보낸 요청을 기록하고, 같은 환불 키는 이전 환불을 돌려준다."""

    def __init__(self, order: dict[str, Any] | None = None) -> None:
        self.order = order if order is not None else shop_order()
        self.requests: list[httpx.Request] = []
        self.refund_failure: tuple[int, str] | None = None
        self.refunds: dict[str, dict[str, Any]] = {}
        self.down = False
        self.pending_human = False  # 사이트 변환기: 환불을 할 일함에 넣고 사람 반영 대기로 답한다

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.down:
            raise httpx.ConnectError("refused")
        path = request.url.path
        if request.method == "GET" and path == f"/api/shop/orders/{ORDER_ID}":
            return httpx.Response(200, json={"data": self.order, "meta": {}})
        if request.method == "GET" and path.startswith("/api/shop/orders/"):
            error = {"code": "ORDER_NOT_FOUND", "message": "주문을 찾을 수 없다"}
            return httpx.Response(404, json={"error": error})
        if request.method == "POST" and path == f"/api/shop/payments/{PAYMENT_ID}/refund":
            body = json.loads(request.content)
            if self.refund_failure:
                status, code = self.refund_failure
                return httpx.Response(status, json={"error": {"code": code, "message": "x"}})
            if self.pending_human:
                data = {
                    "payment_id": PAYMENT_ID,
                    "refund_amount": body["refund_amount"],
                    "applied": "pending_human",
                    "handoff_task_id": "hof_site1",
                }
                return httpx.Response(200, json={"data": data, "meta": {"source": "site_adapter"}})
            self.refunds.setdefault(body["idempotency_key"], body)
            data: dict[str, Any] = {"refund_id": "rfd_0001"}
            if body.get("cancel_order"):  # catbooks-demo #60: 배송 전 전액 환불이면 주문도 취소
                data["order_cancelled"] = True
            return httpx.Response(201, json={"data": data, "meta": {}})
        return httpx.Response(500)

    def refund_calls(self) -> list[httpx.Request]:
        return [r for r in self.requests if r.method == "POST"]


@pytest.fixture
def shop() -> FakeShop:
    fake = FakeShop()
    shop_client.use_client(httpx.AsyncClient(transport=httpx.MockTransport(fake.handle)))
    return fake


@pytest.fixture(autouse=True)
def _fresh(monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setenv("AGENT_TEMPLATE_MOCK", "1")
    monkeypatch.delenv("SUPPORT_MOCK", raising=False)
    monkeypatch.delenv("SUPPORT_ORDER_SOURCE", raising=False)
    monkeypatch.setattr(handlers, "STORE", InquiryStore())
    monkeypatch.setattr(claim_handlers, "CLAIMS", ClaimStore())
    yield
    shop_client.use_client(None)


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def call(
    client: TestClient,
    skill: str,
    input: dict[str, Any] | None = None,
    untrusted: dict[str, Any] | None = None,
    key: str | None = None,
    context: str = "ctx_shop_abc123",
    requester: str | None = None,
) -> dict[str, Any]:
    body = {
        "meta": {
            "context_id": context,
            "token": f"mock:{requester or SHOP_SKILLS.get(skill, 'dashboard-api')}",
            "path": [],
            "deadline": (datetime.now(UTC) + timedelta(seconds=30)).isoformat(),
            "idempotency_key": key,
        },
        "skill": skill,
        "input": input or {},
        "untrusted": untrusted or {},
    }
    return client.post("/a2a", json=body).json()


def create(
    client: TestClient, order_id: str = ORDER_ID, buyer_id: str = "buy_0009"
) -> dict[str, Any]:
    return call(
        client,
        "create_claim",
        {
            "buyer_id": buyer_id,
            "customer_name": "고객",
            "order_id": order_id,
            "type": "cancel",
            "reason": "change_of_mind",
        },
        {"description": "마음이 바뀌었어요"},
        key="k-1",
    )


def process(client: TestClient) -> dict[str, Any]:
    return call(client, "process_claim_queue", {}, key="k-p", context="ctx_req_0009")["output"]


@pytest.fixture
def executed(monkeypatch: pytest.MonkeyPatch) -> None:
    async def run(*args: Any, **kwargs: Any) -> ExecuteDecision:
        return ExecuteDecision(execution_id="exe_1", grade="B", status="executed")

    monkeypatch.setattr(claim_handlers, "execute", run)


# ---------------------------------------------------------------- 주문 변환
def test_shop_order_is_mapped_to_the_judging_model() -> None:
    order = shop_client.to_order(shop_order(delivered_at="2026-10-22T00:00:00Z"))
    assert order is not None
    assert (order.id, order.buyer_id, order.status) == (ORDER_ID, "buy_0009", "delivered")
    assert order.payment_id == PAYMENT_ID and order.shipping_fee == 3000
    assert order.paid_at == datetime(2026, 10, 20, 1, 0, 5, tzinfo=UTC)
    assert [(i.id, i.paid_amount, i.title) for i in order.items] == [
        ("oit_0001", 20000, "고양이의 하루")
    ]
    before_delivery = shop_client.to_order(shop_order())
    assert before_delivery is not None and before_delivery.status == "preparing"


def test_coupon_fields_are_mapped_and_missing_ones_default_to_zero() -> None:
    """shop-api #280 이후 주문은 쿠폰 할인 필드를 담는다. 없던 주문이나 null은 0으로 읽는다."""
    base = shop_order()
    item = {**base["items"][0], "coupon_share": 2000, "paid_amount": 18000}
    order = shop_client.to_order(shop_order(coupon_discount=2000, items=[item]))
    assert order is not None and order.coupon_discount == 2000
    assert [(i.paid_amount, i.coupon_share) for i in order.items] == [(18000, 2000)]
    plain = shop_client.to_order(shop_order())  # 쿠폰 필드가 없는 주문
    assert plain is not None and plain.coupon_discount == 0
    assert [i.coupon_share for i in plain.items] == [0]
    nulls = shop_client.to_order(shop_order(coupon_discount=None))
    assert nulls is not None and nulls.coupon_discount == 0


def test_product_id_is_mapped_and_missing_one_stays_none() -> None:
    """책별 집계의 열쇠. product_id를 옮기고, 없으면 제목으로 추측하지 않고 None이다."""
    order = shop_client.to_order(shop_order())
    assert order is not None and [i.product_id for i in order.items] == ["prd_0001"]
    old_item = {k: v for k, v in shop_order()["items"][0].items() if k != "product_id"}
    old = shop_client.to_order(shop_order(items=[old_item]))
    assert old is not None and [i.product_id for i in old.items] == [None]


def test_claim_lines_carry_the_product_id() -> None:
    from support_agent import claim_rules
    from support_agent.claim_schemas import ClaimLine

    order = shop_client.to_order(shop_order(delivered_at="2026-10-22T00:00:00Z"))
    assert order is not None
    decision = claim_rules.decide(
        order,
        "return",
        "defect",
        items=None,
        evidence_count=1,
        unsealed=False,
        prior_claims=0,
        now=datetime(2026, 10, 23, tzinfo=UTC),
    )
    assert [line.product_id for line in decision.lines] == ["prd_0001"]
    line = ClaimLine(**vars(decision.lines[0]))
    assert line.product_id == "prd_0001"
    legacy = {k: v for k, v in vars(decision.lines[0]).items() if k != "product_id"}
    assert ClaimLine(**legacy).product_id is None  # 옛 클레임 기록은 None으로 읽는다


def test_unpaid_orders_are_not_mapped() -> None:
    assert shop_client.to_order(shop_order(status="pending_payment", payments=[])) is None
    failed = [{**shop_order()["payments"][0], "status": "failed"}]
    assert shop_client.to_order(shop_order(status="payment_failed", payments=failed)) is None


def test_order_source_defaults_to_seed_in_mock_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    assert shop_client.order_source() == "seed"
    monkeypatch.setenv("SUPPORT_ORDER_SOURCE", "shop-api")
    assert shop_client.order_source() == "shop-api"
    monkeypatch.setenv("SUPPORT_ORDER_SOURCE", "seed")
    monkeypatch.delenv("AGENT_TEMPLATE_MOCK")
    monkeypatch.delenv("SUPPORT_MOCK", raising=False)
    assert shop_client.order_source() == "seed"
    monkeypatch.delenv("SUPPORT_ORDER_SOURCE")
    assert shop_client.order_source() == "shop-api"  # 목 모드가 아니면 shop-api


# ---------------------------------------------------------------- 클레임 접수
def test_claim_is_judged_from_the_shop_order(client: TestClient, shop: FakeShop) -> None:
    result = create(client)
    claim = result["output"]
    assert result["status"] == "completed"
    assert (claim["order_id"], claim["payment_id"], claim["buyer_id"]) == (
        ORDER_ID,
        PAYMENT_ID,
        "buy_0009",
    )
    assert claim["lines"][0]["order_item_id"] == "oit_0001"
    assert claim["expected_refund"] == 23000  # 배송 전 취소: 품목 + 배송비
    assert [r.url.path for r in shop.requests] == [f"/api/shop/orders/{ORDER_ID}"]


def test_unknown_unpaid_or_someone_elses_order_is_not_found(
    client: TestClient, shop: FakeShop
) -> None:
    assert create(client, order_id="ord_9999")["outcome"] == "not_found"
    assert create(client, buyer_id="buy_0001")["outcome"] == "not_found"
    shop.order = shop_order(status="pending_payment", payments=[])
    assert create(client)["outcome"] == "not_found"
    assert call(client, "list_claims")["output"]["items"] == []


def test_shop_api_down_fails_without_creating_a_claim(client: TestClient, shop: FakeShop) -> None:
    shop.down = True
    result = create(client)
    assert result["status"] == "failed" and result["outcome"] == "shop_unavailable"
    assert call(client, "list_claims")["output"]["items"] == []


# ---------------------------------------------------------------- 현금 환불
def test_paid_claim_refunds_cash_through_shop_api(
    client: TestClient, shop: FakeShop, executed: None
) -> None:
    create(client)
    run = process(client)
    assert (run["paid"], run["held"]) == (1, 0)
    [refund] = shop.refund_calls()
    body = json.loads(refund.content)
    assert body == {
        "refund_amount": 23000,
        "idempotency_key": "ctx_shop_abc123:refund_request:claim:1",  # 클레임 접수 사건 기준
        "context_id": "ctx_shop_abc123",
        "cancel_order": True,  # 배송 전 취소 클레임이라 주문도 취소해 달라고 한다
    }
    assert refund.headers["authorization"] == "Bearer mock:support"
    claim = call(client, "get_claim", {"claim_id": 1})["output"]
    assert claim["status"] == "completed"
    assert claim["audit"][-1]["detail"]["order_cancelled"] is True


@pytest.mark.asyncio
async def test_refund_sends_cancel_order_only_when_asked(shop: FakeShop) -> None:
    await shop_client.refund(PAYMENT_ID, 1000, "k-a", "ctx_shop_x")
    data = await shop_client.refund(PAYMENT_ID, 1000, "k-b", "ctx_shop_x", cancel_order=True)
    first, second = (json.loads(r.content) for r in shop.refund_calls())
    assert "cancel_order" not in first
    assert second["cancel_order"] is True
    assert data["order_cancelled"] is True


def test_failed_refund_keeps_the_claim_unpaid_and_a_retry_pays_once(
    client: TestClient, shop: FakeShop, executed: None
) -> None:
    create(client)
    shop.refund_failure = (409, "REFUND_EXCEEDS_PAYMENT")
    run = process(client)
    assert (run["paid"], run["held"]) == (0, 1)
    assert run["results"][0]["detail"] == "shop_refund_failed:REFUND_EXCEEDS_PAYMENT"
    assert "REFUND_EXCEEDS_PAYMENT" in run["results"][0]["blockers"][0]["message"]
    assert call(client, "get_claim", {"claim_id": 1})["output"]["status"] == "approved"

    shop.refund_failure = None
    again = process(client)
    assert (again["paid"], again["held"]) == (1, 0)
    keys = {json.loads(r.content)["idempotency_key"] for r in shop.refund_calls()}
    assert keys == {"ctx_shop_abc123:refund_request:claim:1"}  # 재시도도 같은 키


def test_pending_human_refund_is_held_not_paid(
    client: TestClient, shop: FakeShop, executed: None
) -> None:
    """사이트 변환기가 환불을 할 일함에 넣으면(`applied: pending_human`) 돈이 나간 것이 아니다.

    지급 완료·환불액으로 적지 않고 보류로 둔다. 다시 처리해도 같은 키로 보낸다.
    """
    create(client)
    shop.pending_human = True
    run = process(client)
    assert (run["paid"], run["held"]) == (0, 1)
    assert run["results"][0]["detail"] == "shop_refund_pending_human:hof_site1"
    assert "사이트 반영 대기" in run["results"][0]["blockers"][0]["message"]
    claim = call(client, "get_claim", {"claim_id": 1})["output"]
    assert claim["status"] == "approved"
    assert not claim.get("refund_paid")
    again = process(client)
    assert (again["paid"], again["held"]) == (0, 1)
    keys = {json.loads(r.content)["idempotency_key"] for r in shop.refund_calls()}
    assert keys == {"ctx_shop_abc123:refund_request:claim:1"}


def test_unreachable_shop_api_holds_the_refund(
    client: TestClient, shop: FakeShop, executed: None
) -> None:
    create(client)
    shop.down = True
    run = process(client)
    assert run["held"] == 1 and run["results"][0]["detail"] == "shop_refund_failed:UNAVAILABLE"
    assert call(client, "get_claim", {"claim_id": 1})["output"]["status"] == "approved"


def test_seed_orders_are_paid_without_calling_shop_api(
    client: TestClient, executed: None, monkeypatch: pytest.MonkeyPatch
) -> None:
    """목 모드의 시드 주문은 결제 ID가 없어 환불 API를 부르지 않는다."""
    result = call(
        client,
        "create_claim",
        {
            "buyer_id": "buy_0002",
            "customer_name": "고객",
            "order_id": "102",
            "type": "cancel",
            "reason": "change_of_mind",
        },
        key="k-seed",
    )
    assert result["output"]["payment_id"] is None
    assert process(client)["paid"] == 1
