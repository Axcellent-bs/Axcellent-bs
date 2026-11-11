"""고객관리 탭 읽기 API: 목 모드 응답과, 목 모드가 아닐 때 support skill 호출을 본다."""

from typing import Any

import pytest
from fastapi.testclient import TestClient

from ax_contracts.schemas import AgentRequest, AgentResult, TaskStatus
from ax_dashboard_api import support_client
from ax_dashboard_api.main import app

BASE = "/api/dashboard"


class Fake:
    def __init__(self) -> None:
        self.sent: list[AgentRequest] = []
        self.outputs: dict[str, dict[str, Any]] = {}

    async def __call__(self, target: str, req: AgentRequest) -> AgentResult:
        assert target == "support"
        self.sent.append(req)
        return AgentResult(
            meta=req.meta, status=TaskStatus.COMPLETED, output=self.outputs.get(req.skill, {})
        )


@pytest.fixture
def mock_client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.setenv("DASHBOARD_MOCK", "1")
    return TestClient(app)


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> Fake:
    monkeypatch.delenv("DASHBOARD_MOCK", raising=False)
    monkeypatch.delenv("AGENT_TEMPLATE_MOCK", raising=False)
    monkeypatch.setenv("DASHBOARD_API_TOKEN", "test-token")
    fake = Fake()
    monkeypatch.setattr(support_client, "SEND", fake)
    return fake


# ---------------------------------------------------------------- 목 모드
def test_at_risk_customers_are_listed_by_score_and_filtered_by_level(
    mock_client: TestClient,
) -> None:
    body = mock_client.get(BASE + "/customers/at-risk").json()
    assert [c["buyer_id"] for c in body["data"]] == ["buy_0001", "buy_0002"]
    assert body["meta"]["total"] == 2
    high = mock_client.get(BASE + "/customers/at-risk", params={"min_level": "high"}).json()
    assert [c["buyer_id"] for c in high["data"]] == ["buy_0001"]
    assert (
        mock_client.get(BASE + "/customers/at-risk", params={"min_level": "low"}).status_code == 422
    )
    assert mock_client.get(BASE + "/customers/at-risk", params={"limit": 0}).status_code == 422


def test_customer_signals_for_a_known_and_an_unknown_buyer(mock_client: TestClient) -> None:
    known = mock_client.get(BASE + "/customers/buy_0001/signals").json()["data"]
    assert known["level"] == "high" and known["recommended_action"] == "personal_followup"
    assert sum(r["points"] for r in known["reasons"]) == known["score"]
    unknown = mock_client.get(BASE + "/customers/buy_9999/signals").json()["data"]
    assert unknown["level"] == "low" and unknown["reasons"] == []


def test_compensation_suggestion_has_a_request_only_when_eligible(
    mock_client: TestClient,
) -> None:
    late = mock_client.get(BASE + "/conversations/1/compensation-suggestion").json()["data"]
    assert late["eligible"] is True and late["total_value"] == late["unit_value"] * late["count"]
    assert late["request"]["reason_code"] == "late_delivery"
    on_time = mock_client.get(BASE + "/conversations/2/compensation-suggestion").json()["data"]
    assert on_time["eligible"] is False and on_time["request"] is None
    assert mock_client.get(BASE + "/conversations/99/compensation-suggestion").status_code == 404


def test_payment_diagnosis_for_failed_paid_and_missing(mock_client: TestClient) -> None:
    failed = mock_client.get(BASE + "/conversations/1/payment-diagnosis").json()["data"]
    assert failed["situation"] == "payment_failed" and failed["next_action"] == "wait_and_retry"
    paid = mock_client.get(BASE + "/conversations/2/payment-diagnosis").json()["data"]
    assert paid["situation"] == "paid" and paid["next_action"] == "none"
    missing = mock_client.get(BASE + "/conversations/99/payment-diagnosis")
    assert missing.status_code == 404 and missing.json()["error"]["code"] == "NOT_FOUND"


