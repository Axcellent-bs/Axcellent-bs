"""책(상품)별 클레임·문의 집계: 순수 코드, 문의 서비스 쪽 주문별 건수, skill 응답."""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

from ax_contracts.schemas import AgentRequest, AgentResult, TaskStatus
from support_agent import claim_handlers, handlers, inquiry_order_counts, orders, shop_client
from support_agent.book_issue_schemas import BookIssueInput
from support_agent.book_issue_stats import OrderInquiry, book_issue_report
from support_agent.claim_schemas import Claim, ClaimLine
from support_agent.claims import ClaimStore
from support_agent.main import app
from support_agent.schemas import Analysis, Classification, Draft, Validation
from support_agent.store import InquiryStore

NOW = datetime(2026, 11, 14, 12, 0, tzinfo=UTC)


def analysis(category: str) -> Analysis:
    return Analysis(
        classification=Classification(
            intent_ko="문의",
            category=category,
            sentiment="calm",
            urgency="medium",
            search_query=category,
        ),
        drafts=[Draft(style="concise", reply="답변", validation=Validation(passed=True))],
        timings_ms={"classify": 100, "draft": 200},
    )


def line(item: str, product: str | None, title: str, qty: int = 1, cash: int = 10000) -> ClaimLine:
    return ClaimLine(
        order_item_id=item,
        title=title,
        qty=qty,
        requested_qty=qty,
        unit_price=cash // qty,
        refund_cash=cash,
        points_back=0,
        product_id=product,
    )


