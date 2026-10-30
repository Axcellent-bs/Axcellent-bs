"""세무 지원 자료: 부가세 신고 보조 집계와 판매자별 판매·중개 명세(부가가치세법 제75조).

재무 AI는 신고하거나 제출하지 않는다.
사람(세무 담당·세무사)이 신고·제출할 때 쓸 숫자와 파일을 만든다.
근거는 2026-10-30 검토(세무 관점) 기준이다. 확인이 안 된 부분은 설정값과 `checks`(확인 필요 목록)로
드러낸다.

캣북스(통신판매중개)의 부가세 구조
- 판매자의 상품 매출은 캣북스 매출이 아니다. 도서는 면세(부가세법 제26조①8), 문구 등은 과세이고
  판매자가 신고한다
- 캣북스의 과세 매출은 판매 수수료(중개 용역)다. 면세 도서를 중개해도 수수료는 과세다.
  광고·직접 배송 용역이 있으면 더한다
- 회사 부담 쿠폰: 약관상 쿠폰 할인액을 판매자에게 받는 수수료에서 직접 공제하는 구조면 수수료의
  에누리로 과세표준에서 뺀다(대법원 2016.6.23. 2014두298, G마켓). 따로 현금으로 보전하면
  판매장려금으로 빼지 못할 수 있다
  → `tax.coupon_reduces_commission_base`로 고르고 두 값을 모두 보여 준다
- 매입세액: PG 수수료(세금계산서 수취분)
- 신고 기한: 예정 4/25·10/25, 확정 7/25·1/25 (법인 기준, 제48·49조)

판매자별 판매·중개 명세(제75조): 분기 말일의 다음 달 15일까지.
판매자별 사업자등록번호·상호·판매금액(결제수단별)
- 판매 사이트에 사업자등록번호가 없어 그 칸은 비워 두고 "자료 없음"으로 표시한다(지어내지 않음)
- 제출 서식(필드)은 국세청 고시 확인 전이라 우리가 가진 항목으로 만든 초안이다
"""

from __future__ import annotations

import csv
import io
from collections import defaultdict
from datetime import date, timedelta
from typing import Any

from finance_agent import closing
from finance_agent import finance_config as fc
from finance_agent.books import Books, in_range, item_gross, order_sold

METHOD_KO = {
    "card": "카드",
    "cash": "현금영수증",
    "cash_receipt": "현금영수증",
    "transfer": "현금영수증",
}
TAX_KO = {"exempt": "면세", "taxable": "과세"}


def quarter_range(d: date) -> tuple[date, date, str]:
    q = (d.month - 1) // 3
    start = date(d.year, q * 3 + 1, 1)
    end = date(d.year + 1, 1, 1) if q == 3 else date(d.year, q * 3 + 4, 1)
    return start, end, f"{d.year}년 {q + 1}분기"


def filing_deadline(quarter_end: date) -> tuple[str, date]:
    """분기 다음 달 25일. 1·3분기는 예정, 2·4분기는 확정 신고."""
    kind = "예정신고" if quarter_end.month in (4, 10) else "확정신고"
    return kind, date(quarter_end.year, quarter_end.month, 25)


def _vat(amount: int, mode: str) -> tuple[int, int]:
    """(공급가액, 세액). mode: excluded(금액이 공급가액) / included(금액에 세액 포함)."""
    if mode == "included":
        supply = round(amount / 1.1)
        return supply, amount - supply
    return amount, round(amount * 0.1)


