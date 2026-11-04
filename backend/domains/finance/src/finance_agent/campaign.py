"""캠페인 재무: 손익 기록, 예산 브레이크, 실패 교훈, 인사이트. 숫자와 판단은 전부 코드가 한다.

- 손익: 캣북스는 중개업이라 매출은 판매액이 아니라 수수료다 (`CMP-001`)
- 브레이크: 캠페인 예산만 판단한다. 쿠폰 발급과 환불은 대상이 아니다 (`BRK-005`).
  재무는 끝난 쿠폰 금액을 받아 비용에 적을 뿐이다
- 교훈: 적자 캠페인이 다음 예산 한도를 줄이고, 효율 좋음이 나오면 풀린다 (`LRN-*`)
- 값은 `finance_rules.toml`. 판단 결과마다 적용 규칙과 근거를 함께 낸다
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any, Literal

from finance_agent import finance_config as fc
from finance_agent import settlement
from finance_agent.books import Books, item_gross, order_sold
from finance_agent.rules import applied, load_rules

Verdict = Literal["good", "neutral", "bad"]
VERDICT_KO = {"good": "좋음", "neutral": "보통", "bad": "나쁨"}
DECISION_KO = {"approved": "승인", "conditional": "조건부 승인", "rejected": "거절"}


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
        paid = settlement._paid_at(order)
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


def compute_result(data: dict[str, Any], repeat: dict[str, Any] | None = None) -> dict[str, Any]:
    """캠페인 결과 한 건. 판매액을 모르면 0으로 채우지 않고 판단하지 않는다."""
    cfg = fc.load_config()
    book = load_rules().campaign
    spend, coupon, gmv = data["spend_krw"], data.get("coupon_krw") or 0, data.get("gmv_krw")
    record = {
        **data,
        "coupon_krw": coupon,
        "commission_rate": cfg.commission.default_rate,
        "pg_fee_rate": cfg.pg.fee_rate,
        "break_even_roas": break_even_roas(),
        "expense_classes": {"spend_krw": "광고선전비", "coupon_krw": "매출 차감"},
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
    cost = spend + coupon + pg_fee
    profit = revenue - cost
    invested = spend + coupon
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
                f"광고비 {fc.won(spend)} + 쿠폰 {fc.won(coupon)} + PG {fc.won(pg_fee)} "
                f"= {fc.won(cost)}",
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
def fallback_budget() -> dict[str, Any]:
    return {
        "amount_krw": load_rules().brake.monthly_budget_krw,
        "basis": "직전 달 자료가 없어 임시값",
        "provisional": True,
    }


def budget_from_last_month(books: Books, today: date) -> dict[str, Any]:
    """월 예산 = 직전 달 영업 결과 × 비율. 자료가 없으면 임시값, 적자면 0원."""
    from finance_agent import closing  # closing이 LLM 등을 읽어 여기서만 부른다

    ratio = load_rules().brake.budget_ratio_of_last_month
    this_month = today.replace(day=1)
    last_month = (this_month - timedelta(days=1)).replace(day=1)
    if closing.unknown_codes(books) & {"commission_revenue", "pg_fee"}:
        return fallback_budget()
    totals = closing.compute_totals(books, fc.day_start(last_month), fc.day_start(this_month))
    if totals.order_count == 0:
        return fallback_budget()
    return {
        "amount_krw": max(fc.rate_amount(totals.net_result, ratio), 0),
        "basis": f"{last_month.isoformat()[:7]} 영업 결과 {fc.won(totals.net_result)}의 "
        f"{ratio:.0%}",
        "provisional": False,
    }


def committed_this_month(
    campaign_id: str,
    month: str,
    campaigns: dict[str, dict[str, Any]],
    reviews: list[dict[str, Any]],
) -> int:
    """이달 쓴 돈 + 관문을 통과해 시작했지만 결과가 아직 없는 돈. 다시 묻는 같은 캠페인은 뺀다.

    판단만 받고 시작하지 않은 건, 사람이 거절한 건은 예산을 잡지 않는다.
    """
    total = sum(
        c["spend_krw"]
        for c in campaigns.values()
        if _month(c["period_to"]) == month and c["campaign_id"] != campaign_id
    )
    latest: dict[str, dict[str, Any]] = {}
    for review in reviews:
        if review["month"] == month and review.get("started_krw"):
            latest[review["campaign_id"]] = review
    for cid, review in latest.items():
        if cid != campaign_id and cid not in campaigns and review.get("started_krw"):
            total += review["started_krw"]
    return total


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
) -> dict[str, Any]:
    """승인·조건부·거절. 거절 사유가 있어도 모든 기준의 확인 결과를 근거로 남긴다."""
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
            limits.append(f"{detail}. 최소 금액 {fc.won(limit)}까지만 승인")
    elif cash_net_krw <= 0:
        detail = f"앞으로 {weeks}주 누적 순현금 {fc.won(cash_net_krw)}"
        rejects.append(detail)
    else:
        detail = f"앞으로 {weeks}주 누적 순현금 {fc.won(cash_net_krw)}"
        if cash_net_krw < limit:
            limit = cash_net_krw
            limits.append(f"{detail}까지만 승인")
    rules.append(applied("BRK-004", detail))

    # 남은 예산
    committed = committed_this_month(request["campaign_id"], month, campaigns, reviews)
    budget = monthly_budget or fallback_budget()
    remaining = max(budget["amount_krw"] - committed, 0)
    detail = (
        f"이달 예산 {fc.won(budget['amount_krw'])} ({budget['basis']}) "
        f"중 남은 금액 {fc.won(remaining)}"
    )
    rules.append(applied("BRK-001", detail))
    budget_limited = remaining < limit
    if remaining < brake.min_viable_krw:
        rejects.append(f"{detail}, 최소 {fc.won(brake.min_viable_krw)}보다 적음")
    elif budget_limited:
        limit = remaining
        limits.append(f"{detail}까지만 승인")

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

    if not rejects and limit < brake.min_viable_krw:
        rejects.append(
            f"승인 가능 금액 {fc.won(limit)}이 최소 {fc.won(brake.min_viable_krw)}보다 적음"
        )
    rules.append(applied("BRK-005"))

    if rejects:
        decision, limit, reasons = "rejected", 0, rejects
        advice = "이번 요청은 집행하지 않습니다. 필요하면 재무 담당이 기준을 고쳐야 합니다."
    elif limit < requested:
        decision, reasons = "conditional", limits
        advice = f"{fc.won(limit)} 이내로 줄여서 집행하세요."
    else:
        decision, reasons = "approved", ["모든 기준을 통과함"]
        advice = "요청 금액 그대로 집행할 수 있습니다."
    return {
        "campaign_id": request["campaign_id"],
        "campaign_name": request["campaign_name"],
        "channel": channel,
        "requested_krw": requested,
        "decision": decision,
        "approved_limit_krw": limit,
        "month": month,
        "reasons": reasons,
        "recommendation": advice,
        "applied_rules": rules,
        "cash_net_krw": cash_net_krw,
        "remaining_budget_krw": remaining,
        "open_lessons": len(open_lessons),
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
        "today": today,
        "monthly_budget": monthly_budget,
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
    """거절·조건부 승인을 총괄에 알리는 의견."""
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
                    f"최근 {cfg.overrun_window_days}일 동안 승인 한도나 남은 예산을 "
                    "넘는 일이 반복됐습니다."
                ),
                "evidence": [
                    *[
                        _evidence(
                            c["campaign_name"],
                            f"승인 {fc.won(limit_of[c['campaign_id']])}, "
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
