"""재무제표: 기간 손익계산서(당기·전기 비교)와 내려받기(CSV).

일반 회사가 쓰는 K-IFRS 기능별 손익계산서 순서(매출액 → 매출원가 → 매출총이익 → 판매비와관리비 →
영업이익 → 영업외손익 → 법인세비용차감전순이익 → 법인세비용 → 당기순이익)를 따른다.

- 오픈마켓 중개라 매출은 순액(판매 수수료)으로 잡는다. 판매 대금은 판매자 몫이다
- 회사 부담 쿠폰은 고객에게 주는 대가라 판촉비가 아니라 매출에서 뺀다(매출에누리, K-IFRS 1115)
- 매출원가는 없다(재고를 사서 팔지 않음). 0으로 적고 이유를 주석에 쓴다
- 숫자는 모두 `closing.compute_totals`로 계산한다. 결산 화면과 같은 값이다
- 이 장부에 없는 항목(급여·임차료, 영업외손익, 법인세)은 지어내지 않고 `None`(자료 없음)으로 둔다.
  그래서 영업이익은 "장부 기준"이고 당기순이익은 비운다
- 원천 자료를 못 받았으면(주문·상품·쿠폰) 그 자료로 만드는 줄도 `None`이다(fail-closed)

재무상태표·현금흐름표는 자산·부채·현금 잔액 장부가 없어 만들지 않는다.
"""

from __future__ import annotations

import csv
import io
from datetime import date, datetime
from typing import Any, Literal

from finance_agent import closing
from finance_agent import finance_config as fc
from finance_agent.books import Books

Period = Literal["month", "quarter", "year"]
PERIODS: tuple[str, ...] = ("month", "quarter", "year")

ORDER_SOURCES = {"orders", "orders_partial", "products"}
NO_DATA = "이 장부에 없는 항목"

# (코드, 이름, 들여쓰기, 주석). 순서가 곧 표 순서다
ROWS: list[tuple[str, str, int, str]] = [
    ("revenue", "Ⅰ. 매출액(영업수익)", 0, "중개 매출이라 순액(판매 수수료)으로 인식"),
    ("commission", "판매 수수료 수익", 1, "품목 판매가 × 카테고리 수수료율"),
    ("sales_discount", "매출에누리(회사 부담 쿠폰)", 1, "고객에게 준 쿠폰은 매출에서 뺀다"),
    ("cogs", "Ⅱ. 매출원가", 0, "재고를 사서 팔지 않아 매출원가가 없음"),
    ("gross_profit", "Ⅲ. 매출총이익", 0, ""),
    ("sga", "Ⅳ. 판매비와관리비(장부 기준)", 0, "장부에 있는 비용만"),
    ("pg_fee", "지급수수료(PG 결제 수수료)", 1, "PG 정산 기록, 없으면 기본 수수료율"),
    ("other_sga", "급여·임차료 등 그 밖의 판관비", 1, NO_DATA),
    ("operating_income", "Ⅴ. 영업이익(장부 기준)", 0, "그 밖의 판관비는 반영하지 않음"),
    ("non_operating_income", "Ⅵ. 영업외수익", 0, NO_DATA),
    ("non_operating_expense", "Ⅶ. 영업외비용", 0, NO_DATA),
    ("pretax_income", "Ⅷ. 법인세비용차감전순이익", 0, NO_DATA),
    ("income_tax", "Ⅸ. 법인세비용", 0, NO_DATA),
    ("net_income", "Ⅹ. 당기순이익", 0, NO_DATA),
]
TOTAL_CODES = {"revenue", "gross_profit", "operating_income", "net_income"}

# 손익계산서 밖의 참고 숫자 (주석)
NOTES: list[tuple[str, str, str]] = [
    ("gmv", "총거래액(GMV)", "결제된 주문의 품목 판매가 합계. 매출이 아님"),
    ("refund_total", "환불", "판매자 정산에서 차감되어 회사 손익에 넣지 않음"),
    ("order_count", "결제 주문 수(건)", ""),
]


def period_range(period: Period, d: date) -> tuple[date, date]:
    """(시작일, 끝 다음 날). 그 달·분기·해 전체."""
    if period == "month":
        return fc.month_range(d)
    if period == "quarter":
        first_month = (d.month - 1) // 3 * 3 + 1
        start = date(d.year, first_month, 1)
        end_month = first_month + 3
        end = date(d.year + 1, 1, 1) if end_month > 12 else date(d.year, end_month, 1)
        return start, end
    return date(d.year, 1, 1), date(d.year + 1, 1, 1)


def previous_start(period: Period, start: date) -> date:
    """전기(바로 앞 같은 길이 기간)의 시작일."""
    if period == "year":
        return date(start.year - 1, 1, 1)
    months = 1 if period == "month" else 3
    month = start.month - months
    year = start.year
    if month < 1:
        month += 12
        year -= 1
    return date(year, month, 1)


