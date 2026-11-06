"""분류 필요: 오류 코드가 없는 문의를 따로 모으고, 상담원이 분류 이름을 정하고 마친다."""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

from support_agent import claim_handlers, handlers, shop_client, triage
from support_agent.claims import ClaimStore
from support_agent.main import app
from support_agent.schemas import Analysis, Classification
from support_agent.store import InquiryStore


@pytest.fixture(autouse=True)
def _fresh(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_TEMPLATE_MOCK", "1")
    monkeypatch.setattr(handlers, "STORE", InquiryStore())
    monkeypatch.setattr(claim_handlers, "CLAIMS", ClaimStore())


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def call(client: TestClient, skill: str, input: dict[str, Any], key: str | None = None) -> dict:
    body = {
        "meta": {
            "context_id": "ctx_ui_triage",
            "token": "mock:dashboard-api",
            "path": [],
            "deadline": (datetime.now(UTC) + timedelta(seconds=30)).isoformat(),
            "idempotency_key": key,
        },
        "skill": skill,
        "input": input,
        "untrusted": {},
    }
    return client.post("/a2a", json=body).json()


def make(
    category: str,
    order_id: str | None = "ord_1",
    context_id: str = "ctx_ui_1",
    tenant_id: str = "shop-demo",
):  # type: ignore[no-untyped-def]
    analysis = Analysis(
        classification=Classification(
            intent_ko="문의",
            category=category,
            sentiment="calm",
            urgency="medium",
            search_query="문의",
        )
    )
    return handlers.STORE.create(
        context_id=context_id,
        tenant_id=tenant_id,
        user_id=1,
        customer_name="김하늘",
        subject="문의",
        order_id=order_id,
        order_no=None,
        first_message="장바구니 창이 안 열려서 결제가 안 돼요",
        analysis=analysis,
    )


def live_order(monkeypatch: pytest.MonkeyPatch, facts: dict | None) -> None:
    monkeypatch.setattr(shop_client, "order_source", lambda: "live")

    async def fake(order_id: str) -> dict | None:
        return facts

    monkeypatch.setattr(shop_client, "fetch_order_facts", fake)


# ---------------------------------------------------------------- 자동 판정
@pytest.mark.asyncio
async def test_payment_inquiry_without_a_failed_payment_needs_triage(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    live_order(monkeypatch, {"order_id": "ord_1", "payments": [{"status": "succeeded"}]})
    assert await triage.assess(make("payment")) == "no_error_code"


@pytest.mark.asyncio
async def test_payment_inquiry_with_a_failure_code_is_not_flagged(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    live_order(monkeypatch, {"payments": [{"status": "failed", "failure_code": "PG_DECLINED"}]})
    assert await triage.assess(make("payment")) is None


@pytest.mark.asyncio
async def test_unclassified_inquiry_needs_triage() -> None:
    assert await triage.assess(make("general")) == "unclassified"


@pytest.mark.asyncio
async def test_other_categories_are_not_flagged() -> None:
    assert await triage.assess(make("shipping")) is None


@pytest.mark.asyncio
async def test_payment_inquiry_without_an_order_needs_triage() -> None:
    assert await triage.assess(make("payment", order_id=None)) == "no_error_code"


@pytest.mark.asyncio
async def test_an_inquiry_on_a_running_incident_is_not_flagged(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    live_order(monkeypatch, {"payments": []})
    assert await triage.assess(make("payment", context_id="ctx_inc_7")) is None


@pytest.mark.asyncio
async def test_an_unreadable_order_is_not_called_a_missing_cause(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(shop_client, "order_source", lambda: "live")

    async def broken(order_id: str) -> dict | None:
        raise shop_client.ShopError("UNAVAILABLE", "down")

    monkeypatch.setattr(shop_client, "fetch_order_facts", broken)
    assert await triage.assess(make("payment")) is None


@pytest.mark.asyncio
async def test_the_automatic_mark_leaves_a_history_line_and_never_overwrites() -> None:
    conversation = make("general")
    first = await triage.mark_automatically(conversation)
    assert first is not None and first.state == "needed" and first.marked_by == "auto"
    stored = handlers.STORE.get(conversation.id)
    assert stored is not None and stored.triage == first
    assert any(a.kind == "triage_needed" for a in stored.activity)
    handlers.STORE.set_triage(conversation.id, first.model_copy(update={"state": "done"}))
    again = handlers.STORE.get(conversation.id)
    assert again is not None and await triage.mark_automatically(again) is None


# ---------------------------------------------------------------- 상담원이 하는 일 (set_triage)
def test_an_agent_can_send_decide_and_clear(client: TestClient) -> None:
    inquiry = make("shipping")
    sent = call(client, "set_triage", {"inquiry_id": inquiry.id, "action": "send"})
    assert sent["status"] == "completed"
    assert sent["output"]["triage"]["state"] == "needed"
    assert sent["output"]["triage"]["reason"] == "manual"
    assert sent["output"]["triage"]["marked_by"] == "agent"

    decide = {"inquiry_id": inquiry.id, "action": "decide", "label": "장바구니 화면 오류"}
    done = call(client, "set_triage", decide)["output"]["triage"]
    assert done["state"] == "done" and done["label"] == "장바구니 화면 오류" and done["decided_at"]

    cleared = call(client, "set_triage", {"inquiry_id": inquiry.id, "action": "clear"})
    assert cleared["output"]["triage"] is None


def test_deciding_needs_a_label_a_marked_inquiry_and_no_personal_data(client: TestClient) -> None:
    unmarked = make("shipping")
    no_label = call(client, "set_triage", {"inquiry_id": unmarked.id, "action": "decide"})
    assert no_label["status"] == "failed"
    decide = {"inquiry_id": unmarked.id, "action": "decide", "label": "화면 오류"}
    not_marked = call(client, "set_triage", decide)
    assert not_marked["output"]["message"].startswith("분류 필요로 올린")
    call(client, "set_triage", {"inquiry_id": unmarked.id, "action": "send"})
    pii = {"inquiry_id": unmarked.id, "action": "decide", "label": "010-1234-5678 번호"}
    assert call(client, "set_triage", pii)["status"] == "failed"
    stored = handlers.STORE.get(unmarked.id)
    assert stored is not None and stored.triage is not None and stored.triage.state == "needed"


def test_an_unknown_inquiry_is_not_found(client: TestClient) -> None:
    result = call(client, "set_triage", {"inquiry_id": 9999, "action": "send"})
    assert "9999" in result["output"]["message"]


def test_each_step_is_written_to_the_history_without_customer_text(client: TestClient) -> None:
    inquiry = make("shipping")
    call(client, "set_triage", {"inquiry_id": inquiry.id, "action": "send"})
    decide = {"inquiry_id": inquiry.id, "action": "decide", "label": "결제 화면 오류"}
    call(client, "set_triage", decide)
    stored = handlers.STORE.get(inquiry.id)
    assert stored is not None
    assert {"triage_needed", "triage_decided"} <= {a.kind for a in stored.activity}
    text = " ".join(f"{a.title} {a.detail} {a.data}" for a in stored.activity)
    assert "장바구니 창이" not in text


# ---------------------------------------------------------------- 회사별 관리와 목록
def test_the_list_carries_the_company_and_the_triage_mark(client: TestClient) -> None:
    cat = make("general", tenant_id="shop-demo")
    other = make("shipping", tenant_id="acme-books")
    items = {i["id"]: i for i in call(client, "list_inquiries", {})["output"]["items"]}
    assert items[cat.id]["tenant_id"] == "shop-demo" and items[cat.id]["triage"] is None
    assert items[other.id]["tenant_id"] == "acme-books"
    call(client, "set_triage", {"inquiry_id": other.id, "action": "send"})
    items = {i["id"]: i for i in call(client, "list_inquiries", {})["output"]["items"]}
    assert items[other.id]["triage"]["state"] == "needed"
