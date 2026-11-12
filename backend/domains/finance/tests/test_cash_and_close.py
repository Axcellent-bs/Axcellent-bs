"""은행 입금 대조, 13주 현금 흐름, 월 마감 체크리스트.

판매자 정산 내역서·묶음은 고객지원이 준다. 여기서는 고객지원 응답을 흉내 내 넣는다(`kit.statement`).
"""

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
            {"date": "2026-10-17", "amount": -3000, "memo": "출금"},  # 출금은 입금 대조에 안 쓴다
        ]
    )
    assert problems == [] and len(rows) == 4
    r = bank.match(books, rows, TODAY)
    days = {d["date"]: d for d in r["days"]}
    assert days["2026-10-14"]["status"] == "matched"
    assert (days["2026-10-15"]["status"], days["2026-10-15"]["diff"]) == ("short", -100)
    assert days["2026-10-16"]["status"] == "missing"
    assert days["2026-10-17"]["status"] == "unexpected"
    assert days["2026-10-17"]["deposited"] == 5000  # 같은 날 출금 3,000원은 빼지 않는다
    assert r["covered"] == {"from": "2026-10-14", "to": "2026-10-17", "rows": 4}
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
WEEK_PAYOUT = 7 * 9000  # 하루 한 건 도서 10,000원 − 수수료 10%


def _weeks(kit, payout: int = WEEK_PAYOUT, **by_period) -> dict:
    """고객지원 `seller_settlement_weeks`가 줄 내역서 (현금 흐름이 읽는 주 전부)."""
    rows = [{"seller_id": "sel_a", "payout": payout}]
    return {
        p: by_period.get(p) or kit.statement(p, rows) for p in cashflow.settlement_periods(TODAY)
    }


def test_cashflow_reads_this_and_last_weeks_and_trailing_weeks() -> None:
    assert cashflow.settlement_periods(TODAY) == [
        "2026-W35",
        "2026-W36",
        "2026-W37",
        "2026-W38",
        "2026-W39",
        "2026-W40",
        "2026-W41",
        "2026-W42",
        "2026-W43",
    ]


def test_cashflow_scheduled_weeks_and_estimate_from_full_weeks(kit) -> None:
    orders = [
        kit.order(
            f"ord_{k}", kit.at("2026-09-01") + timedelta(days=k), [("sel_a", "book", 1, 10000)]
        )
        for k in range(0, 52)
    ]
    r = cashflow.forecast(kit.books(orders=orders), _weeks(kit), TODAY)
    assert r["unavailable"] == []
    weeks = r["weeks"]
    assert len(weeks) == 13 and weeks[0]["week_start"] == "2026-10-19"
    assert weeks[0]["basis"] == "scheduled" and weeks[0]["inflow"] > 0  # 아직 안 들어온 PG 정산
    est = [w for w in weeks if w["basis"] == "estimated"]
    assert est and r["trailing_weeks"] > 0
    # 같은 주 결제 기준이라 들어옴(PG 지급액)이 나감(판매자 몫)보다 크다
    assert r["average"]["inflow"] > r["average"]["outflow"]
    assert all(w["net"] == w["inflow"] - w["outflow"] for w in weeks)
    # 지난주(W42) 정산 지급 예정일 10-24 → 이번 주 나감
    assert weeks[0]["outflow"] == WEEK_PAYOUT


def test_cashflow_skips_approved_and_held_sellers(kit) -> None:
    orders = [kit.order("ord_1", kit.at("2026-10-14"), [("sel_a", "book", 1, 10000)])]
    rows = [
        {"seller_id": "sel_a", "payout": 9000},
        {"seller_id": "sel_b", "payout": 5000},
        {"seller_id": "sel_c", "payout": 4000, "hold_reasons": [{"code": "x", "label": "x"}]},
    ]
    approved = {"status": "approved", "seller_ids": ["sel_b"]}
    weeks = _weeks(kit, 0, **{"2026-W42": kit.statement("2026-W42", rows, batch=approved)})
    r = cashflow.forecast(kit.books(orders=orders), weeks, TODAY)
    assert r["weeks"][0]["outflow"] == 9000  # 승인된 sel_b는 이미 나감, 보류된 sel_c는 빠짐


def test_cashflow_outflow_is_unknown_when_support_is_down(kit) -> None:
    """고객지원에서 정산을 못 받으면 나감·순현금을 0으로 채우지 않는다. 들어옴은 그대로."""
    orders = [kit.order("ord_1", kit.at("2026-10-22"), [("sel_a", "book", 1, 10000)])]
    books = kit.books(orders=orders)
    for missing in (None, {"2026-W42": kit.statement("2026-W42")}):
        r = cashflow.forecast(books, missing, TODAY)
        assert r["unavailable"] == ["seller_settlement"]
        assert all(w["outflow"] is None and w["net"] is None for w in r["weeks"])
        assert r["weeks"][0]["inflow"] > 0
        assert "고객지원" in r["note"]


