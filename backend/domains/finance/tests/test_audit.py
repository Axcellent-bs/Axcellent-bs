"""⑦ 사후 감사 테스트: 안전 장치 보류·거절 추이가 잡히는지, 쿠폰·환불은 세지 않는지."""

from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest

from finance_agent import audit
from finance_agent import finance_config as fc
from finance_agent.closing import compute_totals


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_TEMPLATE_MOCK", "1")


def _report(books, days: int = 7):
    return asyncio.run(audit.audit_report(books, days))


def _by_code(findings):
    out: dict[str, list] = {}
    for f in findings:
        out.setdefault(f["code"], []).append(f)
    return out


# ---------------------------------------------------------------- 규칙별
def test_refund_rules_no_longer_create_findings(kit, fixed_now) -> None:
    """환불은 고객지원 일이다. 잦고 크고 결제 금액을 넘어도 재무는 의심 건을 내지 않는다."""
    now = kit.at("2026-10-22", "09:00")
    earlier = kit.at("2026-10-13", "09:00")
    orders = [
        kit.order("ord_now", now, [("sel_a", "book", 1, 100000)]),
        kit.order("ord_before", earlier, [("sel_a", "book", 1, 100000)]),
    ]
    refunds = [
        {  # 예전 반복 환불·한도 바로 아래 쪼개기에 걸리던 환불: 같은 고객 주문 3건, 건마다 95,000원
            "refund_id": f"rfd_{i}",
            "order_id": f"ord_{i}",
            "buyer_id": "buy_0982",
            "seller_id": "sel_a",
            "amount": 95000,
            "created_at": now + timedelta(hours=i),
        }
        for i in range(3)
    ]
    executions = [
        {  # 예전 재계산 실패에 걸리던 환불: 결제 100,000원 주문에 150,000원
            "execution_id": "exe_over",
            "created_at": now,
            "action": "refund_request",
            "requester": "support",
            "context_id": "ctx_req_1",
            "params": {"order_id": "ord_now", "refund_amount": 150000},
            "status": "executed",
            "computed_amount": 150000,
        },
        {  # 예전 재계산 실패(형식 오류)에 걸리던 환불
            "execution_id": "exe_bad",
            "created_at": now,
            "action": "refund_request",
            "requester": "support",
            "context_id": "ctx_req_2",
            "params": {"order_id": "ord_now"},
            "status": "executed",
        },
    ]
    books = kit.books(orders=orders, refunds=refunds, executions=executions)
    data, _ = _report(books)
    assert data["findings"] == []
    assert not {k for k in data["stats"] if "refund" in k}
    # 결산의 환불 합계는 그대로 장부에 있다
    start, end = fc.day_start(now.date()), fc.day_start(now.date() + timedelta(days=1))
    assert compute_totals(books, start, end).refund_total == 285000
    # 사건 하나로 좁혀 봐도 같다
    scoped, _ = asyncio.run(audit.audit_report(books, 7, context_id="ctx_req_1"))
    assert scoped["findings"] == []


def test_gate_trend_and_missing_executions(kit, fixed_now) -> None:
    t = kit.at("2026-10-22")
    held = [
        {
            "execution_id": f"exe_{i}",
            "created_at": t,
            "action": "confirm_settlement",
            "status": "held",
            "params": {},
        }
        for i in range(3)
    ]
    data, _ = _report(kit.books(executions=held))
    [trend] = _by_code(data["findings"])["gate_hold_trend"]
    assert "보류 3건·거절 0건" in trend["detail"] and "(지난 7일 0건)" in trend["detail"]
    assert trend["severity"] == "medium" and trend["refs"] == ["exe_0", "exe_1", "exe_2"]
    assert trend["title"] == "안전 장치 보류·거절 증가" and "관문" not in trend["detail"]
    assert set(trend) == {"code", "severity", "title", "detail", "refs"}
    assert data["stats"] == {"gate_held": 3, "gate_rejected": 0}
    prev = [
        e | {"execution_id": f"exe_p{i}", "created_at": t - timedelta(days=8)}
        for i, e in enumerate(held)
    ]
    data, _ = _report(kit.books(executions=held + prev))
    assert "gate_hold_trend" not in _by_code(data["findings"])  # 지난 기간과 같으면 추이 아님
    data, missing = _report(kit.books(executions=None))
    assert (data["stats"]["gate_held"], data["stats"]["gate_rejected"]) == (None, None)
    assert missing == ["stats.gate_held", "stats.gate_rejected"]


def test_quiet_books_have_no_findings(kit, fixed_now) -> None:
    data, _ = _report(kit.books())
    assert data["findings"] == []


def test_context_filter_narrows_the_trend(kit, fixed_now) -> None:
    t = kit.at("2026-10-22")
    rows = [
        {
            "execution_id": f"exe_{i}",
            "created_at": t,
            "action": "confirm_settlement",
            "status": "held",
            "context_id": ctx,
            "params": {},
        }
        for i, ctx in enumerate(["ctx_a", "ctx_a", "ctx_a", "ctx_b"])
    ]
    data, _ = asyncio.run(audit.audit_report(kit.books(executions=rows), 7, context_id="ctx_a"))
    assert data["stats"]["gate_held"] == 3 and data["findings"][0]["refs"] == [
        "exe_0",
        "exe_1",
        "exe_2",
    ]
    data, _ = asyncio.run(audit.audit_report(kit.books(executions=rows), 7, context_id="ctx_b"))
    assert data["stats"]["gate_held"] == 1 and data["findings"] == []
