"""하루 계산 (설계 5.1·5.3, 3-1b).

판매 수집기 회차에서 부른다. 기준일 = 어제(한국). 회사마다 하루 한 번(그 기준일로 끝난 계산이 있으면
다시 돌지 않는다).

1. 다시 계산할 날짜: 지난 성공 계산 뒤 새로 받은(`synced_at`) 주문의 접수 날짜 + 그 주문 환불 날짜 +
   기준일. 13개월보다 오래된 날짜는 그 달 전체를 다시 만들어 달 행으로 합친다.
2. 그 날짜들의 일별 매출·품질 행을 바꿔 넣는다.
3. 기준일 스냅샷 전부(구매자·코호트·떠날 위험·함께 산 상품·즐겨 사는 것·채널 가치).
4. 보관(13개월 → 달 행, 스냅샷 35일 + 월말).
5. 병행 비교(`compare.compare_day`)를 남긴다.

실패하면 계산 기록에 실패(예외 이름만)를 남기고, 저장소는 마지막 성공 기준일 그대로 둔다
(스냅샷은 기준일별로 바꿔 넣으므로 실패한 날 행은 생기지 않는다).
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta

from ax_contracts.data_access.sales_aggregates import AggRun
from ax_contracts.schemas.data import CatalogSales
from data_agent.aggregate import compare
from data_agent.aggregate.backfill import (
    months_back,
    retention_bounds,
    snapshot_day,
    source_cursor,
)
from data_agent.aggregate.build_day import build_day
from data_agent.aggregate.store import AggregateStore
from data_agent.sales_quality import kst_day
from data_agent.sales_store import SalesStore, iso

SQL_CHANGED = (
    "SELECT DISTINCT date(substr(o.created_at, 1, 19), '+9 hours') AS d FROM sales_orders o "
    "WHERE o.tenant_id = ? AND o.synced_at > ? "
    "UNION SELECT DISTINCT date(substr(r.refunded_at, 1, 19), '+9 hours') FROM sales_refunds r "
    "JOIN sales_orders o ON o.tenant_id = r.tenant_id AND o.order_id = r.order_id "
    "WHERE r.tenant_id = ? AND o.synced_at > ?"
)


def last_success(agg: AggregateStore, tenant_id: str) -> tuple[date | None, str | None]:
    """마지막으로 끝난 계산의 (기준일, 시작 시각 iso)."""
    done = [r for r in agg.runs(tenant_id) if r["status"] == "done"]
    if not done:
        return None, None
    last = done[-1]
    return date.fromisoformat(last["as_of"]), last["started_at"]


def changed_days(sales: SalesStore, tenant_id: str, since: str | None, as_of: date) -> list[date]:
    """지난 성공 계산 뒤 새로 받은 주문이 건드린 한국 날짜(접수·환불) + 기준일. 기준일 뒤는 뺀다."""
    days = {as_of}
    if since is not None:
        stamp = iso(datetime.fromisoformat(since))  # 판매 저장소 synced_at과 같은 UTC 글자
        with sales.reading() as conn:
            rows = conn.execute(SQL_CHANGED, (tenant_id, stamp, tenant_id, stamp)).fetchall()
        days |= {date.fromisoformat(r[0]) for r in rows if r[0]}
    return sorted(d for d in days if d <= as_of)


def expand_old(days: list[date], keep_from: date, first: date | None) -> list[date]:
    """13개월보다 오래된 날짜는 그 달의 모든 날로 (달 행을 다시 합칠 수 있게).
    첫 주문 날 앞은 뺀다."""
    out: set[date] = set()
    for d in days:
        if d >= keep_from:
            out.add(d)
            continue
        start = d.replace(day=1)
        end = months_back(start, -1)  # 다음 달 1일
        cur = start
        while cur < end:
            if first is None or cur >= first:
                out.add(cur)
            cur += timedelta(days=1)
    return sorted(out)


def run_daily(  # noqa: PLR0913 - 두 저장소·회사·카탈로그·시각
    sales: SalesStore,
    agg: AggregateStore,
    tenant_id: str,
    cfg: CatalogSales | None,
    now: datetime,
    run_id: str | None = None,
) -> AggRun | None:
    """하루 계산. 이미 그 기준일로 끝났으면 None(돌지 않음)."""
    as_of = kst_day(now) - timedelta(days=1)
    done_as_of, since = last_success(agg, tenant_id)
    if done_as_of is not None and done_as_of >= as_of:
        return None
    run = AggRun(
        run_id=run_id or f"agg_{uuid.uuid4().hex[:12]}", tenant_id=tenant_id, as_of=as_of,
        input="sales_db", started_at=now, status="running",
        source_cursor=source_cursor(sales, tenant_id),
    )  # fmt: skip
    agg.save_run(run)
    try:
        _, first, _ = sales.order_span(tenant_id)
        keep_from, drop_before = retention_bounds(as_of)
        first_day = kst_day(first) if first else None
        if since is None and first_day is not None:  # 처음이면 첫 주문 날부터 전부
            days = [first_day + timedelta(days=i) for i in range((as_of - first_day).days + 1)]
        else:
            days = expand_old(changed_days(sales, tenant_id, since, as_of), keep_from, first_day)
        for d in days:
            rows, quality = build_day(
                sales, tenant_id, d, cfg, run.run_id, now,
                source_cursor=run.source_cursor, with_current=d == as_of,
            )  # fmt: skip
            agg.replace_day(tenant_id, d, rows, quality)
        merged, dropped = agg.roll_up(tenant_id, keep_from, drop_before)
        counts: dict[str, int] = {"days": len(days), "rolled_up_daily_rows": merged,
                                  "dropped_monthly_rows": dropped}  # fmt: skip
        if cfg is not None and first is not None:
            counts |= snapshot_day(sales, agg, tenant_id, as_of, cfg, run, now)
            counts["pruned_snapshot_rows"] = agg.prune_snapshots(tenant_id, as_of)
            result = compare.compare_day(sales, agg, tenant_id, as_of, now)
            counts |= {"compare_same": result.same, "compare_different": result.different}
            print(result.line(), flush=True)
        done = run.model_copy(update={
            "status": "done", "finished_at": datetime.now(now.tzinfo), "rows": counts,
        })  # fmt: skip
    except Exception as exc:  # noqa: BLE001 - 실패도 기록에 남기고 다음 회차에 다시
        failed = run.model_copy(update={
            "status": "failed", "finished_at": datetime.now(now.tzinfo),
            "reason": type(exc).__name__,
        })  # fmt: skip
        agg.save_run(failed)
        print(f"[aggregate] {as_of.month}월 {as_of.day}일 하루 계산 실패: {type(exc).__name__}",
              flush=True)  # fmt: skip
        return failed
    agg.save_run(done)
    return done
