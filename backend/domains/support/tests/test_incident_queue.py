"""장애 보상 대기 → 사람 승인 → 발송 큐 (준하님 결정 2026-11-04: 사람 확인 후)."""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

from ax_contracts.schemas import AgentRequest, AgentResult, ExecuteDecision, TaskStatus
from support_agent import compensation, handlers, incident_queue_handlers
from support_agent.incident_queue import IncidentQueueStore
from support_agent.main import app, load_card
from support_agent.persistence import TABLES, PersistentIncidentQueueStore
from support_agent.store import InquiryStore

CTX = "ctx_inc_0001"
BUYERS = ["buy_0003", "buy_0004", "buy_0006"]


class Sent(list):
    """마케팅으로 보낸 요청 목록. `replies`에 넣은 결과를 차례로 돌려준다."""

    def __init__(self) -> None:
        super().__init__()
        self.replies: list[AgentResult] = []


@pytest.fixture(autouse=True)
def sent(monkeypatch: pytest.MonkeyPatch) -> Sent:
    monkeypatch.setenv("AGENT_TEMPLATE_MOCK", "1")
    monkeypatch.setattr(handlers, "STORE", InquiryStore())
    monkeypatch.setattr(handlers, "LEDGER", compensation.SequenceLedger())
    monkeypatch.setattr(handlers, "INCIDENT_QUEUE", IncidentQueueStore())
    record = Sent()

    async def fake_send(target: str, req: AgentRequest) -> AgentResult:
        assert target == "marketing"
        record.append(req)
        if record.replies:
            return record.replies.pop(0)
        return AgentResult(
            meta=req.meta,
            status=TaskStatus.COMPLETED,
            output={"coupon_id": f"cpn_{len(record):04d}", "count": req.input["count"]},
        )

    monkeypatch.setattr(handlers, "SEND", fake_send)
    return record


def call(
    skill: str,
    input: dict[str, Any],
    *,
    context: str = CTX,
    requester: str = "orchestrator",
    key: str | None = None,
) -> dict[str, Any]:
    body = {
        "meta": {
            "context_id": context,
            "token": f"mock:{requester}",
            "path": [],
            "deadline": (datetime.now(UTC) + timedelta(seconds=30)).isoformat(),
            "idempotency_key": key,
        },
        "skill": skill,
        "input": input,
    }
    return TestClient(app).post("/a2a", json=body).json()


def propose(buyers: list[str] = BUYERS, **fields: Any) -> dict[str, Any]:
    return call("propose_incident_compensation", {"buyer_ids": buyers, **fields})


def decide(
    approve: list[str] | None = None,
    reject: list[str] | None = None,
    *,
    author: str = "상담원A",
    key: str | None = None,
) -> dict[str, Any]:
    return call(
        "decide_incident_compensation",
        {
            "incident_context_id": CTX,
            "approve_buyer_ids": approve or [],
            "reject_buyer_ids": reject or [],
            "author": author,
        },
        context="ctx_ui_0001",
        requester="dashboard-api",
        key=key,
    )


def marketing_reply(
    status: TaskStatus,
    output: dict[str, Any] | None = None,
    outcome: str | None = None,
    reason_code: str | None = None,
) -> AgentResult:
    meta = AgentRequest.model_validate(
        {
            "meta": {
                "context_id": CTX,
                "token": "t",
                "deadline": (datetime.now(UTC) + timedelta(seconds=30)).isoformat(),
            },
            "skill": "issue_coupon",
        }
    ).meta
    return AgentResult(
        meta=meta, status=status, output=output or {}, outcome=outcome, reason_code=reason_code
    )


def held_gate(monkeypatch: pytest.MonkeyPatch) -> None:
    async def decision(*args: Any, **kwargs: Any) -> ExecuteDecision:
        return ExecuteDecision(
            execution_id="exe_1",
            grade="C",
            status="held",
            hold_reason="unregistered_action",
            review_id="rev_1",
        )

    monkeypatch.setattr(handlers, "execute", decision)


