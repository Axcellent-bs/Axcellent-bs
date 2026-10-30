"""목 모드가 아닐 때 클레임·보상 큐·KPI 경로가 support skill을 부르고 화면 모델로 바꾸는지 본다.

응답 dict는 support 에이전트 출력(`claim_schemas.py`)의 실제 모양을 본뜬 것이다.
"""

from copy import deepcopy
from typing import Any

import pytest
from fastapi.testclient import TestClient

from ax_contracts.schemas import AgentRequest, AgentResult, TaskStatus
from ax_dashboard_api import support_client
from ax_dashboard_api.main import app

BASE = "/api/dashboard"

RECEIPT = {
    "order_no": "ORD-2026-0101",
    "status": "delivered",
    "items_total": 30000,
    "shipping_fee": 3000,
    "points_used": 0,
    "paid_amount": 33000,
    "paid_at": "2026-10-10T00:00:00Z",
    "delivered_at": "2026-10-14T00:00:00Z",
    "items": [
        {
            "id": "itm_1",
            "title": "고양이의 하루",
            "qty": 1,
            "paid_amount": 15000,
            "points_share": 0,
            "refunded_qty": 0,
        }
    ],
}
CLAIM = {
    "id": 1,
    "tenant_id": "shop-demo",
    "context_id": "ctx_ui_0001",
    "order_id": "101",
    "order_no": "ORD-2026-0101",
    "buyer_id": "buy_0001",
    "customer": "김하늘",
    "type": "return",
    "reason": "defect",
    "status": "needs_review",
    "created_at": "2026-10-22T01:00:00Z",
    "updated_at": "2026-10-22T01:00:00Z",
    "outcome": "needs_review",
    "resolution": "환불",
    "requires_return": True,
    "evidence_count": 2,
    "unsealed": False,
    "conversation_id": None,
    "reasons": [
        {
            "code": "DEFECT_EVIDENCE",
            "level": "review",
            "message": "사진 증빙 확인 필요",
            "rule": "refund-2",
        },
        {"code": "HIGH_AMOUNT", "level": "review", "message": "5만 원 이상", "rule": None},
    ],
    "lines": [
        {
            "order_item_id": "itm_1",
            "title": "고양이의 하루",
            "qty": 1,
            "requested_qty": 1,
            "unit_price": 15000,
            "refund_cash": 15000,
            "points_back": 0,
        }
    ],
    "deductions": [{"code": "RETURN_SHIPPING", "amount": 6000, "message": "왕복 배송비"}],
    "shipping_refund": 3000,
    "expected_refund": 33000,
    "points_back": 0,
    "abuse_score": 30,
    "inspection": None,
    "return_received_at": None,
    "refund_paid": 0,
    "processed_at": None,
    "decided_by": None,
    "agent_note": None,
    "inspection_note": None,
    "receipt": RECEIPT,
    "audit": [
        {
            "actor": "support",
            "action": "decide",
            "detail": {"outcome": "needs_review", "abuse_score": 30},
            "at": "2026-10-22T01:00:00Z",
        }
    ],
}
ENTRY = {
    "position": 1,
    "claim_id": 1,
    "order_no": "ORD-2026-0101",
    "customer": "김하늘",
    "type": "return",
    "reason": "defect",
    "received_at": "2026-10-22T03:00:00Z",
    "inspection": None,
    "amount": 33000,
    "ready": False,
    "blockers": [
        {"code": "NOT_INSPECTED", "level": "block", "message": "검수 전이라 보류", "rule": None}
    ],
    "items": [{"title": "고양이의 하루", "qty": 1}],
}


def claim(**changes: Any) -> dict[str, Any]:
    return {**deepcopy(CLAIM), **changes}


class Fake:
    """support 대역. skill별 출력을 `outputs`에 넣어 둔다."""

    def __init__(self) -> None:
        self.sent: list[AgentRequest] = []
        self.outputs: dict[str, dict[str, Any]] = {}
        self.outcome: str | None = None
        self.outcomes: dict[str, str] = {}  # skill별 결과 (없으면 `outcome`)

    async def __call__(self, target: str, req: AgentRequest) -> AgentResult:
        assert target == "support"
        self.sent.append(req)
        return AgentResult(
            meta=req.meta,
            status=TaskStatus.COMPLETED,
            outcome=self.outcomes.get(req.skill, self.outcome),
            output=self.outputs.get(req.skill, {}),
        )


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> Fake:
    monkeypatch.delenv("DASHBOARD_MOCK", raising=False)
    monkeypatch.delenv("AGENT_TEMPLATE_MOCK", raising=False)
    monkeypatch.setenv("DASHBOARD_API_TOKEN", "test-token")
    fake = Fake()
    monkeypatch.setattr(support_client, "SEND", fake)
    return fake


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