def test_cashflow_does_not_estimate_without_full_weeks(kit) -> None:
    orders = [kit.order("ord_1", kit.at("2026-10-21"), [("sel_a", "book", 1, 10000)])]
    r = cashflow.forecast(kit.books(orders=orders), _weeks(kit), TODAY)
    assert r["average"] == {"inflow": None, "outflow": None}
    assert all(w["net"] is None for w in r["weeks"] if w["basis"] == "estimated")


def test_cashflow_is_unknown_without_orders(kit) -> None:
    books = kit.books(orders=[])
    books.unavailable.append("orders")
    weeks = cashflow.forecast(books, _weeks(kit), TODAY)["weeks"]
    assert all(w["inflow"] is None for w in weeks)


# ---------------------------------------------------------------- 월 마감 체크리스트
def _month_weeks(kit, **by_period) -> dict:
    return {
        p: by_period.get(p) or kit.statement(p) for p in close_checklist.settlement_periods(TODAY)
    }


def test_checklist_items_follow_records(kit) -> None:
    books = _bank_books(kit)
    rec = handlers.FinanceRecords()
    feed = {"settlements": _month_weeks(kit), "batches": {}}
    r = close_checklist.checklist(books, rec, TODAY, TODAY, **feed)
    status = {i["key"]: i["status"] for i in r["items"]}
    assert r["month"] == "2026-10" and r["total"] == 7
    assert status["daily_closing"] == "pending" and status["bank"] == "pending"
    assert status["bank_out"] == "pending"  # 출금 열이 있는 내역을 아직 안 올림
    assert status["month_closing"] == "pending" and r["ready"] is False

    for k in range(22):
        rec.closings[("day", (date(2026, 10, 1) + timedelta(days=k)).isoformat())] = {"by": "t"}
    rec.closings[("month", "2026-10-01")] = {"by": "t"}
    r = close_checklist.checklist(books, rec, TODAY, TODAY, **feed)
    status = {i["key"]: i["status"] for i in r["items"]}
    assert status["daily_closing"] == "done" and status["month_closing"] == "done"
    assert r["closed"] is True and r["ready"] is False


def test_checklist_settlement_item_reads_support_batches(kit) -> None:
    """지급일(고객지원 내역서의 `pay_date`)이 그 달·오늘 전인 주의 묶음이 승인됐는지만 본다."""
    assert close_checklist.settlement_periods(TODAY) == [
        "2026-W39",
        "2026-W40",
        "2026-W41",
        "2026-W42",
        "2026-W43",
        "2026-W44",
    ]
    rows = [{"seller_id": "sel_a", "payout": 9000}]
    w41 = kit.statement("2026-W41", rows)  # 지급일 10-17
    w42 = kit.statement("2026-W42", rows)  # 지급일 10-24 (오늘 뒤라 아직 아님)
    books, rec = _bank_books(kit), handlers.FinanceRecords()

    def item(weeks, batches=None):
        r = close_checklist.checklist(books, rec, TODAY, TODAY, settlements=weeks, batches=batches)
        return next(i for i in r["items"] if i["key"] == "settlement")

    pending = item(_month_weeks(kit, **{"2026-W41": w41, "2026-W42": w42}), {})
    assert (pending["status"], pending["detail"]) == ("pending", "승인 안 된 주: 2026-W41")
    w41_ok = w41 | {"batch": w41["batch"] | {"status": "approved", "seller_ids": ["sel_a"]}}
    done = item(_month_weeks(kit, **{"2026-W41": w41_ok, "2026-W42": w42}), {})
    assert done["status"] == "done"
    unknown = item(None)
    assert unknown["status"] == "unknown" and "고객지원" in unknown["detail"]


def test_checklist_without_support_marks_settlement_items_unknown(kit) -> None:
    """고객지원이 꺼져 있으면 정산·미지급금 항목은 "확인할 수 없음"이다(끝났다고 하지 않는다)."""
    rec = handlers.FinanceRecords()
    rec.bank_out_periods = [{"from": "2026-10-01", "to": "2026-10-22"}]
    r = close_checklist.checklist(_bank_books(kit), rec, TODAY, TODAY)
    status = {i["key"]: i["status"] for i in r["items"]}
    assert status["settlement"] == status["payables"] == status["bank_out"] == "unknown"
    assert r["ready"] is False


def test_checklist_api() -> None:
    body = TestClient(app).get("/api/finance/close-checklist").json()["data"]
    assert body["month"] == "2026-10" and [i["key"] for i in body["items"]][-1] == "month_closing"
    assert TestClient(app).get("/api/finance/cashflow").json()["data"]["weeks"]


def test_cashflow_and_checklist_apis_prefetch_from_support(support) -> None:
    client = TestClient(app)
    cash = client.get("/api/finance/cashflow").json()["data"]
    assert cash["unavailable"] == []
    asked = [p for skill, p in support.calls if skill == "seller_settlement_weeks"]
    assert asked[0]["periods"] == cashflow.settlement_periods(TODAY)
    checklist = client.get("/api/finance/close-checklist").json()["data"]
    status = {i["key"]: i["status"] for i in checklist["items"]}
    assert status["settlement"] == "done" and status["payables"] == "done"
    skills = {skill for skill, _ in support.calls}
    assert skills == {"seller_settlement_weeks", "seller_settlement_batches"}


