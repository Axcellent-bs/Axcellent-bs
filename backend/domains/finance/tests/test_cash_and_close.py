"""은행 입금 대조, 13주 현금 흐름, 월 마감 체크리스트."""

from __future__ import annotations

from datetime import date, timedelta

import pytest
from fastapi.testclient import TestClient

from finance_agent import bank, cashflow, close_checklist, handlers
from finance_agent.books import pg_settlements
from finance_agent.main import app
from finance_agent.sources import MockSources

TODAY = date(2026, 10, 23)
HUMAN = {"Authorization": "Bearer human:taeyoon"}


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch, fixed_now) -> None:
    monkeypatch.setenv("AGENT_TEMPLATE_MOCK", "1")
    monkeypatch.delenv("FINANCE_HUMAN_TOKENS", raising=False)
    monkeypatch.delenv("SAFETY_HUMAN_TOKENS", raising=False)
    handlers.reset_state(MockSources())


# ---------------------------------------------------------------- 은행 입금 대조
def _bank_books(kit):
    orders = [
        kit.order("ord_1", kit.at("2026-10-12"), [("sel_a", "book", 1, 10000)]),  # 10-14 지급 예정
        kit.order("ord_2", kit.at("2026-10-13"), [("sel_a", "book", 1, 20000)]),  # 10-15
        kit.order("ord_3", kit.at("2026-10-14"), [("sel_a", "book", 1, 30000)]),  # 10-16
    ]
    return kit.books(orders=orders)


def _expected(books, d: str) -> int:
    return sum(
        r["pay_out_amount"]
        for r in pg_settlements(books, TODAY).values()
        if str(r["paid_out_date"])[:10] == d
    )


def test_bank_match_finds_short_missing_and_unexpected(kit) -> None:
    books = _bank_books(kit)
    rows, problems = bank.parse_rows(
        [
            {
                "date": "2026-10-14",
                "amount": _expected(books, "2026-10-14"),
                "memo": "토스페이먼츠",
            },
            {
                "date": "2026-10-15",
                "amount": _expected(books, "2026-10-15") - 100,
                "memo": "토스페이먼츠",
            },
            {"date": "2026-10-17", "amount": 5000, "memo": "이자"},
            {"date": "2026-10-17", "amount": -3000, "memo": "출금"},  # 출금은 대조하지 않는다
        ]
    )
    assert problems == [] and len(rows) == 3
    r = bank.match(books, rows, TODAY)
    days = {d["date"]: d for d in r["days"]}
    assert days["2026-10-14"]["status"] == "matched"
    assert (days["2026-10-15"]["status"], days["2026-10-15"]["diff"]) == ("short", -100)
    assert days["2026-10-16"]["status"] == "missing"
    assert days["2026-10-17"]["status"] == "unexpected"
    assert r["covered"] == {"from": "2026-10-14", "to": "2026-10-17", "rows": 3}
    assert {d["date"] for d in bank.exceptions(r)} == {"2026-10-15", "2026-10-16", "2026-10-17"}


def test_bank_without_upload_judges_nothing(kit) -> None:
    r = bank.match(_bank_books(kit), [], TODAY)
    assert r["uploaded"] is False and r["days"] == []


def test_bank_rows_are_validated_and_deduplicated() -> None:
    rows, problems = bank.parse_rows(
        [
            {"date": "2026-10-14", "amount": 1000, "memo": "a"},
            {"date": "x", "amount": 1},
            {"date": "2026-10-14", "amount": 1.5},
        ]
    )
    assert len(rows) == 1 and len(problems) == 2
    store: list = []
    assert bank.merge_rows(store, rows) == 1
    assert bank.merge_rows(store, rows) == 0  # 같은 내역을 다시 올려도 한 번만
    assert bank.parse_rows([])[1] and bank.parse_rows("x")[1]


def test_bank_upload_needs_a_person() -> None:
    client = TestClient(app)
    body = {"rows": [{"date": "2026-10-20", "amount": 1000, "memo": "토스"}]}
    assert client.post("/api/finance/bank/statement", json=body).status_code == 401
    assert (
        client.post("/api/finance/bank/statement", json={"rows": []}, headers=HUMAN).status_code
        == 400
    )
    res = client.post("/api/finance/bank/statement", json=body, headers=HUMAN).json()
    assert res["meta"]["added"] == 1 and res["data"]["uploaded"] is True
    assert client.get("/api/finance/bank").json()["data"]["covered"]["rows"] == 1


# ---------------------------------------------------------------- 13주 현금 흐름
def test_cashflow_scheduled_weeks_and_estimate_from_full_weeks(kit) -> None:
    orders = [
        kit.order(
            f"ord_{k}", kit.at("2026-09-01") + timedelta(days=k), [("sel_a", "book", 1, 10000)]
        )
        for k in range(0, 52)
    ]
    r = cashflow.forecast(kit.books(orders=orders), {}, TODAY)
    weeks = r["weeks"]
    assert len(weeks) == 13 and weeks[0]["week_start"] == "2026-10-19"
    assert weeks[0]["basis"] == "scheduled" and weeks[0]["inflow"] > 0  # 아직 안 들어온 PG 정산
    est = [w for w in weeks if w["basis"] == "estimated"]
    assert est and r["trailing_weeks"] > 0
    # 같은 주 결제 기준이라 들어옴(PG 지급액)이 나감(판매자 몫)보다 크다
    assert r["average"]["inflow"] > r["average"]["outflow"]
    assert all(w["net"] == w["inflow"] - w["outflow"] for w in weeks)


def test_cashflow_does_not_estimate_without_full_weeks(kit) -> None:
    orders = [kit.order("ord_1", kit.at("2026-10-21"), [("sel_a", "book", 1, 10000)])]
    r = cashflow.forecast(kit.books(orders=orders), {}, TODAY)
    assert r["average"] == {"inflow": None, "outflow": None}
    assert all(w["net"] is None for w in r["weeks"] if w["basis"] == "estimated")


def test_cashflow_is_unknown_without_orders(kit) -> None:
    books = kit.books(orders=[])
    books.unavailable.append("orders")
    assert all(w["inflow"] is None for w in cashflow.forecast(books, {}, TODAY)["weeks"])


# ---------------------------------------------------------------- 월 마감 체크리스트
def test_checklist_items_follow_records(kit) -> None:
    books = _bank_books(kit)
    rec = handlers.FinanceRecords()
    r = close_checklist.checklist(books, rec, TODAY, TODAY)
    status = {i["key"]: i["status"] for i in r["items"]}
    assert r["month"] == "2026-10" and r["total"] == 7
    assert status["daily_closing"] == "pending" and status["bank"] == "pending"
    assert status["month_closing"] == "pending" and r["ready"] is False

    for k in range(22):
        rec.closings[("day", (date(2026, 10, 1) + timedelta(days=k)).isoformat())] = {"by": "t"}
    rec.closings[("month", "2026-10-01")] = {"by": "t"}
    rec.anomalies["x"] = {"status": "open"}
    r = close_checklist.checklist(books, rec, TODAY, TODAY)
    status = {i["key"]: i["status"] for i in r["items"]}
    assert status["daily_closing"] == "done" and status["month_closing"] == "done"
    assert status["audit"] == "pending"  # 처리 전 의심 건이 남아 있다
    assert r["closed"] is True and r["ready"] is False


def test_checklist_api() -> None:
    body = TestClient(app).get("/api/finance/close-checklist").json()["data"]
    assert body["month"] == "2026-10" and [i["key"] for i in body["items"]][-1] == "month_closing"
    assert TestClient(app).get("/api/finance/cashflow").json()["data"]["weeks"]
