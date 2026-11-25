"""① 지표 시계열 (11/25): 고객사 주문·문의 원본에서 5분 버킷 점을 코드로 센다.

shop-api `GET /api/shop/metric-series`(`ShopMetricSeriesResult`) 모양. 데이터 AI 이상 감지가 읽는다.
사이트에 없는 지표(결제 시도·결제 실패·쿠폰)는 예전처럼 빈 점이다(지어내지 않는다).

- `order_count`: 버킷 안에 접수된 주문 수(`created_at`)
- `order_amount`: 버킷 안에 접수된 주문 중 결제가 끝난(받은 금액이 있는) 주문의 `total_amount` 합
- `cancel_count`: 취소 상태 주문 수. 사이트가 취소 시각을 주지 않아 주문이 마지막으로 바뀐 시각
  (`updated_at`, 짝이 없으면 접수 시각)으로 센다
- `refund_amount`: 환불 합. 짝의 환불 시각(`refunded_at`), 없으면 주문이 바뀐 시각
- `inquiry_count`: 버킷 안에 들어온 문의 수(문의 받은함 짝이 있을 때만, 없으면 빈 점)
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from typing import Any

BUCKET = timedelta(minutes=5)
MAX_POINTS = 2016  # 7일치 5분 버킷
# 사이트 주문·문의에서 셀 수 있는 지표
COUNTED = frozenset(
    {"order_count", "order_amount", "cancel_count", "refund_amount", "inquiry_count"}
)


def parse_time(value: Any) -> datetime | None:
    """ISO 시각 → UTC. 읽을 수 없으면 None (그 줄은 세지 않는다)."""
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, str) and value.strip():
        try:
            dt = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    return (dt if dt.tzinfo else dt.replace(tzinfo=UTC)).astimezone(UTC)


def _floor(ts: datetime) -> datetime:
    return ts.replace(minute=ts.minute - ts.minute % 5, second=0, microsecond=0)


def bucketize(
    events: Iterable[tuple[datetime | None, float]], start: datetime, end: datetime
) -> tuple[list[dict[str, Any]], bool]:
    """(시각, 값)들 → [start, end) 안 5분 버킷 합. 값이 있는 버킷만. (점, 잘렸는지)."""
    sums: dict[datetime, float] = defaultdict(float)
    for ts, value in events:
        if ts is not None and start <= ts < end:
            sums[_floor(ts)] += value
    keys = sorted(sums)
    truncated = len(keys) > MAX_POINTS
    return (
        [{"ts": k.isoformat().replace("+00:00", "Z"), "value": sums[k]} for k in keys[:MAX_POINTS]],
        truncated,
    )


def order_events(
    metric: str, orders: list[dict[str, Any]], sales: list[dict[str, Any]]
) -> list[tuple[datetime | None, float]]:
    """지표별 (시각, 값).

    `orders`는 주문 모양(`to_order`), `sales`는 판매 기록 모양(`sales_row`).
    """
    if metric == "order_count":
        return [(parse_time(o["created_at"]), 1.0) for o in orders]
    if metric == "order_amount":
        return [
            (parse_time(o["created_at"]), float(o["total_amount"] or 0))
            for o in orders
            if any(p.get("status") == "succeeded" for p in o.get("payments") or [])
            and o.get("status") != "cancelled"
        ]
    if metric == "cancel_count":
        return [
            (parse_time(o["updated_at"]), 1.0) for o in orders if o.get("status") == "cancelled"
        ]
    if metric == "refund_amount":
        return [
            (parse_time(r["refunded_at"]), float(r["amount"] or 0))
            for s in sales
            for r in s.get("refunds") or []
        ]
    return []
