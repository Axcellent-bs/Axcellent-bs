"""변경 요청 제안 보내기·보내지 않기 (#281 2단계): 안전 관문, 보낸 사실 기록, 이유 기록, 영속."""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

from ax_contracts.schemas import AgentRequest, AgentResult, ExecuteDecision, TaskStatus
from support_agent import change_handlers, handlers
from support_agent.change_requests import ChangeRequestLink, ChangeRequestStore
from support_agent.main import app, load_card
from support_agent.persistence import (
    TABLES,
    PersistentChangeRequestStore,
    PersistentProposalDecisionStore,
)
from support_agent.proposal_decisions import ProposalDecisionStore
from support_agent.schemas import Analysis, Classification
from support_agent.store import InquiryStore

CTX_REQ = "ctx_req_0001"
SEND_INPUT = {
    "inquiry_id": 1,
    "title": "배송 문의 급증 — 안내·화면 점검 검토",
    "evidence": "최근 60분 배송 문의 7건 (평소 창당 1.4건의 5배)",
    "evidence_ids": ["inq_2", "inq_3"],
}
DECLINE_INPUT = {
    "topic_code": "shipping",
    "inquiry_id": 1,
    "reason": "already_known",
    "author": "상담원A",
}


class Sent(list):
    """기획으로 보낸 요청 목록."""


def ticket_reply(req: AgentRequest) -> AgentResult:
    output = {"request_id": "REQ-1", "context_id": CTX_REQ, "outcome": "created"}
    return AgentResult(meta=req.meta, status=TaskStatus.COMPLETED, outcome="created", output=output)


@pytest.fixture(autouse=True)
def _fresh(monkeypatch: pytest.MonkeyPatch) -> Sent:
    monkeypatch.setenv("AGENT_TEMPLATE_MOCK", "1")
    store = InquiryStore()
    for _ in range(3):
        store.create(
            context_id="ctx_inq_0001",
            tenant_id="shop-demo",
            user_id=1,
            customer_name="김하늘",
            subject="배송 지연",
            order_id=None,
            order_no=None,
            first_message="본문",
            analysis=Analysis(
                classification=Classification(
                    intent_ko="문의",
                    category="shipping",
                    sentiment="calm",
                    urgency="medium",
                    search_query="shipping",
                )
            ),
        )
    monkeypatch.setattr(handlers, "STORE", store)
    monkeypatch.setattr(handlers, "CHANGES", ChangeRequestStore())
    monkeypatch.setattr(handlers, "DECISIONS", ProposalDecisionStore())
    sent = Sent()

    async def fake_send(target: str, req: AgentRequest) -> AgentResult:
        assert target == "planning"
        sent.append(req)
        return ticket_reply(req)

    monkeypatch.setattr(handlers, "SEND", fake_send)
    return sent


def call(
    skill: str,
    input: dict[str, Any] | None = None,
    untrusted: dict[str, Any] | None = None,
    key: str | None = None,
) -> dict[str, Any]:
    body = {
        "meta": {
            "context_id": "ctx_ui_0001",
            "token": "mock:dashboard-api",
            "path": [],
            "deadline": (datetime.now(UTC) + timedelta(seconds=30)).isoformat(),
            "idempotency_key": key,
        },
        "skill": skill,
        "input": input or {},
        "untrusted": untrusted or {},
    }
    return TestClient(app).post("/a2a", json=body).json()


def held(monkeypatch: pytest.MonkeyPatch, module: Any) -> None:
    async def decision(*args: Any, **kwargs: Any) -> ExecuteDecision:
        return ExecuteDecision(
            execution_id="exe_1",
            grade="C",
            status="held",
            hold_reason="unregistered_action",
            review_id="rev_1",
        )

    monkeypatch.setattr(module, "execute", decision)


# ---------------------------------------------------------------- 카드
def test_the_card_declares_the_decline_skill_as_a_dashboard_write() -> None:
    skill = {s.id: s for s in load_card().skills}["decline_change_proposal"]
    assert skill.grade == "A" and skill.allowed_requesters == ["dashboard-api"]


