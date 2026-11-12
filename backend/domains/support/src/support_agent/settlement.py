"""판매자 정산 (고객지원): 판매자별 정산 내역서 · 보류 사유 · 지급 기한 점검.

2026-11-12 팀장 결정으로 재무에서 옮겨 왔다. 판매자 정산은 고객지원이 하고, 재무는 결과를 받아
화면에 보여 주고 회계(미지급금 롤포워드·현금 흐름·은행 출금 대조)에만 쓴다.

정산금 `payout` = 판매액 `gross` − 판매 수수료 `commission` − 판매자 부담 쿠폰 `seller_coupon`
− 환불 `refunds`. 기간은 주 단위(월~일), 기본은 지난주.

- 판매액·수수료: 기간 안에 **구매가 확정된**(배송 완료) 주문의 그 판매자 품목
  (수수료는 카테고리 수수료율, 품목별 반올림). 오픈마켓처럼 구매 확정일 기준으로 정산한다
- 판매자 부담 쿠폰: 기간 안에 발급된 `funded_by="seller"` 쿠폰
- 환불: 고객지원이 처리한 환불(`refund_request` → 판매 서버)을 판매 서버 주문에서 읽는다.
  환불 시각과 그 주문의 구매 확정 시각 중 **늦은 쪽**이 기간 안이면 뺀다.
  확정된(판매액을 이미 정산한) 주문의 환불은 환불한 주에 빼고(역정산),
  확정 전 환불은 그 주문이 확정되는 주에 판매액과 같이 뺀다.
  끝내 확정되지 않은 주문(배송 전 취소 등)의 환불은 판매자가 받은 돈이 없으니 빼지 않는다
- 구매 확정 대기(`pending_confirmation`): 기간 끝 전에 팔렸지만 아직 확정되지 않은 주문.
  이번 정산에 넣지 않고, 확정되는 주의 정산으로 넘어간다(보류 사유가 아니다)

보류 사유 (있으면 `held`, 정산 묶음에서 빠진다):
- `negative_payout`: 정산금이 0원 이하
- `no_confirmed_orders`: 이번 주에 확정된 주문이 없고 확정 대기 주문만 있다(줄 돈이 없다)

지급은 고객지원도 확정하지 않는다. 묶음을 `execute("confirm_settlement")`로 요청하면 안전 관문이
항상 C로 보류하고 사람이 승인한다(`settlement_batches.py`).
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from typing import Any

from support_agent import settlement_config as sc
from support_agent.settlement_books import SettlementBooks, in_range, item_gross

MAX_PRODUCTS = 20  # 판매자 한 곳에 보여 줄 상품 수


def _qty(item: dict[str, Any]) -> int:
    q = item.get("qty")
    return q if isinstance(q, int) and not isinstance(q, bool) else 0


def _refund_settles_at(refund: dict[str, Any], confirmed_at: dict[str, Any]) -> Any:
    """환불을 정산에서 빼는 시각: 환불 시각과 주문 구매 확정 시각 중 늦은 쪽.

    주문이 확정되지 않았으면 None(빼지 않는다). 주문을 모르는 환불(예전 자료)은 환불 시각.
    """
    at = refund.get("created_at")
    order_id = refund.get("order_id")
    if order_id not in confirmed_at:
        return at
    done = confirmed_at[order_id]
    if done is None or at is None:
        return None
    return max(at, done)


def _sellers(books: SettlementBooks) -> list[Any]:
    """정산서를 만들 판매자: 판매자 목록 순서, 그 뒤에 목록에 없는 품목 판매자(주문 순서)."""

    def make() -> list[Any]:
        sellers = [s["seller_id"] for s in books.sellers if s.get("seller_id")]
        known = set(sellers)
        for o in books.orders:  # 판매자 목록에 없는 판매자의 품목도 빠뜨리지 않는다
            for i in o["items"]:
                sid = i.get("seller_id")
                if sid and sid not in known:
                    known.add(sid)
                    sellers.append(sid)
        return sellers

    return books.memo("sellers", make)


def _confirmed_at(books: SettlementBooks) -> dict[str, Any]:
    return books.memo(
        "confirmed_at", lambda: {o["order_id"]: o.get("delivered_at") for o in books.orders}
    )


def _refunds_settling(books: SettlementBooks) -> list[tuple[Any, dict[str, Any]]]:
    """환불마다 정산에서 빼는 시각을 장부마다 한 번만 계산해 둔다."""

    def make() -> list[tuple[Any, dict[str, Any]]]:
        confirmed_at = _confirmed_at(books)
        return [(_refund_settles_at(r, confirmed_at), r) for r in books.refunds]

    return books.memo("refunds_settling", make)


def warm(books: SettlementBooks) -> None:
    """장부를 새로 모은 직후 한 번: 정렬·판매자 목록·환불 시각을 미리 계산해 첫 조회가 덜
    기다리게."""
    books.sold()
    books.confirmed()
    books.unconfirmed_until(datetime.max.replace(tzinfo=UTC))
    _sellers(books)
    _refunds_settling(books)
    cached_statements(books, None)  # 화면이 처음 여는 지난주


def week_key(period: str | None, today: date | None = None) -> tuple[str, str]:
    """같은 주를 가리키는 요청이 같은 계산 결과를 쓰게 하는 열쇠 (주 이름, 오늘)."""
    today = today or sc.today()
    name, _, _ = sc.week_range(period, today)
    return name, today.isoformat()


def cached_statements(
    books: SettlementBooks, period: str | None, today: date | None = None
) -> dict[str, Any]:
    """같은 장부·같은 주면 한 번 계산한 정산서를 다시 쓴다(실제 장부만). 받은 쪽은 결과를 고치지
    않는다."""
    today = today or sc.today()
    return books.memo(
        ("statements", *week_key(period, today)), lambda: statements(books, period, today)
    )


def statements(
    books: SettlementBooks,
    period: str | None,
    today: date | None = None,
) -> dict[str, Any]:
    """판매자별 정산서. 확정 주문·대기 주문·환불·쿠폰을 한 번씩만 훑어 판매자별로 모은다.

    판매자마다 주문 전체를 다시 훑던 계산(판매자 200곳 × 주문 30만 건에서 8.8초)을 바꿨다.
    공식은 그대로이고, 판매자별로 같은 주문 순서로 더한다.
    """
    cfg = sc.load_policy()
    today = today or sc.today()
    name, monday, sunday = sc.week_range(period, today)
    start, end = sc.day_start(monday), sc.day_start(sunday + timedelta(days=1))

    sellers = _sellers(books)
    seller_set = set(sellers)
    sold = books.confirmed_between(start, end)
    waiting = books.unconfirmed_until(end)

    # 판매자별 판매액·수수료·주문·수수료율·상품 (확정 주문을 순서대로 한 번 훑는다)
    acc: dict[Any, dict[str, Any]] = {}
    for o in sold:
        for i in o["items"]:
            seller_id = i.get("seller_id")
            if not seller_id:
                continue
            a = acc.get(seller_id)
            if a is None:
                a = acc[seller_id] = {
                    "gross": 0,
                    "commission": 0,
                    "order_ids": set(),
                    "rates": set(),
                    "by_product": {},
                }
            a["order_ids"].add(o["order_id"])
            g = item_gross(i)
            rate = cfg.commission.rate_for(i.get("category_code"))
            a["rates"].add(rate)
            a["gross"] += g
            a["commission"] += sc.rate_amount(g, rate)
            pid = str(i.get("product_id") or "")
            if pid:
                row = a["by_product"].get(pid)
                if row is None:
                    row = a["by_product"][pid] = {
                        "product_id": pid,
                        "name": books.product_name(pid, i),
                        "qty": 0,
                        "gross": 0,
                    }
                row["qty"] += _qty(i)
                row["gross"] += g

    seller_coupon: dict[Any, int] = {}
    for c in books.coupons:
        if c.get("funded_by") == "seller" and in_range(c.get("created_at"), start, end):
            sid = c.get("seller_id")
            if sid not in seller_set:
                continue
            seller_coupon[sid] = seller_coupon.get(sid, 0) + c["total_value"]

    refunds: dict[Any, int] = {}
    for settles_at, r in _refunds_settling(books):
        if in_range(settles_at, start, end):
            sid = r.get("seller_id")
            if sid not in seller_set:
                continue
            refunds[sid] = refunds.get(sid, 0) + r["amount"]

    pending: dict[Any, tuple[int, int]] = {}  # 판매자 → (확정 대기 주문 수, 판매액)
    for o in waiting:
        mine: dict[Any, int] = {}
        for i in o["items"]:
            sid = i.get("seller_id")
            if sid:
                mine[sid] = mine.get(sid, 0) + item_gross(i)
        for sid, g in mine.items():
            n, total = pending.get(sid, (0, 0))
            pending[sid] = (n + 1, total + g)

    rows = []
    for seller_id in sellers:
        a = acc.get(seller_id)
        gross = a["gross"] if a else 0
        commission = a["commission"] if a else 0
        order_ids: set[str] = a["order_ids"] if a else set()
        rates: set[float] = set(a["rates"]) if a else set()
        by_product: dict[str, dict[str, Any]] = a["by_product"] if a else {}
        coupon = seller_coupon.get(seller_id, 0)
        refund_sum = refunds.get(seller_id, 0)
        payout = gross - commission - coupon - refund_sum
        if len(rates) == 1:
            rate_shown = rates.pop()
        elif gross:
            rate_shown = round(commission / gross, 4)
        else:
            rate_shown = cfg.commission.default_rate

        pending_orders, pending_gross = pending.get(seller_id, (0, 0))

        reasons = []
        if not order_ids and pending_orders and payout <= 0:
            reasons.append(
                {
                    "code": "no_confirmed_orders",
                    "label": f"이번 주 확정 주문 없음 (확정 대기 {pending_orders}건은 다음 정산)",
                }
            )
        if order_ids and payout <= 0:
            reasons.append({"code": "negative_payout", "label": "정산금이 0원 이하"})

        if not order_ids and not coupon and not refund_sum and not pending_orders:
            continue  # 이번 기간 거래가 없는 판매자는 내역서를 만들지 않는다
        rows.append(
            {
                "seller_id": seller_id,
                "name": books.seller_name(seller_id),
                "orders": len(order_ids),
                "gross": gross,
                "commission_rate": rate_shown,
                "commission": commission,
                "seller_coupon": coupon,
                "refunds": refund_sum,
                "payout": payout,
                "hold_reasons": reasons,
                "status": "held" if reasons else "ready",
                "review_id": None,
                # 다음 정산으로 넘어가는 구매 확정 대기 주문 (이번 정산금에 들지 않는다)
                "pending_confirmation": {"orders": pending_orders, "gross": pending_gross},
                # 이번 기간에 판 쇼핑몰 상품 (판매액 큰 순). 쇼핑몰 상품 화면과 잇는 열쇠
                "products": [
                    dict(r)
                    for r in sorted(
                        by_product.values(), key=lambda r: (-r["gross"], r["product_id"])
                    )[:MAX_PRODUCTS]
                ],
            }
        )
    pay_date = sunday + timedelta(days=cfg.settlement.pay_days_after_period_end)
    return {
        "period": name,
        "from": sc.iso(start),
        "to": sc.iso(end),
        "pay_date": pay_date.isoformat(),
        "statements": rows,
        "applied_rules": [sc.applied_rule()],
    }


def apply_batch(result: dict[str, Any], batch: dict[str, Any] | None) -> dict[str, Any]:
    """묶음 요청 상태를 내역서에 반영하고 `batch`를 붙인다."""
    empty = {
        "status": "none",
        "review_id": None,
        "execution_id": None,
        "total_payout": 0,
        "seller_ids": [],
    }
    shown = {k: batch.get(k, v) for k, v in empty.items()} if batch else empty
    if batch and shown["status"] == "rejected" and batch.get("closed_reason"):
        # 왜 끝났는지: rejected(사람이 멈춤) · expired(시간 초과)
        # · review_missing(안전 장치에 기록 없음)
        shown = shown | {"closed_reason": batch["closed_reason"]}
    if batch and shown["status"] != "none":
        for row in result["statements"]:
            if row["seller_id"] in shown["seller_ids"]:
                row["status"] = shown["status"]
                row["review_id"] = shown["review_id"]
    return result | {"batch": shown}


def ready(result: dict[str, Any]) -> list[dict[str, Any]]:
    """묶음에 넣을 판매자: 보류 사유가 없고 줄 돈이 있으며 아직 요청·승인되지 않음.

    거절된 묶음은 다시 넣는다. 확정 대기 주문만 있어 이번 정산금이 0원인 판매자는 넣지 않는다.
    """
    return [
        r
        for r in result["statements"]
        if not r["hold_reasons"] and r["status"] in ("ready", "rejected") and (r["payout"] or 0) > 0
    ]


# ---------------------------------------------------------------- 지급 기한 점검
# 오픈마켓(통신판매중개)은 판매자에게 판매 대금을 정해진 날 안에 줘야 한다. 기한(일 수)과 기산점은
# `settlement_policy.toml [settlement]`에 둔다. 경보만 내고, 지급은 정산 묶음 → 안전 장치 C 보류 →
# 사람 승인으로만 한다.
# - 기산점: 구매 확정(배송 완료 시각으로 대신). 배송 전 주문은 기한이 아직 시작되지 않았다
# - 지급 완료: 그 주 정산 묶음이 안전 장치에서 승인된 판매자 (실제 이체 결과는 연동돼 있지 않다)
# - 지급 예정일: 정산 기간 마지막 날 + `pay_days_after_period_end`. 보류 사유가 있으면 예정일이 없다
# - 상태: overdue(기한 지남) · at_risk(예정일이 없거나 기한 뒤) · due_soon(임박) · ok
STATUS_ORDER = {"overdue": 0, "at_risk": 1, "due_soon": 2, "ok": 3}
STATUS_KO = {
    "overdue": "기한 지남",
    "at_risk": "기한 넘길 위험",
    "due_soon": "기한 임박",
    "ok": "정상",
}


def deadline_report(
    books: SettlementBooks,
    batches: dict[str, dict[str, Any]],
    today: date | None = None,
    statements_of: Callable[[str], dict[str, Any]] | None = None,
) -> dict[str, Any]:
    cfg = sc.load_policy()
    s = cfg.settlement
    today = today or sc.today()
    since = today - timedelta(days=s.deadline_lookback_days)
    by_week: dict[str, dict[str, Any]] = {}  # 주 → 판매자 → 정산서 행

    def row_of(week: str, seller_id: str) -> dict[str, Any] | None:
        if week not in by_week:  # 주마다 한 번만 (같은 장부면 조회에서 계산한 정산서를 다시 쓴다)
            result = (statements_of or (lambda w: cached_statements(books, w, today)))(week)
            by_week[week] = {r["seller_id"]: r for r in result["statements"]}
        return by_week[week].get(seller_id)

    sellers: dict[str, dict[str, Any]] = {}
    waiting = 0  # 배송 전이라 기한이 아직 시작되지 않은 주문
    weeks: dict[date, tuple[str, set[str], date]] = {}  # 확정일 → (정산 주, 지급한 판매자, 지급일)
    for o in books.sold_between(sc.day_start(since), datetime.max.replace(tzinfo=UTC)):
        if o.get("delivered_at") is None:  # 구매 확정 전: 기한이 아직 시작되지 않았다
            waiting += sum(1 for item in o["items"] if item.get("seller_id"))
            continue
        # 정산은 구매 확정일이 속한 주에 한다(settlement.py)
        paid_day = o["delivered_at"].date()
        if paid_day not in weeks:
            week = sc.week_of(paid_day)
            batch = batches.get(week) or {}
            approved = batch.get("status") == "approved"
            _, _, sunday = sc.week_range(week, today)
            weeks[paid_day] = (
                week,
                set(batch.get("seller_ids") or []) if approved else set(),
                sunday + timedelta(days=s.pay_days_after_period_end),
            )
        week, paid_sellers, pay_date = weeks[paid_day]
        for item in o["items"]:
            seller_id = item.get("seller_id")
            if not seller_id or seller_id in paid_sellers:
                continue
            confirmed = o["delivered_at"].date()
            deadline = confirmed + timedelta(days=s.deadline_days)
            row = row_of(week, seller_id)
            held = row is not None and bool(row["hold_reasons"])
            expected = None if held or pay_date < today else pay_date
            if today > deadline:
                status = "overdue"
            elif expected is None or expected > deadline:
                status = "at_risk"
            elif (deadline - today).days <= s.deadline_warn_days:
                status = "due_soon"
            else:
                status = "ok"
            rate = cfg.commission.rate_for(item.get("category_code"))
            gross = item_gross(item)
            amount = gross - sc.rate_amount(gross, rate)  # 판매자 몫 (쿠폰·환불 차감 전)
            cur = sellers.setdefault(
                seller_id,
                {
                    "seller_id": seller_id,
                    "name": books.seller_name(seller_id),
                    "orders": set(),
                    "amount": 0,
                    "oldest_confirmed": confirmed,
                    "deadline": deadline,
                    "expected_pay_date": expected,
                    "status": status,
                    "hold_reasons": [],
                },
            )
            cur["orders"].add(o["order_id"])
            cur["amount"] += amount
            if deadline < cur["deadline"]:
                cur.update(
                    oldest_confirmed=confirmed, deadline=deadline, expected_pay_date=expected
                )
            if STATUS_ORDER[status] < STATUS_ORDER[cur["status"]]:
                cur["status"] = status
            if held and row is not None:
                for reason in row["hold_reasons"]:
                    if reason not in cur["hold_reasons"]:
                        cur["hold_reasons"].append(reason)

    rows = sorted(sellers.values(), key=lambda r: (STATUS_ORDER[r["status"]], r["deadline"]))
    out = [
        {
            "seller_id": r["seller_id"],
            "name": r["name"],
            "orders": len(r["orders"]),
            "amount": r["amount"],
            "oldest_confirmed": r["oldest_confirmed"].isoformat(),
            "deadline": r["deadline"].isoformat(),
            "days_left": (r["deadline"] - today).days,
            "expected_pay_date": r["expected_pay_date"].isoformat()
            if r["expected_pay_date"]
            else None,
            "status": r["status"],
            "status_label": STATUS_KO[r["status"]],
            "hold_reasons": r["hold_reasons"],
        }
        for r in rows
    ]
    counts = {k: sum(1 for r in out if r["status"] == k) for k in STATUS_ORDER}
    return {
        "today": today.isoformat(),
        "deadline_days": s.deadline_days,
        "basis": s.deadline_basis,
        "warn_days": s.deadline_warn_days,
        "since": since.isoformat(),
        "counts": counts,
        "waiting_orders": waiting,
        "sellers": out,
    }


def needs_attention(report: dict[str, Any]) -> list[dict[str, Any]]:
    """사람이 볼 판매자: 기한 지남·위험·임박."""
    return [r for r in report["sellers"] if r["status"] != "ok"]
