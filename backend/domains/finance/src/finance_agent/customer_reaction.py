"""고객 반응(고객지원 `book_issue_stats`)으로 캠페인 예산 의견을 줄이는 판단 (`CRX-001`).

- 읽기는 `sources`가 하고(고객지원을 `call_agent`로만 부른다), 여기서는 읽은 값으로 판단만 한다.
  프레임워크에 의존하지 않는 순수 함수다.
- 재무는 막거나 승인하지 않고 **의견 한도**만 줄인다. 걸린 책이 여럿이어도 요청당 한 번만 줄인다
  (책마다 줄이면 10권짜리 캠페인은 바로 0원이 된다).
- 못 읽었거나(`unavailable`) 판매가 적어 비율이 흔들리는 책은 의견을 바꾸지 않고 이유만 남긴다.
  못 읽은 값을 0으로 채우거나 지어내지 않는다.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from ax_contracts.schemas.book_issue import BookIssueReport, BookIssueRow
from finance_agent.rules import CustomerReactionRules

# 고객지원이 불량 계열로 세는 사유(`book_issue_stats`의 `defect_claim_count`)와 같은 묶음이다
STATUS_CLEAR = "clear"  # 읽었고 걸린 책이 없다
STATUS_FLAGGED = "flagged"  # 읽었고 걸린 책이 있다 → 한도를 줄인다
STATUS_UNAVAILABLE = "unavailable"  # 못 읽었다 → 한도는 그대로
STATUS_NOT_RELEVANT = "not_relevant"  # 요청에 상품이 없다 (아무 규칙도 적용하지 않는다)


@dataclass(frozen=True)
class Flag:
    product_id: str
    title: str
    sold_qty: int
    claim_count: int
    defect_claim_count: int
    claim_rate: float | None
    why: str  # "비율"·"불량" (둘 다면 "비율·불량")


@dataclass(frozen=True)
class Reaction:
    """한 요청의 고객 반응 판단. `flagged`가 있을 때만 한도를 줄인다."""

    status: str
    flagged: tuple[Flag, ...] = ()
    # 아래 둘은 이유 문장에 쓰는 제목이다(없으면 상품 ID). 출력 요약에는 `_ids`를 싣는다
    low_sales: tuple[str, ...] = ()  # 판매가 적어 참고만 한 책
    sales_unread: tuple[str, ...] = ()  # 판매 수량을 못 읽어 비율을 못 낸 책 (판매 적음과 다르다)
    low_sales_ids: tuple[str, ...] = ()  # `low_sales`의 상품 ID
    sales_unread_ids: tuple[str, ...] = ()  # `sales_unread`의 상품 ID
    checked: int = 0  # 요청한 상품 중 집계에 나온 책 수 (응답 줄 수가 아니다)
    note: str = ""  # 못 읽은 이유 등
    unread: tuple[str, ...] = field(default_factory=tuple)  # 요청했는데 집계에 없는 상품


NOT_RELEVANT = Reaction(STATUS_NOT_RELEVANT)


def unavailable(note: str) -> Reaction:
    return Reaction(STATUS_UNAVAILABLE, note=note)


def _label(row: BookIssueRow) -> str:
    return row.title or row.product_id


def evaluate(
    report: BookIssueReport, product_ids: list[str], rules: CustomerReactionRules
) -> Reaction:
    """읽은 책별 집계(`BookIssueReport`)에서 걸리는 책을 고른다."""
    rows = {row.product_id: row for row in report.items}
    flagged: list[Flag] = []
    low: list[str] = []
    low_ids: list[str] = []
    unread_sales: list[str] = []
    unread_sales_ids: list[str] = []
    for product_id in product_ids:
        row = rows.get(product_id)
        if row is None:
            continue  # 집계에 없는 상품은 아래 `unread`로 알린다
        sold = row.sold_qty
        if sold is None:  # 판매 수량을 못 읽었으면 비율을 모른다. 불량 건수만으로 판단하지 않는다
            unread_sales.append(_label(row))  # "판매가 적음"이 아니라 "못 읽음"으로 따로 알린다
            unread_sales_ids.append(row.product_id)
            continue
        if sold < rules.min_sold_qty:
            low.append(_label(row))
            low_ids.append(row.product_id)
            continue
        by_rate = (
            row.claim_rate is not None
            and row.claim_rate >= rules.claim_rate
            and row.claim_count >= rules.min_claims
        )
        by_defect = row.defect_claim_count >= rules.defect_claims
        if by_rate or by_defect:
            why = "·".join(n for n, hit in (("비율", by_rate), ("불량", by_defect)) if hit)
            flagged.append(
                Flag(
                    product_id=row.product_id,
                    title=_label(row),
                    sold_qty=sold,
                    claim_count=row.claim_count,
                    defect_claim_count=row.defect_claim_count,
                    claim_rate=row.claim_rate,
                    why=why,
                )
            )
    unread = tuple(p for p in product_ids if p not in rows)
    status = STATUS_FLAGGED if flagged else STATUS_CLEAR
    return Reaction(
        status=status,
        flagged=tuple(flagged),
        low_sales=tuple(low),
        sales_unread=tuple(unread_sales),
        low_sales_ids=tuple(low_ids),
        sales_unread_ids=tuple(unread_sales_ids),
        checked=len({p for p in product_ids if p in rows}),
        unread=unread,
    )


def flag_text(flag: Flag) -> str:
    rate = "비율 모름" if flag.claim_rate is None else f"클레임 {flag.claim_rate:.0%}"
    return (
        f"상품 '{flag.title}': 판매 {flag.sold_qty}개 중 {rate}"
        f"(클레임 {flag.claim_count}건·불량 {flag.defect_claim_count}건)"
    )
