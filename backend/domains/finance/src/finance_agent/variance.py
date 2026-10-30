"""① 월간 변동 분석: 이번 달 손익이 지난달(·작년 같은 달)과 왜 달라졌는지 숫자로 나눠 보여 준다.

숫자와 원인 나누기는 모두 코드가 한다. 설명 문장도 코드 템플릿이다(숫자를 바꾸지 않게).
- 줄: 총거래액, 주문 수, 평균 주문 금액, 수수료 매출, 평균 수수료율, 회사 부담 쿠폰, PG 수수료,
  환불, 영업 결과
- 수수료 매출 변동 = 거래량 효과(총거래액 변동 × 지난달 수수료율)
  + 수수료율·구성 효과(이번 달 총거래액 × 수수료율 변동)
- 분류별(도서·문구…) 총거래액·수수료 매출 비교
- 크게 움직인 줄: 변동률이 `variance.flag_ratio` 이상이고 변동액이 `variance.flag_min_amount` 이상
- 자료를 못 받은 줄은 `None`(확인할 수 없음)이고 원인 나누기도 하지 않는다
"""

from __future__ import annotations

from datetime import date, datetime
from typing import Any

from finance_agent import closing
from finance_agent import finance_config as fc
from finance_agent.books import Books, in_range, item_gross, order_sold

LABELS = {
    "gmv": "총거래액",
    "order_count": "주문 수(건)",
    "aov": "평균 주문 금액",
    "commission_revenue": "수수료 매출",
    "take_rate": "평균 수수료율",
    "coupon_cost": "회사 부담 쿠폰",
    "pg_fee": "PG 수수료",
    "refund_total": "환불",
    "net_result": "영업 결과",
}
RATE_CODES = {"take_rate"}
COUNT_CODES = {"order_count"}
# 늘어나면 나쁜 줄 (설명에서 "늘어 이익을 줄였다"로 쓴다)
COST_CODES = {"coupon_cost", "pg_fee", "refund_total"}


def _month(d: date, back: int = 0) -> tuple[date, date]:
    y, m = d.year, d.month - back
    while m < 1:
        m += 12
        y -= 1
    return fc.month_range(date(y, m, 1))


def _values(books: Books, start: datetime, end: datetime) -> dict[str, Any]:
    t = closing.compute_totals(books, start, end)
    unknown = closing.unknown_codes(books)
    v: dict[str, Any] = {
        "gmv": t.gmv,
        "order_count": t.order_count,
        "aov": round(t.gmv / t.order_count) if t.order_count else None,
        "commission_revenue": t.commission_revenue,
        "take_rate": round(t.commission_revenue / t.gmv, 4) if t.gmv else None,
        "coupon_cost": t.coupon_cost,
        "pg_fee": t.pg_fee,
        "refund_total": t.refund_total,
        "net_result": t.net_result,
    }
    if "gmv" in unknown:
        for code in ("gmv", "order_count", "aov", "take_rate"):
            v[code] = None
    for code in unknown:
        if code in v:
            v[code] = None
    if "commission_revenue" in unknown:
        v["take_rate"] = None
    return v


def by_category(books: Books, start: datetime, end: datetime) -> dict[str, dict[str, int]]:
    cfg = fc.load_config()
    out: dict[str, dict[str, int]] = {}
    for o in books.orders:
        if not order_sold(o):
            continue
        paid = [p["created_at"] for p in o["payments"] if p.get("status") == "succeeded"]
        if not paid or not in_range(min(paid), start, end):
            continue
        for item in o["items"]:
            code = item.get("category_code") or "unknown"
            gross = item_gross(item)
            row = out.setdefault(code, {"gmv": 0, "commission": 0})
            row["gmv"] += gross
            row["commission"] += fc.rate_amount(gross, cfg.commission.rate_for(code))
    return out


def _change(cur: Any, prev: Any) -> tuple[Any, float | None]:
    if cur is None or prev is None:
        return None, None
    diff = round(cur - prev, 4) if isinstance(cur, float) or isinstance(prev, float) else cur - prev
    pct = round((cur - prev) / abs(prev), 4) if prev else None
    return diff, pct


def _ga(word: str) -> str:
    """받침이 있으면 "이", 없으면 "가". 한글이 아니면 괄호 앞 글자로 본다."""
    core = word.split("(")[0].strip()
    last = core[-1] if core else ""
    if "가" <= last <= "힣":
        return word + ("이" if (ord(last) - 0xAC00) % 28 else "가")
    return word + "이"


