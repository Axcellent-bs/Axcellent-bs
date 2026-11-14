"""주문별 문의 건수 (문의 서비스 쪽). 프레임워크에 의존하지 않는 순수 코드다 (LLM 없음).

책별 집계(`book_issue_stats`)가 문의를 상품에 붙일 때 쓴다. 문의는 주문에만 이어져 있고,
클레임 저장소는 다른 서비스에 있어서 문의 쪽에서 주문별 건수만 세어 준다.
고객 이름·글·구매자 ID는 싣지 않는다 (주문 ID·건수·분류 코드뿐).
문의 시각은 첫 고객 메시지의 접수 시각이다.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from datetime import datetime, timedelta

from support_agent.book_issue_schemas import (
    PERIOD_DAYS,
    BookIssuePeriod,
    InquiryOrderCounts,
    OrderInquiryCount,
)
from support_agent.insights import parse_time
from support_agent.schemas import Conversation

MAX_ORDERS = 200


def _started_at(conversation: Conversation) -> datetime | None:
    stamps = [m.created_at for m in conversation.messages if m.role == "customer"]
    return parse_time(min(stamps)) if stamps else None


def inquiry_order_counts(
    conversations: Iterable[Conversation], now: datetime, period: BookIssuePeriod
) -> InquiryOrderCounts:
    start = now - timedelta(days=PERIOD_DAYS[period])
    per_order: dict[str, Counter[str]] = {}
    total = 0
    without_order = 0
    for conversation in conversations:
        started = _started_at(conversation)
        if started is None or not start <= started <= now:
            continue
        total += 1
        if not conversation.order_id:
            without_order += 1
            continue
        per_order.setdefault(str(conversation.order_id), Counter())[
            conversation.category or "unknown"
        ] += 1
    rows = [
        OrderInquiryCount(
            order_id=order_id, inquiry_count=sum(cats.values()), categories=dict(cats)
        )
        for order_id, cats in per_order.items()
    ]
    rows.sort(key=lambda r: (-r.inquiry_count, r.order_id))
    return InquiryOrderCounts(
        period=period,
        period_start=start.strftime("%Y-%m-%dT%H:%M:%SZ"),
        period_end=now.strftime("%Y-%m-%dT%H:%M:%SZ"),
        total_inquiries=total,
        without_order=without_order,
        orders=rows[:MAX_ORDERS],
        truncated=len(rows) > MAX_ORDERS,
    )
