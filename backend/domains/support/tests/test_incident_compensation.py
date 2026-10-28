"""`request_incident_compensation`: 장애 피해 고객 전원에게 보상 쿠폰을 요청한다 (SCN-A 8단계)."""

from datetime import UTC, datetime, timedelta
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from ax_contracts.schemas import AgentRequest, AgentResult, TaskStatus
from support_agent import compensation, handlers, shop_client
from support_agent.main import app, load_card
from support_agent.schemas import Analysis
from support_agent.store import InquiryStore

CTX = "ctx_inc_0001"
FOUR = ["buy_0001", "buy_0002", "buy_0003", "buy_0004"]


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
    buyers: list[str],
    *,
    context: str = CTX,
    requester: str = "orchestrator",
    **fields: Any,
) -> dict[str, Any]:
    body = {
        "meta": {
            "context_id": context,
            "token": f"mock:{requester}",
            "path": [],
            "deadline": (datetime.now(UTC) + timedelta(seconds=30)).isoformat(),
        },
        "skill": "request_incident_compensation",
        "input": {"buyer_ids": buyers, **fields},
    }
    return TestClient(app).post("/a2a", json=body).json()


def reply(
    status: TaskStatus,
    output: dict[str, Any] | None = None,
    outcome: str | None = None,
    reason_code: str | None = None,
):
    return AgentResult(
        meta=AgentRequest.model_validate(
            {
                "meta": {
                    "context_id": CTX,
                    "token": "t",
                    "deadline": (datetime.now(UTC) + timedelta(seconds=30)).isoformat(),
                },
                "skill": "issue_coupon",
            }
        ).meta,
        status=status,
        output=output or {},
        outcome=outcome,
        reason_code=reason_code,
    )


def test_card_declares_a_required_write_skill_for_the_orchestrator_only() -> None:
    skill = {s.id: s for s in load_card().skills}["request_incident_compensation"]
    assert skill.grade == "A" and skill.priority == "required"
    assert skill.allowed_requesters == ["orchestrator"]
    assert call(FOUR, requester="dashboard-api")["status"] == "rejected"
    assert call(FOUR, requester="marketing")["status"] == "rejected"


def test_every_affected_buyer_gets_one_coupon_request_in_order(sent: Sent) -> None:
    result = call(FOUR, impact_ref="impact:ctx_inc_0001")
    assert result["status"] == "completed" and result["outcome"] == "requested"
    out = result["output"]
    assert out["requested"] == 4 and out["stopped_early"] is False
    assert [r["buyer_id"] for r in out["results"]] == FOUR
    assert [r["sequence"] for r in out["results"]] == [1, 2, 3, 4]
    assert [r["status"] for r in out["results"]] == ["requested"] * 4
    assert [r["coupon_id"] for r in out["results"]] == [f"cpn_000{n}" for n in (1, 2, 3, 4)]
    assert all(r["total_value"] == 2000 for r in out["results"])
    assert [req.skill for req in sent] == ["issue_coupon"] * 4
    first = sent[0]
    assert first.input == {
        "purpose_code": "compensation",
        "buyer_id": "buy_0001",
        "count": 1,
        "unit_value": 2000,
        "claimed_total": 2000,
    }
    assert first.meta.context_id == CTX
    refs = result["evidence"]["ref_ids"]
    assert "impact:ctx_inc_0001" in refs and "buyer:buy_0004" in refs


def test_buyers_who_never_wrote_an_inquiry_are_compensated_too(sent: Sent) -> None:
    assert handlers.STORE.list() == []  # 문의가 하나도 없다
    assert call(["buy_0007"])["output"]["requested"] == 1
    assert sent[0].input["buyer_id"] == "buy_0007"


def test_ids_are_normalized_and_duplicates_are_sent_once(sent: Sent) -> None:
    out = call(["usr_1", "buy_0001", "buy_0002", "buy_0002"])["output"]
    assert [r["buyer_id"] for r in out["results"]] == ["buy_0001", "buy_0002"]
    assert len(sent) == 2


@pytest.mark.parametrize("bad", ["", "buy_1", "buy_12345", "ord_0001", "홍길동", "buy_0001; drop"])
def test_a_malformed_buyer_id_rejects_the_whole_request(sent: Sent, bad: str) -> None:
    result = call(["buy_0001", bad])
    assert result["status"] == "failed" and result["outcome"] == "invalid_input"
    assert sent == []  # 일부만 보내지 않는다
    assert bad not in str(result) or bad == ""  # 오류에 입력값을 싣지 않는다


def test_only_incident_contexts_are_accepted(sent: Sent) -> None:
    for context in ("ctx_inq_0001", "ctx_ui_0001", "ctx_req_0001", "ctx_ntc_0001"):
        result = call(FOUR, context=context)
        assert result["status"] == "failed" and result["outcome"] == "invalid_input", context
    assert sent == []


