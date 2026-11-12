"""은행 출금 대조(판매자 이체·항목별·설명 없는 출금), 통장 잔액, 잔액 기초 현금 흐름.

정산 묶음·내역서는 고객지원이 준다(`seller_settlement_batches`·`seller_settlement_weeks`).
"""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from finance_agent import bank, cashflow, close_checklist, handlers
from finance_agent.main import app
from finance_agent.sources import MockSources

TODAY = date(2026, 10, 23)
HUMAN = {"Authorization": "Bearer human:taeyoon"}
OCT = [{"from": "2026-10-01", "to": "2026-10-31"}]


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch, fixed_now) -> None:
    monkeypatch.setenv("AGENT_TEMPLATE_MOCK", "1")
    monkeypatch.delenv("FINANCE_HUMAN_TOKENS", raising=False)
    monkeypatch.delenv("SAFETY_HUMAN_TOKENS", raising=False)
    handlers.reset_state(MockSources())


def _out(d: str, amount: int, memo: str, balance: int | None = None) -> dict:
    return {"date": d, "amount": -amount, "memo": memo, "balance": balance}


def _batch(approved: str, payouts: dict[str, int]) -> dict:
    return {
        "status": "approved",
        "approved_at": f"{approved}T10:00:00+09:00",
        "payouts": payouts,
        "total_payout": sum(payouts.values()),
        "seller_ids": list(payouts),
    }


def _view(rows, periods=OCT, batches=None, today=TODAY, **kw):
    return bank.outflows(
        rows, periods, batches or {}, seller_name=lambda s: f"판매자 {s}", today=today, **kw
    )


# ---------------------------------------------------------------- 판매자 이체
def test_seller_transfers_are_found_waited_or_missing() -> None:
    batches = {
        "2026-W41": _batch("2026-10-14", {"sel_a": 30000, "sel_b": 20000}),  # a만 이체
        "2026-W42": _batch("2026-10-16", {"sel_a": 7000, "sel_c": 3000}),  # 합계 한 건(일괄)
        "2026-W43": _batch("2026-10-22", {"sel_d": 9000}),  # 아직 기한 안
        "pending": {"status": "held", "payouts": {"sel_e": 1}, "total_payout": 1},
    }
    rows = [_out("2026-10-15", 30000, "이체 sel_a"), _out("2026-10-17", 10000, "정산 일괄")]
    v = _view(rows, batches=batches)
    got = {(t["batch"], t["seller_id"]): t for t in v["transfers"]}
    assert got[("2026-W41", "sel_a")]["status"] == "paid"
    assert got[("2026-W41", "sel_a")]["bank_date"] == "2026-10-15"
    assert got[("2026-W41", "sel_b")]["status"] == "not_found"  # 10-17까지 출금 없음
    assert {got[("2026-W42", s)]["status"] for s in ("sel_a", "sel_c")} == {"paid"}
    assert got[("2026-W42", "sel_c")]["bulk"] is True
    assert got[("2026-W43", "sel_d")]["status"] == "waiting"  # 10-25까지 기다린다
    assert ("pending", "sel_e") not in got  # 승인 안 된 묶음은 보지 않는다
    assert v["counts"] | {"unexplained_open": 0} == {
        "paid": 3,
        "waiting": 1,
        "not_found": 1,
        "not_covered": 0,
        "unexplained_open": 0,
    }
    assert v["unexplained"] == []  # 이체로 맞춘 출금은 설명 없는 출금이 아니다


def test_transfer_outside_withdrawal_period_is_not_judged() -> None:
    batches = {"w": _batch("2026-10-20", {"sel_a": 5000})}
    v = _view([], periods=[{"from": "2026-10-01", "to": "2026-10-10"}], batches=batches)
    assert v["transfers"][0]["status"] == "not_covered"


def test_same_withdrawal_is_used_once() -> None:
    batches = {
        "w1": _batch("2026-10-14", {"sel_a": 5000}),
        "w2": _batch("2026-10-14", {"sel_b": 5000}),
    }
    v = _view([_out("2026-10-15", 5000, "이체")], batches=batches)
    assert sorted(t["status"] for t in v["transfers"]) == ["not_found", "paid"]


