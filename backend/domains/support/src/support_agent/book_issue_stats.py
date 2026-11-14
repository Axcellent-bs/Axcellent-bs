"""책(상품)별 클레임·문의 집계. 프레임워크에 의존하지 않는 순수 코드다 (LLM 없음).

- 묶는 열쇠는 상품 ID(`ClaimLine.product_id`)다. 제목으로 추측하지 않는다. 상품 ID가 없는 클레임
  (옛 기록·시드 주문)은 상품별로 세지 않고 `claims_without_product`로 알린다.
- 한 클레임은 상품마다 한 번 센다 (같은 상품 줄이 여럿이어도 1건).
- 기간은 클레임 접수 시각(`created_at`) 기준으로, 지금부터 거슬러 올라간다.
- 문의는 주문에만 이어져 있어서 주문 단위로 센다. 품목이 여럿인 주문의 문의는 그 주문의 모든 상품에
  세고(`inquiry_count`), 상품 하나뿐인 주문의 문의만 센 값(`inquiry_count_single_item`)도 준다.
  문의를 못 읽었으면(`inquiries=None`) 문의 칸은 None이다 (0으로 채우지 않는다).
- 판매 수량 대비 비율은 아직 없다 (판매 서버에서 기간별 판매 수량을 읽는 길이 따로 필요하다).
  그래서 `sold_qty`·`claim_rate`는 None이고 `missing`에 적는다.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta

from support_agent.book_issue_schemas import (
    PERIOD_DAYS,
    BookIssueInput,
    BookIssueReport,
    BookIssueRow,
    CategoryCount,
)
from support_agent.claim_schemas import Claim
from support_agent.insights import parse_time

DEFECT_REASONS = frozenset({"defect", "damaged", "wrong_item", "missing_item"})
TOP_CATEGORIES = 3


@dataclass(frozen=True)
class OrderInquiry:
    """주문 하나의 문의 집계와 그 주문의 상품들. 상품은 `(상품 ID, 제목)`이고, 결제 전 주문이거나
    읽지 못한 주문은 빈 튜플이다."""

    order_id: str
    count: int
    categories: Mapping[str, int]
    products: tuple[tuple[str, str], ...]


@dataclass
class _Inquiry:
    title: str = ""
    count: int = 0
    single: int = 0
    categories: Counter[str] | None = None


def _top(categories: Counter[str]) -> list[CategoryCount]:
    ranked = sorted(categories.items(), key=lambda kv: (-kv[1], kv[0]))
    return [CategoryCount(category=c, count=n) for c, n in ranked[:TOP_CATEGORIES]]


def _claim_row(product_id: str, claims: list[Claim]) -> BookIssueRow:
    by_type: Counter[str] = Counter()
    defect = 0
    return_qty = 0
    refund = 0
    title = ""
    for claim in sorted(claims, key=lambda c: c.created_at):
        mine = [line for line in claim.lines if line.product_id == product_id]
        title = mine[0].title  # 오래된 것부터 돌므로 마지막에 가장 최근 제목이 남는다
        by_type[claim.type] += 1
        if claim.reason in DEFECT_REASONS:
            defect += 1
        if claim.return_received_at:
            return_qty += sum(line.qty for line in mine)
        if claim.refund_paid > 0:
            refund += sum(line.refund_cash for line in mine)
    return BookIssueRow(
        product_id=product_id,
        title=title,
        claim_count=len(claims),
        claims_by_type=dict(by_type),
        defect_claim_count=defect,
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
        parts.append(f"문의 {row.inquiry_count}건(주문 단위)")
    return ", ".join(parts) or "기록 없음"


def _spread_inquiries(
    inquiries: Iterable[OrderInquiry],
) -> tuple[dict[str, _Inquiry], int]:
    """주문별 문의를 상품에 나눠 붙인다. (상품별 집계, 상품에 못 붙인 문의 수)."""
    per_product: dict[str, _Inquiry] = {}
    unattributed = 0
    for order in inquiries:
        if not order.products:
            unattributed += order.count
            continue
        ids = dict(order.products)  # 같은 상품이 두 줄이어도 한 번
        for product_id, title in ids.items():
            slot = per_product.setdefault(product_id, _Inquiry(categories=Counter()))
            slot.title = title
            slot.count += order.count
            if len(ids) == 1:
                slot.single += order.count
            assert slot.categories is not None
            slot.categories.update(order.categories)
    return per_product, unattributed


def book_issue_report(
    claims: Iterable[Claim],
    now: datetime,
    query: BookIssueInput,
    inquiries: Iterable[OrderInquiry] | None = None,
    *,
    total_inquiries: int | None = None,
    notes: Iterable[str] = (),
) -> BookIssueReport:
    """`inquiries`가 None이면 클레임만 센다. 주면 문의 건수를 상품에 붙인다.

    `total_inquiries`는 기간 안의 전체 문의 수(문의 서비스가 센 값), `notes`는 문의를 읽다가 못 읽은
    값(예: 판매 서버 주문 일부를 못 읽음)의 설명이다 — `missing`에 그대로 붙는다.
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
    if inquiries is not None:
        inquiry_by_product, not_attributed = _spread_inquiries(inquiries)
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
                    "top_categories": _top(slot.categories or Counter()),
                }
            )
        # 문의를 읽었는데 이 상품에는 문의가 없으면 0이다 (못 읽은 것과 다르다)
        for product_id, row in list(rows.items()):
            if row.inquiry_count is None:
                rows[product_id] = row.model_copy(
                    update={"inquiry_count": 0, "inquiry_count_single_item": 0}
                )

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

    missing = ["판매 수량을 읽지 않아 클레임 비율(claim_rate)은 내지 않았다"]
    if without_product:
        missing.append(f"상품 ID가 없어 상품별로 세지 못한 클레임 {without_product}건")
    if inquiries is None:
        missing.append("문의 건수를 읽지 않았다")
    elif not_attributed:
        missing.append(f"결제 전·읽지 못한 주문이라 상품에 붙이지 못한 문의 {not_attributed}건")
    missing.extend(notes)
    return BookIssueReport(
        period=query.period,
        period_start=start.strftime("%Y-%m-%dT%H:%M:%SZ"),
        period_end=now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        items=shown[: query.limit],
        total_claims=len(in_period),
        claims_without_product=without_product,
        inquiries_read=inquiries is not None,
        total_inquiries=total_inquiries if inquiries is not None else None,
        inquiries_not_attributed=not_attributed,
        missing=missing,
    )