# ---------------------------------------------------------------- request_change: 안전 관문
def test_a_request_that_the_gate_does_not_execute_sends_and_stores_nothing(
    _fresh: Sent, monkeypatch: pytest.MonkeyPatch
) -> None:
    held(monkeypatch, change_handlers)
    result = call("request_change", SEND_INPUT)
    assert result["outcome"] == "not_executed_held"
    assert _fresh == []  # 기획으로 나가지 않았다
    assert handlers.CHANGES.all() == []  # 연결도 만들지 않았다


def test_a_sent_request_keeps_the_facts_that_were_sent(_fresh: Sent) -> None:
    result = call("request_change", SEND_INPUT)
    assert result["outcome"] == "created"
    link = handlers.CHANGES.all()[0]
    assert link.sent_title == SEND_INPUT["title"]
    assert link.sent_evidence == SEND_INPUT["evidence"]
    assert link.sent_evidence_ids == ["inq_2", "inq_3"]
    assert result["output"]["link"]["sent_evidence_ids"] == ["inq_2", "inq_3"]


def test_the_same_request_is_sent_once_and_the_retry_returns_the_first_result(_fresh: Sent) -> None:
    first = call("request_change", SEND_INPUT, key="change-proposal:1:abc")
    second = call("request_change", SEND_INPUT, key="change-proposal:1:abc")
    assert len(_fresh) == 1  # 기획으로 한 번만 나갔다
    assert second["output"] == first["output"]


def test_links_made_before_this_change_still_load_without_the_new_fields() -> None:
    old = {
        "tenant_id": "shop-demo",
        "context_id": CTX_REQ,
        "inquiry_id": 1,
        "source_ref": "inq_1",
        "status": "requested",
        "seq": 1,
        "created_at": "2026-10-30T00:00:00Z",
        "updated_at": "2026-10-30T00:00:00Z",
    }
    link = ChangeRequestLink.model_validate(old)
    assert link.sent_title is None and link.sent_evidence is None and link.sent_evidence_ids == []


# ---------------------------------------------------------------- 보내지 않기
def test_declining_records_the_reason_and_the_person_but_sends_nothing(_fresh: Sent) -> None:
    result = call("decline_change_proposal", DECLINE_INPUT, {"note": "  이미 개선 중  "})
    assert result["status"] == "completed"
    out = result["output"]
    assert (out["topic_code"], out["inquiry_id"], out["reason"]) == ("shipping", 1, "already_known")
    assert out["decided_by"] == "상담원A" and out["created"] is True
    decision = handlers.DECISIONS.get("shipping", 1)
    assert decision is not None and decision.note == "이미 개선 중"
    assert _fresh == [] and handlers.CHANGES.all() == []  # 기획으로 나가지 않았다


def test_declining_the_same_proposal_twice_keeps_the_first_decision(_fresh: Sent) -> None:
    call("decline_change_proposal", DECLINE_INPUT)
    again = call(
        "decline_change_proposal",
        {**DECLINE_INPUT, "reason": "other", "author": "상담원B"},
        key="ctx_ui_0002:decline_change_proposal:inquiry:1:decline:shipping",
    )
    assert again["output"]["created"] is False
    assert (
        again["output"]["reason"] == "already_known" and again["output"]["decided_by"] == "상담원A"
    )
    assert len(handlers.DECISIONS.all()) == 1


@pytest.mark.parametrize(
    ("untrusted", "message"),
    [
        ({"note": "연락처 010-1234-5678"}, "개인정보"),
        ({"note": "가" * 201}, "200자"),
        ({"note": 123}, "글"),
    ],
)
def test_a_bad_note_is_rejected_and_nothing_is_recorded(
    untrusted: dict[str, Any], message: str
) -> None:
    result = call("decline_change_proposal", DECLINE_INPUT, untrusted)
    assert result["status"] == "failed" and message in str(result["output"])
    assert handlers.DECISIONS.all() == []


