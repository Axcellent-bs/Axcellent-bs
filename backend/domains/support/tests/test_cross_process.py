"""서비스를 나눠 띄우면 문의는 문의 서비스에, 클레임·쿠폰은 클레임 서비스에 따로 있다.

클레임 서비스(`claims`) 프로세스는 문의를 가지고 있지 않다. 그래도 보상 쿠폰 요청이 `not_found`로
끝나지 않고, 쿠폰·환불이 문의에 이어져 재무 기록(`inquiry_resolved`)에 실려야 한다 (#369 후속).
"""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

from ax_agent_template import events as bus
from ax_contracts.schemas import AgentRequest, AgentResult, EventEnvelope, EventType, TaskStatus
from support_agent import claim_handlers, compensation, handlers
from support_agent.claims import ClaimStore
from support_agent.main import app
from support_agent.schemas import ActivityEntry, Analysis, RecordClaimActivityInput
from support_agent.store import InquiryStore

CTX = "ctx_inq_0001"


def _remote_inquiry() -> Any:
    """문의 서비스에 있는 문의 한 건 (이 프로세스의 저장소에는 없다)."""
    store = InquiryStore()
    return store.create(
        context_id=CTX,
        tenant_id="shop-demo",
        user_id=1,
        customer_name="김하늘",
        subject="배송 지연",
        order_id=102,
        order_no="ORD-1",
        first_message="배송이 늦어요",
        analysis=Analysis(),
    )


class Peers:
    """같은 부서의 다른 서비스와 마케팅을 흉내 낸다. 부른 요청을 모두 적어 둔다."""

    def __init__(self) -> None:
        self.inquiry = _remote_inquiry()
        self.sent: list[AgentRequest] = []
        self.claims_dump: list[dict[str, Any]] = []
        self.inquiry_down = False

    def by_skill(self, skill: str) -> list[AgentRequest]:
        return [r for r in self.sent if r.skill == skill]

    async def send(self, target: str, req: AgentRequest) -> AgentResult:
        self.sent.append(req)
        done = TaskStatus.COMPLETED
        if req.skill == "get_inquiry":
            if self.inquiry_down:
                return AgentResult(meta=req.meta, status=TaskStatus.FAILED, outcome="not_found")
            return AgentResult(
                meta=req.meta,
                status=done,
                output={**self.inquiry.model_dump(mode="json"), "claims": []},
            )
        if req.skill == "record_claim_activity":
            # 문의 서비스가 하는 일: 같은 값으로 내력을 남긴다 (문장은 문의 쪽이 코드로 만든다)
            note = RecordClaimActivityInput.model_validate(req.input)
            if note.kind == "compensation":
                self.inquiry.activity.append(
                    ActivityEntry(
                        at="2026-11-03T00:00:00Z",
                        actor="ai",
                        kind="compensation",
                        title=handlers.COMPENSATION_TITLE[note.compensation_result or "unfinished"],
                        data=handlers._compensation_data(note),
                    )
                )
            return AgentResult(meta=req.meta, status=done, output={"inquiry_id": note.inquiry_id})
        if req.skill == "list_claims":
            return AgentResult(meta=req.meta, status=done, output={"items": self.claims_dump})
        count = req.input.get("count", 1)
        return AgentResult(
            meta=req.meta, status=done, output={"coupon_id": "cpn_0001", "count": count}
        )


@pytest.fixture
def peers(monkeypatch: pytest.MonkeyPatch) -> Peers:
    monkeypatch.setenv("AGENT_TEMPLATE_MOCK", "1")
    bus.reset_mock_bus()
    monkeypatch.setattr(handlers, "STORE", InquiryStore())  # 이 프로세스에는 문의가 없다
    monkeypatch.setattr(handlers, "LEDGER", compensation.SequenceLedger())
    monkeypatch.setattr(claim_handlers, "CLAIMS", ClaimStore())
    fake = Peers()
    monkeypatch.setattr(handlers, "SEND", fake.send)
    return fake


def call(skill: str, input: dict[str, Any], key: str, requester: str = "orchestrator") -> dict:
    body = {
        "meta": {
            "context_id": CTX,
            "token": f"mock:{requester}",
            "path": [],
            "deadline": (datetime.now(UTC) + timedelta(seconds=30)).isoformat(),
            "idempotency_key": key,
        },
        "skill": skill,
        "input": input,
    }
    return TestClient(app).post("/a2a", json=body).json()


