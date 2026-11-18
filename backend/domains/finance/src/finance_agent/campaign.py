"""캠페인 재무: 손익 기록, 예산 브레이크, 실패 교훈, 인사이트. 숫자와 판단은 전부 코드가 한다.

- 손익: 회사는 중개업이라 매출은 판매액이 아니라 수수료다 (`CMP-001`)
- 브레이크: 캠페인 예산만 판단한다. 쿠폰 발급과 환불은 대상이 아니다 (`BRK-005`).
  재무는 끝난 쿠폰 금액을 받아 비용에 적을 뿐이다
- 교훈: 적자 캠페인이 다음 예산 한도를 줄이고, 효율 좋음이 나오면 풀린다 (`LRN-*`)
- 값은 `finance_rules.toml`. 판단 결과마다 적용 규칙과 근거를 함께 낸다
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any, Literal

from finance_agent import finance_config as fc
from finance_agent.books import Books, item_gross, order_sold, paid_at
from finance_agent.customer_reaction import (
    NOT_RELEVANT,
    STATUS_FLAGGED,
    STATUS_NOT_RELEVANT,
    STATUS_UNAVAILABLE,
    Reaction,
    flag_text,
)
from finance_agent.rules import applied, load_rules

Verdict = Literal["good", "neutral", "bad"]
VERDICT_KO = {"good": "좋음", "neutral": "보통", "bad": "나쁨"}
# 재무는 비용을 승인·반려하지 않는다(2026-11-06). 판단 이름은 기록 호환으로 두고 의견으로 읽는다
DECISION_KO = {"approved": "권장", "conditional": "줄여서 권장", "rejected": "권하지 않음"}
OPINION_OF = {"approved": "recommend", "conditional": "reduce", "rejected": "not_recommended"}


def _month(day: str) -> str:
    return day[:7]


# ---------------------------------------------------------------- 손익
def break_even_roas() -> float | None:
    """광고비 1원당 판매액이 이만큼 나와야 본전이다. 수수료가 PG 수수료 이하면 None."""
    cfg = fc.load_config()
    margin = cfg.commission.default_rate - cfg.pg.fee_rate
    return round(1 / margin, 2) if margin > 0 else None


def repeat_estimate(books: Books, today: date) -> dict[str, Any] | None:
    """장부에서 재는 재구매 배수. 첫 구매가 충분히 지난 구매자만 본다. 모자라면 None(지어내지 않음).

    배수 = (첫 구매액 + 기간 안 재구매액) ÷ 첫 구매액.
    """
    cfg = load_rules().repeat
    if "orders" in books.unavailable:
        return None
    purchases: dict[str, list[tuple[date, int]]] = {}
    for order in books.orders:
        buyer = order.get("buyer_id")
        if not buyer or not order_sold(order):
            continue
        paid = paid_at(order)
        if paid is None:
            continue
        gross = sum(item_gross(item) for item in order["items"])
        purchases.setdefault(buyer, []).append((paid.date(), gross))
    cutoff = today - timedelta(days=cfg.window_days)
    first_total = repeat_total = repeaters = cohort = 0
    for rows in purchases.values():
        rows.sort(key=lambda r: r[0])
        first_day, first_gross = rows[0]
        if first_day > cutoff or first_gross <= 0:
            continue
        cohort += 1
        first_total += first_gross
        again = sum(g for d, g in rows[1:] if (d - first_day).days <= cfg.window_days)
        repeat_total += again
        repeaters += again > 0
    if cohort < cfg.min_cohort_buyers:
        return None
    return {
        "multiple": round((first_total + repeat_total) / first_total, 3),
        "cohort_buyers": cohort,
        "repeat_buyers": repeaters,
        "window_days": cfg.window_days,
        "source": "books",
    }


def over_started(record: dict[str, Any], started: dict[str, Any]) -> int:
    """시작한 금액보다 많이 쓴 금액. 0이면 정상."""
    return max(record["spend_krw"] - started["total_krw"], 0)


def compute_result(data: dict[str, Any], repeat: dict[str, Any] | None = None) -> dict[str, Any]:
    """캠페인 결과 한 건. 판매액을 모르면 0으로 채우지 않고 판단하지 않는다."""
    cfg = fc.load_config()
    book = load_rules().campaign
    spend, coupon, gmv = data["spend_krw"], data.get("coupon_krw") or 0, data.get("gmv_krw")
    production = data.get("production_cost_krw") or 0
    record = {
        **data,
        "coupon_krw": coupon,
        "commission_rate": cfg.commission.default_rate,
        "pg_fee_rate": cfg.pg.fee_rate,
        "break_even_roas": break_even_roas(),
        "expense_classes": {
            "spend_krw": "광고선전비",
            "production_cost_krw": "광고선전비(제작비)",
            "coupon_krw": "매출 차감",
        },
        **view_cost(data),
    }
    if gmv is None:
        return record | {
            "status": "waiting_revenue",
            "revenue_krw": None,
            "pg_fee_krw": None,
            "cost_krw": None,
            "profit_krw": None,
            "roi": None,
            "roas": None,
            "verdict": None,
            "repeat": None,
            "ltv_profit_krw": None,
            "ltv_roi": None,
            "applied_rules": [applied("CMP-001", "판매액을 받지 못해 계산하지 않았다")],
        }
    revenue = fc.rate_amount(gmv, cfg.commission.default_rate)
    pg_fee = fc.rate_amount(gmv, cfg.pg.fee_rate)
    cost = spend + production + coupon + pg_fee
    profit = revenue - cost
    invested = spend + production + coupon
    roi = round(profit / invested, 4) if invested else None
    roas = round(gmv / spend, 2) if spend else None
    if roi is None:
        verdict: Verdict = "neutral"
    elif roi >= book.good_roi:
        verdict = "good"
    elif roi < book.bad_roi:
        verdict = "bad"
    else:
        verdict = "neutral"
    ltv_profit, ltv_roi, ltv_detail = None, None, "재구매 자료가 모자라 계산하지 않았다"
    if repeat is not None:
        ltv_gmv = round(gmv * repeat["multiple"])
        ltv_profit = (
            fc.rate_amount(ltv_gmv, cfg.commission.default_rate)
            - fc.rate_amount(ltv_gmv, cfg.pg.fee_rate)
            - invested
        )
        ltv_roi = round(ltv_profit / invested, 4) if invested else None
        ltv_detail = f"재구매 배수 {repeat['multiple']}배 → 참고 손익 {fc.won(ltv_profit)}"
    return record | {
        "repeat": repeat,
        "ltv_profit_krw": ltv_profit,
        "ltv_roi": ltv_roi,
        "status": "recorded",
        "revenue_krw": revenue,
        "pg_fee_krw": pg_fee,
        "cost_krw": cost,
        "profit_krw": profit,
        "roi": roi,
        "roas": roas,
        "verdict": verdict,
        "applied_rules": [
            applied(
                "CMP-001",
                f"증분 판매액 {fc.won(gmv)} × 수수료 {cfg.commission.default_rate:.1%} "
                f"= {fc.won(revenue)}",
            ),
            applied(
                "CMP-002",
                f"광고비 {fc.won(spend)}"
                + (f" + 제작비 {fc.won(production)}" if production else "")
                + f" + 쿠폰 {fc.won(coupon)} + PG {fc.won(pg_fee)} = {fc.won(cost)}",
            ),
            applied("CMP-003", f"손익 {fc.won(profit)}, ROI {_pct(roi)}"),
            applied(
                "CMP-004",
                f"ROI {_pct(roi)} → {VERDICT_KO[verdict]} "
                f"(좋음 {book.good_roi:.0%} 이상, 나쁨 {book.bad_roi:.0%} 미만)",
            ),
            applied("CMP-005", ltv_detail),
            applied("EXP-002"),
        ],
    }


VIEWS_FINAL_POINT = "7d"  # 조회당 제작비는 게시 7일 조회수로만 낸다 (CMP-007)


def view_cost(data: dict[str, Any]) -> dict[str, Any]:
    """조회당 제작비 (CMP-007). 제작비·조회수가 없거나 7일 측정 전이면 계산하지 않고 이유를 둔다."""
    production, views, point = (
        data.get("production_cost_krw"),
        data.get("views"),
        data.get("views_point"),
    )
    if production is None:
        return {"cost_per_view": None, "view_cost_status": "no_production_cost"}
    if views is None or point is None:
        return {"cost_per_view": None, "view_cost_status": "no_views"}
    if point != VIEWS_FINAL_POINT:
        return {"cost_per_view": None, "view_cost_status": "measuring"}
    if views == 0:
        return {"cost_per_view": None, "view_cost_status": "zero_views"}
    return {"cost_per_view": round(production / views, 2), "view_cost_status": "measured"}


def _pct(value: float | None) -> str:
    return "계산 불가" if value is None else f"{value:.1%}"


def recommendation(record: dict[str, Any]) -> str:
    be = record.get("break_even_roas")
    match record["verdict"]:
        case "good":
            return (
                f"{record['channel']} 채널을 같은 조건으로 유지하거나 조금씩 늘리는 것을 권합니다."
            )
        case "bad" if (record.get("ltv_profit_krw") or 0) > 0:
            return (
                f"{record['channel']} 채널은 단기 적자지만 재구매를 넣으면 회수될 수 있습니다. "
                "캠페인 고객의 실제 재구매를 확인한 뒤 늘리세요."
            )
        case "bad":
            return (
                f"{record['channel']} 채널은 다음 예산을 줄이고 원인을 먼저 봐야 합니다. "
                f"본전이 되려면 ROAS {be}배가 필요합니다."
            )
        case _:
            return f"{record['channel']} 채널은 추세를 더 보고 판단합니다."


def opinion_of(record: dict[str, Any], at: datetime) -> dict[str, Any]:
    """총괄에 내는 의견. 계약 `FinanceOpinion` 형식이다."""
    return {
        "opinion_id": f"op_{record['campaign_id']}_{record.get('revision', 1)}",
        "kind": "campaign_result",
        "campaign_id": record["campaign_id"],
        "campaign_name": record["campaign_name"],
        "channel": record["channel"],
        "spend_krw": record["spend_krw"],
        "revenue_krw": record["revenue_krw"],
        "profit_krw": record["profit_krw"],
        "roi": record["roi"],
        "verdict": record["verdict"],
        "reasons": [r["detail"] for r in record["applied_rules"] if r["detail"]],
        "recommendation": recommendation(record),
        "applied_rules": record["applied_rules"],
        "submitted_at": fc.iso(at),
    }


# ---------------------------------------------------------------- 교훈
def apply_lessons(
    record: dict[str, Any], lessons: list[dict[str, Any]], at: datetime
) -> str | None:
    """결과를 교훈에 반영한다. 나쁨이면 새 교훈, 좋음이면 그 채널 교훈을 닫는다."""
    if record["verdict"] == "bad":
        if any(x["campaign_id"] == record["campaign_id"] for x in lessons):
            return None
        lessons.append(
            {
                "lesson_id": f"les_{record['campaign_id']}",
                "campaign_id": record["campaign_id"],
                "campaign_name": record["campaign_name"],
                "channel": record["channel"],
                "spend_krw": record["spend_krw"],
                "profit_krw": record["profit_krw"],
                "roi": record["roi"],
                "status": "open",
                "learned_at": fc.iso(at),
            }
        )
        return "added"
    if record["verdict"] == "good":
        closed = 0
        for lesson in lessons:
            if lesson["channel"] == record["channel"] and lesson["status"] == "open":
                lesson |= {
                    "status": "closed",
                    "closed_at": fc.iso(at),
                    "closed_by": record["campaign_id"],
                }
                closed += 1
        return "closed" if closed else None
    return None


# ---------------------------------------------------------------- 브레이크
NO_STARTS: dict[str, dict[str, Any]] = {}


def fallback_budget() -> dict[str, Any]:
    return {
        "amount_krw": load_rules().brake.monthly_budget_krw,
        "basis": "직전 달 자료가 없어 임시값",
        "provisional": True,
    }


def budget_from_last_month(
    books: Books,
    today: date,
    closings: dict[tuple[str, str], dict[str, Any]] | None = None,
    other_pnl: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """월 예산 = 직전 달 영업 결과 × 비율. 확정한 달은 확정 숫자를 쓴다. 자료가 없으면 임시값.

    판매일이 적은 달(서비스를 막 시작한 달)로 낸 값은 한 달을 대표하지 못해 임시값으로 표시한다.
    """
    from finance_agent import closing  # closing이 LLM 등을 읽어 여기서만 부른다

    book = load_rules()
    ratio = book.brake.budget_ratio_of_last_month
    this_month = today.replace(day=1)
    last_month = (this_month - timedelta(days=1)).replace(day=1)
    name = last_month.isoformat()[:7]
    days = closing.sales_days(books, last_month, this_month)
    short = days < book.closing.budget_min_sales_days
    note = f", 판매 {days}일치라 임시값" if short else ""
    snap = closing.usable_snapshot((closings or {}).get(("month", last_month.isoformat())))
    if snap:
        net = next((x["amount"] for x in snap["lines"] if x["code"] == "net_result"), None)
        # 비용을 입력하고 확정한 달은 비용까지 뺀 영업이익이 기준이다
        stated = (snap.get("statement") or {}).get("operating_income")
        if (snap.get("statement") or {}).get("other_sga") is not None and stated is not None:
            net = stated
        if net is not None:
            return {
                "amount_krw": max(fc.rate_amount(net, ratio), 0),
                "basis": f"{name} 확정 영업 결과 {fc.won(net)}의 {ratio:.0%}{note}",
                "provisional": short,
            }
    if closing.unknown_codes(books) & {"commission_revenue", "pg_fee"}:
        return fallback_budget()
    totals = closing.compute_totals(books, fc.day_start(last_month), fc.day_start(this_month))
    if totals.order_count == 0:
        return fallback_budget()
    entered = (other_pnl or {}).get(name)
    costs = entered["salary"] + entered["rent"] + entered["other_sga"] if entered else 0
    result = totals.net_result - costs
    label = "입력 비용을 뺀 영업이익" if entered else "영업 결과"
    return {
        "amount_krw": max(fc.rate_amount(result, ratio), 0),
        "basis": f"{name} {label} {fc.won(result)}의 {ratio:.0%}{note}",
        "provisional": short,
    }


def committed_this_month(
    campaign_id: str,
    month: str,
    campaigns: dict[str, dict[str, Any]],
    started: dict[str, dict[str, Any]],
) -> int:
    """이달 결과가 기록된 집행액 + 시작했지만 결과가 아직 없는 금액. 다시 묻는 같은 캠페인은 뺀다.

    시작 여부는 안전 관문 실행 기록이 기준이다. 사람이 승인한 큰 금액도 여기 잡힌다.
    판단만 받고 시작하지 않은 건, 사람이 거절한 건, 취소한 건은 잡지 않는다.
    """
    total = sum(
        c["spend_krw"]
        for c in campaigns.values()
        if _month(c["period_to"]) == month and c["campaign_id"] != campaign_id
    )
    for cid, row in started.items():
        if cid != campaign_id and cid not in campaigns and row["month"] == month:
            total += row["active_krw"]
    return total


def summarize_starts(
    rows: list[dict[str, Any]], cancels: dict[str, dict[str, Any]]
) -> dict[str, dict[str, Any]]:
    """관문 실행 기록 → 캠페인별 시작 금액. `active_krw`는 취소 뒤 남은 금액이다."""
    out: dict[str, dict[str, Any]] = {}
    for row in sorted(rows, key=lambda r: r["at"]):
        cid = row["campaign_id"]
        cur = out.setdefault(
            cid, {"campaign_id": cid, "total_krw": 0, "active_krw": 0, "month": row["at"][:7]}
        )
        cur["total_krw"] += row["amount_krw"]
        cancel = cancels.get(cid)
        if cancel is None or row["at"] > cancel["canceled_at"]:
            cur["active_krw"] += row["amount_krw"]
            cur["month"] = row["at"][:7]
    return out


def _loss_streak(channel: str, campaigns: dict[str, dict[str, Any]]) -> int:
    rows = [c for c in campaigns.values() if c["channel"] == channel and c["verdict"] is not None]
    streak = 0
    for row in sorted(rows, key=lambda c: c["period_to"], reverse=True):
        if row["profit_krw"] >= 0:
            break
        streak += 1
    return streak


def decide(
    request: dict[str, Any],
    *,
    campaigns: dict[str, dict[str, Any]],
    reviews: list[dict[str, Any]],
    lessons: list[dict[str, Any]],
    cash_net_krw: int | None,
    today: date,
    monthly_budget: dict[str, Any] | None = None,
    started: dict[str, dict[str, Any]] | None = NO_STARTS,
    cash_basis: str = "net",
    reaction: Reaction = NOT_RELEVANT,
) -> dict[str, Any]:
    """재무 의견: 권장·줄여서 권장·권하지 않음. 집행을 막지 않는다(막는 것은 안전 관문과 사람).

    기록 이름은 approved·conditional·rejected 그대로다. 권하지 않는 이유가 있어도 모든 기준의
    확인 결과를 근거로 남긴다.

    `started`가 None이면 안전 관문 기록을 못 읽은 것이다. 쓴 돈을 모르니 권하지 않는다.

    `reaction`은 고객지원 책별 집계로 본 고객 반응이다(`CRX-001`). 요청에 상품이 없으면 아무 규칙도
    적용하지 않는다(기본값). 걸린 책이 있으면 책이 몇 권이든 요청당 한 번만 한도에 곱한다.
    """
    book = load_rules()
    brake, lesson_rules = book.brake, book.lessons
    requested, channel = request["requested_krw"], request["channel"]
    month = today.isoformat()[:7]
    limit = requested
    rejects: list[str] = []
    limits: list[str] = []
    rules: list[dict[str, Any]] = []

    # 연속 적자
    streak = _loss_streak(channel, campaigns)
    detail = f"{channel} 연속 적자 {streak}회 (기준 {brake.loss_streak_reject}회)"
    rules.append(applied("BRK-002", detail))
    if streak >= brake.loss_streak_reject:
        rejects.append(detail)

    # 이달 손실 누적
    month_rows = [c for c in campaigns.values() if c["verdict"] and _month(c["period_to"]) == month]
    loss = -sum(min(c["profit_krw"], 0) for c in month_rows)
    detail = f"이달 캠페인 손실 {fc.won(loss)} (기준 {fc.won(brake.loss_limit_krw)})"
    rules.append(applied("BRK-003", detail))
    if loss >= brake.loss_limit_krw:
        rejects.append(detail)

    # 현금 흐름
    weeks = brake.cash_lookahead_weeks
    if cash_net_krw is None:
        detail = f"앞으로 {weeks}주 현금 흐름을 확인할 수 없음"
        if brake.min_viable_krw < limit:  # 현금을 모르면 큰돈은 쓰지 않고, 흐름은 멈추지 않는다
            limit = brake.min_viable_krw
            limits.append(f"{detail}. 최소 금액 {fc.won(limit)}까지만 권장")
    else:
        # balance: 예상 잔액 최저값 − 최소 현금(통장 잔액을 알 때), net: 누적 순현금(모를 때)
        what = "예상 잔액 여유(최저 잔액 − 최소 현금)" if cash_basis == "balance" else "누적 순현금"
        detail = f"앞으로 {weeks}주 {what} {fc.won(cash_net_krw)}"
        if cash_net_krw <= 0:
            rejects.append(detail)
        elif cash_net_krw < limit:
            limit = cash_net_krw
            limits.append(f"{detail}까지만 권장")
    rules.append(applied("BRK-004", detail))

    # 남은 예산
    committed = committed_this_month(request["campaign_id"], month, campaigns, started or {})
    budget = monthly_budget or fallback_budget()
    remaining = max(budget["amount_krw"] - committed, 0)
    detail = (
        f"이달 예산 {fc.won(budget['amount_krw'])} ({budget['basis']}) "
        f"중 남은 금액 {fc.won(remaining)}"
    )
    rules.append(applied("BRK-001", detail))
    if started is None:
        rejects.append("안전 장치의 집행 기록을 읽지 못해 쓴 돈을 확인할 수 없음")
    budget_limited = remaining < limit
    if remaining < brake.min_viable_krw:
        rejects.append(f"{detail}, 최소 {fc.won(brake.min_viable_krw)}보다 적음")
    elif budget_limited:
        limit = remaining
        limits.append(f"{detail}까지만 권장")

    # 실패 교훈
    open_lessons = [x for x in lessons if x["channel"] == channel and x["status"] == "open"]
    factor = max(lesson_rules.limit_factor ** len(open_lessons), lesson_rules.floor_factor)
    if open_lessons:
        # 요청액이 아니라 실패한 집행액 기준이다. 요청액 기준이면 낮춰 낼 때마다 또 깎인다
        cap = fc.rate_amount(max(x["spend_krw"] for x in open_lessons), factor)
        names = ", ".join(x["campaign_name"] for x in open_lessons)
        detail = (
            f"{channel} 실패 이력 {len(open_lessons)}건 ({names}) → "
            f"실패 집행액의 {factor:.0%}인 {fc.won(cap)}까지"
        )
        rules.append(applied("LRN-001", detail))
        if cap < limit:
            limit = cap
            limits.append(detail)

    # 고객 반응: 걸린 책이 있으면 요청당 한 번만 한도에 곱한다. 못 읽었거나 판매가 적은 책은 의견을
    # 바꾸지 않고 규칙 기록과 이유에 남긴다 (`CRX-001`)
    notes: list[str] = []
    if reaction.status == STATUS_NOT_RELEVANT:
        # 상품이 없는 요청: 한도·이유는 그대로 두고 "확인했는데 해당 없음"만 적용 규칙에 남긴다
        # (안 본 것과 구분된다, 태윤 11/17)
        rules.append(applied("CRX-001", "요청에 상품이 없어 반영 안 함"))
    else:
        crx = book.customer_reaction
        if reaction.status == STATUS_UNAVAILABLE:
            note = "고객 반응을 못 읽어 반영하지 않음"
            rules.append(applied("CRX-001", f"{note} ({reaction.note or '이유 모름'})"))
            notes.append(note)
        else:
            if reaction.status == STATUS_FLAGGED:
                cap = fc.rate_amount(requested, crx.limit_factor)
                names = "; ".join(flag_text(f) for f in reaction.flagged)
                detail = (
                    f"{names} → 요청 금액의 {crx.limit_factor:.0%}인 {fc.won(cap)}까지 "
                    "(걸린 책이 여럿이어도 요청당 한 번)"
                )
                # 다른 규칙이 이미 한도를 더 낮췄으면(cap >= limit) 한도와 이유(limits)는
                # 바꾸지 않는다. 걸린 책은 아래 `CRX-001` 적용 규칙 기록(detail)에만 남는다
                # (동작은 맞고 이유에는 안 나온다)
                if cap < limit:
                    limit = cap
                    limits.append(detail)
            else:
                detail = f"걸린 책 없음 (읽은 책 {reaction.checked}권)"
            if reaction.low_sales:
                low = ", ".join(reaction.low_sales)
                notes.append(f"판매 적어 비율 참고만 (판매 {crx.min_sold_qty}권 미만): {low}")
                detail += f". 판매 적어 비율 참고만: {low}"
            if reaction.sales_unread:
                unread = ", ".join(reaction.sales_unread)
                notes.append(f"판매 수량을 못 읽어 비율 반영 안 함: {unread}")
                detail += f". 판매 수량을 못 읽어 비율 반영 안 함: {unread}"
            if reaction.unread:
                detail += f". 집계에 없는 상품: {', '.join(reaction.unread)}"
            rules.append(applied("CRX-001", detail))

    if not rejects and limit < brake.min_viable_krw:
        rejects.append(f"권장 금액 {fc.won(limit)}이 최소 {fc.won(brake.min_viable_krw)}보다 적음")
    rules.append(applied("BRK-005"))

    if rejects:
        decision, limit, reasons = "rejected", 0, rejects
        advice = (
            "재무는 이번 요청을 권하지 않습니다. 집행 여부는 요청한 쪽과 사람이 정하고, "
            "재무는 결과를 장부에 기록합니다."
        )
    elif limit < requested:
        decision, reasons = "conditional", limits
        advice = (
            f"{fc.won(limit)} 이내로 줄이기를 권합니다. 집행 여부는 요청한 쪽과 사람이 정합니다."
        )
    else:
        decision, reasons = "approved", ["모든 기준을 통과함"]
        advice = "요청 금액 그대로 써도 재무 기준에 걸리지 않습니다."
    reasons = [*reasons, *notes]  # 의견을 바꾸지 않은 참고(못 읽음·판매 적음)는 이유 끝에 덧붙인다
    return {
        "campaign_id": request["campaign_id"],
        "campaign_name": request["campaign_name"],
        "channel": channel,
        "requested_krw": requested,
        # 콘텐츠 카테고리·형식(선택). 결과에 비어 오면 이 값을 이어 쓴다
        "content_category": request.get("content_category"),
        "format": request.get("format"),
        "decision": decision,
        "approved_limit_krw": limit,
        "month": month,
        "reasons": reasons,
        "recommendation": advice,
        "applied_rules": rules,
        "cash_net_krw": cash_net_krw,
        "cash_basis": cash_basis,
        "remaining_budget_krw": remaining,
        "open_lessons": len(open_lessons),
        "customer_reaction": {
            "status": reaction.status,
            "flagged": [f.product_id for f in reaction.flagged],
            # 값은 모두 상품 ID다. 제목은 화면이 붙인다(이유 문장에는 제목이 들어간다)
            "low_sales": list(reaction.low_sales_ids),
            "sales_unread": list(reaction.sales_unread_ids),
        },
    }


def compare_with_history(
    request: dict[str, Any],
    *,
    campaigns: dict[str, dict[str, Any]],
    reviews: list[dict[str, Any]],
    lessons: list[dict[str, Any]],
    cash_net_krw: int | None,
    today: date,
    monthly_budget: dict[str, Any] | None = None,
    started: dict[str, dict[str, Any]] | None = NO_STARTS,
    cash_basis: str = "net",
) -> dict[str, Any]:
    """같은 요청을 실패 이력이 없을 때와 지금으로 판단해 나란히 보여 준다."""
    clean = {
        k: v
        for k, v in campaigns.items()
        if not (v["channel"] == request["channel"] and v["verdict"] == "bad")
    }
    common = {
        "reviews": reviews,
        "cash_net_krw": cash_net_krw,
        "cash_basis": cash_basis,
        "today": today,
        "monthly_budget": monthly_budget,
        "started": started,
    }
    before = decide(request, campaigns=clean, lessons=[], **common)
    after = decide(request, campaigns=campaigns, lessons=lessons, **common)
    return {
        "request": request,
        "before": before,
        "after": after,
        "changed": (before["decision"], before["approved_limit_krw"])
        != (after["decision"], after["approved_limit_krw"]),
    }


def budget_opinion(review: dict[str, Any]) -> dict[str, Any]:
    """권하지 않음·줄여서 권장을 총괄에 알리는 의견."""
    return {
        "opinion_id": review["review_id"].replace("bud_", "op_bud_"),
        "kind": "budget_decision",
        "campaign_id": review["campaign_id"],
        "campaign_name": review["campaign_name"],
        "channel": review["channel"],
        "spend_krw": review["requested_krw"],
        "revenue_krw": None,
        "profit_krw": None,
        "roi": None,
        "verdict": DECISION_KO[review["decision"]],
        "reasons": review["reasons"],
        "recommendation": review["recommendation"],
        "applied_rules": review["applied_rules"],
        "submitted_at": review["reviewed_at"],
    }


# ---------------------------------------------------------------- 인사이트
def _evidence(label: str, value: Any) -> dict[str, Any]:
    return {"label": label, "value": value}


def category_view_costs(campaigns: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """콘텐츠 카테고리별 조회당 제작비 (CMP-007). 7일 조회수를 잰 캠페인만 모은다.

    카테고리마다 제작비·조회수를 합해 나눈다(캠페인마다 나눈 값의 평균이 아니다).
    `latest_production_krw`는 그 카테고리 가장 최근 캠페인의 제작비로, 다음 회차 기준이 된다.
    측정 중인 캠페인은 `measuring`에 센다. 조회당 제작비가 싼 순서다.
    """
    rows: dict[str, dict[str, Any]] = {}
    for c in sorted(campaigns.values(), key=lambda c: (c["period_to"], c["campaign_id"])):
        category = c.get("content_category")
        if not category:
            continue
        row = rows.setdefault(
            category,
            {
                "content_category": category,
                "campaigns": [],
                "production_cost_krw": 0,
                "views": 0,
                "measuring": 0,
                "latest_production_krw": None,
                "format": None,
            },
        )
        if c.get("view_cost_status") == "measuring":
            row["measuring"] += 1
        if c.get("cost_per_view") is None:
            continue
        row["campaigns"].append(c["campaign_id"])
        row["production_cost_krw"] += c["production_cost_krw"]
        row["views"] += c["views"]
        row["latest_production_krw"] = c["production_cost_krw"]
        row["format"] = c.get("format") or row["format"]
    out = []
    for row in rows.values():
        views = row["views"]
        row["cost_per_view"] = round(row["production_cost_krw"] / views, 2) if views else None
        out.append(row)
    return sorted(
        out,
        key=lambda r: (r["cost_per_view"] is None, r["cost_per_view"] or 0, r["content_category"]),
    )


def budget_shift(campaigns: dict[str, dict[str, Any]]) -> dict[str, Any] | None:
    """INS-005: 조회당 제작비가 싼 카테고리로 다음 회차 제작비를 옮기자는 의견. 총액은 그대로다.

    재무는 의견만 낸다. 다음 회차 비중은 기획이 제안하고 사람이 정한다.
    """
    cfg = load_rules().insights
    measured = [r for r in category_view_costs(campaigns) if r["cost_per_view"] is not None]
    if len(measured) < cfg.shift_min_categories:
        return None
    best, worst = measured[0], measured[-1]
    cheap, dear = best["cost_per_view"], worst["cost_per_view"]
    if cheap <= 0 or dear < cheap * cfg.shift_min_ratio:
        return None
    amount = min(
        round(best["latest_production_krw"] * cfg.shift_pct), worst["latest_production_krw"]
    )
    if amount <= 0:
        return None
    ratio = round(worst["cost_per_view"] / best["cost_per_view"], 1)
    latest = max(best["campaigns"][-1], worst["campaigns"][-1])
    return {
        "insight_id": (
            f"budget_shift:{best['content_category']}:{worst['content_category']}:{latest}"
        ),
        "kind": "budget_shift",
        "severity": "info",
        "title": (
            f"{best['content_category']} 조회당 제작비가 {worst['content_category']}의 "
            f"1/{ratio}입니다"
        ),
        "message": (
            f"게시 7일 조회수 기준으로 {best['content_category']}는 조회 1회에 "
            f"{best['cost_per_view']:,}원, {worst['content_category']}는 "
            f"{worst['cost_per_view']:,}원이 들었습니다. 다음 회차 제작비를 옮기면 "
            "같은 돈으로 조회수를 더 얻을 수 있습니다. 총 제작비는 늘리지 않습니다."
        ),
        "evidence": [
            *[
                _evidence(
                    r["content_category"],
                    f"제작비 {fc.won(r['production_cost_krw'])} ÷ 7일 조회수 "
                    f"{r['views']:,}회 = 조회당 {r['cost_per_view']:,}원",
                )
                for r in measured
            ],
            _evidence("옮길 금액", fc.won(amount)),
        ],
        "recommendation": (
            f"다음 회차 {best['content_category']} 제작비를 "
            f"{fc.won(best['latest_production_krw'])} → "
            f"{fc.won(best['latest_production_krw'] + amount)}로 올리고, "
            f"{worst['content_category']}를 {fc.won(worst['latest_production_krw'])} → "
            f"{fc.won(worst['latest_production_krw'] - amount)}로 줄이는 것을 기획·마케팅과 "
            "논의하세요."
        ),
        "categories": measured,
        "shift": {
            "from": worst["content_category"],
            "to": best["content_category"],
            "amount_krw": amount,
            "ratio": ratio,
        },
        "applied_rules": [
            applied("CMP-007"),
            applied(
                "INS-005",
                f"{ratio}배 차이 ≥ {cfg.shift_min_ratio}배 → 제작비 {cfg.shift_pct:.0%} 이동",
            ),
        ],
    }


def category_budget_advice(
    campaigns: dict[str, dict[str, Any]], category: str | None
) -> dict[str, Any]:
    """기획이 물은 카테고리의 다음 회차 제작비 의견 (`category_budget_advice`, CMP-007·INS-005).

    재배분 의견(budget_shift)이 있으면 늘릴 쪽은 더하고 줄일 쪽은 빼고, 없으면 최근 회차 그대로다.
    7일 조회수를 잰 캠페인이 없으면 권장값을 내지 않는다(지어내지 않는다).
    """
    rows = category_view_costs(campaigns)
    shift = budget_shift(campaigns)
    move = shift["shift"] if shift else None

    def recommend(row: dict[str, Any]) -> int | None:
        base = row["latest_production_krw"]
        if base is None:
            return None
        if move and row["content_category"] == move["to"]:
            return base + move["amount_krw"]
        if move and row["content_category"] == move["from"]:
            return base - move["amount_krw"]
        return base

    out_rows = [
        {
            "content_category": r["content_category"],
            "measured_campaigns": len(r["campaigns"]),
            "measuring": r["measuring"],
            "cost_per_view": r["cost_per_view"],
            "latest_production_krw": r["latest_production_krw"],
            "recommended_production_krw": recommend(r),
        }
        for r in rows
    ]
    mine = next((r for r in out_rows if r["content_category"] == category), None)
    if category is None:
        status = "advice" if any(r["cost_per_view"] is not None for r in out_rows) else "no_data"
    elif mine is None:
        status = "no_data"
    elif mine["cost_per_view"] is None:
        status = "measuring"
    else:
        status = "advice"
    if category is None:
        no_gap = "카테고리 사이 제작비를 옮길 만큼 차이가 없습니다."
        summary = shift["recommendation"] if shift else no_gap
    elif status == "no_data":
        summary = (
            f"'{category}' 카테고리는 재무에 기록된 캠페인이 없어 제작비 의견을 낼 수 없습니다."
        )
    elif status == "measuring":
        summary = (
            f"'{category}' 카테고리는 게시 7일 조회수를 재는 중이라 조회당 제작비가 아직 없습니다."
        )
    else:
        rec = mine["recommended_production_krw"]
        base = mine["latest_production_krw"]
        why = (
            f" ({move['from']}보다 조회당 제작비가 {move['ratio']}배 싸서 늘림)"
            if move and category == move["to"]
            else f" ({move['to']}보다 조회당 제작비가 {move['ratio']}배 비싸서 줄임)"
            if move and category == move["from"]
            else ""
        )
        summary = (
            f"{category}: 조회 1회에 {mine['cost_per_view']:,}원. "
            f"다음 회차 제작비 {fc.won(base)} → {fc.won(rec)} 권장{why}."
        )
    return {
        "status": status,
        "content_category": category,
        "recommended_production_krw": mine["recommended_production_krw"] if mine else None,
        "rows": out_rows,
        "shift": (
            {
                "from_category": move["from"],
                "to_category": move["to"],
                "amount_krw": move["amount_krw"],
                "ratio": move["ratio"],
            }
            if move
            else None
        ),
        "summary": summary,
        "applied_rules": ["CMP-007", "INS-005"] if move else ["CMP-007"],
    }


def insights(
    campaigns: dict[str, dict[str, Any]],
    reviews: list[dict[str, Any]],
    tax_pack: dict[str, Any] | None,
    today: date,
) -> list[dict[str, Any]]:
    """사람이 놓치기 쉬운 패턴. 인사이트마다 근거 숫자를 함께 낸다. 데이터가 없으면 내지 않는다."""
    cfg = load_rules().insights
    found: list[dict[str, Any]] = []
    judged = sorted(
        (c for c in campaigns.values() if c["verdict"] is not None), key=lambda c: c["period_to"]
    )
    by_channel: dict[str, list[dict[str, Any]]] = {}
    for row in judged:
        by_channel.setdefault(row["channel"], []).append(row)

    for channel, rows in by_channel.items():
        recent = [r for r in rows if r["roi"] is not None][-cfg.trend_min_campaigns :]
        if len(recent) == cfg.trend_min_campaigns and all(
            a["roi"] > b["roi"] for a, b in zip(recent, recent[1:], strict=False)
        ):
            found.append(
                {
                    "insight_id": f"channel_decline:{channel}:{recent[-1]['campaign_id']}",
                    "kind": "channel_decline",
                    "severity": "warn",
                    "title": f"{channel} 채널 효율이 계속 떨어집니다",
                    "message": (
                        f"최근 캠페인 {len(recent)}개의 ROI가 연속으로 내려갔습니다. "
                        "각 캠페인은 따로 보면 놓치기 쉽습니다."
                    ),
                    "evidence": [_evidence(r["campaign_name"], _pct(r["roi"])) for r in recent],
                    "recommendation": f"{channel} 다음 집행 전에 소재와 타깃을 먼저 점검하세요.",
                    "applied_rules": [applied("INS-001")],
                }
            )
        spend = sum(r["spend_krw"] for r in rows)
        gmv = sum(r["gmv_krw"] for r in rows)
        be = rows[-1]["break_even_roas"]
        if spend and be is not None and gmv / spend < be:
            found.append(
                {
                    "insight_id": f"below_break_even:{channel}:{rows[-1]['campaign_id']}",
                    "kind": "below_break_even",
                    "severity": "warn",
                    "title": f"{channel} 채널 ROAS가 손익분기보다 낮습니다",
                    "message": (
                        "판매액만 보면 성과가 좋아 보여도 회사 몫은 수수료뿐입니다. "
                        "PG 수수료까지 빼면 남는 비율이 작아 손익분기 ROAS가 높습니다."
                    ),
                    "evidence": [
                        _evidence("누적 광고비", fc.won(spend)),
                        _evidence("누적 증분 판매액", fc.won(gmv)),
                        _evidence("실제 ROAS", f"{gmv / spend:.2f}배"),
                        _evidence("손익분기 ROAS", f"{be}배"),
                    ],
                    "recommendation": "재구매 효과를 잴 수 없다면 이 채널의 예산을 늘리지 마세요.",
                    "applied_rules": [applied("INS-002", f"손익분기 ROAS {be}배")],
                }
            )

    since = (today - timedelta(days=cfg.overrun_window_days)).isoformat()
    limit_of: dict[str, int] = {}
    for review in reviews:
        if review["decision"] != "rejected":
            limit_of[review["campaign_id"]] = review["approved_limit_krw"]
    overspent = [
        c
        for c in judged
        if c["period_to"] >= since
        and c["campaign_id"] in limit_of
        and c["spend_krw"] > limit_of[c["campaign_id"]]
    ]
    over_budget = [
        r
        for r in reviews
        if r["reviewed_at"][:10] >= since and r["requested_krw"] > r["remaining_budget_krw"]
    ]
    if len(overspent) + len(over_budget) >= cfg.overrun_min_count:
        found.append(
            {
                "insight_id": f"repeat_overrun:{today.isoformat()[:7]}",
                "kind": "repeat_overrun",
                "severity": "warn",
                "title": "예산 초과가 반복됩니다",
                "message": (
                    f"최근 {cfg.overrun_window_days}일 동안 재무 권장 금액이나 남은 예산을 "
                    "넘는 일이 반복됐습니다."
                ),
                "evidence": [
                    *[
                        _evidence(
                            c["campaign_name"],
                            f"권장 {fc.won(limit_of[c['campaign_id']])}, "
                            f"실제 {fc.won(c['spend_krw'])}",
                        )
                        for c in overspent
                    ],
                    *[
                        _evidence(
                            r["campaign_name"],
                            f"요청 {fc.won(r['requested_krw'])}, "
                            f"남은 예산 {fc.won(r['remaining_budget_krw'])}",
                        )
                        for r in over_budget
                    ],
                ],
                "recommendation": "월 예산이 현실에 맞는지 다시 정하거나 캠페인 수를 줄이세요.",
                "applied_rules": [applied("INS-003")],
            }
        )

    shift = budget_shift(campaigns)
    if shift is not None:
        found.append(shift)

    if tax_pack is not None:
        due = date.fromisoformat(tax_pack["filing"]["due"])
        days = (due - today).days
        if 0 <= days <= cfg.tax_deadline_warn_days:
            found.append(
                {
                    "insight_id": f"tax_deadline:{tax_pack['filing']['due']}",
                    "kind": "tax_deadline",
                    "severity": "info",
                    "title": f"부가세 {tax_pack['filing']['kind']} 기한이 {days}일 남았습니다",
                    "message": "세무사 확인 전인 설정이 있으면 낼 세액이 달라질 수 있습니다.",
                    "evidence": [
                        _evidence("기한", tax_pack["filing"]["due"]),
                        _evidence("낼 세액 (참고)", tax_pack.get("payable")),
                        *[_evidence("확인 필요", c) for c in tax_pack.get("checks", [])],
                    ],
                    "recommendation": "세무사에게 자료를 보내고 확인 필요 항목을 정리하세요.",
                    "applied_rules": [applied("INS-004"), applied("TAX-001")],
                }
            )
    return found
