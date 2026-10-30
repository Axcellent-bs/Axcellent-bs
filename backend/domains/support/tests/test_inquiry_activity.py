"""문의의 AI 처리 내력: AI·상담원이 한 일과 이어진 클레임 결과가 시간순으로 문의에 남는다."""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

from ax_contracts.schemas import AgentRequest, AgentResult, TaskStatus
from support_agent import claim_handlers, handlers
from support_agent.claims import ClaimStore
from support_agent.main import app
from support_agent.store import InquiryStore


@pytest.fixture(autouse=True)
def _fresh(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_TEMPLATE_MOCK", "1")
    monkeypatch.setattr(handlers, "STORE", InquiryStore())
    monkeypatch.setattr(claim_handlers, "CLAIMS", ClaimStore())


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def call(
    client: TestClient,
    skill: str,
    input: dict[str, Any] | None = None,
    untrusted: dict[str, Any] | None = None,
    key: str | None = None,
    token: str = "mock:dashboard-api",
) -> dict[str, Any]:
    body = {
        "meta": {
            "context_id": "ctx_inq_0001",
            "token": token,
            "path": [],
            "deadline": (datetime.now(UTC) + timedelta(seconds=30)).isoformat(),
            "idempotency_key": key,
        },
        "skill": skill,
        "input": input or {},
        "untrusted": untrusted or {},
    }
    return client.post("/a2a", json=body).json()


def create(client: TestClient) -> dict[str, Any]:
    return call(
        client,
        "create_inquiry",
        {"user_id": 1, "customer_name": "김하늘", "order_id": "101", "order_no": "ORD-1"},
        {"subject": "책이 찢어졌어요", "message": "표지가 찢어져 왔어요. 환불해 주세요."},
        key="k-create",
    )["output"]


def kinds(client: TestClient, inquiry_id: int) -> list[str]:
    out = call(client, "get_inquiry", {"inquiry_id": inquiry_id})["output"]
    return [a["kind"] for a in out["activity"]]


def test_a_new_inquiry_records_the_ai_analysis(client: TestClient) -> None:
    created = create(client)
    [entry] = created["activity"]
    assert (entry["kind"], entry["actor"]) == ("inquiry_analyzed", "ai")
    assert entry["data"]["category"] == "refund"
    assert entry["data"]["drafts"] == 3
    assert "text" not in entry["data"]  # 고객 원문·답변 본문은 담지 않는다


def test_the_work_on_an_inquiry_is_recorded_in_order(client: TestClient) -> None:
    created = create(client)
    inquiry_id = created["id"]
    draft = created["messages"][0]["analysis"]["drafts"][0]
    call(client, "reject_draft", {"inquiry_id": inquiry_id, "draft_style": "concise"}, key="k-j")
    call(
        client,
        "send_reply",
        {"inquiry_id": inquiry_id, "draft_style": draft["style"], "draft_text": draft["reply"]},
        {"text": draft["reply"]},
        key="k-r",
    )
    call(client, "summarize_inquiry", {"inquiry_id": inquiry_id})
    call(client, "set_inquiry_status", {"inquiry_id": inquiry_id, "status": "resolved"}, key="k-s")

    assert kinds(client, inquiry_id) == [
        "inquiry_analyzed",
        "draft_rejected",
        "reply_sent",
        "summary_made",
        "status_changed",
        "handed_to_orchestrator",
    ]
    entries = call(client, "get_inquiry", {"inquiry_id": inquiry_id})["output"]["activity"]
    sent = next(e for e in entries if e["kind"] == "reply_sent")
    assert (sent["actor"], sent["data"]["how"]) == ("agent", "accepted")
    assert [e["at"] for e in entries] == sorted(e["at"] for e in entries)


def test_a_reply_blocked_by_the_guard_is_recorded_without_the_text(client: TestClient) -> None:
    inquiry_id = create(client)["id"]
    text = "환불을 무조건 약속드립니다. 010-1234-5678로 연락 주세요."
    call(client, "send_reply", {"inquiry_id": inquiry_id}, {"text": text}, key="k-r")

    blocked = call(client, "get_inquiry", {"inquiry_id": inquiry_id})["output"]["activity"][-1]
    assert blocked["kind"] == "reply_blocked"
    assert {"promise_refund", "pii_in_reply"} <= set(blocked["data"]["findings"])
    assert text not in str(blocked)


def test_a_new_customer_message_records_a_new_analysis(client: TestClient) -> None:
    inquiry_id = create(client)["id"]
    call(
        client,
        "add_customer_message",
        {"inquiry_id": inquiry_id},
        {"message": "언제 되나요?"},
        key="k-m",
    )
    assert kinds(client, inquiry_id) == ["inquiry_analyzed", "inquiry_analyzed"]


def test_claim_results_are_recorded_on_the_linked_inquiry(client: TestClient) -> None:
    inquiry_id = create(client)["id"]
    claim = call(
        client,
        "create_claim",
        {
            "buyer_id": "buy_0001",
            "customer_name": "김하늘",
            "order_id": "101",
            "type": "return",
            "reason": "defect",
            "evidence_count": 2,
            "conversation_id": inquiry_id,
        },
        {"description": "표지가 찢어져 왔어요"},
        key="k-c",
    )["output"]
    call(client, "decide_claim", {"claim_id": claim["id"], "decision": "approve"}, key="k-d")
    call(client, "receive_return", {"claim_id": claim["id"], "inspection": "pass"}, key="k-rr")

    got = call(client, "get_inquiry", {"inquiry_id": inquiry_id})["output"]
    assert kinds(client, inquiry_id) == [
        "inquiry_analyzed",
        "claim_created",
        "claim_decided",
        "claim_return_received",
    ]
    decided = next(a for a in got["activity"] if a["kind"] == "claim_decided")
    assert decided["ref"] == f"claim:{claim['id']}"
    assert (decided["actor"], decided["data"]["decision"]) == ("agent", "approve")
    # 상담 콘솔의 "연결된 주문 · 클레임"에 이어진 클레임이 보인다
    assert [c["id"] for c in got["claims"]] == [claim["id"]]
    assert got["claims"][0]["status"] == "return_received"


def test_a_claim_without_an_inquiry_leaves_no_activity(client: TestClient) -> None:
    inquiry_id = create(client)["id"]
    call(
        client,
        "create_claim",
        {
            "buyer_id": "buy_0001",
            "customer_name": "김하늘",
            "order_id": "101",
            "type": "return",
            "reason": "defect",
            "evidence_count": 2,
        },
        {"description": "표지가 찢어져 왔어요"},
        key="k-c2",
    )
    assert kinds(client, inquiry_id) == ["inquiry_analyzed"]
    assert call(client, "get_inquiry", {"inquiry_id": inquiry_id})["output"]["claims"] == []


def test_the_activity_survives_a_restart(memory_repo: type) -> None:
    from support_agent.persistence import PersistentInquiryStore
    from support_agent.schemas import Analysis

    repo = memory_repo()
    store = PersistentInquiryStore(repo)
    created = store.create(
        context_id="ctx_x",
        tenant_id="shop-demo",
        user_id=1,
        customer_name="김하늘",
        subject="s",
        order_id=None,
        order_no=None,
        first_message="m",
        analysis=Analysis(),
    )
    store.log_activity(created.id, kind="summary_made", title="AI가 상담 기록을 정리했어요")
    assert store.log_activity(999, kind="x", title="없는 문의") is None  # 처리를 막지 않는다

    restarted = PersistentInquiryStore(repo)
    assert [a.kind for a in restarted.get(created.id).activity] == ["summary_made"]


CLAIM_INPUT = {
    "buyer_id": "buy_0001",
    "customer_name": "김하늘",
    "order_id": "101",
    "type": "return",
    "reason": "defect",
    "evidence_count": 2,
}


def test_split_services_hand_the_claim_result_to_the_inquiry_service(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    """클레임 서비스에 그 문의가 없으면(프로세스가 나뉘어 있으면) 문의 서비스에 skill로 알린다."""
    sent: list[AgentRequest] = []

    async def fake_send(target: str, req: AgentRequest) -> AgentResult:
        sent.append(req)
        assert target == "support"
        return AgentResult(meta=req.meta, status=TaskStatus.COMPLETED)

    monkeypatch.setattr(handlers, "SEND", fake_send)
    claim = call(
        client,
        "create_claim",
        {**CLAIM_INPUT, "conversation_id": 42},  # 이 서비스에는 문의 42가 없다
        {"description": "표지가 찢어져 왔어요"},
        key="k-c",
    )["output"]
    call(client, "decide_claim", {"claim_id": claim["id"], "decision": "reject"}, key="k-d")

    assert [r.skill for r in sent] == ["record_claim_activity"] * 2
    created, decided = sent
    assert created.input["inquiry_id"] == 42 and created.input["kind"] == "claim_created"
    assert (decided.input["kind"], decided.input["decision"]) == ("claim_decided", "reject")
    assert decided.input["claim_id"] == claim["id"]
    assert created.meta.idempotency_key != decided.meta.idempotency_key
    assert "description" not in str(sent)  # 고객 글은 보내지 않는다


def test_a_failed_hand_over_does_not_break_the_claim(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def down(target: str, req: AgentRequest) -> AgentResult:
        raise ConnectionError("inquiry service is down")

    monkeypatch.setattr(handlers, "SEND", down)
    claim = call(
        client,
        "create_claim",
        {**CLAIM_INPUT, "conversation_id": 42},
        {"description": "표지가 찢어져 왔어요"},
        key="k-c",
    )
    assert claim["status"] == "completed" and claim["output"]["id"]


def test_the_inquiry_service_records_a_claim_result_it_is_told_about(client: TestClient) -> None:
    inquiry_id = create(client)["id"]
    told = call(
        client,
        "record_claim_activity",
        {
            "inquiry_id": inquiry_id,
            "kind": "claim_paid",
            "claim_id": 5,
            "claim_type": "return",
            "reason": "defect",
            "status": "completed",
            "amount": 33000,
        },
        token="mock:support",
        key="k-told",
    )
    assert told["status"] == "completed"
    entry = call(client, "get_inquiry", {"inquiry_id": inquiry_id})["output"]["activity"][-1]
    assert (entry["kind"], entry["actor"], entry["ref"]) == ("claim_paid", "system", "claim:5")
    assert entry["data"]["amount"] == 33000

    unknown = call(
        client,
        "record_claim_activity",
        {"inquiry_id": 999, "kind": "claim_paid", "claim_id": 5},
        token="mock:support",
        key="k-unknown",
    )
    assert unknown["outcome"] == "not_found"


def test_only_the_support_department_may_record_claim_activity(client: TestClient) -> None:
    inquiry_id = create(client)["id"]
    denied = call(
        client,
        "record_claim_activity",
        {"inquiry_id": inquiry_id, "kind": "claim_paid", "claim_id": 5},
        token="mock:marketing",
        key="k-denied",
    )
    assert denied["status"] != "completed"
    assert kinds(client, inquiry_id) == ["inquiry_analyzed"]


def test_an_abusive_message_is_recorded_without_the_words(client: TestClient) -> None:
    created = call(
        client,
        "create_inquiry",
        {"user_id": 1, "customer_name": "김하늘", "order_id": "101", "order_no": "ORD-1"},
        {"subject": "환불", "message": "씨발 표지가 찢어져 왔어요. 환불해 주세요."},
        key="k-abuse",
    )["output"]
    assert [a["kind"] for a in created["activity"]] == ["abusive_message", "inquiry_analyzed"]
    entry = created["activity"][0]
    assert (entry["actor"], entry["data"]["level"], entry["data"]["kinds"]) == (
        "system",
        "profanity",
        ["profanity"],
    )
    assert "씨발" not in str(entry)  # 찾은 낱말도 원문도 담지 않는다
    assert created["messages"][0]["display_text"].startswith("[비속어]")


def test_a_polite_message_leaves_no_abuse_entry(client: TestClient) -> None:
    created = create(client)
    assert "abusive_message" not in [a["kind"] for a in created["activity"]]