def test_surge_and_change_requests_in_mock_mode(mock_client: TestClient) -> None:
    surge = mock_client.get(BASE + "/inquiry-surge").json()["data"]
    assert surge["surges"][0]["topic_code"] == "payment"
    assert surge["surges"][0]["count"] >= len(surge["surges"][0]["sample_inquiry_ids"])
    rows = mock_client.get(BASE + "/change-requests").json()
    assert rows["meta"]["total"] == 1 and rows["data"][0]["status"] == "requested"
    done = mock_client.get(BASE + "/change-requests", params={"status": "completed"}).json()
    assert done["data"] == []
    assert mock_client.get(BASE + "/change-requests", params={"status": "x"}).status_code == 422


# ---------------------------------------------------------------- support A2A
def test_at_risk_calls_support_with_the_filters_and_keeps_its_total(fake: Fake) -> None:
    fake.outputs["list_at_risk_customers"] = {"items": [{"buyer_id": "buy_0007"}], "total": 12}
    body = TestClient(app).get(BASE + "/customers/at-risk", params={"limit": 5}).json()
    assert body["data"] == [{"buyer_id": "buy_0007"}] and body["meta"]["total"] == 12
    sent = fake.sent[0]
    assert sent.skill == "list_at_risk_customers"
    assert sent.input == {"min_level": "medium", "limit": 5}
    assert sent.meta.idempotency_key is None  # 읽기라 멱등 키가 없다


@pytest.mark.parametrize(
    ("path", "skill", "payload"),
    [
        ("/customers/buy_0001/signals", "customer_signals", {"buyer_id": "buy_0001"}),
        ("/conversations/4/compensation-suggestion", "suggest_compensation", {"inquiry_id": 4}),
        ("/conversations/4/payment-diagnosis", "diagnose_payment", {"inquiry_id": 4}),
    ],
)
def test_single_lookups_pass_the_id_to_the_support_skill(
    fake: Fake, path: str, skill: str, payload: dict
) -> None:
    fake.outputs[skill] = {"message": "ok"}
    body = TestClient(app).get(BASE + path).json()
    assert body["data"] == {"message": "ok"}
    assert fake.sent[0].skill == skill and fake.sent[0].input == payload


SEND_BODY = {
    "inquiry_id": 3,
    "title": "배송 문의 급증 — 안내·화면 점검 검토",
    "evidence": "최근 60분 배송 문의 7건 (평소 창당 1.4건의 5배)",
    "evidence_ids": ["inq_5", "inq_9"],
}
SENT_OUTPUT = {"outcome": "created", "request_id": "REQ-1", "context_id": "ctx_req_0001"}


def test_sending_a_proposal_calls_request_change_with_a_key_that_stays_the_same(
    fake: Fake,
) -> None:
    fake.outputs["request_change"] = SENT_OUTPUT
    client = TestClient(app)
    first = client.post(BASE + "/change-proposals/send", json=SEND_BODY)
    second = client.post(BASE + "/change-proposals/send", json=SEND_BODY)
    assert first.status_code == 201 and first.json()["data"]["request_id"] == "REQ-1"
    sent = [r for r in fake.sent if r.skill == "request_change"]
    assert len(sent) == 2 and second.status_code == 201
    assert sent[0].input == SEND_BODY  # 상담원이 본 그대로 보낸다
    assert sent[0].meta.context_id != sent[1].meta.context_id  # 요청마다 새 사건 ID지만
    assert sent[0].meta.idempotency_key == sent[1].meta.idempotency_key  # 키는 같아 한 번만 처리


def test_an_edited_proposal_is_a_different_request(fake: Fake) -> None:
    fake.outputs["request_change"] = SENT_OUTPUT
    client = TestClient(app)
    client.post(BASE + "/change-proposals/send", json=SEND_BODY)
    client.post(BASE + "/change-proposals/send", json={**SEND_BODY, "title": "고쳐서 보낸 제목"})
    keys = {r.meta.idempotency_key for r in fake.sent if r.skill == "request_change"}
    assert len(keys) == 2


def test_a_request_planning_did_not_accept_is_a_conflict_not_a_success(fake: Fake) -> None:
    fake.outputs["request_change"] = {"planning_outcome": "rejected", "message": "받지 않았다"}
    response = TestClient(app).post(BASE + "/change-proposals/send", json=SEND_BODY)
    assert response.status_code == 409 and response.json()["error"]["code"] == "PLANNING_REFUSED"