# ---------------------------------------------------------------- 읽기
def test_list_maps_status_rules_and_hides_refund_on_rejected(
    client: TestClient, fake: Fake
) -> None:
    fake.outputs["list_claims"] = {
        "items": [claim(), claim(id=2, status="approved"), claim(id=3, status="rejected")]
    }
    body = client.get(BASE + "/claims").json()
    rows = {c["id"]: c for c in body["data"]}
    assert body["meta"]["total"] == 3
    assert rows[2]["status"] == "return_received"  # 화면에 없는 approved는 처리 대기로 보낸다
    assert [r["code"] for r in rows[1]["reasons"]] == ["DEFECT_EVIDENCE", "OVER_AUTO_LIMIT"]
    assert rows[1]["deductions"][0]["code"] == "RETURN_FEE"
    assert rows[1]["payable"] == 33000 and rows[1]["abuse"]["score"] == 30
    assert rows[3]["payable"] == 0 and rows[3]["expected_refund"] == 0
    assert fake.sent[0].skill == "list_claims" and fake.sent[0].meta.idempotency_key is None


def test_list_filters_on_screen_status(client: TestClient, fake: Fake) -> None:
    fake.outputs["list_claims"] = {"items": [claim(), claim(id=2, status="approved")]}
    ids = [c["id"] for c in client.get(BASE + "/claims?status=return_received").json()["data"]]
    assert ids == [2]


def test_detail_maps_audit_receipt_inspection_and_approval(client: TestClient, fake: Fake) -> None:
    audit = claim()["audit"] + [
        {
            "actor": "support",
            "action": "approve",
            "detail": {"agent_action": "approve"},
            "at": "t1",
        },
        {"actor": "support", "action": "receive", "detail": {}, "at": "t2"},
    ]
    fake.outputs["get_claim"] = claim(
        status="return_received",
        return_received_at="2026-10-22T03:00:00Z",
        audit=audit,
        agent_note="확인함",
    )
    data = client.get(BASE + "/claims/1").json()["data"]
    assert [a["action"] for a in data["audit"]] == ["decided", "agent_override", "received"]
    assert data["inspection"] == "pending"  # 입고했는데 검수 전
    assert data["manager_approved"] is True and data["agent_note"] == "확인함"
    assert data["receipt"]["items"][0]["title"] == "고양이의 하루"
    assert fake.sent[0].input == {"claim_id": 1}


@pytest.mark.parametrize(
    ("support", "screen"), [("pass", "ok"), ("fail", "mismatch"), ("partial", "mismatch")]
)
def test_inspection_values_use_screen_vocabulary(
    client: TestClient, fake: Fake, support: str, screen: str
) -> None:
    fake.outputs["get_claim"] = claim(
        status="return_received", return_received_at="t", inspection=support
    )
    assert client.get(BASE + "/claims/1").json()["data"]["inspection"] == screen


def test_stats_merge_approved_and_map_rule_codes(client: TestClient, fake: Fake) -> None:
    fake.outputs["claim_stats"] = {
        "total": 3,
        "by_status": {"needs_review": 1, "approved": 1, "return_received": 1},
        "rule_hits": {"HIGH_AMOUNT": 2, "DEFECT_EVIDENCE": 1},
        "agent_reviews": 1,
        "agent_overrides": 0,
    }
    data = client.get(BASE + "/claims/stats").json()["data"]
    assert data["by_status"] == {"needs_review": 1, "return_received": 2}
    assert data["rule_hits"] == {"OVER_AUTO_LIMIT": 2, "DEFECT_EVIDENCE": 1}
    assert data["agent_reviews"] == 1 and data["overridden_rules"] == {}