def compensate(key: str = "c-1") -> dict[str, Any]:
    return call(
        "request_compensation",
        {"inquiry_id": 1, "buyer_id": "buy_0001", "reason_code": "late_delivery", "count": 1},
        key,
    )


def resolved() -> list[EventEnvelope]:
    return [e for e in bus.MOCK_PUBLISHED if e.type == EventType.SUPPORT_INQUIRY_RESOLVED]


def test_a_compensation_for_an_inquiry_in_another_process_is_not_not_found(peers: Peers) -> None:
    result = compensate()
    assert result["status"] == "completed" and result.get("outcome") != "not_found"
    assert len(peers.by_skill("get_inquiry")) >= 1
    assert result["output"]["marketing_status"] == "completed"


def test_the_coupon_is_reported_to_the_inquiry_service_as_codes_only(peers: Peers) -> None:
    compensate()
    (note,) = peers.by_skill("record_claim_activity")
    assert note.input["kind"] == "compensation"
    assert note.input["compensation_result"] == "requested"
    assert note.input["coupon_id"] == "cpn_0001" and note.input["total_value"] == 2000
    assert "title" not in note.input  # 문장은 문의 쪽이 코드로 만든다


def test_an_inquiry_nobody_has_is_still_not_found(peers: Peers) -> None:
    peers.inquiry_down = True
    result = compensate()
    assert result["outcome"] == "not_found"
    assert peers.by_skill("issue_coupon") == []  # 문의가 없으면 쿠폰도 요청하지 않는다


def test_a_coupon_paid_after_closing_reissues_the_resolved_event(peers: Peers) -> None:
    peers.inquiry.status = "resolved"
    compensate()
    (again,) = resolved()
    assert again.data["coupon_id"] == "cpn_0001" and again.data["resolution"] == "compensated"
    assert again.idempotency_key == f"{CTX}:support.inquiry_resolved:1:coupon:cpn_0001"


def test_a_refund_paid_after_closing_reissues_the_resolved_event(peers: Peers) -> None:
    """문의가 이미 닫힌 뒤 클레임 서비스에서 환불이 끝나면 금액을 채워 한 번 더 보낸다."""
    peers.inquiry.status = "resolved"
    created = call(
        "create_claim",
        {
            "buyer_id": "buy_0002",
            "customer_name": "김하늘",
            "order_id": "102",
            "type": "cancel",
            "reason": "change_of_mind",
            "evidence_count": 0,
            "conversation_id": 1,
        },
        "k-c",
        requester="dashboard-api",
    )
    assert created["status"] == "completed"
    paid = call("process_claim_queue", {}, "k-p", requester="dashboard-api")["output"]
    assert paid["paid"] == 1
    (again,) = resolved()
    assert again.data["refund_amount"] and again.data["refund_amount"] > 0
    assert again.data["claim_id"] == 1
    assert again.idempotency_key == f"{CTX}:support.inquiry_resolved:1:refund:1"
    assert len(peers.by_skill("get_inquiry")) >= 1  # 문의는 문의 서비스에서 읽었다


def test_a_coupon_for_an_open_inquiry_sends_no_event(peers: Peers) -> None:
    compensate()
    assert resolved() == []  # 닫을 때 나가는 이벤트가 쿠폰을 싣는다


def test_the_inquiry_service_logs_the_compensation_like_a_local_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """문의 서비스 쪽: 받은 코드 값으로 같은 모양의 내력을 남기고, 같은 결과는 한 번만 남긴다."""
    store = InquiryStore()
    store.create(
        context_id=CTX,
        tenant_id="shop-demo",
        user_id=1,
        customer_name="김하늘",
        subject="배송 지연",
        order_id=102,
        order_no="ORD-1",
        first_message="배송이 늦어요",
        analysis=Analysis(),
    )
    payload = {
        "inquiry_id": 1,
        "kind": "compensation",
        "compensation_result": "requested",
        "reason_code": "late_delivery",
        "outcome": None,
        "count": 1,
        "total_value": 2000,
        "marketing_status": "completed",
        "coupon_id": "cpn_0001",
    }
    monkeypatch.setenv("AGENT_TEMPLATE_MOCK", "1")
    monkeypatch.setattr(handlers, "STORE", store)
    for key in ("n-1", "n-2"):  # 다시 보내도 내력은 한 줄이다
        res = call("record_claim_activity", payload, key, requester="support")
        assert res["status"] == "completed"
    entries = [a for a in store.get(1).activity if a.kind == "compensation"]
    assert len(entries) == 1
    assert entries[0].title == handlers.COMPENSATION_TITLE["requested"]
    assert entries[0].data["coupon_id"] == "cpn_0001" and entries[0].data["total_value"] == 2000