def test_only_the_payment_failure_reason_and_sane_counts_are_accepted(sent: Sent) -> None:
    assert call(FOUR, reason_code="late_delivery")["status"] == "failed"
    assert call(FOUR, count=0)["status"] == "failed"
    assert call(FOUR, count=4)["status"] == "failed"
    assert call([])["status"] == "failed"
    assert call([f"buy_{n:04d}" for n in range(51)])["status"] == "failed"
    assert sent == []


def test_the_same_request_twice_sends_each_buyer_once(sent: Sent) -> None:
    call(FOUR)
    again = call(FOUR)["output"]
    assert [r["status"] for r in again["results"]] == ["duplicate"] * 4
    assert again["requested"] == 0 and len(sent) == 4


def test_a_buyer_already_compensated_through_an_inquiry_is_not_sent_again(sent: Sent) -> None:
    shop_client.use_client(
        httpx.AsyncClient(
            transport=httpx.MockTransport(
                lambda request: httpx.Response(
                    200,
                    json={
                        "data": {
                            "order_id": "ord_0001",
                            "buyer_id": "buy_0001",
                            "status": "payment_failed",
                            "payments": [
                                {
                                    "payment_id": "pay_1",
                                    "status": "failed",
                                    "failure_code": "PG_UNAVAILABLE",
                                }
                            ],
                        },
                        "meta": {},
                    },
                )
            )
        )
    )
    handlers.STORE.create(
        context_id=CTX,
        tenant_id="shop-demo",
        user_id=1,
        customer_name="김하늘",
        subject="결제 오류",
        order_id="ord_0001",
        order_no=None,
        first_message="결제가 안 돼요",
        analysis=Analysis(),
    )
    body = {
        "meta": {
            "context_id": CTX,
            "token": "mock:orchestrator",
            "path": [],
            "deadline": (datetime.now(UTC) + timedelta(seconds=30)).isoformat(),
        },
        "skill": "request_compensation",
        "input": {
            "inquiry_id": 1,
            "buyer_id": "buy_0001",
            "reason_code": "payment_failure",
            "count": 1,
        },
    }
    first = TestClient(app).post("/a2a", json=body).json()
    assert first["output"]["unit_value"] == 2000  # 문의 경로도 같은 정책표
    out = call(["buy_0001", "buy_0002"])["output"]
    assert [r["status"] for r in out["results"]] == ["duplicate", "requested"]
    assert len(sent) == 2


def test_when_marketing_is_unreachable_the_rest_are_not_sent(sent: Sent) -> None:
    sent.replies.append(reply(TaskStatus.FAILED, outcome="unreachable"))
    result = call(FOUR)
    out = result["output"]
    assert result["outcome"] == "partial" and out["stopped_early"] is True
    assert [r["status"] for r in out["results"]] == ["failed", "skipped", "skipped", "skipped"]
    assert len(sent) == 1
    assert out["requested"] == 0


def test_a_held_coupon_does_not_stop_the_others_and_is_not_a_failure(sent: Sent) -> None:
    sent.replies.append(
        reply(TaskStatus.INPUT_REQUIRED, {"review_id": "rev_1"}, "held", "human_input")
    )
    out = call(FOUR)["output"]
    assert [r["status"] for r in out["results"]] == ["held", "requested", "requested", "requested"]
    assert out["requested"] == 3 and out["stopped_early"] is False
    assert out["results"][0]["total_value"] == 0  # 지급 전이라 합계에 넣지 않는다


def test_a_buyer_marketing_already_linked_is_not_counted_as_a_new_coupon(sent: Sent) -> None:
    # 마케팅은 같은 사건·구매자에게 번호만 달라도 새로 지급하지 않고 `linked_duplicate`로 잇는다
    sent.replies.append(reply(TaskStatus.COMPLETED, {"coupon_id": "cpn_0009"}, "linked_duplicate"))
    out = call(["buy_0001", "buy_0002"])["output"]
    first, second = out["results"]
    assert (first["status"], first["total_value"]) == ("duplicate", 0)
    assert first["marketing_outcome"] == "linked_duplicate" and first["coupon_id"] == "cpn_0009"
    assert second["status"] == "requested" and second["sequence"] == 2  # 번호는 소비됐다
    assert out["requested"] == 1


def test_a_rejected_request_stops_the_rest_instead_of_repeating_the_same_error(
    sent: Sent,
) -> None:
    sent.replies.append(reply(TaskStatus.REJECTED, outcome="SEQUENCE_GAP"))
    result = call(FOUR)
    out = result["output"]
    assert result["outcome"] == "partial" and out["stopped_early"] is True
    assert [r["status"] for r in out["results"]] == ["failed", "skipped", "skipped", "skipped"]
    assert out["results"][0]["marketing_outcome"] == "SEQUENCE_GAP"
    assert len(sent) == 1


