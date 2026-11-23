"""회사 나누기: 고객지원 문의·클레임은 요청 회사(meta.tenant_id) 것만 보인다 (설계 7.2 1·6단계).

- 회사 A로 회사 B의 문의·클레임을 읽으면 없는 것처럼 not_found
- 목록·보상 큐·통계는 그 회사 건만
- 별칭 shop-demo와 회사 칸 없는 요청은 캣북스
- 스스로 도는 루틴(요청 밖)은 모든 회사 건을 본다 (보관 기간 정리·개인정보 삭제)
"""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

from ax_agent_template import company_scope
from support_agent import claim_handlers, handlers, orders
from support_agent.claim_rules import decide
from support_agent.claims import ClaimStore
from support_agent.main import app
from support_agent.schemas import Analysis, Classification
from support_agent.store import InquiryStore

ACME = "acme-books"


@pytest.fixture(autouse=True)
def _fresh(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_TEMPLATE_MOCK", "1")
    monkeypatch.setattr(handlers, "STORE", InquiryStore())
    monkeypatch.setattr(claim_handlers, "CLAIMS", ClaimStore())


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def call(
    client: TestClient, skill: str, input: dict[str, Any], tenant_id: str | None = None
) -> dict[str, Any]:
    body = {
        "meta": {
            "context_id": "ctx_ui_scope",
            "token": "mock:dashboard-api",
            "path": [],
            "deadline": (datetime.now(UTC) + timedelta(seconds=30)).isoformat(),
            **({"tenant_id": tenant_id} if tenant_id else {}),
        },
        "skill": skill,
        "input": input,
        "untrusted": {},
    }
    return client.post("/a2a", json=body).json()


def inquiry(tenant_id: str, context_id: str = "ctx_ui_1"):  # type: ignore[no-untyped-def]
    analysis = Analysis(
        classification=Classification(
            intent_ko="문의",
            category="shipping",
            sentiment="calm",
            urgency="medium",
            search_query="배송",
        )
    )
    return handlers.STORE.create(
        context_id=context_id,
        tenant_id=tenant_id,
        user_id=1,
        customer_name="김하늘",
        subject="배송 문의",
        order_id=None,
        order_no=None,
        first_message="언제 와요?",
        analysis=analysis,
    )


def claim(tenant_id: str):  # type: ignore[no-untyped-def]
    order = orders.SEED_ORDERS[0]
    decision = decide(
        order,
        "cancel",
        "change_of_mind",
        items=None,
        evidence_count=0,
        unsealed=False,
        prior_claims=0,
        now=order.paid_at + timedelta(hours=1),
    )
    made = claim_handlers.CLAIMS.create(
        context_id=f"ctx_shop_{tenant_id.replace('-', '')}",
        tenant_id=tenant_id,
        order=order,
        customer="고객",
        claim_type="cancel",
        reason="change_of_mind",
        decision=decision,
        evidence_count=0,
        unsealed=False,
        conversation_id=None,
    )
    made.status = "approved"
    made.expected_refund = made.expected_refund or 1000
    return made


# ---------------------------------------------------------------- 문의
def test_another_companys_inquiry_is_not_found(client: TestClient) -> None:
    cat = inquiry("shop-demo")
    acme = inquiry(ACME, context_id="ctx_ui_2")
    assert call(client, "get_inquiry", {"inquiry_id": acme.id})["outcome"] == "not_found"
    assert call(client, "get_inquiry", {"inquiry_id": cat.id}, ACME)["outcome"] == "not_found"
    mine = call(client, "get_inquiry", {"inquiry_id": acme.id}, ACME)
    assert mine["status"] == "completed" and mine["output"]["id"] == acme.id


def test_inquiry_list_holds_only_the_requesting_company(client: TestClient) -> None:
    cat = inquiry("shop-demo")
    acme = inquiry(ACME, context_id="ctx_ui_2")
    ids = {i["id"] for i in call(client, "list_inquiries", {})["output"]["items"]}
    assert ids == {cat.id}
    ids = {i["id"] for i in call(client, "list_inquiries", {}, "catbooks")["output"]["items"]}
    assert ids == {cat.id}  # 정식 id와 별칭 shop-demo는 같은 회사
    ids = {i["id"] for i in call(client, "list_inquiries", {}, ACME)["output"]["items"]}
    assert ids == {acme.id}


# ---------------------------------------------------------------- 클레임
def test_another_companys_claim_is_not_found(client: TestClient) -> None:
    acme = claim(ACME)
    assert call(client, "get_claim", {"claim_id": acme.id})["outcome"] == "not_found"
    mine = call(client, "get_claim", {"claim_id": acme.id}, ACME)
    assert mine["status"] == "completed" and mine["output"]["id"] == acme.id


def test_claim_list_and_queue_hold_only_the_requesting_company(client: TestClient) -> None:
    cat = claim("shop-demo")
    acme = claim(ACME)
    listed = {c["id"] for c in call(client, "list_claims", {})["output"]["items"]}
    assert listed == {cat.id}
    listed = {c["id"] for c in call(client, "list_claims", {}, ACME)["output"]["items"]}
    assert listed == {acme.id}
    queued = {e["claim_id"] for e in call(client, "claim_queue", {}, ACME)["output"]["queue"]}
    assert queued == {acme.id}


# ---------------------------------------------------------------- 요청 밖
def test_routines_outside_a_request_see_every_company() -> None:
    cat = inquiry("shop-demo")
    acme = inquiry(ACME, context_id="ctx_ui_2")
    assert company_scope.active() is None
    assert {c.id for c in handlers.STORE.all()} == {cat.id, acme.id}
    with company_scope.using(ACME):
        assert {c.id for c in handlers.STORE.all()} == {acme.id}
        assert handlers.STORE.get(cat.id) is None
    assert handlers.STORE.get(cat.id) is not None


def test_unknown_or_missing_company_falls_back_to_catbooks() -> None:
    assert company_scope.scope_id(None) == "catbooks"
    assert company_scope.scope_id("shop-demo") == "catbooks"
    assert company_scope.scope_id("Not A Valid Id!") == "catbooks"
    with company_scope.using(None):
        assert company_scope.visible("shop-demo") and not company_scope.visible(ACME)
