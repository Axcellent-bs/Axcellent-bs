"""⑦ 내부통제·사후 감사 테스트: 의심 코드마다 잡히는지, 안 잡혀야 할 때 안 잡히는지."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta

import pytest

from finance_agent import audit
from finance_agent import books as bk
from finance_agent import finance_config as fc
from finance_agent.closing import compute_totals

ALL_CODES = {
    "split_under_limit",
    "duplicate_coupon",
    "amount_recheck_failed",
    "gate_hold_trend",
}


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


def test_seed_triggers_every_finding_code(fixed_now) -> None:
    data, missing = _report(bk.seed_books())
    assert missing == []
    assert (data["from"], data["to"]) == ("2026-10-17T00:00:00Z", "2026-10-24T00:00:00Z")
    found = _by_code(data["findings"])
    assert set(found) == ALL_CODES
    for f in data["findings"]:
        assert set(f) == {"code", "severity", "title", "detail", "refs"}
        assert f["severity"] in ("high", "medium", "low")
    order = [audit.SEVERITY_ORDER[f["severity"]] for f in data["findings"]]
    assert order == sorted(order)  # 심각한 것 먼저

    [split] = found["split_under_limit"]
    assert "buy_0007" in split["detail"] and "19,900원" in split["detail"]
    assert "20,000원" in split["detail"] and split["severity"] == "high"
    assert len([r for r in split["refs"] if r.startswith("cpn_")]) == 3
    assert any(r.startswith("exe_") for r in split["refs"])
    [dup] = found["duplicate_coupon"]
    assert "buy_0011" in dup["detail"] and "ctx_inc_0104" in dup["detail"]
    [recheck] = found["amount_recheck_failed"]
    assert "쿠폰 지급 요청 총액" in recheck["detail"] and recheck["refs"][0].startswith("exe_")
    [trend] = found["gate_hold_trend"]
    assert "보류 3건·거절 1건" in trend["detail"] and "1건)" in trend["detail"]
    assert trend["title"] == "안전 장치 보류·거절 증가" and "관문" not in trend["detail"]
    assert "관문" not in recheck["title"]


def test_seed_stats(fixed_now) -> None:
    data, _ = _report(bk.seed_books())
    stats = data["stats"]
    assert set(stats) == {
        "coupon_count",
        "coupon_amount",
        "gate_held",
        "gate_rejected",
    }
    assert (stats["gate_held"], stats["gate_rejected"]) == (3, 1)
    assert stats["coupon_amount"] > 0 and stats["coupon_count"] > 0


@pytest.mark.parametrize("offset", range(7))
def test_every_code_on_any_weekday(monkeypatch: pytest.MonkeyPatch, offset: int) -> None:
    now = datetime(2026, 10, 19, 12, tzinfo=UTC) + timedelta(days=offset)
    monkeypatch.setattr(fc, "now", lambda: now)
    data, _ = _report(bk.seed_books())
    assert {f["code"] for f in data["findings"]} == ALL_CODES


# ---------------------------------------------------------------- 규칙별
def _coupon(kit, cid, buyer, unit, when, ctx=None, count=1, purpose="compensation"):
    return {
        "coupon_id": cid,
        "buyer_id": buyer,
        "unit_value": unit,
        "count": count,
        "total_value": unit * count,
        "purpose_code": purpose,
        "context_id": ctx,
        "created_at": when,
        "funded_by": "company",
    }


def test_split_coupons_needs_near_limit_and_short_window(kit, fixed_now) -> None:
    t = kit.at("2026-10-22", "10:00")
    near = [
        _coupon(kit, f"cpn_{i}", "buy_0981", 18000, t + timedelta(minutes=20 * i)) for i in range(3)
    ]
    found = _by_code(_report(kit.books(coupons=near))[0]["findings"])
    assert "split_under_limit" in found
    spread = [c | {"created_at": t + timedelta(hours=2 * i)} for i, c in enumerate(near)]
    assert "split_under_limit" not in _by_code(_report(kit.books(coupons=spread))[0]["findings"])
    small = [c | {"unit_value": 10000, "total_value": 10000} for c in near]
    assert "split_under_limit" not in _by_code(_report(kit.books(coupons=small))[0]["findings"])
    one = [_coupon(kit, "cpn_9", "buy_0981", 19000, t, count=3)]  # 한 번에 3장은 쪼개기가 아니다
    assert "split_under_limit" not in _by_code(_report(kit.books(coupons=one))[0]["findings"])


def test_duplicate_coupon_same_context_only(kit, fixed_now) -> None:
    t = kit.at("2026-10-21")
    two = [
        _coupon(kit, "cpn_1", "buy_0984", 5000, t, "ctx_inc_1"),
        _coupon(kit, "cpn_2", "buy_0984", 5000, t, "ctx_inc_1"),
    ]
    assert "duplicate_coupon" in _by_code(_report(kit.books(coupons=two))[0]["findings"])
    two[1]["context_id"] = "ctx_inc_2"
    assert "duplicate_coupon" not in _by_code(_report(kit.books(coupons=two))[0]["findings"])
    camp = [c | {"purpose_code": "campaign", "context_id": "ctx_cmp_1"} for c in two]
    assert "duplicate_coupon" not in _by_code(_report(kit.books(coupons=camp))[0]["findings"])


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


def test_amount_recheck_trusts_matching_coupon_without_impact(kit, fixed_now) -> None:
    """장애 보상 쿠폰은 사후에 피해 고객 자료가 없어도 금액만 맞으면 의심하지 않는다."""
    when = kit.at("2026-10-20")
    executions = [
        {
            "execution_id": "exe_3",
            "created_at": when,
            "action": "issue_coupon",
            "requester": "marketing",
            "context_id": "ctx_inc_9",
            "params": {
                "count": 2,
                "unit_value": 5000,
                "total_value": 10000,
                "purpose_code": "compensation",
                "buyer_id": "buy_0985",
            },
            "status": "executed",
            "computed_amount": 10000,
        },
    ]
    found = _by_code(_report(kit.books(executions=executions))[0]["findings"])
    assert "amount_recheck_failed" not in found


def test_coupon_recheck_detail_is_plain_korean(kit, fixed_now) -> None:
    executions = [
        {
            "execution_id": "exe_9",
            "created_at": kit.at("2026-10-22"),
            "action": "issue_coupon",
            "requester": "marketing",
            "context_id": "ctx_mkt_9",
            "params": {"count": 2, "unit_value": 5000, "total_value": 12000},
            "status": "executed",
            "computed_amount": 12000,
        }
    ]
    found = _by_code(_report(kit.books(executions=executions))[0]["findings"])
    [f] = found["amount_recheck_failed"]
    assert f["refs"] == ["exe_9"]
    assert "쿠폰 지급 요청 총액" in f["detail"] and "10,000원" in f["detail"]
    assert "issue_coupon" not in f["detail"] and "{" not in f["detail"]


def test_gate_trend_and_missing_executions(kit, fixed_now) -> None:
    t = kit.at("2026-10-22")
    held = [
        {
            "execution_id": f"exe_{i}",
            "created_at": t,
            "action": "issue_coupon",
            "status": "held",
            "params": {},
        }
        for i in range(3)
    ]
    data, _ = _report(kit.books(executions=held))
    assert "gate_hold_trend" in _by_code(data["findings"])
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