@pytest.mark.parametrize(
    "changes",
    [
        {"title": ""},
        {"title": "가" * 201},
        {"evidence": ""},
        {"evidence_ids": [f"inq_{i}" for i in range(21)]},
    ],
)
def test_an_invalid_proposal_is_a_422_and_support_is_not_called(
    fake: Fake, changes: dict[str, Any]
) -> None:
    response = TestClient(app).post(BASE + "/change-proposals/send", json={**SEND_BODY, **changes})
    assert response.status_code == 422
    assert not [r for r in fake.sent if r.skill == "request_change"]


def test_declining_records_the_reason_with_the_author_from_the_token(fake: Fake) -> None:
    fake.outputs["decline_change_proposal"] = {"topic_code": "shipping", "created": True}
    response = TestClient(app).post(
        BASE + "/change-proposals/decline",
        json={
            "topic_code": "shipping",
            "inquiry_id": 3,
            "reason": "already_known",
            "note": "이미 개선 중",
            "author": "남이 보낸 이름",  # 본문의 작성자는 쓰지 않는다
        },
    )
    assert response.status_code == 201
    sent = next(r for r in fake.sent if r.skill == "decline_change_proposal")
    assert sent.input == {
        "topic_code": "shipping",
        "inquiry_id": 3,
        "reason": "already_known",
        "author": "tester",  # 사람 토큰의 이름
    }
    assert sent.untrusted == {"note": "이미 개선 중"}  # 상담원이 쓴 글은 untrusted로만
    assert sent.meta.idempotency_key.endswith(":decline_change_proposal:inquiry:3:decline:shipping")
    assert not [r for r in fake.sent if r.skill == "request_change"]  # 기획으로는 보내지 않는다


def test_declining_without_a_note_sends_no_untrusted_text(fake: Fake) -> None:
    TestClient(app).post(
        BASE + "/change-proposals/decline",
        json={"topic_code": "shipping", "inquiry_id": 3, "reason": "other"},
    )
    sent = next(r for r in fake.sent if r.skill == "decline_change_proposal")
    assert sent.untrusted == {}


@pytest.mark.parametrize(
    "changes",
    [{"reason": "whatever"}, {"note": "가" * 201}, {"topic_code": ""}],
)
def test_an_invalid_decline_is_a_422_and_support_is_not_called(
    fake: Fake, changes: dict[str, Any]
) -> None:
    body = {"topic_code": "shipping", "inquiry_id": 3, "reason": "other", **changes}
    assert TestClient(app).post(BASE + "/change-proposals/decline", json=body).status_code == 422
    assert not fake.sent


def test_sending_and_declining_need_a_human_token(fake: Fake) -> None:
    client = TestClient(app)
    client.headers.pop("authorization")
    send = client.post(BASE + "/change-proposals/send", json=SEND_BODY)
    decline = client.post(
        BASE + "/change-proposals/decline",
        json={"topic_code": "shipping", "inquiry_id": 3, "reason": "other"},
    )
    assert send.status_code == 401 and decline.status_code == 401
    assert not fake.sent


def test_sending_and_declining_are_not_available_in_mock_mode(mock_client: TestClient) -> None:
    send = mock_client.post(BASE + "/change-proposals/send", json=SEND_BODY)
    decline = mock_client.post(
        BASE + "/change-proposals/decline",
        json={"topic_code": "shipping", "inquiry_id": 3, "reason": "other"},
    )
    assert send.status_code == 501 and decline.status_code == 501


def test_change_proposals_in_mock_mode(mock_client: TestClient) -> None:
    body = mock_client.get(BASE + "/change-proposals").json()["data"]
    proposal = body["proposals"][0]
    assert proposal["topic_code"] == "payment" and proposal["inquiry_id"] == 1
    assert proposal["evidence_ids"] == ["inq_3", "inq_4"]  # 대표 문의 빼고 나머지 샘플