def test_queue_entries_and_awaiting_return(client: TestClient, fake: Fake) -> None:
    ready = {
        **ENTRY,
        "position": 2,
        "claim_id": 2,
        "ready": True,
        "blockers": [],
        "inspection": "pass",
    }
    fake.outputs["claim_queue"] = {
        "queue": [ENTRY, ready],
        "awaiting_return": [claim(status="awaiting_return")],
    }
    body = client.get(BASE + "/queue-entries").json()
    first, second = body["data"]["queue"]
    assert body["meta"]["total"] == 2
    assert first["inspection"] == "pending" and first["blockers"] == [
        {"code": "NOT_INSPECTED", "message": "검수 전이라 보류"}
    ]
    assert second["inspection"] == "ok" and second["compensation"] == 0
    assert body["data"]["awaiting_return"][0]["status"] == "awaiting_return"


def test_kpis_come_from_inquiry_overview(client: TestClient, fake: Fake) -> None:
    fake.outputs["inquiry_overview"] = {
        "kpis": {
            "conversations": 2,
            "by_status": {"open": 2},
            "agent_replies": 1,
            "draft_adoption_rate": 0.5,
            "accepted_as_is": 1,
            "edited": 0,
            "rejected": 1,
            "avg_edit_ratio": None,
            "avg_first_draft_ms": 500,
            "drafts_generated": 3,
            "drafts_blocked_by_guard": 1,
            "guard_findings": {"promise_refund": 1},
            "by_category": {"refund": 1},
            "by_urgency": {"high": 1},
        },
        "waiting": [],
    }
    data = client.get(BASE + "/kpis").json()["data"]
    assert data["draft_adoption_rate"] == 0.5 and data["avg_first_draft_ms"] == 500
    assert "by_category" not in data  # 화면 KPI 모델에 없는 필드는 빼고 준다


# ---------------------------------------------------------------- 쓰기
def test_approve_sends_note_as_untrusted_with_idempotency_key(
    client: TestClient, fake: Fake
) -> None:
    fake.outputs["decide_claim"] = claim(status="awaiting_return", decided_by="agent_console")
    response = client.post(BASE + "/claims/1/approve", json={"note": "사진 확인"})
    assert response.status_code == 200
    sent = fake.sent[0]
    assert sent.skill == "decide_claim"
    assert sent.input == {"claim_id": 1, "decision": "approve"}
    assert sent.untrusted == {"note": "사진 확인"}
    assert sent.meta.idempotency_key == f"{sent.meta.context_id}:decide_claim:claim:1:approve"
    assert response.json()["data"]["status"] == "awaiting_return"


def test_reject_calls_decide_claim(client: TestClient, fake: Fake) -> None:
    fake.outputs["decide_claim"] = claim(status="rejected")
    client.post(BASE + "/claims/1/reject", json={"note": ""})
    assert fake.sent[0].input == {"claim_id": 1, "decision": "reject"}


def test_receive_and_inspection_translate_screen_values(client: TestClient, fake: Fake) -> None:
    fake.outputs["receive_return"] = claim(status="return_received", return_received_at="t")
    fake.outputs["inspect_return"] = claim(
        status="return_received", return_received_at="t", inspection="fail"
    )
    client.post(BASE + "/claims/1/receive", json={"inspection": "ok"})
    client.post(BASE + "/claims/1/inspection", json={"inspection": "mismatch", "note": "다른 상품"})
    receive, inspect = fake.sent
    assert receive.skill == "receive_return" and receive.input["inspection"] == "pass"
    assert inspect.skill == "inspect_return" and inspect.input["inspection"] == "fail"
    assert inspect.untrusted == {"note": "다른 상품"}


def test_unknown_inspection_value_is_422_without_calling_support(
    client: TestClient, fake: Fake
) -> None:
    response = client.post(BASE + "/claims/1/receive", json={"inspection": "bogus"})
    assert response.status_code == 422
    assert response.json()["error"]["code"] == "INVALID_REQUEST"
    assert fake.sent == []


def test_invalid_state_becomes_409(client: TestClient, fake: Fake) -> None:
    fake.outcome = "invalid_state"
    fake.outputs["decide_claim"] = {"message": "클레임 1는 rejected 상태라 결정할 수 없다"}
    response = client.post(BASE + "/claims/1/approve", json={"note": ""})
    assert response.status_code == 409
    assert response.json()["error"]["code"] == "INVALID_STATE"


def test_not_found_becomes_404(client: TestClient, fake: Fake) -> None:
    fake.outcome = "not_found"
    fake.outputs["get_claim"] = {"message": "클레임 9를 찾을 수 없다"}
    assert client.get(BASE + "/claims/9").status_code == 404