def test_a_failed_request_keeps_the_sequence_for_a_retry(sent: Sent) -> None:
    sent.replies.append(reply(TaskStatus.FAILED, outcome="unreachable"))
    call(["buy_0001"])
    retry = call(["buy_0001"])["output"]
    assert retry["results"][0]["status"] == "requested"
    assert retry["results"][0]["sequence"] == sent[0].sequence  # 소비되지 않은 번호로 다시 보낸다


def test_the_output_carries_no_customer_text_or_names(sent: Sent) -> None:
    text = str(call(FOUR))
    assert "김하늘" not in text and "untrusted" not in text.lower().replace('"untrusted": {}', "")


@pytest.mark.parametrize("bad", ["buy_١٢٣٤", "usr_١", "buy_000١"])
def test_unicode_digits_are_not_accepted_as_a_buyer_id(sent: Sent, bad: str) -> None:
    result = call(["buy_0001", bad])
    assert result["status"] == "failed" and result["outcome"] == "invalid_input"
    assert sent == []


def test_marketing_still_verifying_is_pending_not_failed_and_stops_the_rest(sent: Sent) -> None:
    sent.replies.append(reply(TaskStatus.WORKING))
    result = call(FOUR)
    out = result["output"]
    assert [r["status"] for r in out["results"]] == ["pending", "skipped", "skipped", "skipped"]
    assert out["stopped_early"] is True and result["outcome"] == "partial"
    assert len(sent) == 1 and out["requested"] == 0
    retry = call(FOUR)["output"]  # 번호가 소비되지 않아 같은 번호로 다시 요청한다
    assert [r["status"] for r in retry["results"]] == ["requested"] * 4
    assert retry["results"][0]["sequence"] == 1


