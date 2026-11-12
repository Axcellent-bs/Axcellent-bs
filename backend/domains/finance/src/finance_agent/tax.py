"""세무 지원 자료: 부가세 신고 보조 집계와 판매자별 판매·중개 명세(부가가치세법 제75조).

재무 AI는 신고하거나 제출하지 않는다.
사람(세무 담당·세무사)이 신고·제출할 때 쓸 숫자와 파일을 만든다.
근거는 2026-10-30 검토(세무 관점) 기준이다. 확인이 안 된 부분은 설정값과 `checks`(확인 필요 목록)로
드러낸다.

회사(통신판매중개)의 부가세 구조
- 판매자의 상품 매출은 회사 매출이 아니다. 도서는 면세(부가세법 제26조①8), 문구 등은 과세이고
  판매자가 신고한다
- 회사의 과세 매출은 판매 수수료(중개 용역)다. 면세 도서를 중개해도 수수료는 과세다.
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
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from finance_agent import closing
from finance_agent import finance_config as fc
from finance_agent.books import Books, item_gross
from finance_agent.rules import applied, applied_many, load_rules

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
    """(공급가액, 세액). mode: excluded(금액이 공급가액) / included(금액에 세액 포함).

    원 단위는 회사 정책과 같은 0.5 올림(`fc.rate_amount`, Decimal)이다. 예전에는 Python `round()`
    (0.5에서 짝수 쪽)라 세액이 x.5원이면 1원 적게 나왔다(재무 평가 eval/run.py·holdout.py).
    """
    if mode == "included":
        supply = int(
            (Decimal(amount) / Decimal("1.1")).quantize(Decimal("1"), rounding=ROUND_HALF_UP)
        )
        return supply, amount - supply
    return amount, fc.rate_amount(amount, 0.1)


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
        for o in books.sold_between(start, end):
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
                    "label": "배송 용역(회사 공급)",
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
    supplier = "회사" if t.shipping_supplier == "platform" else "판매자"
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
        "applied_rules": applied_many(
            "VAT-001", "VAT-002", "VAT-003", "VAT-004", "VAT-006", "TAX-001"
        ),
        "basis": "신고서가 아니라 신고 보조 집계입니다. 신고는 세무 담당이 홈택스에서 합니다",
    }


def quarter_finished(d: date, today: date | None = None) -> bool:
    return quarter_range(d)[1] <= (today or fc.today())


def filing_key(d: date) -> str:
    return quarter_range(d)[0].isoformat()


def filing_drift(fixed: dict[str, Any], now: dict[str, Any]) -> list[dict[str, Any]]:
    """확정한 부가세 숫자와 지금 숫자의 차이. 지금 숫자를 모르면 비교하지 않는다."""
    labels = {"output_vat": "매출세액", "input_vat": "매입세액", "payable": "낼 세액"}
    out = []
    for code, label in labels.items():
        a, b = fixed.get(code), now.get(code)
        if a is not None and b is not None and a != b:
            out.append({"code": code, "label": label, "confirmed": a, "current": b, "diff": b - a})
    return out


def vat_view(
    books: Books, d: date, filings: dict[str, dict[str, Any]] | None = None
) -> dict[str, Any]:
    """화면과 skill이 보는 부가세. 확정한 분기는 확정 숫자이고 차이는 따로 보여 준다."""
    now = vat_pack(books, d)
    filing = (filings or {}).get(filing_key(d))
    status = {"closable": quarter_finished(d), "filing_status": "draft", "drift": []}
    if filing is None:
        return now | status
    return (
        filing["pack"]
        | status
        | {
            "filing_status": "confirmed",
            "confirmed_by": filing["by"],
            "confirmed_at": filing["at"],
            "drift": filing_drift(filing["pack"], now),
        }
    )


def find_vat_drifts(books: Books, filings: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for key, filing in filings.items():
        diff = filing_drift(filing["pack"], vat_pack(books, date.fromisoformat(key)))
        if diff:
            out.append({"quarter": filing["pack"]["quarter"], "from": key, "lines": diff})
    return sorted(out, key=lambda x: x["from"])


def income_tax_amount(base: int) -> int:
    """법인세 누진 계산(원 미만 버림). 0 이하면 0."""
    if base <= 0:
        return 0
    amount, lower = 0.0, 0
    for bracket in load_rules().income_tax.brackets:
        upper = bracket.upto_krw if bracket.upto_krw is not None else base
        if base > lower:
            amount += (min(base, upper) - lower) * bracket.rate
        lower = upper
    return int(amount)


def income_tax_estimate(
    books: Books,
    year: int | None = None,
    today: date | None = None,
    other_pnl: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """법인세 참고 추정. 장부의 영업 결과만 쓴다. 세무조정 전이라 신고에 쓰지 않는다."""
    today = today or fc.today()
    year = year or today.year
    start_d = date(year, 1, 1)
    end_d = min(date(year + 1, 1, 1), today + timedelta(days=1))
    book = load_rules().income_tax
    missing = "pg_fee" in closing.unknown_codes(
        books
    ) or "commission_revenue" in closing.unknown_codes(books)
    base = None
    if not missing:
        base = closing.compute_totals(books, fc.day_start(start_d), fc.day_start(end_d)).net_result
    months = [v for k, v in (other_pnl or {}).items() if k.startswith(f"{year}-")]
    if base is not None:
        for row in months:
            base += (
                row["non_operating_income"]
                - row["non_operating_expense"]
                - row["salary"]
                - row["rent"]
                - row["other_sga"]
            )
    income_tax = None if base is None else income_tax_amount(base)
    local_tax = None if income_tax is None else int(income_tax * book.local_rate_of_tax)
    return {
        "year": year,
        "from": start_d.isoformat(),
        "to": (end_d - timedelta(days=1)).isoformat(),
        "operating_result": base,
        "income_tax": income_tax,
        "local_income_tax": local_tax,
        "total": None if income_tax is None or local_tax is None else income_tax + local_tax,
        "brackets": [b.model_dump() for b in book.brackets],
        "entered_months": len(months),
        "not_included": (
            ["입력하지 않은 달의 비용", "세무조정", "이월결손금", "세액공제"]
            if months
            else [
                "급여",
                "임차료",
                "광고비 외 비용",
                "영업외손익",
                "세무조정",
                "이월결손금",
                "세액공제",
            ]
        ),
        "status": "reference_only",
        "basis": "참고용 추정입니다. 세무조정 전이라 신고에 쓰지 않고, 세무사 검토가 필요합니다.",
        "applied_rules": [
            applied("CIT-001", None if base is None else f"영업 결과 {fc.won(base)} 기준"),
            applied("TAX-001"),
        ],
    }


def _paid_at(order: dict[str, Any]):
    times = [p["created_at"] for p in order["payments"] if p.get("status") == "succeeded"]
    return min(times) if times else None


def _tax_type(category: str | None) -> str:
    cfg = fc.load_config().tax
    return cfg.category_tax.get(category or "", cfg.default_tax)


def seller_sales_by_tax(books: Books, start_d: date, end_d: date) -> dict[str, int]:
    """판매자 상품 매출(회사 매출 아님)을 면세·과세로 나눈 합. 참고용."""
    start, end = fc.day_start(start_d), fc.day_start(end_d)
    out: dict[str, int] = {"exempt": 0, "taxable": 0}
    for o in books.sold_between(start, end):
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
    for o in books.sold_between(start, end):
        paid = _paid_at(o)
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
        "applied_rules": applied_many("VAT-005", "TAX-001"),
        "basis": "부가가치세법 제75조 판매·중개 명세 초안. 제출 서식은 국세청 고시 확인 필요",
        "unavailable": "orders" in books.unavailable,
    }


def _seller_report_table(report: dict[str, Any]) -> tuple[list[str], list[list[Any]]]:
    methods = ["카드", "현금영수증", "기타"]
    head = [
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
    rows = [
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
        for r in report["rows"]
    ]
    return head, rows


def seller_report_csv(report: dict[str, Any]) -> str:
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\r\n")
    head, rows = _seller_report_table(report)
    w.writerow(head)
    w.writerows(rows)
    w.writerow([])
    w.writerow(["작성 기준", report["basis"]])
    w.writerow(["제출 기한", report["due"]])
    return "﻿" + buf.getvalue()


def seller_report_xlsx(report: dict[str, Any], note: str | None = None) -> bytes:
    """내려받기용 엑셀. CSV와 같은 내용이고, 숫자는 숫자 칸으로 넣는다.

    `note`(예: 확정 전 숫자)가 있으면 맨 위에 한 줄 넣고 표를 한 줄 내린다.
    """
    from openpyxl import Workbook
    from openpyxl.styles import Font
    from openpyxl.utils import get_column_letter

    wb = Workbook()
    ws = wb.active
    ws.title = "판매자별 판매 명세"
    head, rows = _seller_report_table(report)

    def text(row: int, col: int, value: Any) -> Any:
        cell = ws.cell(row=row, column=col)
        cell.value = str(value)
        cell.data_type = "s"  # "="로 시작해도 수식으로 실행되지 않게 글자로 넣는다
        return cell

    top = 1
    if note:
        text(1, 1, note).font = Font(bold=True, color="B45309")
        top = 2
    for col, name in enumerate(head, start=1):
        text(top, col, name).font = Font(bold=True)
    for r, row in enumerate(rows, start=top + 1):
        for col, value in enumerate(row, start=1):
            if isinstance(value, int) and not isinstance(value, bool):
                ws.cell(row=r, column=col, value=value).number_format = "#,##0"
            else:
                text(r, col, value)
    last = len(rows) + top + 2
    text(last, 1, "작성 기준")
    text(last, 2, report["basis"])
    text(last + 1, 1, "제출 기한")
    text(last + 1, 2, report["due"])
    ws.freeze_panes = f"A{top + 1}"
    for col, width in enumerate((14, 22, 16, 10, 8, 14, 14, 12, 10, 14, 14), start=1):
        ws.column_dimensions[get_column_letter(col)].width = width
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()