def test_change_proposals_call_support_read_only(fake: Fake) -> None:
    fake.outputs["propose_change_requests"] = {"proposals": []}
    assert TestClient(app).get(BASE + "/change-proposals").json()["data"] == {"proposals": []}
    assert [s.skill for s in fake.sent] == ["propose_change_requests"]  # 보내는 skill은 안 부른다


def test_incident_compensation_in_mock_mode(mock_client: TestClient) -> None:
    body = mock_client.get(BASE + "/incident-compensation").json()["data"]
    assert body["incidents"][0]["issued_buyers"] == 2
    picked = mock_client.get(BASE + "/incident-compensation", params={"incident_id": "ctx_inc_7"})
    assert picked.json()["data"]["incidents"][0]["context_id"] == "ctx_inc_7"


def test_incident_compensation_calls_support_read_only(fake: Fake) -> None:
    fake.outputs["list_incident_compensation"] = {"incidents": [], "notes": []}
    client = TestClient(app)
    assert client.get(BASE + "/incident-compensation").json()["data"] == {
        "incidents": [],
        "notes": [],
    }
    client.get(BASE + "/incident-compensation", params={"incident_id": "ctx_inc_0001"})
    assert [s.skill for s in fake.sent] == [
        "list_incident_compensation"
    ] * 2  # 요청하는 skill은 안 부른다
    assert fake.sent[0].input == {} and fake.sent[1].input == {
        "incident_context_id": "ctx_inc_0001"
    }


def test_incident_compensation_rejects_a_non_incident_id(fake: Fake) -> None:
    res = TestClient(app).get(BASE + "/incident-compensation", params={"incident_id": "ctx_req_1"})
    assert res.status_code == 422 and fake.sent == []


def test_surge_and_change_requests_call_support(fake: Fake) -> None:
    fake.outputs["detect_inquiry_surge"] = {"surges": [], "total_in_window": 0}
    fake.outputs["list_change_requests"] = {"items": [{"inquiry_id": 3}]}
    client = TestClient(app)
    assert client.get(BASE + "/inquiry-surge").json()["data"]["total_in_window"] == 0
    rows = client.get(BASE + "/change-requests", params={"inquiry_id": 3}).json()
    assert rows["data"] == [{"inquiry_id": 3}] and rows["meta"]["total"] == 1
    assert [s.skill for s in fake.sent] == ["detect_inquiry_surge", "list_change_requests"]
    assert fake.sent[1].input == {"inquiry_id": 3}


def test_support_errors_become_api_errors(fake: Fake, monkeypatch: pytest.MonkeyPatch) -> None:
    async def not_found(target: str, req: AgentRequest) -> AgentResult:
        return AgentResult(
            meta=req.meta,
            status=TaskStatus.COMPLETED,
            outcome="not_found",
            output={"message": "문의 9를 찾을 수 없다"},
        )

    monkeypatch.setattr(support_client, "SEND", not_found)
    res = TestClient(app).get(BASE + "/conversations/9/payment-diagnosis")
    assert res.status_code == 404 and res.json()["error"]["code"] == "NOT_FOUND"


def test_the_new_routes_are_reads_so_they_need_no_token(mock_client: TestClient) -> None:
    mock_client.headers.pop("authorization")
    assert mock_client.get(BASE + "/customers/at-risk").status_code == 200


# ---------------------------------------------------------------- 장애 보상 대기 → 사람 승인
QUEUE_BODY = {
    "incident_id": "ctx_inc_0001",
    "approve_buyer_ids": ["buy_0003", "buy_0006"],
    "reject_buyer_ids": ["buy_0004"],
}


def test_the_incident_queue_is_read_from_support(fake: Fake) -> None:
    fake.outputs["list_incident_queue"] = {"incidents": [], "notes": []}
    response = TestClient(app).get(
        BASE + "/incident-compensation-queue",
        params={"incident_id": "ctx_inc_0001", "include_closed": "true"},
    )
    assert response.status_code == 200
    sent = next(r for r in fake.sent if r.skill == "list_incident_queue")
    assert sent.input == {"include_closed": True, "incident_context_id": "ctx_inc_0001"}