def test_one_call_cannot_pass_the_incident_buyer_limit(
    sent: Sent, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(compensation, "MAX_INCIDENT_BUYERS", 3)
    result = call(FOUR)
    out = result["output"]
    assert [r["status"] for r in out["results"]] == ["requested"] * 3 + ["skipped"]
    assert out["limit_reached"] is True and out["stopped_early"] is False
    assert result["outcome"] == "partial" and len(sent) == 3
    assert any("상한" in note for note in out["notes"])


def test_one_call_cannot_pass_the_incident_total_limit(
    sent: Sent, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(compensation, "MAX_INCIDENT_TOTAL", 5000)  # 고객당 2,000원
    out = call(FOUR)["output"]
    assert [r["status"] for r in out["results"]] == ["requested", "requested", "skipped", "skipped"]
    assert out["limit_reached"] is True and len(sent) == 2


def test_the_limit_holds_across_several_calls_in_the_same_incident(
    sent: Sent, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(compensation, "MAX_INCIDENT_BUYERS", 4)
    assert call(["buy_0001", "buy_0002", "buy_0003"])["output"]["requested"] == 3
    out = call(["buy_0004", "buy_0005"])["output"]  # 다른 목록으로 다시 불러도 사건 합계는 이어진다
    assert [r["status"] for r in out["results"]] == ["requested", "skipped"]
    assert out["limit_reached"] is True and len(sent) == 4


def test_another_incident_has_its_own_limit(sent: Sent, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(compensation, "MAX_INCIDENT_BUYERS", 2)
    call(["buy_0001", "buy_0002"])
    assert call(["buy_0003"])["output"]["limit_reached"] is True
    other = call(["buy_0003"], context="ctx_inc_0002")["output"]
    assert other["requested"] == 1 and other["limit_reached"] is False


def test_resending_an_already_requested_buyer_is_not_blocked_by_the_limit(
    sent: Sent, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(compensation, "MAX_INCIDENT_BUYERS", 2)
    call(["buy_0001", "buy_0002"])
    again = call(["buy_0001", "buy_0002"])["output"]
    assert [r["status"] for r in again["results"]] == ["duplicate", "duplicate"]
    assert again["limit_reached"] is False and len(sent) == 2


def test_a_held_coupon_counts_toward_the_limit_in_the_same_call(
    sent: Sent, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(compensation, "MAX_INCIDENT_TOTAL", 5000)
    sent.replies.append(
        reply(TaskStatus.INPUT_REQUIRED, {"review_id": "rev_1"}, "held", "human_input")
    )
    out = call(["buy_0001", "buy_0002", "buy_0003"])["output"]
    assert [r["status"] for r in out["results"]] == ["held", "requested", "skipped"]


def test_a_buyer_marketing_linked_to_an_earlier_coupon_is_not_counted_as_issued(
    sent: Sent,
) -> None:
    sent.replies.append(reply(TaskStatus.COMPLETED, {"coupon_id": "cpn_0009"}, "linked_duplicate"))
    call(["buy_0001", "buy_0002"])
    from support_agent import incident_handlers

    assert incident_handlers.issued_in_incident(CTX) == (1, 2000)  # 새로 지급한 한 명뿐


# ---------------------------------------------------------------- 쿠폰 서비스가 꺼진 고객사 (업무 요청 라우터)
SEVEN = [f"buy_00{n:02d}" for n in range(1, 8)]


def handed(task_id: str = "hof_comp1") -> AgentResult:
    """라우터(call_agent)가 꺼진 서비스 요청에 돌려주는 결과와 같은 모양."""
    return reply(
        TaskStatus.INPUT_REQUIRED,
        {"task_id": task_id, "to_service": "coupon", "assignee_unit": "마케팅", "assignee": None},
        outcome="handed_to_human",
        reason_code="dept_input",
    )


def test_with_coupon_off_seven_buyers_become_one_waiting_request(sent: Sent, monkeypatch) -> None:
    monkeypatch.setenv("AX_ENABLED_SERVICES", "inquiry,claims,reviews")  # coupon 없음
    sent.replies.append(handed())
    result = call(SEVEN)

    assert len(sent) == 1  # 고객마다가 아니라 사건 하나에 한 건
    [req] = sent
    assert req.skill == "issue_coupon"
    assert req.meta.idempotency_key == f"{CTX}:request_incident_compensation:payment_failure"
    unit = compensation.decide("payment_failure", 1).total_value
    assert req.input == {
        "purpose_code": "compensation",
        "batch": True,
        "reason_code": "payment_failure",
        "unit_value": unit,
        "count": 1,
        "buyer_ids": SEVEN,
        "buyer_count": 7,
        "total_value": 7 * unit,
        "limit_reached": False,
    }
    # 지급 전이라 완료가 아니라 담당 부서 처리 대기
    assert (result["status"], result["outcome"], result["reason_code"]) == (
        "input-required",
        "handed_to_human",
        "dept_input",
    )
    assert result["output"]["task_id"] == "hof_comp1"
    assert result["output"]["buyer_count"] == 7


def test_with_coupon_off_the_batch_respects_the_incident_limits(sent: Sent, monkeypatch) -> None:
    monkeypatch.setenv("AX_ENABLED_SERVICES", "inquiry,claims")
    monkeypatch.setattr(compensation, "MAX_INCIDENT_BUYERS", 3)
    sent.replies.append(handed())
    result = call(SEVEN)
    [req] = sent
    assert req.input["buyer_ids"] == SEVEN[:3]
    assert req.input["limit_reached"] is True
    assert result["output"]["limit_reached"] is True


def test_with_coupon_off_and_no_department_the_compensation_fails(sent: Sent, monkeypatch) -> None:
    monkeypatch.setenv("AX_ENABLED_SERVICES", "inquiry,claims")
    sent.replies.append(reply(TaskStatus.FAILED, {"to_service": "coupon"}, outcome="no_assignee"))
    result = call(SEVEN)
    assert (result["status"], result["outcome"]) == ("failed", "no_assignee")
    assert result["output"]["buyer_count"] == 7
    assert len(sent) == 1


def test_with_coupon_on_buyers_are_still_sent_one_by_one(sent: Sent, monkeypatch) -> None:
    monkeypatch.delenv("AX_ENABLED_SERVICES", raising=False)
    result = call(SEVEN)
    assert len(sent) == 7
    assert result["status"] == "completed"


@pytest.mark.asyncio
async def test_a_single_coupon_handed_to_a_human_is_waiting_not_completed(sent: Sent) -> None:
    """문의 경로(request_compensation)도 넘김을 받으면 완료로 두지 않고 번호도 쓰지 않는다."""
    sent.replies.append(handed("hof_one"))
    request = AgentRequest.model_validate(
        {
            "meta": {
                "context_id": CTX,
                "token": "t",
                "deadline": (datetime.now(UTC) + timedelta(seconds=30)).isoformat(),
            },
            "skill": "request_compensation",
        }
    )
    decision = compensation.decide("payment_failure", 1)
    key = f"{CTX}:request_compensation:buy_0001:payment_failure"
    from ax_contracts.schemas import Evidence

    result = await handlers._request_coupon(request, "buy_0001", decision, key, Evidence())
    assert (result.status, result.outcome) == ("input-required", "handed_to_human")
    assert result.output["task_id"] == "hof_one"
    assert result.output["buyer_id"] == "buy_0001"
    assert handlers.LEDGER.peek(CTX) == 1  # 번호를 쓰지 않았다
    assert key not in handlers.LEDGER.results  # 지급 기록으로 남기지 않는다