# ---------------------------------------------------------------- 항목별·설명 없는 출금
def test_categories_compare_with_entered_costs_only_for_full_months() -> None:
    rows = [
        _out("2026-10-25", 1_000_000, "10월 급여"),
        _out("2026-10-26", 200_000, "사무실 월세"),
        _out("2026-10-27", 30_000, "네이버 광고"),
        _out("2026-10-28", 5_000, "ATM 출금"),
        _out("2026-09-30", 9_000, "급여"),  # 출금 기간 밖이라 보지 않는다
        {"date": "2026-10-28", "amount": 1000, "memo": "이자", "balance": None},  # 입금은 제외
    ]
    other = {"2026-10": {"salary": 1_000_000, "rent": 250_000}}
    campaigns = {"c1": {"period_to": "2026-10-20", "spend_krw": 30_000}}
    v = _view(rows, other_pnl=other, campaigns=campaigns, today=date(2026, 11, 2))
    october = v["months"][0]
    assert october["month"] == "2026-10" and october["fully_covered"] is True
    rows_by = {r["category"]: r for r in october["rows"]}
    assert (rows_by["급여"]["bank"], rows_by["급여"]["diff"]) == (1_000_000, 0)
    assert (rows_by["임차료"]["bank"], rows_by["임차료"]["diff"]) == (200_000, -50_000)
    assert (rows_by["광고비"]["book"], rows_by["광고비"]["diff"]) == (30_000, 0)
    assert rows_by["세금"]["judged"] is False and rows_by["세금"]["comparable"] is False
    assert [u["memo"] for u in v["unexplained"]] == ["ATM 출금"]
    assert v["counts"]["unexplained_open"] == 1


def test_partial_month_is_shown_but_not_judged() -> None:
    v = _view(
        [_out("2026-10-05", 1_000, "급여")],
        periods=[{"from": "2026-10-01", "to": "2026-10-10"}],
        other_pnl={"2026-10": {"salary": 5_000}},
    )
    row = v["months"][0]["rows"][0]
    assert v["months"][0]["fully_covered"] is False
    assert (row["bank"], row["book"], row["diff"], row["judged"]) == (1_000, 5_000, None, False)


def test_nothing_is_judged_before_a_statement_with_withdrawals() -> None:
    v = _view([_out("2026-10-05", 1_000, "급여")], periods=[])
    assert v["uploaded"] is False and v["transfers"] == [] and v["unexplained"] == []


# ---------------------------------------------------------------- 통장 잔액
def test_latest_balance_follows_the_running_balance() -> None:
    rows = [
        {"date": "2026-10-20", "amount": 1000, "memo": "", "balance": 1000},
        {"date": "2026-10-21", "amount": 500, "memo": "", "balance": 1400},  # 늦은 줄이 먼저 와도
        {"date": "2026-10-21", "amount": -100, "memo": "", "balance": 900},
    ]
    assert bank.latest_balance(rows) == {"as_of": "2026-10-21", "balance": 1400, "ordered": True}
    assert bank.latest_balance([{"date": "2026-10-21", "amount": 1, "memo": ""}]) is None
    same = [
        {"date": "2026-10-21", "amount": 5, "memo": "", "balance": 100},
        {"date": "2026-10-21", "amount": 5, "memo": "", "balance": 200},
    ]
    assert bank.latest_balance(same)["ordered"] is False  # type: ignore[index]


# ---------------------------------------------------------------- 현금 흐름
def test_cashflow_starts_from_bank_balance_and_subtracts_fixed_costs(kit) -> None:
    orders = [
        kit.order(f"o{k}", kit.at("2026-09-01") + timedelta(days=k), [("sel_a", "book", 1, 10000)])
        for k in range(52)
    ]
    books = kit.books(orders=orders)
    rows = [{"seller_id": "sel_a", "payout": 63000}]
    weeks = {p: kit.statement(p, rows) for p in cashflow.settlement_periods(TODAY)}
    plain = cashflow.forecast(books, weeks, TODAY)
    fixed = cashflow.fixed_costs(
        {"2026-09": {"salary": 1}, "2026-10": {"salary": 520_000, "rent": 0}}
    )
    assert fixed == {"month": "2026-10", "monthly": 520_000, "weekly": 120_000}
    opening = {"as_of": "2026-10-22", "balance": 50_000, "ordered": True}
    r = cashflow.forecast(books, weeks, TODAY, opening=opening, fixed=fixed)
    for a, b in zip(plain["weeks"], r["weeks"], strict=True):
        assert b["fixed"] == 120_000 and b["net"] == a["net"] - 120_000
        assert b["balance"] == 50_000 + b["cumulative"]
    assert r["opening"]["stale"] is False and r["applied_rules"][0]["rule_id"] == "CSH-001"
    assert r["short_weeks"] == [w["week_start"] for w in r["weeks"] if w["balance"] < 0]
    assert plain["opening"] is None and all(w["balance"] is None for w in plain["weeks"])
    old = cashflow.forecast(books, weeks, TODAY, opening=opening | {"as_of": "2026-10-01"})
    assert old["opening"]["stale"] is True and "오래돼" in old["note"]


