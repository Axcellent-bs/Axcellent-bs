"""`list_incident_compensation`: 사건별로 보상 쿠폰을 받은 고객을 돌려준다 (읽기)."""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

from ax_contracts.schemas import AgentRequest, AgentResult, TaskStatus
from support_agent import compensation, handlers
from support_agent.main import app, load_card
from support_agent.store import InquiryStore

CTX = "ctx_inc_0001"
OTHER = "ctx_inc_0002"
FOUR = ["buy_0001", "buy_0002", "buy_0003", "buy_0004"]


@pytest.fixture(autouse=True)
def sent(monkeypatch: pytest.MonkeyPatch) -> list[AgentRequest]:
    monkeypatch.setenv("AGENT_TEMPLATE_MOCK", "1")
    monkeypatch.setattr(handlers, "STORE", InquiryStore())
    monkeypatch.setattr(handlers, "LEDGER", compensation.SequenceLedger())
    record: list[AgentRequest] = []

    async def fake_send(target: str, req: AgentRequest) -> AgentResult:
        record.append(req)
        return AgentResult(
            meta=req.meta,
            status=TaskStatus.COMPLETED,
            output={"coupon_id": f"cpn_{len(record):04d}", "count": req.input["count"]},
        )

    monkeypatch.setattr(handlers, "SEND", fake_send)
    return record


def _call(skill: str, input: dict[str, Any], context: str, requester: str) -> dict[str, Any]:
    body = {
        "meta": {
            "context_id": context,
            "token": f"mock:{requester}",
            "path": [],
            "deadline": (datetime.now(UTC) + timedelta(seconds=30)).isoformat(),
        },
        "skill": skill,
        "input": input,
    }
    return TestClient(app).post("/a2a", json=body).json()


def compensate(buyers: list[str], context: str = CTX) -> None:
    result = _call("request_incident_compensation", {"buyer_ids": buyers}, context, "orchestrator")
    assert result["status"] == "completed"


def listing(requester: str = "dashboard-api", **input: Any) -> dict[str, Any]:
    return _call("list_incident_compensation", input, "ctx_req_0001", requester)


def test_card_declares_a_read_skill_for_the_dashboard_and_the_orchestrator() -> None:
    skill = {s.id: s for s in load_card().skills}["list_incident_compensation"]
    assert skill.grade == "read" and skill.allowed_requesters == ["dashboard-api", "orchestrator"]
    assert listing(requester="marketing")["status"] == "rejected"


def test_it_lists_who_got_a_coupon_in_each_incident() -> None:
    compensate(FOUR[:3])
    compensate(["buy_0009"], context=OTHER)
    out = listing()["output"]
    by_ctx = {i["context_id"]: i for i in out["incidents"]}
    assert set(by_ctx) == {CTX, OTHER}
    first = by_ctx[CTX]
    assert [b["buyer_id"] for b in first["buyers"]] == FOUR[:3]
    assert first["issued_buyers"] == 3 and first["issued_total"] == 6000
    assert first["buyers"][0] == {
        "buyer_id": "buy_0001",
        "status": "issued",
        "reason_code": "payment_failure",
        "coupon_id": "cpn_0001",
        "count": 1,
        "unit_value": 2000,
        "total_value": 2000,
        "marketing_outcome": None,
    }
    assert by_ctx[OTHER]["issued_buyers"] == 1


def test_one_incident_can_be_asked_for_and_an_unknown_one_is_empty() -> None:
    compensate(FOUR[:2])
    compensate(["buy_0009"], context=OTHER)
    only = listing(incident_context_id=CTX)["output"]["incidents"]
    assert [i["context_id"] for i in only] == [CTX]
    nothing = listing(incident_context_id="ctx_inc_9999")["output"]["incidents"]
    assert nothing == [
        {
            "context_id": "ctx_inc_9999",
            "buyers": [],
            "issued_buyers": 0,
            "issued_total": 0,
            "missing_buyer_ids": None,
        }
    ]


def test_buyer_ids_to_compare_come_back_as_the_missing_ones() -> None:
    compensate(FOUR[:3])
    out = listing(incident_context_id=CTX, buyer_ids=[*FOUR, "buy_0010"])["output"]
    (incident,) = out["incidents"]
    assert incident["missing_buyer_ids"] == ["buy_0004", "buy_0010"]
    # 비교하지 않으면 누락 목록을 만들지 않는다
    assert listing(incident_context_id=CTX)["output"]["incidents"][0]["missing_buyer_ids"] is None


def test_compare_ids_are_normalized_and_a_malformed_one_is_rejected() -> None:
    compensate(["buy_0001"])
    ok = listing(incident_context_id=CTX, buyer_ids=["usr_1", "buy_0002", "buy_0002"])
    assert ok["output"]["incidents"][0]["missing_buyer_ids"] == ["buy_0002"]
    bad = listing(incident_context_id=CTX, buyer_ids=["buy_12"])
    assert bad["status"] == "failed" and bad["outcome"] == "invalid_input"


def test_only_incident_contexts_are_accepted_as_a_filter() -> None:
    assert listing(incident_context_id="ctx_req_0001")["status"] == "failed"


def test_a_coupon_marketing_linked_to_an_earlier_one_is_not_counted_as_issued(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    async def linked(target: str, req: AgentRequest) -> AgentResult:
        return AgentResult(
            meta=req.meta,
            status=TaskStatus.COMPLETED,
            outcome="linked_duplicate",
            output={"coupon_id": "cpn_0001", "count": 1},
        )

    monkeypatch.setattr(handlers, "SEND", linked)
    compensate(["buy_0001"])
    (incident,) = listing(incident_context_id=CTX)["output"]["incidents"]
    assert incident["buyers"][0]["status"] == "linked_duplicate"
    assert incident["issued_buyers"] == 0 and incident["issued_total"] == 0


def test_a_held_coupon_has_no_record_yet(monkeypatch: pytest.MonkeyPatch) -> None:
    async def held(target: str, req: AgentRequest) -> AgentResult:
        return AgentResult(
            meta=req.meta,
            status=TaskStatus.INPUT_REQUIRED,
            reason_code="human_input",
            outcome="held",
            output={"review_id": "rev_1"},
        )

    monkeypatch.setattr(handlers, "SEND", held)
    compensate(["buy_0001"])
    (incident,) = listing(incident_context_id=CTX)["output"]["incidents"]
    assert incident["buyers"] == []  # 지급이 끝나야 기록에 남는다
    notes = listing(incident_context_id=CTX)["output"]["notes"]
    assert any("보류" in n for n in notes)


def test_it_changes_nothing_and_carries_no_customer_text() -> None:
    compensate(FOUR[:2])
    before = dict(handlers.LEDGER.results)
    result = listing()
    assert dict(handlers.LEDGER.results) == before
    text = str(result)
    assert "원문" not in text and "customer" not in text