def test_process_queue_maps_results_and_blockers(client: TestClient, fake: Fake) -> None:
    fake.outputs["process_claim_queue"] = {
        "results": [
            {"claim_id": 1, "result": "paid", "detail": None, "blockers": [], "refund": 12000},
            {
                "claim_id": 2,
                "result": "held",
                "detail": "verifying",
                "refund": None,
                "blockers": [
                    {
                        "code": "ON_HOLD",
                        "level": "block",
                        "message": "재무 교차검증 중",
                        "rule": None,
                    }
                ],
            },
        ],
        "paid": 1,
        "held": 1,
        "queue": [ENTRY],
    }
    response = client.post(BASE + "/queue-entries/process", json={"limit": 5})
    data = response.json()["data"]
    assert data["paid"] == 1 and data["held"] == 1
    assert data["results"][0] == {
        "claim_id": 1,
        "result": "paid",
        "blockers": [],
        "refund": 12000,
        "compensation": 0,
    }
    assert data["results"][1]["blockers"] == [{"code": "ON_HOLD", "message": "재무 교차검증 중"}]
    assert fake.sent[0].input == {"limit": 5, "claim_ids": None}


def test_process_queue_without_body(client: TestClient, fake: Fake) -> None:
    fake.outputs["process_claim_queue"] = {"results": [], "paid": 0, "held": 0, "queue": []}
    assert client.post(BASE + "/queue-entries/process").status_code == 200
    assert fake.sent[0].input == {"limit": None, "claim_ids": None}


def test_detail_without_receipt_is_a_bad_gateway(client: TestClient, fake: Fake) -> None:
    fake.outputs["get_claim"] = claim(receipt=None)
    response = client.get(BASE + "/claims/1")
    assert response.status_code == 502
    assert response.json()["error"]["code"] == "UPSTREAM_ERROR"


def test_string_order_and_buyer_ids_pass_through_and_user_id_is_derived(
    client: TestClient, fake: Fake
) -> None:
    fake.outputs["list_claims"] = {
        "items": [
            claim(id=1, order_id="ord_0201", buyer_id="buy_0007"),
            claim(id=2, order_id="ord_0202", buyer_id="buyer-x"),  # buy_<번호> 형식이 아니다
        ]
    }
    rows = {c["id"]: c for c in client.get(BASE + "/claims").json()["data"]}
    assert rows[1]["order_id"] == "ord_0201" and rows[1]["user_id"] == 7
    assert rows[2]["order_id"] == "ord_0202" and rows[2]["user_id"] is None  # 지어내지 않는다


def test_detail_keeps_string_item_ids(client: TestClient, fake: Fake) -> None:
    fake.outputs["get_claim"] = claim(order_id="ord_0201")
    data = client.get(BASE + "/claims/1").json()["data"]
    assert data["order_id"] == "ord_0201"
    assert data["lines"][0]["order_item_id"] == "itm_1"
    assert data["receipt"]["items"][0]["id"] == "itm_1"


def test_user_numbers_and_buyer_ids_convert_both_ways() -> None:
    from ax_dashboard_api import claim_mapping

    assert claim_mapping.buyer_id_for(1) == "buy_0001"
    assert claim_mapping.buyer_id_for(123) == "buy_0123"
    assert claim_mapping._user_id("buy_0001") == 1
    assert claim_mapping._user_id("buy_0123") == 123
    assert claim_mapping._user_id("usr_1") is None and claim_mapping._user_id("buyer-x") is None


def test_conversation_carries_linked_claims_and_activity(client: TestClient, fake: Fake) -> None:
    """상담 콘솔이 문의를 열면 이어진 클레임(화면 모양)과 AI 처리 내력이 함께 온다."""
    fake.outputs["get_inquiry"] = {
        "id": 7,
        "customer_name": "김하늘",
        "subject": "배송 문의",
        "status": "open",
        "updated_at": "2026-10-22T00:00:00Z",
        "preview": "언제 오나요",
        "awaiting_reply": True,
        "messages": [],
        "claims": [{**CLAIM, "conversation_id": 7}],
        "activity": [
            {
                "at": "2026-10-22T00:00:01Z",
                "actor": "agent",
                "kind": "claim_decided",
                "title": "상담원이 클레임을 승인했어요",
                "ref": "claim:1",
                "data": {"claim_id": 1, "decision": "approve"},
            }
        ],
    }
    data = client.get(BASE + "/conversations/7").json()["data"]
    assert [c["id"] for c in data["claims"]] == [1]
    assert data["claims"][0]["conversation_id"] == 7
    assert data["activity"][0]["kind"] == "claim_decided"
    assert data["activity"][0]["data"]["decision"] == "approve"


