"""책(상품)별 클레임·문의 집계. 프레임워크에 의존하지 않는 순수 코드다 (LLM 없음).

- 묶는 열쇠는 상품 ID(`ClaimLine.product_id`)다. 제목으로 추측하지 않는다. 상품 ID가 없는 클레임
  (옛 기록·시드 주문)은 상품별로 세지 않고 `claims_without_product`로 알린다.
- 한 클레임은 상품마다 한 번 센다 (같은 상품 줄이 여럿이어도 1건).
- 기간은 클레임 접수 시각(`created_at`) 기준으로, 지금부터 거슬러 올라간다.
- 문의는 주문에만 이어져 있어서 주문 단위로 센다. 품목이 여럿인 주문의 문의는 그 주문의 모든 상품에
  세고(`inquiry_count`), 상품 하나뿐인 주문의 문의만 센 값(`inquiry_count_single_item`)도 준다.
  문의를 못 읽었으면(`inquiries=None`) 문의 칸은 None이다 (0으로 채우지 않는다).
- 판매 수량 대비 비율(`claim_rate`)은 판매 수량을 끝까지 읽었을 때만 낸다 (`sold=None`이면 None).
  비율 = 접수 기간의 클레임 수량 / 같은 기간 결제 끝난 주문의 판매 수량이다. 오래전 주문의 클레임도
  접수 기간에 들어와 1을 넘을 수 있다. 판매 수량이 0이면 비율을 낼 수 없어 None이다.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Literal

from support_agent.book_issue_schemas import (
    PERIOD_DAYS,
    BookIssueInput,
    BookIssueReport,
    BookIssueRow,
    CategoryCount,
)
from support_agent.claim_schemas import Claim, ClaimLine
from support_agent.insights import parse_time

DEFECT_REASONS = frozenset({"defect", "damaged", "wrong_item", "missing_item"})
TOP_CATEGORIES = 3


OrderStatus = Literal["ok", "unpaid", "unread", "no_product"]


@dataclass(frozen=True)
class OrderInquiry:
    """주문 하나의 문의 집계와 그 주문의 상품들. 상품은 `(상품 ID, 제목)`이다.

    `status`: `ok`(상품을 알아냄), `unpaid`(결제 전·없는 주문이라 일부러 뺌 — 결제 실패 문의는
    상품 불만이 아니다), `unread`(판매 서버에서 못 읽음·마감으로 건너뜀), `no_product`(품목에
    상품 ID가 하나도 없음).
    `products`가 비면 상품에 붙이지 않는다(`ok`인데 비어 있으면 `unpaid`로 본다).
    `has_unknown_items`: 이 주문에 상품 ID가 없는 품목도 있다 (그 품목의 몫은 셀 수 없다).
    """

    order_id: str
    count: int
    categories: Mapping[str, int]
    products: tuple[tuple[str, str], ...]
    status: OrderStatus = "ok"
    has_unknown_items: bool = False

    @property
    def attributed(self) -> bool:
        return bool(self.products)

    @property
    def incomplete(self) -> bool:
        """이 주문 때문에 상품별 문의 건수가 모자랄 수 있는가 (일부러 뺀 결제 전 주문은 아니다)."""
        return self.status in ("unread", "no_product") or self.has_unknown_items


@dataclass
class _Inquiry:
    title: str = ""
    count: int = 0
    single: int = 0
    categories: Counter[str] | None = None


def _top(categories: Counter[str]) -> list[CategoryCount]:
    ranked = sorted(categories.items(), key=lambda kv: (-kv[1], kv[0]))
    return [CategoryCount(category=c, count=n) for c, n in ranked[:TOP_CATEGORIES]]


def _paid_share(claim: Claim, mine: list[ClaimLine]) -> int:
    """클레임에서 실제 지급한 금액(`refund_paid`, 공제·배송비 반영)을 이 상품 품목 몫으로 나눈다.

    품목 환불액(`refund_cash`) 비율로 나눈다. 상품 하나뿐인 클레임이면 지급액 전부다.
    """
    total = sum(line.refund_cash for line in claim.lines)
    if total <= 0:
        return 0
    return round(claim.refund_paid * sum(line.refund_cash for line in mine) / total)


def _claim_row(product_id: str, claims: list[Claim]) -> BookIssueRow:
    by_type: Counter[str] = Counter()
    defect = 0
    claim_qty = 0
    return_qty = 0
    refund = 0
    title = ""
    for claim in sorted(claims, key=lambda c: c.created_at):
        mine = [line for line in claim.lines if line.product_id == product_id]
        title = mine[0].title  # 오래된 것부터 돌므로 마지막에 가장 최근 제목이 남는다
        by_type[claim.type] += 1
        claim_qty += sum(line.requested_qty for line in mine)
        if claim.reason in DEFECT_REASONS:
            defect += 1
        if claim.return_received_at:
            return_qty += sum(line.qty for line in mine)
        if claim.refund_paid > 0:
            refund += _paid_share(claim, mine)
    return BookIssueRow(
        product_id=product_id,
        title=title,
        claim_count=len(claims),
        claims_by_type=dict(by_type),
        defect_claim_count=defect,
        claim_qty=claim_qty,
        return_qty=return_qty,
        refund_cash_total=refund,
        reason="",
    )


def _reason(row: BookIssueRow) -> str:
    parts = []
    if row.claim_count:
        parts.append(f"클레임 {row.claim_count}건")
    if row.defect_claim_count:
        parts.append(f"불량·파손 등 {row.defect_claim_count}건")
    if row.return_qty:
        parts.append(f"반품 입고 {row.return_qty}권")
    if row.refund_cash_total:
        parts.append(f"환불 {row.refund_cash_total:,}원")
    if row.inquiry_count:
        partial = " — 일부 주문만 읽어 더 많을 수 있음" if row.inquiry_complete is False else ""
        parts.append(f"문의 {row.inquiry_count}건(주문 단위{partial})")
    if row.claim_rate is not None and row.sold_qty:
        parts.append(f"판매 {row.sold_qty}권 중 {row.claim_qty}권 클레임({row.claim_rate:.1%})")
    return ", ".join(parts) or "기록 없음"


def _spread_inquiries(
    inquiries: Iterable[OrderInquiry],
) -> tuple[dict[str, _Inquiry], int, bool]:
    """주문별 문의를 상품에 나눠 붙인다.

    돌려주는 것: (상품별 집계, 상품에 못 붙인 문의 수, 건수가 모자랄 수 있는가).
    """
    per_product: dict[str, _Inquiry] = {}
    unattributed = 0
    incomplete = False
    for order in inquiries:
        incomplete = incomplete or order.incomplete
        if not order.attributed:
            unattributed += order.count
            continue
        ids = dict(order.products)  # 같은 상품이 두 줄이어도 한 번
        for product_id, title in ids.items():
            slot = per_product.setdefault(product_id, _Inquiry(categories=Counter()))
            slot.title = title
            slot.count += order.count
            # 상품 ID 없는 품목이 같이 있는 주문은 상품 하나뿐인 주문이 아니다
            if len(ids) == 1 and not order.has_unknown_items:
                slot.single += order.count
            assert slot.categories is not None
            slot.categories.update(order.categories)
    return per_product, unattributed, incomplete


def book_issue_report(
    claims: Iterable[Claim],
    now: datetime,
    query: BookIssueInput,
    inquiries: Iterable[OrderInquiry] | None = None,
    *,
    total_inquiries: int | None = None,
    inquiries_truncated: bool = False,
    sold: Mapping[str, int] | None = None,
    sales_orders_scanned: int | None = None,
    notes: Iterable[str] = (),
) -> BookIssueReport:
    """`inquiries`가 None이면 클레임만 센다. 주면 문의 건수를 상품에 붙인다.

    `sold`는 상품 ID → 같은 기간 판매 수량이다. 주면(끝까지 읽은 것만 준다) 모든 줄에 `sold_qty`와
    `claim_rate`를 채운다. None이면 둘 다 None이다.

    `total_inquiries`는 기간 안의 전체 문의 수(문의 서비스가 센 값), `inquiries_truncated`는 문의가
    있는 주문이 상한을 넘어 잘렸는가, `notes`는 문의를 읽다가 못 읽은 값(예: 판매 서버 주문 일부를
    못 읽음)의 설명이다 — `missing`에 그대로 붙는다.

    문의를 일부 주문만 읽었으면(`inquiries_complete=False`) 문의가 없는 상품도 0이 아니라 None이다.
    """
    start = now - timedelta(days=PERIOD_DAYS[query.period])
    in_period = [c for c in claims if start <= parse_time(c.created_at) <= now]
    by_product: dict[str, list[Claim]] = {}
    without_product = 0
    for claim in in_period:
        ids = {line.product_id for line in claim.lines if line.product_id}
        if not ids:
            without_product += 1
            continue
        for product_id in ids:
            by_product.setdefault(product_id, []).append(claim)
    rows: dict[str, BookIssueRow] = {pid: _claim_row(pid, cs) for pid, cs in by_product.items()}

    inquiry_by_product: dict[str, _Inquiry] = {}
    not_attributed: int | None = None
    inquiry_orders = list(inquiries) if inquiries is not None else None
    complete: bool | None = None
    if inquiry_orders is not None:
        inquiry_by_product, not_attributed, incomplete = _spread_inquiries(inquiry_orders)
        complete = not (incomplete or inquiries_truncated)
        for product_id, slot in inquiry_by_product.items():
            row = rows.get(product_id) or BookIssueRow(
                product_id=product_id,
                title=slot.title,
                claim_count=0,
                defect_claim_count=0,
                return_qty=0,
                refund_cash_total=0,
                reason="",
            )
            rows[product_id] = row.model_copy(
                update={
                    "title": row.title or slot.title,
                    "inquiry_count": slot.count,
                    "inquiry_count_single_item": slot.single,
                    "inquiry_complete": complete,
                    "top_categories": _top(slot.categories or Counter()),
                }
            )
        # 문의를 전부 읽었는데 이 상품에 문의가 없으면 0이다.
        # 일부만 읽었으면 없다고 말할 수 없어 None이다
        for product_id, row in list(rows.items()):
            if row.inquiry_count is None:
                zero = 0 if complete else None
                rows[product_id] = row.model_copy(
                    update={
                        "inquiry_count": zero,
                        "inquiry_count_single_item": zero,
                        "inquiry_complete": complete,
                    }
                )

    no_sales_rate = 0
    if sold is not None:
        for product_id, row in list(rows.items()):
            sold_qty = sold.get(product_id, 0)
            rate = round(row.claim_qty / sold_qty, 3) if sold_qty > 0 else None
            if rate is None and row.claim_qty:
                no_sales_rate += 1
            rows[product_id] = row.model_copy(update={"sold_qty": sold_qty, "claim_rate": rate})

    wanted = set(query.product_ids) if query.product_ids else None
    shown = [
        row.model_copy(update={"reason": _reason(row)})
        for row in rows.values()
        if (wanted is None or row.product_id in wanted)
        and (row.claim_count >= query.min_claims or (row.inquiry_count or 0) >= query.min_inquiries)
    ]
    shown.sort(
        key=lambda r: (-r.claim_count, -r.defect_claim_count, -(r.inquiry_count or 0), r.product_id)
    )

    missing = []
    if sold is None:
        missing.append("판매 수량을 읽지 않아 클레임 비율(claim_rate)은 내지 않았다")
    else:
        missing.append(
            "클레임 비율은 접수 기간의 클레임 수량을 같은 기간 판매 수량으로 나눈 값이라 "
            "오래전 주문의 클레임도 들어가 1을 넘을 수 있다"
        )
        if no_sales_rate:
            missing.append(f"기간 안 판매가 없어 비율을 내지 못한 상품 {no_sales_rate}개")
    if without_product:
        missing.append(f"상품 ID가 없어 상품별로 세지 못한 클레임 {without_product}건")
    if inquiry_orders is None:
        missing.append("문의 건수를 읽지 않았다")
    else:
        excluded = sum(o.count for o in inquiry_orders if not o.attributed and not o.incomplete)
        lost = sum(o.count for o in inquiry_orders if not o.attributed and o.incomplete)
        unknown_orders = sum(1 for o in inquiry_orders if o.attributed and o.has_unknown_items)
        if excluded:
            missing.append(
                f"결제 전이거나 없는 주문의 문의 {excluded}건은 "
                "상품 불만이 아니라서 상품에 붙이지 않았다"
            )
        if lost:
            missing.append(
                f"읽지 못했거나 상품 ID가 없는 주문이라 상품에 붙이지 못한 문의 {lost}건 "
                "(상품별 문의 건수가 모자랄 수 있다)"
            )
        if unknown_orders:
            missing.append(
                f"상품 ID가 없는 품목이 든 주문 {unknown_orders}개: 그 품목 몫의 문의는 셀 수 없다"
            )
        if complete is False:
            missing.append(
                "문의를 일부 주문만 읽어서 문의가 없는 상품도 0이라고 하지 않고 비워 둔다"
            )
    missing.extend(notes)
    return BookIssueReport(
        period=query.period,
        period_start=start.strftime("%Y-%m-%dT%H:%M:%SZ"),
        period_end=now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        items=shown[: query.limit],
        total_claims=len(in_period),
        claims_without_product=without_product,
        sales_read=sold is not None,
        sales_orders_scanned=sales_orders_scanned if sold is not None else None,
        inquiries_read=inquiry_orders is not None,
        inquiries_complete=complete,
        total_inquiries=total_inquiries if inquiry_orders is not None else None,
        inquiries_not_attributed=not_attributed,
        missing=missing,
    )