def vat_pack(books: Books, d: date | None = None) -> dict[str, Any]:
    cfg = fc.load_config()
    t = cfg.tax
    d = d or fc.today()
    start_d, end_d, name = quarter_range(d)
    start, end = fc.day_start(start_d), fc.day_start(end_d)
    unknown = closing.unknown_codes(books)
    totals = closing.compute_totals(books, start, end)

    commission = None if "commission_revenue" in unknown else totals.commission_revenue
    coupon = None if "coupon_cost" in unknown else totals.coupon_cost
    if commission is not None and coupon is not None:
        after_coupon = max(commission - coupon, 0)
        over = max(coupon - commission, 0)  # 수수료를 넘는 쿠폰은 에누리로 뺄 수 없다(수수료 한도)
    else:
        after_coupon = over = None
    base_amount = after_coupon if t.coupon_reduces_commission_base else commission

    shipping = 0
    if t.shipping_supplier == "platform":
        for o in books.orders:
            if order_sold(o) and in_range(_paid_at(o), start, end):
                shipping += int(o.get("shipping_fee") or 0)
    sales_rows = []
    if base_amount is not None:
        supply, tax_amt = _vat(base_amount, t.commission_vat)
        sales_rows.append(
            {
                "code": "commission",
                "label": "판매 수수료(중개 용역)",
                "supply": supply,
                "vat": tax_amt,
            }
        )
        if t.shipping_supplier == "platform":
            s_supply, s_tax = _vat(shipping, "included")
            sales_rows.append(
                {
                    "code": "shipping",
                    "label": "배송 용역(캣북스 공급)",
                    "supply": s_supply,
                    "vat": s_tax,
                }
            )

    pg_fee = None if "pg_fee" in unknown else totals.pg_fee
    purchase_rows = []
    if pg_fee is not None:
        p_supply, p_tax = _vat(pg_fee, t.pg_fee_vat)
        purchase_rows.append(
            {"code": "pg_fee", "label": "PG 결제 수수료", "supply": p_supply, "vat": p_tax}
        )

    def total(rows: list[dict[str, Any]], key: str) -> int | None:
        return sum(r[key] for r in rows) if rows else None

    out_vat, in_vat = total(sales_rows, "vat"), total(purchase_rows, "vat")
    payable = None if out_vat is None or in_vat is None else out_vat - in_vat

    seller_sales = seller_sales_by_tax(books, start_d, end_d) if "gmv" not in unknown else None
    kind, due = filing_deadline(end_d)
    vat_word = {"excluded": "별도", "included": "포함"}
    supplier = "캣북스" if t.shipping_supplier == "platform" else "판매자"
    checks = [
        f"판매 수수료가 부가세 {vat_word[t.commission_vat]} 금액인지 (약관 확인)",
        "회사 부담 쿠폰을 약관상 수수료에서 공제하는 구조인지 (2014두298형이면 에누리)",
        f"배송비 공급자: 지금 설정은 '{supplier}'",
        f"PG 수수료가 부가세 {vat_word[t.pg_fee_vat]}로 세금계산서를 받는지",
        "광고·기타 과세 매출, PG 외 매입세액(클라우드·광고 집행 등)은 이 장부에 없다",
    ]
    return {
        "quarter": name,
        "from": start_d.isoformat(),
        "to": (end_d - timedelta(days=1)).isoformat(),
        "filing": {"kind": kind, "due": due.isoformat()},
        "sales": sales_rows,
        "purchases": purchase_rows,
        "output_vat": out_vat,
        "input_vat": in_vat,
        "payable": payable,
        "coupon": {
            "company_coupon": coupon,
            "commission_before_coupon": commission,
            "commission_after_coupon": after_coupon,
            "coupon_over_commission": over,
            "reduces_base": t.coupon_reduces_commission_base,
        },
        "seller_sales": seller_sales,
        "settings": {
            "commission_vat": t.commission_vat,
            "pg_fee_vat": t.pg_fee_vat,
            "shipping_supplier": t.shipping_supplier,
        },
        "checks": checks,
        "basis": "신고서가 아니라 신고 보조 집계입니다. 신고는 세무 담당이 홈택스에서 합니다",
    }


def _paid_at(order: dict[str, Any]):
    times = [p["created_at"] for p in order["payments"] if p.get("status") == "succeeded"]
    return min(times) if times else None