def period_name(period: Period, start: date) -> str:
    if period == "month":
        return f"{start.year}년 {start.month}월"
    if period == "quarter":
        return f"{start.year}년 {(start.month - 1) // 3 + 1}분기"
    return f"{start.year}년"


def _sum(*values: int | None) -> int | None:
    return None if any(v is None for v in values) else sum(v for v in values if v is not None)


def column(books: Books, start: datetime, end: datetime) -> dict[str, int | None]:
    """한 기간의 손익계산서 값. 못 받은 자료로 만드는 줄은 None."""
    t = closing.compute_totals(books, start, end)
    missing = set(books.unavailable)
    orders_ok = not (ORDER_SOURCES & missing)
    coupons_ok = "coupons" not in missing
    commission = t.commission_revenue if orders_ok else None
    discount = -t.coupon_cost if coupons_ok else None
    pg_fee = -t.pg_fee if orders_ok else None
    revenue = _sum(commission, discount)
    gross = _sum(revenue, 0)
    return {
        "revenue": revenue,
        "commission": commission,
        "sales_discount": discount,
        "cogs": 0,
        "gross_profit": gross,
        "sga": pg_fee,
        "pg_fee": pg_fee,
        "other_sga": None,
        "operating_income": _sum(gross, pg_fee),
        "non_operating_income": None,
        "non_operating_expense": None,
        "pretax_income": None,
        "income_tax": None,
        "net_income": None,
        "gmv": t.gmv if orders_ok else None,
        "refund_total": t.refund_total if orders_ok else None,
        "order_count": t.order_count if orders_ok else None,
    }


def income_statement(books: Books, period: Period, d: date) -> dict[str, Any]:
    """손익계산서(당기·전기). 금액 단위는 원, 비용은 − 로 적는다."""
    start, end = period_range(period, d)
    prev = previous_start(period, start)
    current = column(books, fc.day_start(start), fc.day_start(end))
    previous = column(books, fc.day_start(prev), fc.day_start(start))
    company = closing.tenant_name()
    return {
        "title": "손익계산서",
        "company": company or None,
        "period": period,
        "current": {
            "name": period_name(period, start),
            "from": fc.iso(fc.day_start(start)),
            "to": fc.iso(fc.day_start(end)),
        },
        "previous": {
            "name": period_name(period, prev),
            "from": fc.iso(fc.day_start(prev)),
            "to": fc.iso(fc.day_start(start)),
        },
        "unit": "원",
        "rows": [
            {
                "code": code,
                "label": label,
                "level": level,
                "total": code in TOTAL_CODES,
                "current": current[code],
                "previous": previous[code],
                "note": note,
            }
            for code, label, level, note in ROWS
        ],
        "notes": [
            {
                "code": code,
                "label": label,
                "current": current[code],
                "previous": previous[code],
                "note": note,
            }
            for code, label, note in NOTES
        ],
        "basis": "K-IFRS 기능별 손익계산서. 장부(판매 사이트·안전 관문 기록)에 있는 항목만 계산",
    }


def _cell(value: int | None) -> str:
    return "자료 없음" if value is None else str(value)


def to_csv(report: dict[str, Any], unavailable: list[str]) -> str:
    """내려받기용 CSV. 엑셀에서 한글이 깨지지 않게 BOM을 붙인다."""
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\r\n")
    cur, prev = report["current"], report["previous"]
    w.writerow([report["title"]])
    if report["company"]:
        w.writerow(["회사", report["company"]])
    w.writerow(["당기", cur["name"], f"{cur['from']} ~ {cur['to']}"])
    w.writerow(["전기", prev["name"], f"{prev['from']} ~ {prev['to']}"])
    w.writerow(["단위", report["unit"]])
    w.writerow([])
    w.writerow(["과목", f"당기 ({cur['name']})", f"전기 ({prev['name']})", "비고"])
    for row in report["rows"]:
        label = ("  " * row["level"]) + row["label"]
        w.writerow([label, _cell(row["current"]), _cell(row["previous"]), row["note"]])
    w.writerow([])
    w.writerow(["주석(참고)"])
    for note in report["notes"]:
        w.writerow([note["label"], _cell(note["current"]), _cell(note["previous"]), note["note"]])
    w.writerow([])
    w.writerow(["작성 기준", report["basis"]])
    if unavailable:
        w.writerow(["못 받은 자료", ", ".join(unavailable)])
    return "﻿" + buf.getvalue()


def filename(report: dict[str, Any]) -> str:
    """ASCII 파일 이름 (예: income-statement-month-2026-10-01.csv)."""
    return f"income-statement-{report['period']}-{report['current']['from'][:10]}.csv"