def test_conversation_reads_linked_claims_from_the_claims_service(
    client: TestClient, fake: Fake
) -> None:
    """문의 서비스는 클레임을 모른다(프로세스가 다르다). 클레임 서비스에서 읽어 붙인다."""
    fake.outputs["get_inquiry"] = {
        "id": 7,
        "customer_name": "김하늘",
        "subject": "배송 문의",
        "status": "open",
        "updated_at": "2026-10-22T00:00:00Z",
        "preview": "언제 오나요",
        "awaiting_reply": True,
        "messages": [],
        "claims": [],
        "activity": [],
    }
    fake.outputs["list_claims"] = {
        "items": [
            {**CLAIM, "id": 1, "conversation_id": 7},
            {**CLAIM, "id": 2, "conversation_id": 8},  # 다른 문의의 클레임은 붙이지 않는다
            {**CLAIM, "id": 3, "conversation_id": None},
        ]
    }
    data = client.get(BASE + "/conversations/7").json()["data"]
    assert [c["id"] for c in data["claims"]] == [1]
    assert {r.skill for r in fake.sent} == {"get_inquiry", "list_claims"}


# ---------------------------------------------------------------- 상담 콘솔 클레임 접수
INQUIRY = {
    "id": 7,
    "user_id": 1,
    "customer_name": "김하늘",
    "subject": "찢어졌어요",
    "status": "open",
    "order_id": "ord_0002",
    "updated_at": "2026-10-22T00:00:00Z",
    "preview": "p",
    "awaiting_reply": True,
    "messages": [],
}


def test_claim_suggestion_reads_the_request_without_filing_it(
    client: TestClient, fake: Fake
) -> None:
    fake.outputs["analyze_claim_request"] = {
        "request_kind": "refund",
        "reason": "defect",
        "claim_type": "return",
        "items": ["고양이의 하루"],
        "unmatched_items": [],
        "source": "llm",
        "notes": [],
        "preview": {"outcome": "needs_review", "expected_refund": 33000, "reasons": []},
        "suggested_claim": {
            "buyer_id": "buy_0001",
            "items": [{"order_item_id": "itm_1", "qty": 1}],
        },
    }
    data = client.post(BASE + "/conversations/7/claim-suggestion").json()["data"]
    assert (data["claim_type"], data["reason"]) == ("return", "defect")
    assert data["preview"]["expected_refund"] == 33000
    assert data["suggested_items"] == [{"order_item_id": "itm_1", "qty": 1}]
    assert [r.skill for r in fake.sent] == ["analyze_claim_request"]  # 접수하지 않는다
    assert fake.sent[0].input == {"inquiry_id": 7}


def test_claim_suggestion_without_a_customer_message_is_a_conflict(
    client: TestClient, fake: Fake
) -> None:
    fake.outputs["analyze_claim_request"] = {"message": "분석할 고객 메시지가 없다"}
    response = client.post(BASE + "/conversations/7/claim-suggestion")
    assert response.status_code == 409 and response.json()["error"]["code"] == "NO_REQUEST"


def test_console_files_a_claim_for_the_inquiry_order_and_buyer(
    client: TestClient, fake: Fake
) -> None:
    fake.outputs["get_inquiry"] = INQUIRY
    fake.outputs["create_claim"] = claim(status="needs_review", conversation_id=7)
    response = client.post(
        BASE + "/conversations/7/claims",
        json={"type": "return", "reason": "defect", "evidence_count": 2, "description": "찢어짐"},
    )
    assert response.status_code == 201
    assert response.json()["data"]["conversation_id"] == 7
    create = next(r for r in fake.sent if r.skill == "create_claim")
    # 구매자·주문은 화면이 정하지 않고 문의에서 읽는다
    assert create.input["buyer_id"] == "buy_0001" and create.input["order_id"] == "ord_0002"
    assert create.input["conversation_id"] == 7 and create.input["customer_name"] == "김하늘"
    assert create.untrusted == {"description": "찢어짐"}  # 고객·상담원 글은 untrusted로만
    assert create.meta.idempotency_key.endswith(":create_claim:inquiry:7:order:ord_0002:return")