# ---------------------------------------------------------------- 마감 기한 (CLS-005)
def _items(r) -> dict:
    return {i["key"]: i for i in r["items"]}


def test_checklist_items_have_due_and_overdue(kit) -> None:
    """기한은 finance_policy.toml [closing]. 안 끝났는데 오늘(10-23)이 기한 뒤면 overdue."""
    books, rec = _bank_books(kit), handlers.FinanceRecords()
    rows = [{"seller_id": "sel_a", "payout": 9000}]
    w41 = kit.statement("2026-W41", rows)  # 지급일 10-17, 승인 안 됨
    feed = {"settlements": _month_weeks(kit, **{"2026-W41": w41}), "batches": {}}
    items = _items(close_checklist.checklist(books, rec, TODAY, TODAY, **feed))
    due = {k: (i["due"], i["overdue"]) for k, i in items.items()}
    assert due == {
        "daily_closing": ("2026-10-02", True),  # 가장 이른 남은 날(10-01)의 다음 날
        "reconciliation": ("2026-11-03", False),
        "bank": ("2026-11-03", False),
        "settlement": ("2026-10-17", True),  # 승인 안 된 주(W41)의 지급 예정일
        "bank_out": ("2026-11-03", False),
        "payables": ("2026-11-05", False),
        "month_closing": ("2026-11-05", False),
    }
    # 다 끝난 항목은 기한이 지나도 overdue가 아니다. 정산은 그 달 마지막 지급 예정일을 보여 준다
    for k in range(22):
        rec.closings[("day", (date(2026, 10, 1) + timedelta(days=k)).isoformat())] = {"by": "t"}
    w41_ok = w41 | {"batch": w41["batch"] | {"status": "approved", "seller_ids": ["sel_a"]}}
    feed["settlements"] = _month_weeks(kit, **{"2026-W41": w41_ok})
    items = _items(close_checklist.checklist(books, rec, TODAY, TODAY, **feed))
    assert (items["daily_closing"]["due"], items["daily_closing"]["overdue"]) == (
        "2026-11-01",
        False,
    )
    assert items["settlement"]["status"] == "done"
    assert (items["settlement"]["due"], items["settlement"]["overdue"]) == ("2026-10-31", False)
    # 한 달 뒤에 보면 남은 일은 모두 기한 지남
    late = _items(close_checklist.checklist(books, rec, TODAY, date(2026, 11, 6), **feed))
    assert late["month_closing"]["overdue"] is True and late["bank"]["overdue"] is True


def test_checklist_due_without_support_uses_policy_pay_date(kit) -> None:
    """고객지원 내역서가 없으면 지급 예정일은 정산 기간 끝(일요일) + 6일로 잡는다."""
    items = _items(
        close_checklist.checklist(_bank_books(kit), handlers.FinanceRecords(), TODAY, TODAY)
    )
    assert items["settlement"]["status"] == "unknown"
    assert (items["settlement"]["due"], items["settlement"]["overdue"]) == ("2026-10-31", False)


def test_checklist_daily_text_and_bank_hint(kit) -> None:
    books, rec = _bank_books(kit), handlers.FinanceRecords()
    items = _items(close_checklist.checklist(books, rec, TODAY, TODAY))
    assert items["daily_closing"]["detail"] == (
        "22일 중 0일 확정 · 남은 날 22일: 10-01, 10-02, 10-03, 10-04, 10-05 외 17일"
    )
    hint = "(② 매출·PG 대사 > 은행 거래내역 파일 올리기)"
    assert items["bank"]["detail"] == f"이 달 은행 거래내역 파일을 올리면 대조합니다{hint}"
    assert items["bank_out"]["detail"] == (
        f"이 달 출금 열이 있는 은행 거래내역 파일을 올리면 대조합니다{hint}"
    )
    assert items["bank"]["status"] == items["bank_out"]["status"] == "pending"  # 끝났다고 하지 않음
    assert items["bank"]["link"] == items["bank_out"]["link"] == "reconciliation"

    for k in range(17):
        rec.closings[("day", (date(2026, 10, 1) + timedelta(days=k)).isoformat())] = {"by": "t"}
    items = _items(close_checklist.checklist(books, rec, TODAY, TODAY))
    assert items["daily_closing"]["detail"] == (
        "22일 중 17일 확정 · 남은 날 5일: 10-18, 10-19, 10-20, 10-21, 10-22"
    )
    assert items["daily_closing"]["due"] == "2026-10-19"
    first = _items(close_checklist.checklist(books, rec, date(2026, 10, 1), date(2026, 10, 1)))
    assert first["daily_closing"]["detail"] == "확정할 지난 날 없음"


def test_checklist_api_returns_due(kit) -> None:
    body = TestClient(app).get("/api/finance/close-checklist").json()["data"]
    assert all(len(i["due"]) == 10 and isinstance(i["overdue"], bool) for i in body["items"])
