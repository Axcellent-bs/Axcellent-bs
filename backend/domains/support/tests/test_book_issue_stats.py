"""책(상품)별 클레임 집계: 순수 코드와 skill 응답."""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

from support_agent import claim_handlers, handlers
from support_agent.book_issue_schemas import BookIssueInput
from support_agent.book_issue_stats import book_issue_report
from support_agent.claim_schemas import Claim, ClaimLine
from support_agent.claims import ClaimStore
from support_agent.main import app
from support_agent.store import InquiryStore

NOW = datetime(2026, 11, 14, 12, 0, tzinfo=UTC)


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

    ok = call({"period": "week"})
    assert ok["status"] == "completed"
    assert ok["output"]["items"][0]["product_id"] == "prd_1"
    assert ok["output"]["total_claims"] == 1
    bad = call({"period": "year"})
    assert bad["status"] != "completed"