def test_console_cannot_file_a_claim_for_an_inquiry_without_an_order(
    client: TestClient, fake: Fake
) -> None:
    fake.outputs["get_inquiry"] = {**INQUIRY, "order_id": None}
    response = client.post(
        BASE + "/conversations/7/claims", json={"type": "cancel", "reason": "change_of_mind"}
    )
    assert response.status_code == 409 and response.json()["error"]["code"] == "NO_ORDER"
    assert [r.skill for r in fake.sent] == ["get_inquiry"]


def test_a_duplicate_claim_is_reported_not_swallowed(client: TestClient, fake: Fake) -> None:
    fake.outputs["get_inquiry"] = INQUIRY
    fake.outputs["create_claim"] = {"message": "같은 품목에 진행 중인 클레임이 있다"}
    fake.outcomes["create_claim"] = "duplicate_claim"
    response = client.post(
        BASE + "/conversations/7/claims", json={"type": "cancel", "reason": "other"}
    )
    assert response.status_code == 409 and response.json()["error"]["code"] == "DUPLICATE_CLAIM"
    assert [r.skill for r in fake.sent] == ["get_inquiry", "create_claim"]


# ---------------------------------------------------------------- 후기(해결 결과 평가)
FEEDBACK = {
    "rating": 2,
    "resolved": False,
    "comment": "아직도 안 왔어요",
    "status": "visible",
    "flags": [{"code": "CS_FOLLOW_UP", "message": "선제 연락이 필요하다", "info": True}],
    "source": "customer",
    "created_at": "2026-10-29T07:00:00Z",
}


def test_feedback_list_passes_filters_and_returns_items(client: TestClient, fake: Fake) -> None:
    fake.outputs["list_feedback"] = {
        "items": [
            {
                "inquiry_id": 7,
                "customer_name": "김하늘",
                "subject": "배송 문의",
                "category": "shipping",
                "feedback": FEEDBACK,
            }
        ],
        "total": 1,
    }
    body = client.get(BASE + "/feedback?status=visible&max_rating=2&limit=20").json()
    assert body["meta"]["total"] == 1
    assert body["data"][0]["feedback"]["flags"][0]["code"] == "CS_FOLLOW_UP"
    sent = fake.sent[0]
    assert sent.skill == "list_feedback"
    assert sent.input == {"limit": 20, "status": "visible", "max_rating": 2}


def test_feedback_stats_are_passed_through_without_inventing_numbers(
    client: TestClient, fake: Fake
) -> None:
    fake.outputs["feedback_overview"] = {
        "resolved_inquiries": 0,
        "rated": 0,
        "response_rate": None,
        "average_rating": None,
        "satisfied_rate": None,
        "solved_rate": None,
        "distribution": {"1": 0, "2": 0, "3": 0, "4": 0, "5": 0},
        "by_category": {},
        "pending_review": 0,
        "hidden": 0,
        "follow_up": 0,
    }
    data = client.get(BASE + "/feedback/stats").json()["data"]
    assert (
        data["average_rating"] is None and data["satisfied_rate"] is None
    )  # 0으로 지어내지 않는다


def test_hiding_a_review_asks_the_support_skill_with_an_idempotency_key(
    client: TestClient, fake: Fake
) -> None:
    fake.outputs["set_feedback_status"] = {
        "inquiry_id": 7,
        "customer_name": "김하늘",
        "subject": "s",
        "feedback": {**FEEDBACK, "status": "hidden", "decided_by": "dashboard-api"},
    }
    response = client.post(BASE + "/feedback/7/status", json={"status": "hidden"})
    assert response.status_code == 200 and response.json()["data"]["feedback"]["status"] == "hidden"
    sent = fake.sent[0]
    assert sent.skill == "set_feedback_status" and sent.input == {
        "inquiry_id": 7,
        "status": "hidden",
    }
    assert sent.meta.idempotency_key.endswith(":set_feedback_status:inquiry:7:hidden")
    assert client.post(BASE + "/feedback/7/status", json={"status": "flagged"}).status_code == 422


