"""③ 판매자 정산: 판매자별 정산 내역서와 보류 사유.

정산금 `payout` = 판매액 `gross` − 판매 수수료 `commission` − 판매자 부담 쿠폰 `seller_coupon`
− 환불 `refunds`. 기간은 주 단위(월~일), 기본은 지난주.

- 판매액·수수료: 기간 안에 **구매가 확정된**(배송 완료) 주문의 그 판매자 품목
  (수수료는 카테고리 수수료율, 품목별 반올림). 오픈마켓처럼 구매 확정일 기준으로 정산한다
- 판매자 부담 쿠폰: 기간 안에 발급된 `funded_by="seller"` 쿠폰
- 환불: 그 판매자 몫 환불을, 환불 시각과 그 주문의 구매 확정 시각 중 **늦은 쪽**이 기간 안이면 뺀다.
  확정된(판매액을 이미 정산한) 주문의 환불은 환불한 주에 빼고(역정산),
  확정 전 환불은 그 주문이 확정되는 주에 판매액과 같이 뺀다.
  끝내 확정되지 않은 주문(배송 전 취소 등)의 환불은 판매자가 받은 돈이 없으니 빼지 않는다.
  고객지원이 환불하면(`refund_request` → shop-api) 재무는 그 결과를 shop-api 주문에서 읽어 반영한다
- 구매 확정 대기(`pending_confirmation`): 기간 끝 전에 팔렸지만 아직 확정되지 않은 주문.
  이번 정산에 넣지 않고, 확정되는 주의 정산으로 넘어간다(보류 사유가 아니다)

보류 사유 (있으면 `held`, 정산 묶음에서 빠진다):
- `negative_payout`: 정산금이 0원 이하
- `no_confirmed_orders`: 이번 주에 확정된 주문이 없고 확정 대기 주문만 있다(줄 돈이 없다)

예전에는 확정되지 않은 주문이 한 건만 있어도 그 판매자의 한 주 정산 전체를 보류했다
(`unconfirmed_purchase`). 주문이 계속 들어오면 판매자가 영영 지급받지 못해서 바꿨다.

내역서마다 이번 기간에 판 쇼핑몰 상품(`products`: 상품 번호·이름·수량·판매액)을 붙인다.
판매자 번호(`seller_id`)와 상품 번호(`product_id`)는 쇼핑몰(shop-api)의 것 그대로다.

지급 확정은 재무가 하지 않는다. 묶음을 `execute("confirm_settlement")`로 요청하면 안전 관문이
항상 C로 보류하고 사람이 승인한다 (`settlement_batch.py`의 API 쪽).
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from finance_agent import finance_config as fc
from finance_agent.books import Books, in_range, item_gross
from finance_agent.rules import applied_many

MAX_PRODUCTS = 20  # 판매자 한 곳에 보여 줄 상품 수


def _qty(item: dict[str, Any]) -> int:
    q = item.get("qty")
    return q if isinstance(q, int) and not isinstance(q, bool) else 0


def _paid_at(order: dict[str, Any]):
    times = [p["created_at"] for p in order["payments"] if p.get("status") == "succeeded"]
    return min(times) if times else None


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


def statements(
    books: Books,
    period: str | None,
    today: date | None = None,
) -> dict[str, Any]:
    cfg = fc.load_config()
    today = today or fc.today()
    name, monday, sunday = fc.week_range(period, today)
    start, end = fc.day_start(monday), fc.day_start(sunday + timedelta(days=1))

    sellers = [s["seller_id"] for s in books.sellers if s.get("seller_id")]
    for o in books.orders:  # 판매자 목록에 없는 판매자의 품목도 빠뜨리지 않는다
        for i in o["items"]:
            if i.get("seller_id") and i["seller_id"] not in sellers:
                sellers.append(i["seller_id"])

    sold = books.confirmed_between(start, end)
    waiting = books.unconfirmed_until(end)
    confirmed_at = {o["order_id"]: o.get("delivered_at") for o in books.orders}
    rows = []
    for seller_id in sellers:
        gross = commission = 0
        order_ids: set[str] = set()
        rates: set[float] = set()
        by_product: dict[str, dict[str, Any]] = {}
        for o in sold:
            mine = [i for i in o["items"] if i.get("seller_id") == seller_id]
            if not mine:
                continue
            order_ids.add(o["order_id"])
            for i in mine:
                g = item_gross(i)
                rate = cfg.commission.rate_for(i.get("category_code"))
                rates.add(rate)
                gross += g
                commission += fc.rate_amount(g, rate)
                pid = str(i.get("product_id") or "")
                if pid:
                    row = by_product.setdefault(
                        pid,
                        {
                            "product_id": pid,
                            "name": books.product_name(pid, i),
                            "qty": 0,
                            "gross": 0,
                        },
                    )
                    row["qty"] += _qty(i)
                    row["gross"] += g
        seller_coupon = sum(
            c["total_value"]
            for c in books.coupons
            if c.get("funded_by") == "seller"
            and c.get("seller_id") == seller_id
            and in_range(c.get("created_at"), start, end)
        )
        refunds = sum(
            r["amount"]
            for r in books.refunds
            if r.get("seller_id") == seller_id
            and in_range(_refund_settles_at(r, confirmed_at), start, end)
        )
        payout = gross - commission - seller_coupon - refunds
        if len(rates) == 1:
            rate_shown = rates.pop()
        elif gross:
            rate_shown = round(commission / gross, 4)
        else:
            rate_shown = cfg.commission.default_rate

        pending_orders, pending_gross = 0, 0
        for o in waiting:
            mine = [i for i in o["items"] if i.get("seller_id") == seller_id]
            if mine:
                pending_orders += 1
                pending_gross += sum(item_gross(i) for i in mine)

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

        if not order_ids and not seller_coupon and not refunds and not pending_orders:
            continue  # 이번 기간 거래가 없는 판매자는 내역서를 만들지 않는다
        rows.append(
            {
                "seller_id": seller_id,
                "name": books.seller_name(seller_id),
                "orders": len(order_ids),
                "gross": gross,
                "commission_rate": rate_shown,
                "commission": commission,
                "seller_coupon": seller_coupon,
                "refunds": refunds,
                "payout": payout,
                "hold_reasons": reasons,
                "status": "held" if reasons else "ready",
                "review_id": None,
                # 다음 정산으로 넘어가는 구매 확정 대기 주문 (이번 정산금에 들지 않는다)
                "pending_confirmation": {"orders": pending_orders, "gross": pending_gross},
                # 이번 기간에 판 쇼핑몰 상품 (판매액 큰 순). 쇼핑몰 상품 화면과 잇는 열쇠
                "products": sorted(
                    by_product.values(), key=lambda r: (-r["gross"], r["product_id"])
                )[:MAX_PRODUCTS],
            }
        )
    pay_date = sunday + timedelta(days=cfg.settlement.pay_days_after_period_end)
    return {
        "period": name,
        "from": fc.iso(start),
        "to": fc.iso(end),
        "pay_date": pay_date.isoformat(),
        "statements": rows,
        "applied_rules": applied_many("SET-001"),
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