def test_a_bad_incident_id_is_a_422_when_reading_the_queue(fake: Fake) -> None:
    response = TestClient(app).get(
        BASE + "/incident-compensation-queue", params={"incident_id": "inc_1"}
    )
    assert response.status_code == 422
    assert fake.sent == []


def test_deciding_sends_the_choices_with_the_author_from_the_token(fake: Fake) -> None:
    fake.outputs["decide_incident_compensation"] = {"approved": 2, "rejected": 1, "sent": 2}
    response = TestClient(app).post(
        BASE + "/incident-compensation-queue/decide",
        json={**QUEUE_BODY, "author": "남이 보낸 이름"},  # 본문의 작성자는 쓰지 않는다
    )
    assert response.status_code == 201
    sent = next(r for r in fake.sent if r.skill == "decide_incident_compensation")
    assert sent.input == {
        "incident_context_id": "ctx_inc_0001",
        "approve_buyer_ids": ["buy_0003", "buy_0006"],
        "reject_buyer_ids": ["buy_0004"],
        "author": "tester",  # 사람 토큰의 이름
    }
    assert sent.meta.idempotency_key.startswith("incident-compensation-decide:ctx_inc_0001:")


def test_the_same_choice_gets_the_same_key_so_a_double_click_is_processed_once(
    fake: Fake,
) -> None:
    client = TestClient(app)
    client.post(BASE + "/incident-compensation-queue/decide", json=QUEUE_BODY)
    reordered = {**QUEUE_BODY, "approve_buyer_ids": ["buy_0006", "buy_0003"]}
    client.post(BASE + "/incident-compensation-queue/decide", json=reordered)
    keys = [r.meta.idempotency_key for r in fake.sent if r.skill == "decide_incident_compensation"]
    assert len(keys) == 2 and keys[0] == keys[1]


@pytest.mark.parametrize(
    "changes",
    [
        {"incident_id": "inc_1"},
        {"approve_buyer_ids": [], "reject_buyer_ids": []},
        {"approve_buyer_ids": [f"buy_{i:04d}" for i in range(101)]},
    ],
)
def test_an_invalid_decision_is_rejected_and_support_is_not_called(
    fake: Fake, changes: dict[str, Any]
) -> None:
    response = TestClient(app).post(
        BASE + "/incident-compensation-queue/decide", json={**QUEUE_BODY, **changes}
    )
    assert response.status_code == 422
    assert not [r for r in fake.sent if r.skill == "decide_incident_compensation"]


def test_deciding_needs_a_human_token(fake: Fake) -> None:
    response = TestClient(app).post(
        BASE + "/incident-compensation-queue/decide",
        json=QUEUE_BODY,
        headers={"Authorization": "Bearer not-a-person"},
    )
    assert response.status_code in (401, 403)
    assert not [r for r in fake.sent if r.skill == "decide_incident_compensation"]


# ---------------------------------------------------------------- 기획 질문 답하기
def test_planning_questions_are_read_from_support(fake: Fake) -> None:
    fake.outputs["list_planning_questions"] = {"items": [], "open_count": 0}
    response = TestClient(app).get(BASE + "/planning-questions")
    assert response.status_code == 200
    sent = next(r for r in fake.sent if r.skill == "list_planning_questions")
    assert sent.input == {"status": "open"}


def test_a_bad_status_is_a_422_when_reading_planning_questions(fake: Fake) -> None:
    assert (
        TestClient(app).get(BASE + "/planning-questions", params={"status": "x"}).status_code == 422
    )
    assert fake.sent == []


def test_answering_sends_the_answer_with_the_author_from_the_token(fake: Fake) -> None:
    fake.outputs["send_planning_answer"] = {"status": "answered", "planning_outcome": "applied"}
    response = TestClient(app).post(
        BASE + "/planning-questions/answer",
        json={
            "question_id": "Q-REQ-1-1",
            "answer": "완료 기준은 안내 문구가 보이는 것입니다",
            "evidence_ids": ["inq_1"],
            "author": "남이 보낸 이름",  # 본문의 작성자는 쓰지 않는다
        },
    )
    assert response.status_code == 201
    sent = next(r for r in fake.sent if r.skill == "send_planning_answer")
    assert sent.input == {
        "question_id": "Q-REQ-1-1",
        "answer": "완료 기준은 안내 문구가 보이는 것입니다",
        "evidence_ids": ["inq_1"],
        "author": "tester",  # 사람 토큰의 이름
    }
    assert sent.meta.idempotency_key == "planning-answer:Q-REQ-1-1"  # 여러 번 눌러도 한 번


