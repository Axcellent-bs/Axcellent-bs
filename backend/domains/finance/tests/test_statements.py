"""재무제표(손익계산서) 테스트: 일반 손익계산서 순서, 당기·전기, 자료 없음, 내려받기."""

from __future__ import annotations

from datetime import date

import pytest
from fastapi.testclient import TestClient

from finance_agent import closing, handlers, statements
from finance_agent import finance_config as fc
from finance_agent.main import app
from finance_agent.sources import MockSources


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch, fixed_now) -> None:
    monkeypatch.setenv("AGENT_TEMPLATE_MOCK", "1")
    monkeypatch.delenv("TENANT_NAME", raising=False)
    handlers.reset_state(MockSources())


def _books(kit):
    orders = [
        kit.order("ord_1", kit.at("2026-10-20"), [("sel_a", "book", 2, 10000)]),  # 수수료 2,000
        kit.order("ord_2", kit.at("2026-09-10"), [("sel_b", "book", 1, 50000)]),  # 전기 5,000
    ]
    coupons = [
        {
            "coupon_id": "c1",
            "total_value": 500,
            "funded_by": "company",
            "created_at": kit.at("2026-10-20"),
        },
        {
            "coupon_id": "c2",
            "total_value": 900,
            "funded_by": "seller",
            "seller_id": "sel_a",
            "created_at": kit.at("2026-10-20"),
        },
    ]
    return kit.books(orders=orders, coupons=coupons)


def _rows(report):
    return {r["code"]: r for r in report["rows"]}


def test_rows_follow_the_usual_income_statement_order(kit) -> None:
    report = statements.income_statement(_books(kit), "month", date(2026, 10, 23))
    labels = [r["label"] for r in report["rows"] if r["level"] == 0]
    assert [label.split(".")[0] for label in labels] == [
        "Ⅰ",
        "Ⅱ",
        "Ⅲ",
        "Ⅳ",
        "Ⅴ",
        "Ⅵ",
        "Ⅶ",
        "Ⅷ",
        "Ⅸ",
        "Ⅹ",
    ]
    assert (report["current"]["name"], report["previous"]["name"]) == ("2026년 10월", "2026년 9월")


def test_numbers_match_the_closing_totals(kit) -> None:
    books = _books(kit)
    report = statements.income_statement(books, "month", date(2026, 10, 23))
    rows = _rows(report)
    t = closing.compute_totals(
        books, fc.day_start(date(2026, 10, 1)), fc.day_start(date(2026, 11, 1))
    )
    assert rows["commission"]["current"] == t.commission_revenue == 2000
    assert rows["sales_discount"]["current"] == -500  # 회사 부담 쿠폰만, 매출에서 뺀다
    assert rows["revenue"]["current"] == 1500
    assert rows["cogs"]["current"] == 0
    assert rows["pg_fee"]["current"] == -t.pg_fee
    assert rows["operating_income"]["current"] == t.net_result  # 결산 화면 영업 결과와 같다
    assert rows["commission"]["previous"] == 5000
    notes = {n["code"]: n for n in report["notes"]}
    assert notes["gmv"]["current"] == 20000  # 총거래액은 매출이 아니라 주석


def test_items_not_in_the_books_are_left_empty(kit) -> None:
    rows = _rows(statements.income_statement(_books(kit), "year", date(2026, 10, 23)))
    for code in ("other_sga", "non_operating_income", "income_tax", "net_income"):
        assert rows[code]["current"] is None  # 지어내지 않는다


def test_missing_orders_make_order_rows_unknown(kit) -> None:
    books = _books(kit)
    books.unavailable.append("orders_partial")
    rows = _rows(statements.income_statement(books, "month", date(2026, 10, 23)))
    assert rows["commission"]["current"] is None
    assert rows["operating_income"]["current"] is None
    assert rows["sales_discount"]["current"] == -500  # 쿠폰 자료는 받았다


@pytest.mark.parametrize(
    ("period", "d", "start", "end", "prev"),
    [
        ("month", date(2026, 1, 15), date(2026, 1, 1), date(2026, 2, 1), date(2025, 12, 1)),
        ("quarter", date(2026, 11, 3), date(2026, 10, 1), date(2027, 1, 1), date(2026, 7, 1)),
        ("quarter", date(2026, 2, 3), date(2026, 1, 1), date(2026, 4, 1), date(2025, 10, 1)),
        ("year", date(2026, 6, 1), date(2026, 1, 1), date(2027, 1, 1), date(2025, 1, 1)),
    ],
)
def test_period_ranges(period, d, start, end, prev) -> None:
    assert statements.period_range(period, d) == (start, end)
    assert statements.previous_start(period, start) == prev


def test_api_returns_json_and_a_csv_download() -> None:
    client = TestClient(app)
    body = client.get("/api/finance/statements", params={"period": "quarter"}).json()
    assert (
        body["data"]["title"] == "손익계산서" and body["data"]["current"]["name"] == "2026년 4분기"
    )

    res = client.get("/api/finance/statements", params={"period": "month", "format": "csv"})
    assert res.status_code == 200
    assert res.headers["content-type"].startswith("text/csv")
    assert 'filename="income-statement-month-2026-10-01.csv"' in res.headers["content-disposition"]
    text = res.content.decode("utf-8")
    assert text.startswith("﻿손익계산서")  # 엑셀에서 한글이 안 깨지게 BOM
    assert "Ⅴ. 영업이익(장부 기준)" in text and "자료 없음" in text


def test_api_rejects_unknown_period_and_format() -> None:
    client = TestClient(app)
    assert client.get("/api/finance/statements", params={"period": "day"}).status_code == 400
    assert client.get("/api/finance/statements", params={"format": "pdf"}).status_code == 400
