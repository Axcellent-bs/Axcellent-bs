"""세무 지원 자료: 부가세 신고 보조 집계, 판매자별 판매 명세(제75조 초안)."""

from __future__ import annotations

from datetime import date

import pytest
from fastapi.testclient import TestClient

from finance_agent import finance_config as fc
from finance_agent import handlers, tax
from finance_agent.main import app
from finance_agent.sources import MockSources

TODAY = date(2026, 10, 23)


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch, fixed_now) -> None:
    monkeypatch.setenv("AGENT_TEMPLATE_MOCK", "1")
    handlers.reset_state(MockSources())


def _books(kit):
    orders = [
        kit.order(
            "ord_1", kit.at("2026-10-05"), [("sel_a", "book", 1, 100000)], shipping=3000
        ),  # 수수료 10,000
        kit.order("ord_2", kit.at("2026-10-06"), [("sel_b", "stationery", 1, 50000)]),  # 4,000
        kit.order("ord_9", kit.at("2026-09-30"), [("sel_a", "book", 1, 70000)]),  # 3분기
    ]
    coupons = [
        {
            "coupon_id": "c1",
            "total_value": 3000,
            "funded_by": "company",
            "created_at": kit.at("2026-10-07"),
        }
    ]
    return kit.books(orders=orders, coupons=coupons)


def _with_tax(monkeypatch: pytest.MonkeyPatch, **kw) -> None:
    cfg = fc.load_config()
    new = cfg.model_copy(update={"tax": cfg.tax.model_copy(update=kw)})
    monkeypatch.setattr(fc, "load_config", lambda: new)


def test_quarter_and_filing_deadline() -> None:
    assert tax.quarter_range(date(2026, 10, 23))[:2] == (date(2026, 10, 1), date(2027, 1, 1))
    assert tax.filing_deadline(date(2027, 1, 1)) == ("확정신고", date(2027, 1, 25))
    assert tax.filing_deadline(date(2026, 4, 1)) == ("예정신고", date(2026, 4, 25))


def test_commission_is_taxable_and_book_sales_are_not_platform_revenue(kit) -> None:
    p = tax.vat_pack(_books(kit), TODAY)
    sales = {r["code"]: r for r in p["sales"]}
    # 수수료(부가세 별도) 14,000 → 세액 1,400. 판매자 상품 매출(도서·문구)은 캣북스 과세 매출에 없다
    assert (sales["commission"]["supply"], sales["commission"]["vat"]) == (14000, 1400)
    assert set(sales) == {"commission"}  # 배송비 공급자 기본값은 판매자
    assert p["seller_sales"] == {"exempt": 100000, "taxable": 50000}  # 참고: 도서 면세·문구 과세
    assert p["coupon"]["commission_after_coupon"] == 11000 and p["coupon"]["reduces_base"] is False
    assert p["payable"] == p["output_vat"] - p["input_vat"]
    assert p["filing"] == {"kind": "확정신고", "due": "2027-01-25"}
    assert any("약관" in c for c in p["checks"])


def test_coupon_deduction_and_vat_included_settings(kit, monkeypatch: pytest.MonkeyPatch) -> None:
    _with_tax(
        monkeypatch,
        coupon_reduces_commission_base=True,
        commission_vat="included",
        shipping_supplier="platform",
    )
    p = tax.vat_pack(_books(kit), TODAY)
    sales = {r["code"]: r for r in p["sales"]}
    # 쿠폰 3,000을 수수료에서 공제 → 11,000(부가세 포함) = 공급가액 10,000 + 세액 1,000
    assert (sales["commission"]["supply"], sales["commission"]["vat"]) == (10000, 1000)
    assert (sales["shipping"]["supply"], sales["shipping"]["vat"]) == (2727, 273)  # 배송비 3,000


def test_coupon_over_commission_is_not_deducted_below_zero(kit) -> None:
    books = _books(kit)
    books.coupons.append(
        {
            "coupon_id": "c2",
            "total_value": 50000,
            "funded_by": "company",
            "created_at": kit.at("2026-10-08"),
        }
    )
    c = tax.vat_pack(books, TODAY)["coupon"]
    assert c["commission_after_coupon"] == 0 and c["coupon_over_commission"] == 53000 - 14000


def test_vat_pack_is_unknown_without_orders(kit) -> None:
    books = _books(kit)
    books.unavailable.append("orders")
    p = tax.vat_pack(books, TODAY)
    assert p["sales"] == [] and p["output_vat"] is None and p["payable"] is None
    assert p["seller_sales"] is None


def test_seller_report_by_month_method_and_tax_type(kit) -> None:
    r = tax.seller_report(_books(kit), TODAY)  # 테스트 주문은 결제수단이 없어 "기타"
    rows = {(x["seller_id"], x["month"]): x for x in r["rows"]}
    assert set(rows) == {("sel_a", "2026-10"), ("sel_b", "2026-10")}  # 3분기 주문은 빠진다
    a = rows[("sel_a", "2026-10")]
    assert (a["total"], a["by_method"], a["by_tax"], a["business_no"]) == (
        100000,
        {"기타": 100000},
        {"면세": 100000},
        None,
    )
    assert r["due"] == "2027-01-15" and r["missing"] == ["business_no"]
    text = tax.seller_report_csv(r)
    assert text.startswith("﻿판매자번호") and "자료 없음" in text


def test_tax_apis() -> None:
    client = TestClient(app)
    assert client.get("/api/finance/tax/vat").json()["data"]["quarter"] == "2026년 4분기"
    res = client.get("/api/finance/tax/seller-report", params={"format": "csv"})
    assert (
        res.status_code == 200
        and 'filename="seller-sales-2026-10.csv"' in res.headers["content-disposition"]
    )
    assert client.get("/api/finance/tax/seller-report", params={"format": "pdf"}).status_code == 400