# ---------------------------------------------------------------- 카드
def test_the_card_declares_who_may_call_each_queue_skill() -> None:
    skills = {s.id: s for s in load_card().skills}
    assert skills["propose_incident_compensation"].grade == "A"
    assert skills["propose_incident_compensation"].allowed_requesters == ["orchestrator"]
    assert skills["decide_incident_compensation"].grade == "A"
    assert skills["decide_incident_compensation"].allowed_requesters == ["dashboard-api"]
    assert skills["list_incident_queue"].grade == "read"


def test_only_the_right_requester_can_call_each_skill() -> None:
    assert propose(buyers=BUYERS)["status"] == "completed"
    wrong = call("propose_incident_compensation", {"buyer_ids": BUYERS}, requester="dashboard-api")
    assert wrong["status"] == "rejected"
    by_orchestrator = call(
        "decide_incident_compensation",
        {"incident_context_id": CTX, "approve_buyer_ids": BUYERS, "author": "x"},
        requester="orchestrator",
    )
    assert by_orchestrator["status"] == "rejected"  # 승인은 사람 화면(dashboard-api)만


# ---------------------------------------------------------------- 대기에 올리기
def test_proposing_queues_every_buyer_and_requests_no_coupon(sent: Sent) -> None:
    result = propose(impact_ref="impact:inc_1")
    assert result["status"] == "completed"
    out = result["output"]
    assert out["queued"] == 3 and out["unit_value"] == 2000
    assert [r["status"] for r in out["results"]] == ["queued"] * 3
    assert sent == []  # 사람이 승인하기 전에는 마케팅으로 아무것도 나가지 않았다
    rows = handlers.INCIDENT_QUEUE.list(CTX)
    assert [r.buyer_id for r in rows] == BUYERS
    assert all(r.status == "pending" and r.total_value == 2000 for r in rows)
    assert rows[0].impact_ref == "impact:inc_1" and rows[0].created_by == "orchestrator"


def test_proposing_outside_an_incident_is_refused(sent: Sent) -> None:
    result = call("propose_incident_compensation", {"buyer_ids": BUYERS}, context="ctx_req_0001")
    assert result["status"] == "failed"
    assert handlers.INCIDENT_QUEUE.list() == [] and sent == []


def test_a_bad_buyer_id_refuses_the_whole_list() -> None:
    result = propose(["buy_0003", "buy_12"])
    assert result["status"] == "failed"
    assert handlers.INCIDENT_QUEUE.list() == []


def test_proposing_the_same_list_twice_queues_once() -> None:
    first = propose()
    again = propose()
    assert again["output"] == first["output"]  # 같은 요청은 첫 결과를 그대로 돌려준다
    assert len(handlers.INCIDENT_QUEUE.list(CTX)) == 3
    # 키를 새로 주고 다시 보내도 줄은 늘지 않는다
    other_key = call(
        "propose_incident_compensation", {"buyer_ids": BUYERS}, key="ctx_inc_0001:again"
    )
    assert other_key["output"]["queued"] == 0
    assert len(handlers.INCIDENT_QUEUE.list(CTX)) == 3


def test_a_buyer_already_in_the_queue_is_reported_not_added_again() -> None:
    propose(["buy_0003"])
    result = propose(["buy_0003", "buy_0004"])
    by = {r["buyer_id"]: r for r in result["output"]["results"]}
    assert by["buy_0003"]["status"] == "already_queued"
    assert by["buy_0003"]["queue_status"] == "pending"
    assert by["buy_0004"]["status"] == "queued" and result["output"]["queued"] == 1


def test_a_buyer_who_already_got_the_coupon_is_not_queued_again() -> None:
    key = f"{CTX}:request_compensation:buy_0003:payment_failure"
    handlers.LEDGER.results[key] = {"marketing_status": "completed", "total_value": 2000}
    result = propose(["buy_0003", "buy_0004"])
    by = {r["buyer_id"]: r["status"] for r in result["output"]["results"]}
    assert by == {"buy_0003": "already_compensated", "buy_0004": "queued"}