def _tax_type(category: str | None) -> str:
    cfg = fc.load_config().tax
    return cfg.category_tax.get(category or "", cfg.default_tax)


def seller_sales_by_tax(books: Books, start_d: date, end_d: date) -> dict[str, int]:
    """판매자 상품 매출(캣북스 매출 아님)을 면세·과세로 나눈 합. 참고용."""
    start, end = fc.day_start(start_d), fc.day_start(end_d)
    out: dict[str, int] = {"exempt": 0, "taxable": 0}
    for o in books.orders:
        if order_sold(o) and in_range(_paid_at(o), start, end):
            for item in o["items"]:
                out[_tax_type(item.get("category_code"))] += item_gross(item)
    return out


def _method(order: dict[str, Any]) -> str:
    codes = [p.get("method_code") for p in order["payments"] if p.get("status") == "succeeded"]
    return METHOD_KO.get(str(codes[0]) if codes else "", "기타")


def seller_report(books: Books, d: date | None = None) -> dict[str, Any]:
    """판매자별·월별 판매·중개 명세 초안 (제75조)."""
    d = d or fc.today()
    start_d, end_d, name = quarter_range(d)
    start, end = fc.day_start(start_d), fc.day_start(end_d)
    rows: dict[tuple[str, str], dict[str, Any]] = {}
    for o in books.orders:
        paid = _paid_at(o)
        if not order_sold(o) or not in_range(paid, start, end):
            continue
        month = paid.date().isoformat()[:7]
        method = _method(o)
        for item in o["items"]:
            seller = item.get("seller_id")
            if not seller:
                continue
            row = rows.setdefault(
                (seller, month),
                {
                    "seller_id": seller,
                    "name": books.seller_name(seller),
                    "business_no": None,  # 판매 사이트에 사업자등록번호가 없다
                    "month": month,
                    "total": 0,
                    "by_method": defaultdict(int),
                    "by_tax": defaultdict(int),
                    "orders": set(),
                },
            )
            gross = item_gross(item)
            row["total"] += gross
            row["by_method"][method] += gross
            row["by_tax"][TAX_KO[_tax_type(item.get("category_code"))]] += gross
            row["orders"].add(o["order_id"])
    out = [
        r
        | {
            "by_method": dict(r["by_method"]),
            "by_tax": dict(r["by_tax"]),
            "orders": len(r["orders"]),
        }
        for r in sorted(rows.values(), key=lambda x: (x["seller_id"], x["month"]))
    ]
    q_end = end_d
    due = date(q_end.year, q_end.month, 15)
    return {
        "quarter": name,
        "from": start_d.isoformat(),
        "to": (end_d - timedelta(days=1)).isoformat(),
        "due": due.isoformat(),
        "rows": out,
        "missing": ["business_no"] if out else [],
        "basis": "부가가치세법 제75조 판매·중개 명세 초안. 제출 서식은 국세청 고시 확인 필요",
        "unavailable": "orders" in books.unavailable,
    }


def seller_report_csv(report: dict[str, Any]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\r\n")
    methods = ["카드", "현금영수증", "기타"]
    w.writerow(
        [
            "판매자번호",
            "상호",
            "사업자등록번호",
            "월",
            "주문수",
            "판매금액",
            *methods,
            "면세",
            "과세",
        ]
    )
    for r in report["rows"]:
        w.writerow(
            [
                r["seller_id"],
                r["name"],
                r["business_no"] or "자료 없음",
                r["month"],
                r["orders"],
                r["total"],
                *[r["by_method"].get(m, 0) for m in methods],
                r["by_tax"].get("면세", 0),
                r["by_tax"].get("과세", 0),
            ]
        )
    w.writerow([])
    w.writerow(["작성 기준", report["basis"]])
    w.writerow(["제출 기한", report["due"]])
    return "﻿" + buf.getvalue()
