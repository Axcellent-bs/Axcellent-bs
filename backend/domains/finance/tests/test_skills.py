"""finance 단독 테스트 (공통 구성안 11장 8항목 + 재무 구성안 8장)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

from ax_agent_template import events
from ax_contracts.schemas import EventEnvelope, EventType, ExecuteDecision
from finance_agent import handlers
from finance_agent.main import app, load_card
from finance_agent.sources import MockSources

CTX = "ctx_inc_0001"


@pytest.fixture(autouse=True)
def _mock_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_TEMPLATE_MOCK", "1")
    events.reset_mock_bus()
    handlers.reset_state(MockSources())


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def _body(
    skill: str,
    data: dict,
    *,
    token: str = "mock:safety",
    path: list[str] | None = None,
    key: str | None = None,
    untrusted: dict | None = None,
) -> dict:
    return {
        "meta": {
            "context_id": CTX,
            "token": token,
            "path": path or [],
            "deadline": (datetime.now(UTC) + timedelta(seconds=30)).isoformat(),
            **({"idempotency_key": key} if key else {}),
        },
        "skill": skill,
        "input": data,
        "untrusted": untrusted or {},
    }


def _call(client: TestClient, skill: str, data: dict, **kw: Any) -> dict:
    response = client.post("/a2a", json=_body(skill, data, **kw))
    assert response.status_code == 200
    return response.json()


def _fake_execute(monkeypatch: pytest.MonkeyPatch, decision: ExecuteDecision) -> list[dict]:
    calls: list[dict] = []

    async def fake(action: str, params: dict, context: Any, evidence: Any) -> ExecuteDecision:
        calls.append({"action": action, "params": params, "key": context.idempotency_key})
        return decision

    monkeypatch.setattr(handlers, "execute", fake)
    return calls


def _decision(status: str = "executed", grade: str = "B", **kw: Any) -> ExecuteDecision:
    return ExecuteDecision(execution_id="exe_9", grade=grade, status=status, **kw)  # type: ignore[arg-type]


# ---------------------------------------------------------------- ① Agent Card
def test_agent_card(client: TestClient) -> None:
    card = load_card()
    skills = {s.id: s for s in card.skills}
    assert set(skills) == {
        "closing_report",
        "audit_report",
        "record_support_cost",
        "answer_planning_question",
    }
    assert skills["closing_report"].allowed_requesters == ["orchestrator", "data"]
    assert "review_threshold_change" not in skills  # 한도는 제안만, 사람이 기준표 PR로 바꾼다
    # 재무는 장애 대응 흐름의 단계가 아니다 (2026-10-27, 요구사항 DEP-FIN-01)
    assert "loss_aggregation" not in skills
    assert skills["audit_report"].allowed_requesters == ["orchestrator", "data"]
    assert (skills["audit_report"].grade, skills["audit_report"].priority) == (
        "read",
        "optional",
    )
    served = client.get("/.well-known/agent-card.json").json()
    assert served["name"] == "finance" and len(served["skills"]) == 4


# ------------------------------------------- 금액 다시 계산 (예전 verify_amount, recheck.py)
def test_refunds_accumulate_from_events(client: TestClient) -> None:
    import asyncio

    asyncio.run(
        handlers.on_event(
            EventEnvelope(
                event_id="evt_r1",
                type=EventType.PAYMENT_REFUNDED,
                occurred_at=datetime.now(UTC),
                source="shop-api",
                idempotency_key="r1",
                data={
                    "payment_id": "pay_1",
                    "order_id": "ord_1001",
                    "refund_id": "rfd_1",
                    "refund_amount": 20_000,
                },
            )
        )
    )
    ledger = client.get("/api/finance/ledger").json()["data"]
    assert ledger["refunds"] == [{"order_id": "ord_1001", "refunded": 20_000}]


# ---------------------------------------------------------------- 공통 안전장치
def test_loop_is_rejected(client: TestClient) -> None:
    """③ path에 자기 이름이 있는 새 요청은 거부."""
    result = _call(client, "closing_report", {}, token="mock:orchestrator", path=["finance"])
    assert (result["status"], result["outcome"]) == ("rejected", "LOOP_DETECTED")


@pytest.mark.parametrize("token", ["mock:marketing", "mock:support", "mock:safety"])
def test_only_orchestrator_and_data_can_ask_reports(client: TestClient, token: str) -> None:
    """④ 권한: 결산·감사 리포트는 통합 에이전트와 데이터 AI만 부른다."""
    for skill in ("closing_report", "audit_report"):
        result = _call(client, skill, {}, token=token)
        assert (result["status"], result["outcome"]) == ("rejected", "FORBIDDEN_SKILL")


@pytest.mark.parametrize(
    "skill", ["verify_amount", "review_hold", "seller_review", "settlement_hold"]
)
def test_removed_skills_are_not_served(client: TestClient, skill: str) -> None:
    """재무 재설계: 건별 교차검증·AI 1차 판단 skill은 없다(안전 장치가 불러도 거부).

    마켓운영(판매자 입점 심사·정산 보류)도 재무 일이 아니라 없앴다 (2026-10-30).
    """
    result = _call(client, skill, {}, token="mock:safety")
    assert result["status"] == "rejected"
    assert skill not in {s.id for s in load_card().skills}


def test_impact_parser_uses_data_field_names() -> None:
    """data `ImpactCounts` 필드 이름(failed_order_count 등)을 그대로 읽는다."""
    from finance_agent.sources import _impact_from

    impact = _impact_from(
        {"failed_order_count": 2, "affected_customer_count": 1, "damage_amount": 5000}, "r"
    )
    assert impact is not None and (impact.failed_orders, impact.lost_amount) == (2, 5000)
    assert (
        _impact_from({"failed_orders": 2, "affected_customers": 1, "lost_amount": 1}, "r") is None
    )


# ---------------------------------------------------------------- 대시보드 조회 API
def test_removed_lists_are_gone(client: TestClient) -> None:
    """재무 재설계로 `/verifications`·`/ai-reviews`, 마켓운영을 빼며 `/market-ops`가 없어졌다."""
    assert client.get("/api/finance/verifications").status_code == 404
    assert client.get("/api/finance/ai-reviews").status_code == 404
    assert client.get("/api/finance/market-ops").status_code == 404
    assert client.get("/api/finance/ledger").json()["data"] == {"compensation": [], "refunds": []}


def test_dashboard_can_read_but_not_post(client: TestClient) -> None:
    """dashboard-web(3001)은 GET만 허용. A2A 쓰기 경로는 브라우저에 열지 않는다."""
    get = client.get("/api/finance/ledger", headers={"Origin": "http://localhost:3001"})
    assert get.headers.get("access-control-allow-origin") == "http://localhost:3001"
    preflight = client.options(
        "/a2a",
        headers={"Origin": "http://localhost:3001", "Access-Control-Request-Method": "POST"},
    )
    assert preflight.status_code == 400


# ---------------------------------------------------------------- 추가 테스트
def test_partial_refunds_add_up(client: TestClient) -> None:
    """같은 주문 환불은 장부에 쌓인다. 결제 금액과 비교하지 않고 받은 금액을 그대로 적는다."""
    import asyncio

    async def refunded(amount: int, n: int) -> None:
        await handlers.on_event(
            EventEnvelope(
                event_id=f"evt_rf{n}",
                type=EventType.PAYMENT_REFUNDED,
                occurred_at=datetime.now(UTC),
                source="shop-api",
                idempotency_key=f"rf{n}",
                data={
                    "payment_id": "pay_2",
                    "order_id": "ord_1002",
                    "refund_id": f"rfd_{n}",
                    "refund_amount": amount,
                },
            )
        )

    asyncio.run(refunded(30_000, 1))
    assert handlers.state().ledger.refunds == {"ord_1002": 30_000}
    asyncio.run(refunded(20_000, 2))
    assert handlers.state().ledger.refunds == {"ord_1002": 50_000}


def test_expired_deadline_is_canceled(client: TestClient) -> None:
    body = _body("closing_report", {}, token="mock:orchestrator")
    body["meta"]["deadline"] = (datetime.now(UTC) - timedelta(seconds=1)).isoformat()
    result = client.post("/a2a", json=body).json()
    assert (result["status"], result["outcome"]) == ("canceled", "TIMEOUT")


def test_too_many_hops_are_refused(client: TestClient) -> None:
    result = _call(
        client,
        "audit_report",
        {},
        token="mock:orchestrator",
        path=["orchestrator", "support", "marketing", "data"],
    )
    assert (result["status"], result["outcome"]) == ("rejected", "HOP_LIMIT")


def test_card_descriptions_say_whether_state_changes() -> None:
    """다른 AI가 고를 수 있게, 보기만 하는 skill은 '상태를 바꾸지 않는다'를 적는다."""
    for skill in load_card().skills:
        if skill.grade == "read":
            assert "상태를 바꾸지 않는다" in skill.description
        else:
            assert "execute" in skill.description