def _sentence(code: str, cur: Any, prev: Any, diff: Any, pct: float | None) -> str:
    label = _ga(LABELS[code].replace("(건)", ""))
    if code in RATE_CODES:
        return f"{label} {prev * 100:.1f}%에서 {cur * 100:.1f}%로 바뀌었습니다."
    unit = (lambda n: f"{n:,}건") if code in COUNT_CODES else fc.won
    way = "늘었" if diff > 0 else "줄었"
    rate = f" ({abs(pct) * 100:.0f}%)" if pct is not None else ""
    tail = ""
    if code in COST_CODES:
        tail = (
            " 비용이 늘어 영업 결과를 줄였습니다."
            if diff > 0
            else " 비용이 줄어 영업 결과를 늘렸습니다."
        )
    return f"{label} {unit(abs(diff))}{rate} {way}습니다 ({unit(prev)} → {unit(cur)}).{tail}"


def variance_report(books: Books, d: date | None = None) -> dict[str, Any]:
    cfg = fc.load_config().variance
    d = d or fc.today()
    cur_s, cur_e = _month(d)
    prev_s, prev_e = _month(d, 1)
    yoy_s, yoy_e = _month(d, 12)
    spans = {
        "current": (cur_s, cur_e),
        "previous": (prev_s, prev_e),
        "last_year": (yoy_s, yoy_e),
    }
    vals = {k: _values(books, fc.day_start(s), fc.day_start(e)) for k, (s, e) in spans.items()}
    cur, prev, yoy = vals["current"], vals["previous"], vals["last_year"]

    lines = []
    for code, label in LABELS.items():
        diff, pct = _change(cur[code], prev[code])
        yoy_diff, yoy_pct = _change(cur[code], yoy[code])
        big = (
            diff is not None
            and pct is not None
            and abs(pct) >= cfg.flag_ratio
            and (code in RATE_CODES or code in COUNT_CODES or abs(diff) >= cfg.flag_min_amount)
        )
        lines.append(
            {
                "code": code,
                "label": label,
                "current": cur[code],
                "previous": prev[code],
                "diff": diff,
                "pct": pct,
                "last_year": yoy[code],
                "yoy_diff": yoy_diff,
                "yoy_pct": yoy_pct,
                "flag": big,
                "note": _sentence(code, cur[code], prev[code], diff, pct) if big else "",
            }
        )

    drivers = None
    if None not in (cur["gmv"], prev["gmv"], cur["take_rate"], prev["take_rate"]):
        volume = round((cur["gmv"] - prev["gmv"]) * prev["take_rate"])
        rate_mix = cur["commission_revenue"] - prev["commission_revenue"] - volume
        drivers = {
            "commission_change": cur["commission_revenue"] - prev["commission_revenue"],
            "volume_effect": volume,
            "rate_mix_effect": rate_mix,
        }

    categories = []
    if "gmv" not in closing.unknown_codes(books):
        cur_c = by_category(books, fc.day_start(cur_s), fc.day_start(cur_e))
        prev_c = by_category(books, fc.day_start(prev_s), fc.day_start(prev_e))
        for code in sorted(set(cur_c) | set(prev_c)):
            a, b = (
                cur_c.get(code, {"gmv": 0, "commission": 0}),
                prev_c.get(code, {"gmv": 0, "commission": 0}),
            )
            categories.append(
                {
                    "category": code,
                    "gmv": a["gmv"],
                    "gmv_prev": b["gmv"],
                    "commission": a["commission"],
                    "commission_prev": b["commission"],
                    "commission_diff": a["commission"] - b["commission"],
                }
            )
        categories.sort(key=lambda c: -abs(c["commission_diff"]))

    summary = [line["note"] for line in lines if line["flag"]]
    if drivers and drivers["commission_change"]:
        summary.append(
            f"수수료 매출 변동 {fc.won(drivers['commission_change'])} 중 거래량 효과 "
            f"{fc.won(drivers['volume_effect'])}, "
            f"수수료율·구성 효과 {fc.won(drivers['rate_mix_effect'])}입니다."
        )
    if not summary:
        summary.append("지난달과 비교해 크게 움직인 줄이 없습니다.")
    return {
        "month": cur_s.isoformat()[:7],
        "spans": {
            k: {"from": fc.iso(fc.day_start(s)), "to": fc.iso(fc.day_start(e))}
            for k, (s, e) in spans.items()
        },
        "lines": lines,
        "drivers": drivers,
        "categories": categories,
        "summary": summary,
        "thresholds": {"flag_ratio": cfg.flag_ratio, "flag_min_amount": cfg.flag_min_amount},
    }
