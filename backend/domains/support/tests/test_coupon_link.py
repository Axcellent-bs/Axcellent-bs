"""보상 쿠폰을 문의와 잇는다: 닫을 때 쿠폰을 싣고, 닫은 뒤 지급되면 다시 알린다."""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

from ax_agent_template import events as bus
from ax_contracts.schemas import AgentRequest, AgentResult, EventEnvelope, EventType, TaskStatus
from support_agent import claim_handlers, compensation, handlers
from support_agent.claims import ClaimStore
from support_agent.main import app
from support_agent.schemas import Analysis
from support_agent.store import InquiryStore

CTX = "ctx_inq_0001"


@pytest.fixture(autouse=True)
def _fresh(monkeypatch: pytest.MonkeyPatch) -> list[AgentRequest]:
    monkeypatch.setenv("AGENT_TEMPLATE_MOCK", "1")
    bus.reset_mock_bus()
    store = InquiryStore()
    store.create(
        context_id=CTX,
        tenant_id="shop-demo",
        user_id=1,
        customer_name="김하늘",
        subject="배송 지연",
        order_id=101,
        order_no="ORD-1",
        first_message="배송이 늦어요",
        analysis=Analysis(),
    )
    monkeypatch.setattr(handlers, "STORE", store)
    monkeypatch.setattr(handlers, "LEDGER", compensation.SequenceLedger())
    monkeypatch.setattr(claim_handlers, "CLAIMS", ClaimStore())
    sent: list[AgentRequest] = []

    async def fake_send(target: str, req: AgentRequest) -> AgentResult:
        sent.append(req)
        return AgentResult(
            meta=req.meta,
            status=TaskStatus.COMPLETED,
            output={"coupon_id": f"cpn_{len(sent):04d}", "count": req.input["count"]},
        )

    monkeypatch.setattr(handlers, "SEND", fake_send)
    return sent


def call(skill: str, input: dict[str, Any], key: str) -> dict[str, Any]:
    body = {
        "meta": {
            "context_id": CTX,
            "token": "mock:orchestrator",
            "path": [],
            "deadline": (datetime.now(UTC) + timedelta(seconds=30)).isoformat(),
            "idempotency_key": key,
        },
        "skill": skill,
        "input": input,
    }
    return TestClient(app).post("/a2a", json=body).json()


def compensate(key: str = "c-1", reason: str = "late_delivery") -> dict[str, Any]:
    return call(
        "request_compensation",
        {"inquiry_id": 1, "buyer_id": "buy_0001", "reason_code": reason, "count": 1},
        key,
    )


def close(key: str = "s-1") -> None:
    call("set_inquiry_status", {"inquiry_id": 1, "status": "resolved"}, key)


def resolved() -> list[EventEnvelope]:
    return [e for e in bus.MOCK_PUBLISHED if e.type == EventType.SUPPORT_INQUIRY_RESOLVED]


def test_coupon_before_closing_fills_coupon_id_and_compensated() -> None:
    assert compensate()["status"] == "completed"
    close()
    (event,) = resolved()
    assert event.data["coupon_id"] == "cpn_0001"
    assert event.data["resolution"] == "compensated"
    assert event.idempotency_key == f"{CTX}:support.inquiry_resolved:1"


def test_the_coupon_id_is_kept_in_the_activity_log() -> None:
    compensate()
    entry = [a for a in handlers.STORE.get(1).activity if a.kind == "compensation"][-1]
    assert entry.data["coupon_id"] == "cpn_0001"


def test_coupon_after_closing_reissues_with_its_own_key() -> None:
    close()
    (closed,) = resolved()
    assert closed.data["coupon_id"] is None and closed.data["resolution"] == "answered"
    compensate()
    first, again = resolved()
    assert first is closed
    assert again.data["coupon_id"] == "cpn_0001" and again.data["resolution"] == "compensated"
    assert again.idempotency_key == f"{CTX}:support.inquiry_resolved:1:coupon:cpn_0001"


def test_retrying_the_same_compensation_does_not_send_a_second_event() -> None:
    close()
    compensate(key="c-1")
    compensate(key="c-1")
    assert len(resolved()) == 2  # 닫을 때 1건 + 쿠폰 재발행 1건


def test_an_open_inquiry_gets_no_coupon_reissue() -> None:
    compensate()
    assert resolved() == []  # 닫을 때 나가는 이벤트가 쿠폰을 싣는다


def test_no_coupon_when_marketing_did_not_issue_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def held(target: str, req: AgentRequest) -> AgentResult:
        return AgentResult(meta=req.meta, status=TaskStatus.REJECTED, outcome="SEQUENCE_GAP")

    monkeypatch.setattr(handlers, "SEND", held)
    close()
    compensate()
    (event,) = resolved()
    assert event.data["coupon_id"] is None and event.data["resolution"] == "answered"


def test_ineligible_reason_sends_nothing_and_leaves_no_coupon() -> None:
    close()
    compensate(reason="no_such_reason")
    (event,) = resolved()
    assert event.data["coupon_id"] is None