def test_declining_needs_an_existing_inquiry_and_a_known_reason() -> None:
    assert (
        call("decline_change_proposal", {**DECLINE_INPUT, "inquiry_id": 99})["outcome"]
        == "not_found"
    )
    assert (
        call("decline_change_proposal", {**DECLINE_INPUT, "reason": "whatever"})["status"]
        == "failed"
    )
    assert call("decline_change_proposal", {**DECLINE_INPUT, "author": ""})["status"] == "failed"
    assert handlers.DECISIONS.all() == []


def test_a_decline_the_gate_does_not_execute_is_not_recorded(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    held(monkeypatch, handlers)
    result = call("decline_change_proposal", DECLINE_INPUT)
    assert result["outcome"] == "not_executed_held"
    assert handlers.DECISIONS.all() == []


def test_proposals_show_what_was_declined_and_why() -> None:
    now = datetime.now(UTC)
    for conversation in handlers.STORE.all():
        conversation.messages[0].created_at = (now - timedelta(minutes=5)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
    for _ in range(3):  # 최소 건수를 넘긴다
        handlers.STORE.create(
            context_id="ctx_inq_0001",
            tenant_id="shop-demo",
            user_id=1,
            customer_name="이름",
            subject="제목",
            order_id=None,
            order_no=None,
            first_message="본문",
            analysis=handlers.STORE.get(1).messages[0].analysis,
        )
    for conversation in handlers.STORE.all():
        conversation.messages[0].created_at = (now - timedelta(minutes=5)).strftime(
            "%Y-%m-%dT%H:%M:%SZ"
        )
    before = call("propose_change_requests", {"min_count": 5, "ratio": 1.5})["output"]["proposals"]
    assert before and before[0]["declined"] is False and before[0]["decline_reason"] is None
    rep = before[0]["inquiry_id"]
    call("decline_change_proposal", {**DECLINE_INPUT, "inquiry_id": rep})
    after = call("propose_change_requests", {"min_count": 5, "ratio": 1.5})["output"]["proposals"]
    assert after[0]["declined"] is True and after[0]["decline_reason"] == "already_known"


# ---------------------------------------------------------------- 영속
def test_declines_and_sent_facts_survive_a_restart(memory_repo) -> None:  # type: ignore[no-untyped-def]
    repo = memory_repo()
    decisions = PersistentProposalDecisionStore(repo)
    decisions.decline(
        "shipping", 1, "other", context_id="ctx_ui_0001", decided_by="상담원A", note="메모"
    )
    reloaded = PersistentProposalDecisionStore(repo).get("shipping", 1)
    assert reloaded is not None and (reloaded.reason, reloaded.note) == ("other", "메모")

    changes = PersistentChangeRequestStore(repo)
    changes.link(
        CTX_REQ,
        1,
        request_id="REQ-1",
        sent_title="제목",
        sent_evidence="근거",
        sent_evidence_ids=["inq_2"],
    )
    link = PersistentChangeRequestStore(repo).get(CTX_REQ, 1)
    assert link is not None and link.sent_title == "제목" and link.sent_evidence_ids == ["inq_2"]


def test_a_failed_database_write_does_not_leave_a_decision_in_memory(memory_repo) -> None:  # type: ignore[no-untyped-def]
    repo = memory_repo()
    store = PersistentProposalDecisionStore(repo)
    repo.fail = True
    with pytest.raises(RuntimeError):
        store.decline("shipping", 1, "other", context_id="ctx_ui_0001", decided_by="상담원A")
    assert store.get("shipping", 1) is None


def test_the_decision_table_is_declared_for_the_database() -> None:
    table = next(t for t in TABLES if t.name == "proposal_decisions")
    assert table.pk == ("tenant_id", "topic_code", "inquiry_id")
    assert table.columns == ("context_id", "reason")  # 모든 테이블은 사건 ID를 갖는다