def test_console_files_a_test_review_and_returns_the_conversation(
    client: TestClient, fake: Fake
) -> None:
    fake.outputs["submit_feedback"] = {"inquiry_id": 7, "rated": True, "rating": 4}
    fake.outputs["get_inquiry"] = {**INQUIRY, "status": "resolved", "feedback": FEEDBACK}
    response = client.post(
        BASE + "/conversations/7/feedback",
        json={"rating": 4, "resolved": True, "comment": "빨라서 좋았어요"},
    )
    assert response.status_code == 201
    assert response.json()["data"]["feedback"]["rating"] == 2  # 문의를 다시 읽어 돌려준다
    submit = next(r for r in fake.sent if r.skill == "submit_feedback")
    assert submit.input == {"inquiry_id": 7, "rating": 4, "resolved": True, "source": "console"}
    assert submit.untrusted == {"comment": "빨라서 좋았어요"}  # 고객이 쓴 글은 untrusted로만
    bad = client.post(BASE + "/conversations/7/feedback", json={"rating": 6, "resolved": True})
    assert bad.status_code == 422


def test_rating_an_unresolved_inquiry_is_a_conflict(client: TestClient, fake: Fake) -> None:
    fake.outputs["submit_feedback"] = {"message": "해결된 문의에만 평가를 남길 수 있다"}
    fake.outcomes["submit_feedback"] = "invalid_state"
    response = client.post(BASE + "/conversations/7/feedback", json={"rating": 5, "resolved": True})
    assert response.status_code == 409 and response.json()["error"]["code"] == "INVALID_STATE"


# ---------------------------------------------------------------- 해결 메모
NOTE = {
    "id": 1,
    "author": "tester",
    "root_cause": "택배사 분류 오류",
    "action_taken": None,
    "result": "다음 날 도착",
    "created_at": "2026-10-30T01:00:00Z",
}


def test_console_adds_a_resolution_note_with_the_author_from_the_token(
    client: TestClient, fake: Fake
) -> None:
    fake.outputs["add_resolution_note"] = {"inquiry_id": 7, "note_id": 1}
    fake.outputs["get_inquiry"] = {**INQUIRY, "status": "resolved", "resolution_notes": [NOTE]}
    response = client.post(
        BASE + "/conversations/7/resolution-notes",
        json={
            "root_cause": "택배사 분류 오류",
            "result": "다음 날 도착",
            "author": "남이 보낸 이름",  # 본문의 작성자는 쓰지 않는다
        },
    )
    assert response.status_code == 201
    notes = response.json()["data"]["resolution_notes"]
    assert notes[0]["author"] == "tester" and notes[0]["action_taken"] is None
    sent = next(r for r in fake.sent if r.skill == "add_resolution_note")
    assert sent.input == {"inquiry_id": 7, "author": "tester"}  # 사람 토큰의 이름
    assert sent.untrusted == {
        "root_cause": "택배사 분류 오류",
        "action_taken": "",
        "result": "다음 날 도착",
    }  # 상담원이 쓴 글은 untrusted로만
    assert sent.meta.idempotency_key.endswith(":add_resolution_note:inquiry:7")


@pytest.mark.parametrize(
    "body",
    [
        {},
        {"root_cause": "  ", "action_taken": "", "result": " "},
        {"root_cause": "가" * 1001},
    ],
)
def test_an_empty_or_too_long_note_is_a_422_and_support_is_not_called(
    client: TestClient, fake: Fake, body: dict[str, str]
) -> None:
    response = client.post(BASE + "/conversations/7/resolution-notes", json=body)
    assert response.status_code == 422
    assert not [r for r in fake.sent if r.skill == "add_resolution_note"]


def test_a_note_on_an_unresolved_inquiry_is_a_conflict(client: TestClient, fake: Fake) -> None:
    fake.outputs["add_resolution_note"] = {"message": "해결된 문의에만 해결 메모를 남길 수 있다"}
    fake.outcomes["add_resolution_note"] = "invalid_state"
    response = client.post(BASE + "/conversations/7/resolution-notes", json={"result": "끝"})
    assert response.status_code == 409 and response.json()["error"]["code"] == "INVALID_STATE"


def test_a_note_needs_a_human_token(client: TestClient, fake: Fake) -> None:
    client.headers.pop("authorization")
    response = client.post(BASE + "/conversations/7/resolution-notes", json={"result": "끝"})
    assert response.status_code == 401
    assert not fake.sent