def claim(
    claim_id: int,
    lines: list[ClaimLine],
    *,
    days_ago: float = 1,
    type_: str = "return",
    reason: str = "defect",
    received: bool = False,
    paid: int = 0,
) -> Claim:
    at = (NOW - timedelta(days=days_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")
    return Claim(
        id=claim_id,
        context_id=f"ctx_{claim_id}",
        order_id=f"ord_{claim_id}",
        order_no=f"ord_{claim_id}",
        buyer_id="buy_0001",
        customer="구매자",
        type=type_,  # type: ignore[arg-type]
        reason=reason,  # type: ignore[arg-type]
        status="completed" if paid else "needs_review",
        created_at=at,
        updated_at=at,
        outcome="needs_review",
        resolution="",
        requires_return=type_ != "cancel",
        lines=lines,
        return_received_at=at if received else None,
        refund_paid=paid,
    )


def t_claim(
    claim_id: int,
    product: str,
    *,
    cash: int = 10000,
    received: bool = False,
    paid: int = 0,
) -> Claim:
    return claim(
        claim_id, [line(f"i{claim_id}", product, "책", 1, cash)], received=received, paid=paid
    )


def report(claims: list[Claim], **query: Any):
    return book_issue_report(claims, NOW, BookIssueInput(**query))


def test_claims_are_grouped_by_product_id_not_by_title() -> None:
    result = report(
        [
            claim(1, [line("a", "prd_1", "고양이의 하루")]),
            claim(2, [line("b", "prd_1", "고양이의 하루 (개정판)")], type_="exchange"),
            claim(3, [line("c", "prd_2", "고양이의 하루")], reason="change_of_mind"),
        ]
    )
    rows = {r.product_id: r for r in result.items}
    assert rows["prd_1"].claim_count == 2
    assert rows["prd_1"].claims_by_type == {"return": 1, "exchange": 1}
    assert rows["prd_1"].title == "고양이의 하루 (개정판)"  # 가장 최근 접수의 제목
    assert rows["prd_2"].claim_count == 1 and rows["prd_2"].defect_claim_count == 0
    assert [r.product_id for r in result.items] == ["prd_1", "prd_2"]  # 많은 순


def test_one_claim_counts_once_per_product_even_with_two_lines() -> None:
    result = report([claim(1, [line("a", "prd_1", "책", 1), line("b", "prd_1", "책", 2, 20000)])])
    assert result.items[0].claim_count == 1


def test_return_qty_and_refund_only_count_received_and_paid_claims() -> None:
    result = report(
        [
            claim(1, [line("a", "prd_1", "책", 2, 20000)], received=True, paid=20000),
            claim(2, [line("b", "prd_1", "책", 1, 10000)]),  # 입고·지급 전
        ]
    )
    row = result.items[0]
    assert (row.claim_count, row.return_qty, row.refund_cash_total) == (2, 2, 20000)
    assert "불량·파손 등 2건" in row.reason and "환불 20,000원" in row.reason


def test_claims_without_product_id_are_reported_not_guessed() -> None:
    result = report(
        [claim(1, [line("a", None, "옛 클레임 책")]), claim(2, [line("b", "prd_1", "책")])]
    )
    assert [r.product_id for r in result.items] == ["prd_1"]
    assert result.claims_without_product == 1 and result.total_claims == 2
    assert any("상품 ID가 없어" in m for m in result.missing)


def test_period_window_and_filters() -> None:
    claims = [
        claim(1, [line("a", "prd_1", "책")], days_ago=0.5),
        claim(2, [line("b", "prd_1", "책")], days_ago=5),
        claim(3, [line("c", "prd_2", "다른 책")], days_ago=40),
    ]
    assert report(claims, period="day").items[0].claim_count == 1
    assert report(claims, period="week").items[0].claim_count == 2
    month = report(claims, period="month")
    assert month.total_claims == 2 and [r.product_id for r in month.items] == ["prd_1"]
    assert report(claims, min_claims=2).items[0].product_id == "prd_1"
    assert report(claims, min_claims=3).items == []
    assert report(claims, product_ids=["prd_9"]).items == []


def test_rate_and_sold_qty_are_none_and_explained() -> None:
    result = report([claim(1, [line("a", "prd_1", "책")])])
    row = result.items[0]
    assert row.sold_qty is None and row.claim_rate is None
    assert any("claim_rate" in m for m in result.missing)
    assert not result.sales_read and result.sales_orders_scanned is None


def test_claim_rate_is_claimed_qty_over_sold_qty() -> None:
    claims = [
        claim(1, [line("a", "prd_1", "책", 1)]),
        claim(2, [line("b", "prd_1", "책", 1)]),
    ]
    result = book_issue_report(claims, NOW, BookIssueInput(), sold={"prd_1": 40})
    row = result.items[0]
    assert (row.claim_qty, row.sold_qty, row.claim_rate) == (2, 40, 0.05)
    assert "판매 40권 중 2권 클레임(5.0%)" in row.reason
    assert result.sales_read
    assert not any("읽지 않아" in m for m in result.missing)
    assert any("1을 넘을 수 있다" in m for m in result.missing)  # 접수 기간 기준 비율임을 알린다


def test_claim_rate_can_exceed_one_and_is_none_without_sales() -> None:
    claims = [
        claim(1, [line("a", "prd_1", "책", 3, 30000)]),
        claim(2, [line("b", "prd_2", "책 B")]),
    ]
    result = book_issue_report(claims, NOW, BookIssueInput(), sold={"prd_1": 2})
    rows = {r.product_id: r for r in result.items}
    assert rows["prd_1"].claim_rate == 1.5  # 오래전 주문의 클레임이 들어오면 1을 넘을 수 있다
    assert rows["prd_2"].sold_qty == 0 and rows["prd_2"].claim_rate is None
    assert any("판매가 없어 비율을 내지 못한 상품 1개" in m for m in result.missing)


def test_inquiry_only_product_with_sales_has_zero_rate() -> None:
    result = book_issue_report(
        [],
        NOW,
        BookIssueInput(),
        [OrderInquiry("ord_1", 1, {"payment": 1}, (("prd_1", "책"),))],
        sold={"prd_1": 10},
    )
    row = result.items[0]
    assert (row.claim_qty, row.sold_qty, row.claim_rate) == (0, 10, 0.0)


# ------------------------------------------------------------ 판매 수량 읽기 (주문 목록)
def _order(order_id: str, items: list[tuple[str, int]], *, paid: bool = True) -> dict[str, Any]:
    return {
        "order_id": order_id,
        "status": "paid" if paid else "payment_failed",
        "payments": [{"status": "succeeded" if paid else "failed"}],
        "items": [{"product_id": p, "qty": q} for p, q in items],
    }


def _shop_pages(pages: list[list[dict[str, Any]]], seen: list[Any] | None = None) -> None:
    import httpx

    def handle(request: httpx.Request) -> httpx.Response:
        if seen is not None:
            seen.append(dict(request.url.params))
        if request.url.path != "/api/shop/orders":
            return httpx.Response(404)
        cursor = request.url.params.get("cursor")
        index = int(cursor) if cursor else 0
        nxt = str(index + 1) if index + 1 < len(pages) else None
        return httpx.Response(200, json={"data": pages[index], "meta": {"next_cursor": nxt}})

    shop_client.use_client(httpx.AsyncClient(transport=httpx.MockTransport(handle)))


@pytest.fixture
def _reset_shop(monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.setenv("AGENT_TEMPLATE_MOCK", "1")  # 판매 서버 호출에 쓰는 목 토큰
    yield
    shop_client.use_client(None)


@pytest.mark.asyncio
async def test_fetch_sold_qty_sums_paid_orders_across_pages(_reset_shop: Any) -> None:
    seen: list[Any] = []
    _shop_pages(
        [
            [_order("o1", [("prd_1", 2), ("prd_2", 1)]), _order("o2", [("prd_1", 5)], paid=False)],
            [_order("o3", [("prd_1", 1)])],
        ],
        seen,
    )
    read = await shop_client.fetch_sold_qty(NOW - timedelta(days=30), NOW)
    assert read.qty_by_product == {"prd_1": 3, "prd_2": 1}  # 결제 안 된 주문은 뺀다
    assert read.orders_scanned == 3 and read.complete
    assert seen[0]["since"] == "2026-10-15T12:00:00Z" and seen[0]["until"] == "2026-11-14T12:00:00Z"
    assert seen[1]["cursor"] == "1"


@pytest.mark.asyncio
async def test_fetch_sold_qty_reports_incomplete_when_cut_short(_reset_shop: Any) -> None:
    _shop_pages([[_order("o1", [("prd_1", 1)])], [_order("o2", [("prd_1", 1)])]])
    cut = await shop_client.fetch_sold_qty(NOW - timedelta(days=1), NOW, max_pages=1)
    assert not cut.complete and cut.orders_scanned == 1
    late = await shop_client.fetch_sold_qty(
        NOW - timedelta(days=1), NOW, stop_at=datetime(2020, 1, 1, tzinfo=UTC)
    )
    assert not late.complete and late.orders_scanned == 0


@pytest.mark.asyncio
async def test_fetch_sold_qty_raises_shop_error(_reset_shop: Any) -> None:
    import httpx

    shop_client.use_client(
        httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(500)))
    )
    with pytest.raises(shop_client.ShopError):
        await shop_client.fetch_sold_qty(NOW - timedelta(days=1), NOW)


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("AGENT_TEMPLATE_MOCK", "1")
    monkeypatch.setattr(handlers, "STORE", InquiryStore())
    store = ClaimStore()
    just_now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")  # skill은 실제 지금을 쓴다
    store._claims[1] = claim(1, [line("a", "prd_1", "책")]).model_copy(
        update={"created_at": just_now}
    )
    monkeypatch.setattr(claim_handlers, "CLAIMS", store)
    return TestClient(app)


def test_skill_returns_the_report_and_validates_input(client: TestClient) -> None:
    def call(payload: dict[str, Any]) -> dict[str, Any]:
        body = {
            "meta": {
                "context_id": "ctx_req_0001",
                "token": "mock:dashboard-api",
                "path": [],
                "deadline": (datetime.now(UTC) + timedelta(seconds=30)).isoformat(),
                "idempotency_key": None,
            },
            "skill": "book_issue_stats",
            "input": payload,
            "untrusted": {},
        }
        return client.post("/a2a", json=body).json()

    ok = call({"period": "week", "include_inquiries": False})
    assert ok["status"] == "completed"
    assert ok["output"]["items"][0]["product_id"] == "prd_1"
    assert ok["output"]["total_claims"] == 1
    bad = call({"period": "year"})
    assert bad["status"] != "completed"


# ---------------------------------------------------------------- 문의 합치기 (순수 코드)
def order_inquiry(
    order_id: str,
    count: int,
    products: list[tuple[str, str]],
    status: Any = "ok",
    **cats: int,
) -> OrderInquiry:
    return OrderInquiry(order_id, count, cats or {"payment": count}, tuple(products), status)


def test_inquiries_are_attributed_per_order_to_every_product_in_it() -> None:
    inquiries = [
        order_inquiry("ord_1", 2, [("prd_1", "책 A")], payment=1, delivery=1),
        order_inquiry("ord_2", 3, [("prd_1", "책 A"), ("prd_2", "책 B")], delivery=3),
    ]
    result = book_issue_report([], NOW, BookIssueInput(), inquiries, total_inquiries=7)
    rows = {r.product_id: r for r in result.items}
    assert (rows["prd_1"].inquiry_count, rows["prd_1"].inquiry_count_single_item) == (5, 2)
    assert (rows["prd_2"].inquiry_count, rows["prd_2"].inquiry_count_single_item) == (3, 0)
    assert rows["prd_1"].inquiry_attribution == "order_level"
    assert [(c.category, c.count) for c in rows["prd_1"].top_categories] == [
        ("delivery", 4),
        ("payment", 1),
    ]
    assert result.inquiries_read and result.total_inquiries == 7
    assert result.inquiries_not_attributed == 0


def test_inquiry_only_products_are_listed_and_claim_rows_get_zero_when_read() -> None:
    result = book_issue_report(
        [claim(1, [line("a", "prd_1", "책 A")])],
        NOW,
        BookIssueInput(),
        [order_inquiry("ord_9", 2, [("prd_2", "책 B")])],
    )
    rows = {r.product_id: r for r in result.items}
    assert rows["prd_1"].inquiry_count == 0  # 읽었는데 문의가 없으면 0
    assert rows["prd_2"].claim_count == 0 and rows["prd_2"].inquiry_count == 2
    assert "문의 2건" in rows["prd_2"].reason
    assert [r.product_id for r in result.items] == ["prd_1", "prd_2"]  # 클레임 있는 것이 먼저


def test_unpaid_orders_are_left_out_on_purpose_and_the_counts_stay_complete() -> None:
    result = book_issue_report(
        [],
        NOW,
        BookIssueInput(),
        [
            order_inquiry("ord_1", 4, [], status="unpaid"),
            order_inquiry("ord_2", 1, [("prd_1", "책")]),
        ],
    )
    assert result.inquiries_not_attributed == 4 and result.inquiries_complete is True
    assert any("결제 전이거나 없는 주문의 문의 4건" in m for m in result.missing)
    assert result.items[0].inquiry_count == 1 and result.items[0].inquiry_complete is True


def test_unread_orders_make_the_counts_incomplete_and_unknown_products_none() -> None:
    claims = [t_claim(1, "prd_1"), t_claim(2, "prd_2")]
    result = book_issue_report(
        claims,
        NOW,
        BookIssueInput(),
        [
            order_inquiry("ord_1", 5, [], status="unread"),
            order_inquiry("ord_2", 2, [("prd_2", "책 B")]),
        ],
    )
    rows = {r.product_id: r for r in result.items}
    assert result.inquiries_complete is False and result.inquiries_not_attributed == 5
    # 문의를 전부 읽지 못했으니 prd_1에 문의가 없다고 말할 수 없다: 0이 아니라 None
    assert rows["prd_1"].inquiry_count is None and rows["prd_1"].inquiry_complete is False
    assert rows["prd_2"].inquiry_count == 2 and rows["prd_2"].inquiry_complete is False
    assert "일부 주문만 읽어 더 많을 수 있음" in rows["prd_2"].reason
    assert any("상품에 붙이지 못한 문의 5건" in m for m in result.missing)


def test_truncated_inquiry_orders_make_the_counts_incomplete() -> None:
    result = book_issue_report(
        [t_claim(1, "prd_1")],
        NOW,
        BookIssueInput(),
        [order_inquiry("ord_2", 2, [("prd_2", "책 B")])],
        inquiries_truncated=True,
    )
    rows = {r.product_id: r for r in result.items}
    assert result.inquiries_complete is False
    assert rows["prd_1"].inquiry_count is None


def test_items_without_a_product_id_do_not_count_as_a_single_item_order() -> None:
    result = book_issue_report(
        [],
        NOW,
        BookIssueInput(),
        [
            OrderInquiry("ord_1", 3, {"payment": 3}, (("prd_1", "책"),), has_unknown_items=True),
            order_inquiry("ord_2", 1, [("prd_1", "책")]),
            order_inquiry("ord_3", 2, [], status="no_product"),
        ],
    )
    row = result.items[0]
    assert (row.inquiry_count, row.inquiry_count_single_item) == (
        4,
        1,
    )  # ord_1은 단일 주문이 아니다
    assert result.inquiries_complete is False
    assert any("상품 ID가 없는 품목이 든 주문 1개" in m for m in result.missing)
    assert any("상품 ID가 없는 주문이라 상품에 붙이지 못한 문의 2건" in m for m in result.missing)


def test_paid_amount_is_split_by_item_refund_and_keeps_deductions() -> None:
    deducted = t_claim(1, "prd_1", cash=10000, received=True, paid=4000)  # 변심 반품 배송비 공제
    two = t_claim(2, "prd_2", cash=6000, received=True, paid=8000).model_copy(
        update={"lines": [line("x", "prd_2", "책 B", 1, 6000), line("y", "prd_3", "책 C", 1, 4000)]}
    )
    rows = {
        r.product_id: r for r in book_issue_report([deducted, two], NOW, BookIssueInput()).items
    }
    assert rows["prd_1"].refund_cash_total == 4000  # 품목 환불액 10,000이 아니라 실제 지급액
    assert (rows["prd_2"].refund_cash_total, rows["prd_3"].refund_cash_total) == (4800, 3200)


def test_without_inquiries_the_inquiry_fields_stay_none() -> None:
    result = report([claim(1, [line("a", "prd_1", "책")])])
    row = result.items[0]
    assert row.inquiry_count is None and row.inquiry_count_single_item is None
    assert not result.inquiries_read and result.total_inquiries is None
    assert any("문의 건수를 읽지 않았다" in m for m in result.missing)


def test_min_inquiries_filter() -> None:
    inquiries = [order_inquiry("ord_1", 1, [("prd_1", "책")])]
    assert book_issue_report([], NOW, BookIssueInput(min_inquiries=2), inquiries).items == []
    assert len(book_issue_report([], NOW, BookIssueInput(min_inquiries=1), inquiries).items) == 1


# ---------------------------------------------------------------- 문의 서비스 쪽 (주문별 건수)
def conversation_store(rows: list[tuple[str | None, str, float]]) -> InquiryStore:
    store = InquiryStore()
    for order_id, category, days_ago in rows:
        conversation = store.create(
            context_id="ctx_req_0001",
            tenant_id="shop-demo",
            user_id=1,
            customer_name="김하늘",
            subject="문의",
            order_id=order_id,
            order_no=None,
            first_message="본문",
            analysis=analysis(category),
        )
        stamp = (NOW - timedelta(days=days_ago)).strftime("%Y-%m-%dT%H:%M:%SZ")
        conversation.messages[0].created_at = stamp
    return store


def test_inquiry_order_counts_groups_by_order_in_the_period() -> None:
    store = conversation_store(
        [
            ("ord_1", "payment", 1),
            ("ord_1", "delivery", 2),
            ("ord_2", "payment", 3),
            (None, "payment", 1),  # 주문 없는 문의
            ("ord_3", "payment", 40),  # 기간 밖
        ]
    )
    result = inquiry_order_counts.inquiry_order_counts(store.all(), NOW, "month")
    assert result.total_inquiries == 4 and result.without_order == 1
    assert [(o.order_id, o.inquiry_count) for o in result.orders] == [("ord_1", 2), ("ord_2", 1)]
    assert result.orders[0].categories == {"payment": 1, "delivery": 1}
    assert not result.truncated
    day = inquiry_order_counts.inquiry_order_counts(store.all(), NOW, "day")
    assert day.total_inquiries == 2 and len(day.orders) == 1


def test_inquiry_order_counts_truncates_many_orders() -> None:
    extra = inquiry_order_counts.MAX_ORDERS + 3
    store = conversation_store([(f"ord_{i}", "payment", 1) for i in range(extra)])
    result = inquiry_order_counts.inquiry_order_counts(store.all(), NOW, "month")
    assert len(result.orders) == inquiry_order_counts.MAX_ORDERS and result.truncated
    assert result.total_inquiries == extra


# ---------------------------------------------------------------- claims 쪽 합치기
def run_skill(client: TestClient, payload: dict[str, Any]) -> dict[str, Any]:
    body = {
        "meta": {
            "context_id": "ctx_req_0001",
            "token": "mock:dashboard-api",
            "path": [],
            "deadline": (datetime.now(UTC) + timedelta(seconds=30)).isoformat(),
            "idempotency_key": None,
        },
        "skill": "book_issue_stats",
        "input": payload,
        "untrusted": {},
    }
    return client.post("/a2a", json=body).json()


def fake_orders(monkeypatch: pytest.MonkeyPatch, mapping: dict[str, Any]) -> None:
    async def load(order_id: str, *, client: Any = None) -> Any:
        value = mapping[order_id]
        if isinstance(value, Exception):
            raise value
        return value

    monkeypatch.setattr(shop_client, "load_order", load)


def one_order(order_id: str, items: list[tuple[str, str | None]]) -> Any:
    base = orders.get_order("101")
    assert base is not None
    return base.model_copy(
        update={
            "id": order_id,
            "items": [
                base.items[0].model_copy(update={"id": f"i{n}", "title": t, "product_id": p})
                for n, (t, p) in enumerate(items)
            ],
        }
    )


def fake_send(
    monkeypatch: pytest.MonkeyPatch,
    output: dict[str, Any] | None,
    exc: Exception | None = None,
) -> None:
    async def send(target: str, request: AgentRequest) -> AgentResult:
        assert (target, request.skill) == ("support", "inquiry_order_counts")
        if exc is not None:
            raise exc
        if output is None:
            return AgentResult(meta=request.meta, status=TaskStatus.FAILED, outcome="down")
        return AgentResult(meta=request.meta, status=TaskStatus.COMPLETED, output=output)

    monkeypatch.setattr(handlers, "SEND", send)


def counts_output(rows: list[dict[str, Any]], total: int = 5) -> dict[str, Any]:
    return {
        "period": "month",
        "period_start": "2026-10-01T00:00:00Z",
        "period_end": "2026-11-01T00:00:00Z",
        "total_inquiries": total,
        "without_order": 0,
        "orders": rows,
        "truncated": False,
    }


def test_skill_attaches_inquiries_from_the_inquiry_service(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_send(
        monkeypatch,
        counts_output(
            [
                {"order_id": "ord_a", "inquiry_count": 2, "categories": {"delivery": 2}},
                {"order_id": "ord_b", "inquiry_count": 1, "categories": {"payment": 1}},
                {"order_id": "ord_c", "inquiry_count": 3, "categories": {"payment": 3}},
            ],
            total=9,
        ),
    )
    fake_orders(
        monkeypatch,
        {
            "ord_a": one_order("ord_a", [("책 A", "prd_1")]),
            "ord_b": None,  # 결제 전
            "ord_c": shop_client.ShopError("UNAVAILABLE", "판매 서버 연결 실패"),
        },
    )
    out = run_skill(client, {"period": "month"})["output"]
    rows = {r["product_id"]: r for r in out["items"]}
    assert rows["prd_1"]["inquiry_count"] == 2 and rows["prd_1"]["claim_count"] == 1
    assert out["inquiries_read"] and out["total_inquiries"] == 9
    assert out["inquiries_not_attributed"] == 4  # 결제 전 1건 + 읽지 못한 3건
    assert any("주문 1개를 읽지 못했거나" in m for m in out["missing"])
    assert out["inquiries_complete"] is False  # 못 읽은 주문이 있어 건수가 모자랄 수 있다


def test_skill_still_returns_claims_when_the_inquiry_service_is_down(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_send(monkeypatch, None)
    out = run_skill(client, {})["output"]
    assert out["items"][0]["product_id"] == "prd_1"
    assert out["items"][0]["inquiry_count"] is None and not out["inquiries_read"]
    assert any("문의 건수를 읽지 못했다: down" in m for m in out["missing"])
    fake_send(monkeypatch, None, exc=RuntimeError("boom"))
    out = run_skill(client, {})["output"]
    assert any("문의 서비스에 닿지 못함" in m for m in out["missing"])


# ---------------------------------------------------------------- skill: 판매 수량과 비율
def test_skill_adds_sold_qty_and_claim_rate_from_the_shop(
    client: TestClient, monkeypatch: pytest.MonkeyPatch, _reset_shop: Any
) -> None:
    monkeypatch.setenv("SUPPORT_ORDER_SOURCE", "shop-api")
    _shop_pages([[_order("o1", [("prd_1", 3)]), _order("o2", [("prd_1", 1)])]])
    out = run_skill(client, {"include_inquiries": False})["output"]
    row = out["items"][0]
    assert (row["sold_qty"], row["claim_qty"], row["claim_rate"]) == (4, 1, 0.25)
    assert out["sales_read"] and out["sales_orders_scanned"] == 2


def test_skill_leaves_rate_empty_when_sales_cannot_be_read_completely(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SUPPORT_ORDER_SOURCE", "shop-api")

    async def partial(*args: Any, **kwargs: Any) -> shop_client.SalesRead:
        return shop_client.SalesRead({"prd_1": 99}, 30000, False)

    monkeypatch.setattr(shop_client, "fetch_sold_qty", partial)
    out = run_skill(client, {"include_inquiries": False})["output"]
    assert out["items"][0]["claim_rate"] is None and out["items"][0]["sold_qty"] is None
    assert not out["sales_read"]
    assert any("30000건까지만 읽어서 판매 수량을 내지 않았다" in m for m in out["missing"])

    async def down(*args: Any, **kwargs: Any) -> shop_client.SalesRead:
        raise shop_client.ShopError("UNAVAILABLE", "판매 서버 연결 실패")

    monkeypatch.setattr(shop_client, "fetch_sold_qty", down)
    out = run_skill(client, {"include_inquiries": False})["output"]
    assert out["items"][0]["claim_count"] == 1  # 클레임 집계는 그대로 나간다
    assert any("판매 수량을 읽지 못했다: 판매 서버 연결 실패" in m for m in out["missing"])


def test_skill_skips_sales_in_seed_mode_and_when_asked(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    out = run_skill(client, {"include_inquiries": False})["output"]  # 목 모드 = 시드 주문
    assert any("시드라 판매 수량을 읽지 않았다" in m for m in out["missing"])
    monkeypatch.setenv("SUPPORT_ORDER_SOURCE", "shop-api")

    async def must_not_call(*args: Any, **kwargs: Any) -> None:
        raise AssertionError("include_sales=false인데 판매 서버를 불렀다")

    monkeypatch.setattr(shop_client, "fetch_sold_qty", must_not_call)
    out = run_skill(client, {"include_inquiries": False, "include_sales": False})["output"]
    assert not out["sales_read"] and out["items"][0]["claim_rate"] is None


@pytest.mark.asyncio
async def test_fetch_sold_qty_reuses_one_http_client_for_all_pages(
    monkeypatch: pytest.MonkeyPatch, _reset_shop: Any
) -> None:
    """페이지마다 클라이언트를 새로 만들면 연결·TLS 설정 비용이 쌓인다 (4만 건에서 2.7배 느림)."""
    import httpx

    pages = [[_order(f"o{n}", [("prd_1", n)])] for n in (1, 2, 3)]

    def handle(request: httpx.Request) -> httpx.Response:
        cursor = request.url.params.get("cursor")
        index = int(cursor) if cursor else 0
        nxt = str(index + 1) if index + 1 < len(pages) else None
        return httpx.Response(200, json={"data": pages[index], "meta": {"next_cursor": nxt}})

    made: list[int] = []
    real = httpx.AsyncClient

    def factory(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        made.append(1)
        return real(*args, transport=httpx.MockTransport(handle), **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", factory)
    shop_client.use_client(None)  # 바꿔 끼운 클라이언트 없이, 코드가 직접 만드는 길
    read = await shop_client.fetch_sold_qty(NOW - timedelta(days=1), NOW)
    assert read.qty_by_product == {"prd_1": 6} and read.complete and read.orders_scanned == 3
    assert len(made) == 1  # 세 페이지를 클라이언트 하나로

    made.clear()  # 한 번만 부르는 읽기는 지금처럼 자기 클라이언트를 만들고 닫는다
    await shop_client._send("GET", "/orders")
    await shop_client._send("GET", "/orders")
    assert len(made) == 2


# ---------------------------------------------------------------- 검토에서 나온 수정 (핸들러 쪽)
def _inquiry_orders(*ids: str) -> dict[str, Any]:
    return counts_output(
        [{"order_id": i, "inquiry_count": 1, "categories": {"payment": 1}} for i in ids],
        total=len(ids),
    )


def test_other_tenants_claims_are_not_counted(client: TestClient) -> None:
    store = claim_handlers.CLAIMS
    other = claim(2, [line("b", "prd_1", "책")]).model_copy(
        update={
            "tenant_id": "other-co",
            "created_at": datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ"),
        }
    )
    store._claims[2] = other
    out = run_skill(client, {"include_inquiries": False, "include_sales": False})["output"]
    assert out["items"][0]["claim_count"] == 1 and out["total_claims"] == 1


def test_inquiry_order_counts_only_counts_the_requesting_tenant(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    store = conversation_store([("ord_1", "payment", 1), ("ord_2", "payment", 1)])
    list(store.all())[1].tenant_id = "other-co"
    now = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    for conversation in store.all():
        conversation.messages[0].created_at = now
    monkeypatch.setattr(handlers, "STORE", store)
    body = {
        "meta": {
            "context_id": "ctx_req_0001",
            "token": "mock:dashboard-api",
            "path": [],
            "deadline": (datetime.now(UTC) + timedelta(seconds=30)).isoformat(),
            "idempotency_key": None,
        },
        "skill": "inquiry_order_counts",
        "input": {"period": "week"},
        "untrusted": {},
    }
    out = client.post("/a2a", json=body).json()["output"]
    assert out["total_inquiries"] == 1 and [o["order_id"] for o in out["orders"]] == ["ord_1"]


def test_an_unexpected_shop_error_does_not_take_down_the_claim_counts(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_send(monkeypatch, _inquiry_orders("ord_a", "ord_b"))
    fake_orders(
        monkeypatch, {"ord_a": one_order("ord_a", [("책 A", "prd_1")]), "ord_b": KeyError("x")}
    )
    out = run_skill(client, {"include_sales": False})["output"]
    assert out["items"][0]["claim_count"] == 1  # 클레임 집계는 그대로 나간다
    assert out["items"][0]["inquiry_count"] == 1 and out["inquiries_complete"] is False

    monkeypatch.setenv("SUPPORT_ORDER_SOURCE", "shop-api")

    async def broken(*args: Any, **kwargs: Any) -> shop_client.SalesRead:
        raise ValueError("invalid literal for int()")

    monkeypatch.setattr(shop_client, "fetch_sold_qty", broken)
    out = run_skill(client, {"include_inquiries": False})["output"]
    assert out["items"][0]["claim_count"] == 1 and not out["sales_read"]
    assert any("판매 수량을 읽지 못했다 (응답을 해석하지 못함)" in m for m in out["missing"])


def test_inquiry_order_reads_stop_near_the_deadline(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_send(monkeypatch, _inquiry_orders("ord_a", "ord_b"))

    async def must_not_read(*args: Any, **kwargs: Any) -> Any:
        raise AssertionError("마감이 가까운데 주문을 읽었다")

    monkeypatch.setattr(shop_client, "load_order", must_not_read)
    monkeypatch.setattr(claim_handlers, "DEADLINE_MARGIN", 10_000)  # 마감까지 남은 시간보다 크다
    out = run_skill(client, {"include_sales": False})["output"]
    assert out["inquiries_complete"] is False
    assert out["items"][0]["inquiry_count"] is None  # 읽지 못했으니 0이 아니다
    assert any("2개를 읽지 못했거나 마감 때문에 건너뛰어" in m for m in out["missing"])


def test_inquiry_order_reads_share_one_http_client(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    import httpx

    fake_send(monkeypatch, _inquiry_orders("ord_a", "ord_b", "ord_c"))
    monkeypatch.setenv("SUPPORT_ORDER_SOURCE", "shop-api")

    def handle(request: httpx.Request) -> httpx.Response:
        order = _order(request.url.path.rsplit("/", 1)[-1], [("prd_1", 1)])
        order["created_at"] = "2026-11-14T00:00:00Z"
        order["payments"] = [{**order["payments"][0], "created_at": "2026-11-14T00:00:01Z"}]
        order.update(buyer_id="buy_0001")
        order["items"] = [
            {
                "order_item_id": "oit_1",
                "product_id": "prd_1",
                "title": "책",
                "qty": 1,
                "paid_amount": 1000,
            }
        ]
        order["payments"][0]["payment_id"] = "pay_1"
        return httpx.Response(200, json={"data": order, "meta": {}})

    made: list[int] = []
    real = httpx.AsyncClient

    def factory(*args: Any, **kwargs: Any) -> httpx.AsyncClient:
        made.append(1)
        return real(*args, transport=httpx.MockTransport(handle), **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", factory)
    shop_client.use_client(None)
    try:
        out = run_skill(client, {"include_sales": False})["output"]
    finally:
        shop_client.use_client(None)
    assert out["items"][0]["inquiry_count"] == 3 and out["inquiries_complete"] is True
    assert len(made) == 1  # 주문 세 개를 클라이언트 하나로