def test_the_incident_limit_counts_the_queue_too(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(compensation, "MAX_INCIDENT_BUYERS", 2)
    result = propose()
    out = result["output"]
    assert out["queued"] == 2 and out["limit_reached"] is True
    assert [r["status"] for r in out["results"]] == ["queued", "queued", "skipped"]


def test_a_gate_that_does_not_execute_queues_nothing(monkeypatch: pytest.MonkeyPatch) -> None:
    held_gate(monkeypatch)
    result = propose()
    assert result["outcome"] == "not_executed_held"
    assert handlers.INCIDENT_QUEUE.list() == []


# ---------------------------------------------------------------- 승인·거절
def test_approving_sends_only_the_approved_buyers_to_marketing(sent: Sent) -> None:
    propose()
    result = decide(approve=["buy_0003", "buy_0006"], reject=["buy_0004"])
    assert result["status"] == "completed"
    out = result["output"]
    assert (out["approved"], out["rejected"], out["sent"]) == (2, 1, 2)
    assert [r["buyer_id"] for r in out["results"]] == ["buy_0003", "buy_0006", "buy_0004"]
    assert {r.input["buyer_id"] for r in sent} == {"buy_0003", "buy_0006"}  # 거절한 고객은 안 나감
    assert all(r.meta.context_id == CTX for r in sent)  # 쿠폰 번호는 사건 단위
    states = {i.buyer_id: i for i in handlers.INCIDENT_QUEUE.list(CTX)}
    assert states["buy_0003"].status == "sent" and states["buy_0003"].coupon_id is not None
    assert states["buy_0003"].decided_by == "상담원A"
    assert states["buy_0004"].status == "rejected" and states["buy_0004"].decided_by == "상담원A"
    assert states["buy_0004"].coupon_id is None


def test_nothing_is_sent_for_buyers_that_are_not_pending(sent: Sent) -> None:
    propose()
    decide(reject=["buy_0004"])
    again = decide(approve=["buy_0004", "buy_0009"])
    by = {r["buyer_id"]: r["status"] for r in again["output"]["results"]}
    assert by == {"buy_0004": "not_pending", "buy_0009": "not_found"}  # 거절은 되돌리지 않는다
    assert sent == []


def test_the_same_buyer_cannot_be_approved_and_rejected_together(sent: Sent) -> None:
    propose()
    result = decide(approve=["buy_0003"], reject=["buy_0003"])
    assert result["status"] == "failed"
    assert sent == []
    assert handlers.INCIDENT_QUEUE.find_buyer(CTX, "buy_0003").status == "pending"


def test_an_empty_decision_is_refused(sent: Sent) -> None:
    propose()
    assert decide()["status"] == "failed"
    assert sent == []


def test_approving_twice_does_not_send_a_second_coupon(sent: Sent) -> None:
    propose()
    decide(approve=["buy_0003"], key="ctx_ui_0001:decide:1")
    decide(approve=["buy_0003"], key="ctx_ui_0001:decide:2")
    assert len(sent) == 1
    assert handlers.INCIDENT_QUEUE.find_buyer(CTX, "buy_0003").status == "sent"


def test_a_gate_that_does_not_execute_changes_no_line(
    sent: Sent, monkeypatch: pytest.MonkeyPatch
) -> None:
    propose()
    held_gate(monkeypatch)
    result = decide(approve=BUYERS)
    assert result["outcome"] == "not_executed_held"
    assert sent == []
    assert {i.status for i in handlers.INCIDENT_QUEUE.list(CTX)} == {"pending"}


def test_marketing_not_reached_keeps_the_buyer_approved_and_a_retry_sends(sent: Sent) -> None:
    propose(["buy_0003"])
    sent.replies.append(marketing_reply(TaskStatus.FAILED))
    first = decide(approve=["buy_0003"], key="ctx_ui_0001:decide:1")
    assert first["output"]["stopped_early"] is True and first["output"]["sent"] == 0
    assert handlers.INCIDENT_QUEUE.find_buyer(CTX, "buy_0003").status == "approved"
    retry = decide(approve=["buy_0003"], key="ctx_ui_0001:decide:2")
    assert retry["output"]["sent"] == 1
    assert handlers.INCIDENT_QUEUE.find_buyer(CTX, "buy_0003").status == "sent"


def test_a_marketing_hold_marks_the_line_held(sent: Sent) -> None:
    propose(["buy_0003"])
    sent.replies.append(
        marketing_reply(TaskStatus.INPUT_REQUIRED, {"review_id": "rev_9"}, "held", "human_input")
    )
    result = decide(approve=["buy_0003"])
    assert result["output"]["results"][0]["status"] == "held"
    assert handlers.INCIDENT_QUEUE.find_buyer(CTX, "buy_0003").status == "held"


# ---------------------------------------------------------------- 목록
def test_listing_shows_pending_totals_and_hides_closed_lines_by_default() -> None:
    propose()
    decide(approve=["buy_0003"], reject=["buy_0004"])
    listed = call("list_incident_queue", {"incident_context_id": CTX}, requester="dashboard-api")
    incident = listed["output"]["incidents"][0]
    assert incident["context_id"] == CTX
    assert incident["pending_buyers"] == 1 and incident["pending_total"] == 2000
    assert [r["buyer_id"] for r in incident["rows"]] == ["buy_0006"]  # 보낸·거절한 줄은 숨김
    everything = call(
        "list_incident_queue",
        {"incident_context_id": CTX, "include_closed": True},
        requester="dashboard-api",
    )
    rows = {r["buyer_id"]: r for r in everything["output"]["incidents"][0]["rows"]}
    assert rows["buy_0003"]["status"] == "sent" and rows["buy_0003"]["coupon_id"]
    assert rows["buy_0004"]["status"] == "rejected" and rows["buy_0004"]["decided_by"] == "상담원A"


def test_listing_an_incident_without_lines_answers_an_empty_incident() -> None:
    listed = call("list_incident_queue", {"incident_context_id": CTX}, requester="dashboard-api")
    incident = listed["output"]["incidents"][0]
    assert incident["rows"] == [] and incident["pending_buyers"] == 0


# ---------------------------------------------------------------- 영속
class FakeRepo:
    def __init__(self) -> None:
        self.docs: dict[tuple, dict[str, Any]] = {}

    def load(self, table: str) -> list[dict[str, Any]]:
        assert table == "incident_compensation_items"
        return list(self.docs.values())

    def upsert(self, table: str, values: dict[str, Any], doc: dict[str, Any]) -> None:
        assert table == "incident_compensation_items"
        self.docs[(values["context_id"], values["buyer_id"], values["reason_code"])] = doc

    def get(self, table: str, key: tuple) -> dict[str, Any] | None:  # pragma: no cover
        return self.docs.get(tuple(key))


def test_the_queue_survives_a_restart() -> None:
    repo = FakeRepo()
    store = PersistentIncidentQueueStore(repo)  # type: ignore[arg-type]
    item, created = store.add(
        CTX,
        "buy_0003",
        "payment_failure",
        count=1,
        unit_value=2000,
        impact_ref=None,
        created_by="orchestrator",
    )
    assert created is True
    store.decide(item, "approved", "상담원A")
    reloaded = PersistentIncidentQueueStore(repo)  # type: ignore[arg-type]
    again = reloaded.get(CTX, "buy_0003", "payment_failure")
    assert again is not None and again.status == "approved" and again.decided_by == "상담원A"


def test_the_queue_table_is_part_of_the_schema() -> None:
    assert "incident_compensation_items" in {t.name for t in TABLES}


def test_module_exports_the_three_handlers() -> None:
    assert set(incident_queue_handlers.INCIDENT_QUEUE_HANDLERS) == {
        "propose_incident_compensation",
        "decide_incident_compensation",
        "list_incident_queue",
    }