@pytest.mark.parametrize(
    "changes",
    [
        {"question_id": "REQ-1"},
        {"answer": ""},
        {"answer": "가" * 1001},
        {"evidence_ids": ["x"] * 21},
    ],
)
def test_a_bad_planning_answer_is_a_422_and_sends_nothing(
    fake: Fake, changes: dict[str, object]
) -> None:
    body = {"question_id": "Q-REQ-1-1", "answer": "답", **changes}
    assert TestClient(app).post(BASE + "/planning-questions/answer", json=body).status_code == 422
    assert not [r for r in fake.sent if r.skill == "send_planning_answer"]


# ---------------------------------------------------------------- 고객 아이디어
def test_ideas_are_read_from_support(fake: Fake) -> None:
    fake.outputs["list_ideas"] = {"items": [], "total": 0, "new_count": 0, "groups": []}
    response = TestClient(app).get(BASE + "/ideas", params={"kind": "event", "min_ideas": 2})
    assert response.status_code == 200
    sent = next(r for r in fake.sent if r.skill == "list_ideas")
    assert sent.input == {"kind": "event", "min_ideas": 2}


@pytest.mark.parametrize("params", [{"kind": "x"}, {"area": "x"}, {"status": "x"}])
def test_a_bad_idea_filter_is_a_422(fake: Fake, params: dict[str, str]) -> None:
    assert TestClient(app).get(BASE + "/ideas", params=params).status_code == 422
    assert fake.sent == []


def test_sending_the_idea_digest_uses_the_author_from_the_token(fake: Fake) -> None:
    fake.outputs["send_idea_digest"] = {"sent": [], "failed": 0}
    response = TestClient(app).post(
        BASE + "/ideas/digest", json={"kind": "event", "author": "남이 보낸 이름"}
    )
    assert response.status_code == 201
    sent = next(r for r in fake.sent if r.skill == "send_idea_digest")
    assert sent.input == {"kind": "event", "min_ideas": 3, "author": "tester"}


def test_idea_trends_are_read_from_support(fake: Fake) -> None:
    fake.outputs["list_idea_trends"] = {"period": "month", "buckets": []}
    response = TestClient(app).get(BASE + "/ideas/trends", params={"period": "month", "top": 3})
    assert response.status_code == 200
    sent = next(r for r in fake.sent if r.skill == "list_idea_trends")
    assert sent.input == {"period": "month", "buckets": 8, "top": 3}


@pytest.mark.parametrize("params", [{"period": "x"}, {"buckets": 0}, {"top": 99}])
def test_a_bad_idea_trend_query_is_a_422(fake: Fake, params: dict[str, object]) -> None:
    assert TestClient(app).get(BASE + "/ideas/trends", params=params).status_code == 422
    assert fake.sent == []


def test_inquiry_pull_status_is_read_from_support(fake: Fake) -> None:
    fake.outputs["inquiry_pull_status"] = {"enabled": True, "connected": False, "pending": 2}
    response = TestClient(app).get(BASE + "/inquiry-pull")
    assert response.status_code == 200
    assert response.json()["data"]["connected"] is False
    assert next(r for r in fake.sent if r.skill == "inquiry_pull_status").input == {}


def test_inquiry_pull_status_in_mock_mode_is_empty(mock_client: TestClient) -> None:
    data = mock_client.get(BASE + "/inquiry-pull").json()["data"]
    assert data["enabled"] is False and data["connected"] is None