def test_fixed_costs_without_entries_is_none() -> None:
    assert cashflow.fixed_costs({}) is None
    assert cashflow.fixed_costs({"2026-10": {"non_operating_income": 5}}) is None


# ---------------------------------------------------------------- API와 마감 체크
def test_upload_with_withdrawals_note_and_checklist(kit) -> None:
    client = TestClient(app)
    rows = [
        {"date": "2026-10-01", "amount": 1000, "memo": "토스", "balance": 1000},
        {"date": "2026-10-21", "amount": -300, "memo": "ATM", "balance": 700},
    ]
    body = {"rows": rows, "has_withdrawals": True}
    res = client.post("/api/finance/bank/statement", json=body, headers=HUMAN).json()
    assert res["meta"]["withdrawals"] == 1 and res["meta"]["has_withdrawals"] is True
    v = client.get("/api/finance/bank/outflows").json()["data"]
    assert v["uploaded"] is True and [u["amount"] for u in v["unexplained"]] == [300]
    row_id = v["unexplained"][0]["id"]

    rec = handlers.state().records
    status = {
        i["key"]: i
        for i in close_checklist.checklist(kit.books(), rec, TODAY, TODAY, batches={})["items"]
    }
    assert status["bank_out"]["status"] == "pending"

    url = f"/api/finance/bank/outflows/{row_id}/note"
    assert client.post(url, json={"note": "현금 시재"}).status_code == 401
    assert client.post(url, json={"note": ""}, headers=HUMAN).status_code == 400
    missing = "/api/finance/bank/outflows/nope/note"
    assert client.post(missing, json={"note": "x"}, headers=HUMAN).status_code == 404
    done = client.post(url, json={"note": "현금 시재"}, headers=HUMAN).json()["data"]
    assert done["unexplained"][0]["note"] == "현금 시재" and done["counts"]["unexplained_open"] == 0
    status = {
        i["key"]: i
        for i in close_checklist.checklist(kit.books(), rec, TODAY, TODAY, batches={})["items"]
    }
    assert status["bank_out"]["status"] == "done"

    cash = client.get("/api/finance/cashflow").json()["data"]
    assert cash["opening"]["balance"] == 700 and cash["opening"]["as_of"] == "2026-10-21"


def test_transfers_are_not_judged_without_support_batches() -> None:
    """고객지원에서 정산 묶음을 못 받으면 이체를 "못 찾음"으로 보지 않고 못 받았다고 남긴다."""
    v = bank.outflows([_out("2026-10-15", 5000, "이체")], OCT, None, seller_name=str, today=TODAY)
    assert v["transfers"] == [] and v["unavailable"] == ["seller_settlement_batches"]
    assert _view([])["unavailable"] == []


def test_outflows_api_matches_support_batches(support) -> None:
    support.batches = {"2026-W41": _batch("2026-10-14", {"sel_0001": 30000})}
    client = TestClient(app)
    rows = [{"date": "2026-10-15", "amount": -30000, "memo": "이체", "balance": 0}]
    client.post(
        "/api/finance/bank/statement", json={"rows": rows, "has_withdrawals": True}, headers=HUMAN
    )
    v = client.get("/api/finance/bank/outflows").json()["data"]
    assert [(t["batch"], t["status"]) for t in v["transfers"]] == [("2026-W41", "paid")]
    assert v["unexplained"] == [] and v["unavailable"] == []


def test_upload_without_withdrawal_column_keeps_old_behavior() -> None:
    client = TestClient(app)
    body = {"rows": [{"date": "2026-10-20", "amount": 1000, "memo": "토스"}]}
    client.post("/api/finance/bank/statement", json=body, headers=HUMAN)
    assert client.get("/api/finance/bank/outflows").json()["data"]["uploaded"] is False
