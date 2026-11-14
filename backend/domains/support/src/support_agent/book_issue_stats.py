"""책(상품)별 클레임 집계. 프레임워크에 의존하지 않는 순수 코드다 (LLM 없음).

- 묶는 열쇠는 상품 ID(`ClaimLine.product_id`)다. 제목으로 추측하지 않는다. 상품 ID가 없는 클레임
  (옛 기록·시드 주문)은 상품별로 세지 않고 `claims_without_product`로 알린다.
- 한 클레임은 상품마다 한 번 센다 (같은 상품 줄이 여럿이어도 1건).
- 기간은 클레임 접수 시각(`created_at`) 기준으로, 지금부터 거슬러 올라간다.
- 문의 건수와 판매 수량 대비 비율은 아직 없다 (문의 저장소는 다른 서비스에 있고, 판매 수량은
  판매 서버에서 읽는 길이 따로 필요하다). 그래서 `sold_qty`·`claim_rate`는 None이고
  `missing`에 적는다.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from datetime import datetime, timedelta

from support_agent.book_issue_schemas import (
    PERIOD_DAYS,
    BookIssueInput,
    BookIssueReport,
    BookIssueRow,
)
from support_agent.claim_schemas import Claim
from support_agent.insights import parse_time

DEFECT_REASONS = frozenset({"defect", "damaged", "wrong_item", "missing_item"})


def _row(product_id: str, claims: list[Claim]) -> BookIssueRow:
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
    count = len(claims)
    parts = [f"클레임 {count}건"]
    if defect:
        parts.append(f"불량·파손 등 {defect}건")
    if return_qty:
        parts.append(f"반품 입고 {return_qty}권")
    if refund:
        parts.append(f"환불 {refund:,}원")
    return BookIssueRow(
        product_id=product_id,
        title=title,
        claim_count=count,
        claims_by_type=dict(by_type),
        defect_claim_count=defect,
        return_qty=return_qty,
        refund_cash_total=refund,
        reason=", ".join(parts),
    )


def book_issue_report(
    claims: Iterable[Claim], now: datetime, query: BookIssueInput
) -> BookIssueReport:
    start = now - timedelta(days=PERIOD_DAYS[query.period])
    in_period = [c for c in claims if start <= parse_time(c.created_at) <= now]
    wanted = set(query.product_ids) if query.product_ids else None
    by_product: dict[str, list[Claim]] = {}
    without_product = 0
    for claim in in_period:
        ids = {line.product_id for line in claim.lines if line.product_id}
        if not ids:
            without_product += 1
            continue
        for product_id in ids:
            if wanted is None or product_id in wanted:
                by_product.setdefault(product_id, []).append(claim)
    rows = [_row(pid, cs) for pid, cs in by_product.items() if len(cs) >= query.min_claims]
    rows.sort(key=lambda r: (-r.claim_count, -r.defect_claim_count, r.product_id))
    missing = ["판매 수량을 읽지 않아 클레임 비율(claim_rate)은 내지 않았다"]
    if without_product:
        missing.append(f"상품 ID가 없어 상품별로 세지 못한 클레임 {without_product}건")
    return BookIssueReport(
        period=query.period,
        period_start=start.strftime("%Y-%m-%dT%H:%M:%SZ"),
        period_end=now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        items=rows[: query.limit],
        total_claims=len(in_period),
        claims_without_product=without_product,
        missing=missing,
    )