def test_stuck_intake_is_listed_and_retried_through_support(fake: Fake) -> None:
    fake.outputs["list_stuck_intake"] = {
        "items": [{"kind": "claim", "seq": 3, "reason": "INGEST_FAILED", "attempts": 6}],
        "total": 1,
    }
    fake.outputs["retry_stuck_intake"] = {"kind": "claim", "seq": 3, "requeued": True}
    client = TestClient(app)
    assert client.get(BASE + "/inquiry-pull/stuck").json()["data"]["total"] == 1
    done = client.post(BASE + "/inquiry-pull/stuck/claim/3/retry")
    assert done.status_code == 200 and done.json()["data"]["requeued"] is True
    sent = next(r for r in fake.sent if r.skill == "retry_stuck_intake")
    assert sent.input == {"kind": "claim", "seq": 3}
    assert client.post(BASE + "/inquiry-pull/stuck/other/3/retry").status_code == 422
    assert client.post(BASE + "/inquiry-pull/stuck/claim/0/retry").status_code == 422


def test_stuck_intake_in_mock_mode(mock_client: TestClient) -> None:
    assert mock_client.get(BASE + "/inquiry-pull/stuck").json()["data"] == {"items": [], "total": 0}
    assert mock_client.post(BASE + "/inquiry-pull/stuck/claim/1/retry").status_code == 501


def test_inquiry_pull_status_takes_the_claim_numbers_from_the_claims_server(fake: Fake) -> None:
    fake.outputs["inquiry_pull_status"] = {
        "enabled": True,
        "connected": True,
        "pending": 2,
        "claims_pending": 0,
        "last_error": None,
    }
    fake.outputs["claim_pull_status"] = {
        "connected": False,
        "last_error": "UNAVAILABLE",
        "claims_pending": 7,
        "claims_ingested_total": 3,
        "returns_ingested_total": 1,
    }
    data = TestClient(app).get(BASE + "/inquiry-pull").json()["data"]
    assert data["pending"] == 2 and data["claims_pending"] == 7  # 문의는 문의 서버, 클레임은 claims
    assert data["claims_ingested_total"] == 3 and data["returns_ingested_total"] == 1
    assert data["connected"] is False and data["last_error"] == "UNAVAILABLE"


def test_inquiry_pull_still_answers_when_the_claims_server_is_down(
    fake: Fake, monkeypatch: pytest.MonkeyPatch
) -> None:
    from ax_dashboard_api.envelope import ApiError

    fake.outputs["inquiry_pull_status"] = {
        "enabled": True, "connected": True, "pending": 2, "claims_pending": None,
        "claims_ingested_total": 0, "returns_ingested_total": 0,
    }  # fmt: skip

    async def send(target: str, req: AgentRequest) -> AgentResult:
        if req.skill == "claim_pull_status":
            raise ApiError(504, "TIMEOUT", "claims가 느리다")
        return AgentResult(
            meta=req.meta, status=TaskStatus.COMPLETED, output=fake.outputs[req.skill]
        )

    monkeypatch.setattr(support_client, "SEND", send)
    res = TestClient(app).get(BASE + "/inquiry-pull")
    assert res.status_code == 200  # 문의 값은 그대로 보여 준다
    assert res.json()["data"]["pending"] == 2 and res.json()["data"]["claims_unavailable"] is True
    # 문의 서버의 빈 claims_*·returns_* 칸은 "0건"으로 읽히니 싣지 않는다
    assert not [k for k in res.json()["data"] if k.startswith(("claims_p", "claims_i", "returns_"))]


def test_inquiry_pull_shows_both_servers_errors_and_times_apart(fake: Fake) -> None:
    fake.outputs["inquiry_pull_status"] = {
        "enabled": True, "connected": False, "last_error": "UNAVAILABLE",
        "last_attempt_at": "t-inquiry", "last_ok_at": "t-inquiry-ok",
    }  # fmt: skip
    fake.outputs["claim_pull_status"] = {
        "connected": False, "last_error": "INGEST_FAILED", "last_attempt_at": "t-claims",
        "last_ok_at": None,
    }  # fmt: skip
    data = TestClient(app).get(BASE + "/inquiry-pull").json()["data"]
    assert data["last_error"] == "UNAVAILABLE" and data["claims_last_error"] == "INGEST_FAILED"
    assert data["last_attempt_at"] == "t-inquiry" and data["claims_last_attempt_at"] == "t-claims"
