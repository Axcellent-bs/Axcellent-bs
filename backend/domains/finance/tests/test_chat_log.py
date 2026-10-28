"""재무 AI가 한 일을 AI 대화방에 보이게 하는 규칙 (장부 기록 대화, 사후 감사 → 재무 이상 사건)."""

from __future__ import annotations

from typing import Any

import pytest

from ax_contracts.schemas import EventEnvelope, EventType
from finance_agent import chat_log


@pytest.fixture
def logged(monkeypatch: pytest.MonkeyPatch) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []

    def fake(context_id: str, tenant_id: str, kind: str, payload: dict, **kw: Any) -> None:
        rows.append({"context_id": context_id, "kind": kind, "payload": payload, **kw})

    monkeypatch.setattr(chat_log, "log_event", fake)
    return rows


@pytest.fixture
def published(monkeypatch: pytest.MonkeyPatch) -> list[EventEnvelope]:
    sent: list[EventEnvelope] = []

    async def fake(envelope: EventEnvelope) -> None:
        sent.append(envelope)

    monkeypatch.setattr(chat_log.events, "publish", fake)
    monkeypatch.setattr(chat_log, "_seen_findings", set())
    return sent


def test_ledger_record_is_one_line(logged: list[dict[str, Any]]) -> None:
    chat_log.ledger_recorded("ctx_inc_1", "shop-demo", "marketing", "coupon", 2000)
    assert len(logged) == 1
    row = logged[0]
    assert row["kind"] == "result_sent" and row["task_status"] == "completed"
    assert row["source"] == "finance" and row["requester"] == "marketing"
    assert row["skill"] == "ledger_record"
    assert row["payload"] == {"ledger_kind": "coupon", "amount": 2000, "count": 1}


def test_ledger_without_incident_is_not_a_chat(logged: list[dict[str, Any]]) -> None:
    """대화방은 사건마다 방이 있다. 사건 번호 없는 기록은 대화로 남기지 않는다(장부에는 적힌다)."""
    chat_log.ledger_recorded(None, "shop-demo", "shop-api", "refund", 18000)
    assert logged == []


@pytest.mark.asyncio
async def test_new_audit_finding_opens_finance_incident(published: list[EventEnvelope]) -> None:
    finding = {
        "code": "repeat_refund", "severity": "medium",
        "detail": "buy_0008의 주문 3건 환불, 합계 49,500원 (최근 7일)", "refs": ["ref_1", "ord_1"],
    }
    await chat_log.audit_findings([finding], "shop-demo")
    await chat_log.audit_findings([dict(finding)], "shop-demo")  # 화면이 감사를 다시 불러도
    assert len(published) == 1
    env = published[0]
    assert env.type == EventType.FINANCE_ANOMALY_DETECTED and env.source == "finance"
    assert env.data["component"] == "finance" and env.data["metric_code"] == "repeat_refund"
    assert env.data["buyer_ids"] == ["buy_0008"]
    await chat_log.audit_findings([{**finding, "refs": ["ref_9"]}], "shop-demo")  # 다른 의심 건은 새로
    assert len(published) == 2
