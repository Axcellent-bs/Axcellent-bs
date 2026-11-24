"""집계 읽기 (3-2 ①, 설계 1.2·2.4): 매출 분석의 한 기간 집계를 일별 행 + 그날만 원본으로.

- 다 지난 한국 날짜(기간 안의 꽉 찬 날)는 `agg_sales_daily` 행을 더한다.
- 덜 끝난 날(오늘 0시~지금, 비교 기간의 끝날 "같은 시각까지")만 원본에서 그 몇 시간을 센다.
  병행 기간(3-3 전)에는 원본이 `sales.db`고, 3-3 뒤에는 회사 서버 그날 조회로 바뀐다(설계 5.2).
- 구매자 숫자(구매자·재구매 고객·새/재구매 매출)는 날짜끼리 더할 수 없다. 정의(설계 10절 결정 1:
  "어제 24시까지"로 바꿀지)가 정해질 때까지 원본에서 그 기간 그대로 센다. 바꾸는 곳은 `_buyers` 하나다.
- 집계가 그 날들을 다 덮지 않으면(계산 전·실패) `None`: 부르는 쪽이 원본 경로 그대로 간다.

`DATA_SALES_READ=aggregate`일 때만 쓴다(기본 끔). 병행 비교(3-1b)가 며칠 같음을 확인한 뒤 켠다.
"""

from __future__ import annotations

import logging
import os
from datetime import date, datetime, timedelta

from data_agent.aggregate.build_day import day_window
from data_agent.aggregate.store import AggregateStore, db_path
from data_agent.sales_quality import KST
from data_agent.sales_sql import Tally, tally
from data_agent.sales_store import SalesStore

logger = logging.getLogger(__name__)
READ_ENV = "DATA_SALES_READ"
READ_AGGREGATE = "aggregate"


def enabled(env: dict[str, str] | None = None) -> bool:
    env = dict(os.environ) if env is None else env
    return (env.get(READ_ENV) or "").strip() == READ_AGGREGATE


def _midnight_on_or_after(moment: datetime) -> date:
    local = moment.astimezone(KST)
    day = local.date()
    return day if local == datetime.combine(day, datetime.min.time(), KST) else day + timedelta(1)


def covered(agg: AggregateStore, tenant_id: str, last_day: date) -> bool:
    """마지막으로 끝난 하루 계산이 `last_day`까지 덮는가."""
    done = [r for r in agg.runs(tenant_id) if r["status"] == "done"]
    return bool(done) and date.fromisoformat(done[-1]["as_of"]) >= last_day


def _add(t: Tally, part: Tally) -> None:
    t.revenue += part.revenue
    t.orders += part.orders
    t.coupon_orders += part.coupon_orders
    t.rows += part.rows
    for mine, theirs in (
        (t.by_category, part.by_category), (t.by_product, part.by_product),
        (t.product_orders, part.product_orders), (t.by_channel, part.by_channel),
        (t.channel_orders, part.channel_orders),
    ):  # fmt: skip
        for k, v in theirs.items():
            mine[k] += v
    for k, v in part.product_meta.items():
        t.product_meta.setdefault(k, v)
    t.refund_lines.extend(part.refund_lines)


def _blank(value: str) -> str | None:
    return value or None


def _add_rows(t: Tally, agg: AggregateStore, tenant_id: str, start: date, end: date) -> int:
    """[start, end) 날짜 일별 행을 더한다. 읽은 행 수."""
    rows = agg.sales_rows(tenant_id, start, end)
    for r in rows:
        net = r["revenue_net"] - r["refund_amount"]
        if r["grain"] == "order":
            key = (_blank(r["src_source"]), _blank(r["src_medium"]))
            t.revenue += net
            t.orders += r["orders"]
            t.coupon_orders += r["orders"] if r["coupon_used"] else 0
            t.by_channel[key] += net
            t.channel_orders[key] += r["orders"]
        else:
            t.by_category[r["category_code"]] += net
            if r["product_id"]:
                t.by_product[r["product_id"]] += net
                t.product_orders[r["product_id"]] += r["orders"]
    return len(rows)


def _created(agg: AggregateStore, tenant_id: str, start: date, end: date) -> int:
    total, day = 0, start
    while day < end:
        q = agg.quality(tenant_id, day)
        total += int(q["created_orders"]) if q is not None else 0
        day += timedelta(days=1)
    return total


def _buyers(t: Tally, sales: SalesStore, tenant_id: str, start: datetime, end: datetime) -> None:
    """구매자 숫자: 결정 1 전까지 원본 그 기간 그대로 (날짜끼리 더할 수 없다)."""
    src, _ = tally(sales, tenant_id, start, end)
    t.buyers, t.repeat_buyers = src.buyers, src.repeat_buyers
    t.new_revenue, t.repeat_revenue = src.new_revenue, src.repeat_revenue


def mixed_tally(
    sales: SalesStore, agg: AggregateStore, tenant_id: str, start: datetime, end: datetime
) -> tuple[Tally, int] | None:
    """[start, end) 집계(꽉 찬 날 = 일별 행, 나머지 = 원본)와 접수 주문 수. 덮지 못하면 None."""
    first = _midnight_on_or_after(start)
    last = end.astimezone(KST).date()  # 끝 날 0시 (그 앞까지가 꽉 찬 날)
    if last <= first:
        return None  # 꽉 찬 날이 없다(오늘 하루 등): 원본 그대로
    if not covered(agg, tenant_id, last - timedelta(days=1)):
        return None
    t = Tally(window=(start, end))
    read = _add_rows(t, agg, tenant_id, first, last)
    created = _created(agg, tenant_id, first, last)
    for a, b in ((start, day_window(first)[0]), (day_window(last)[0], end)):
        if a < b:
            part, n = tally(sales, tenant_id, a, b)
            _add(t, part)
            created += n
    t.rows += read
    _buyers(t, sales, tenant_id, start, end)
    return t, created


def read_tally(
    sales: SalesStore, tenant_id: str, start: datetime, end: datetime
) -> tuple[Tally, int]:
    """매출 분석이 부르는 한 기간 집계. 스위치가 켜지고 집계가 덮으면 집계 경로, 아니면 원본."""
    if enabled():
        try:
            agg = AggregateStore(db_path())
            try:
                got = mixed_tally(sales, agg, tenant_id, start, end)
            finally:
                agg.close()
            if got is not None:
                return got
        except Exception:  # noqa: BLE001 - 집계를 못 읽으면 원본 경로로 (화면은 그대로)
            logger.warning("[aggregate] 집계 읽기 실패: 원본으로 계산", exc_info=True)
    return tally(sales, tenant_id, start, end)