def test_closing_an_inquiry_reads_its_claim_from_the_claims_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """문의 서비스 프로세스에는 클레임이 없다. 닫을 때 클레임 서비스에서 읽어 환불 금액을 싣는다."""
    monkeypatch.setenv("AGENT_TEMPLATE_MOCK", "1")
    bus.reset_mock_bus()
    # 클레임 서비스 쪽: 문의 1에 이어진 클레임을 만들고 환불까지 끝낸다
    claims_side = ClaimStore()
    monkeypatch.setattr(claim_handlers, "CLAIMS", claims_side)
    monkeypatch.setattr(handlers, "STORE", InquiryStore())
    client = TestClient(app)

    def api(skill: str, input: dict[str, Any], key: str) -> dict[str, Any]:
        body = {
            "meta": {
                "context_id": "ctx_req_0001",
                "token": "mock:dashboard-api",
                "path": [],
                "deadline": (datetime.now(UTC) + timedelta(seconds=30)).isoformat(),
                "idempotency_key": key,
            },
            "skill": skill,
            "input": input,
        }
        return client.post("/a2a", json=body).json()

    created = api(
        "create_claim",
        {
            "buyer_id": "buy_0002",
            "customer_name": "김하늘",
            "order_id": "102",
            "type": "cancel",
            "reason": "change_of_mind",
            "evidence_count": 0,
            "conversation_id": 1,
        },
        "k-c",
    )
    assert created["status"] == "completed"
    paid = api("process_claim_queue", {}, "k-p")["output"]
    assert paid["paid"] == 1
    dumped = [c.model_dump(mode="json") for c in claims_side.list()]

    # 문의 서비스 쪽: 클레임 저장소는 비어 있고, 클레임 처리 내력만 문의에 남아 있다
    monkeypatch.setattr(claim_handlers, "CLAIMS", ClaimStore())
    inquiry_store = InquiryStore()
    inquiry_store.create(
        context_id=CTX,
        tenant_id="shop-demo",
        user_id=1,
        customer_name="김하늘",
        subject="환불 문의",
        order_id=102,
        order_no="ORD-1",
        first_message="취소하고 싶어요",
        analysis=Analysis(),
    )
    inquiry_store.log_activity(1, kind="claim_created", title="클레임 접수", ref="claim:1")
    monkeypatch.setattr(handlers, "STORE", inquiry_store)

    sent: list[AgentRequest] = []

    async def claims_service(target: str, req: AgentRequest) -> AgentResult:
        sent.append(req)
        return AgentResult(meta=req.meta, status=TaskStatus.COMPLETED, output={"items": dumped})

    monkeypatch.setattr(handlers, "SEND", claims_service)
    res = api("set_inquiry_status", {"inquiry_id": 1, "status": "resolved"}, "k-s")
    assert res["status"] == "completed"
    assert [r.skill for r in sent] == ["list_claims"]
    (event,) = resolved()
    assert event.data["claim_id"] == 1
    assert event.data["refund_amount"] == dumped[0]["expected_refund"] > 0
    assert event.data["resolution"] == "claim_decided"


def test_closing_without_any_claim_does_not_call_the_claims_service(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("AGENT_TEMPLATE_MOCK", "1")
    bus.reset_mock_bus()
    store = InquiryStore()
    store.create(
        context_id=CTX,
        tenant_id="shop-demo",
        user_id=1,
        customer_name="김하늘",
        subject="배송 문의",
        order_id=102,
        order_no="ORD-1",
        first_message="언제 와요?",
        analysis=Analysis(),
    )
    monkeypatch.setattr(handlers, "STORE", store)
    monkeypatch.setattr(claim_handlers, "CLAIMS", ClaimStore())
    sent: list[AgentRequest] = []

    async def spy(target: str, req: AgentRequest) -> AgentResult:
        sent.append(req)
        return AgentResult(meta=req.meta, status=TaskStatus.COMPLETED, output={"items": []})

    monkeypatch.setattr(handlers, "SEND", spy)
    res = call(
        "set_inquiry_status", {"inquiry_id": 1, "status": "resolved"}, "s-1", "dashboard-api"
    )
    assert res["status"] == "completed" and sent == []  # 클레임 내력이 없으면 묻지 않는다
