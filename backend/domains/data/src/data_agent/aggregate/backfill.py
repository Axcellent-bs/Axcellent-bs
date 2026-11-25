"""첫 채우기와 보관 (설계 5.4·5.5).

- `backfill`: 그 회사가 `sales.db`에 가진 첫 주문 날짜부터 어제(한국)까지 하루씩 일별 행을 만들어
  바꿔 넣고, 보관 규칙을 적용한다. 다시 돌려도 같은 결과(같은 날짜를 통째로 바꿔 넣음).
- 보관: 일별 행은 기준일이 속한 달부터 거슬러 13개월(그 달 포함)만 두고, 그보다 오래된 달은
  달 행(`agg_sales_monthly`)으로 합친다. 달 행은 5년(60개월)보다 오래되면 지운다.
- 기간 스냅샷(구매자 수 등)은 다음 PR에서 이 뒤에 붙인다.
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timedelta

from ax_contracts.data_access.sales_aggregates import AggRun
from ax_contracts.schemas.data import CatalogSales
from data_agent.aggregate.build_day import build_day
from data_agent.aggregate.build_more import build_more
from data_agent.aggregate.build_snapshots import build_snapshots, windows
from data_agent.aggregate.store import AggregateStore
from data_agent.sales_quality import kst_day
from data_agent.sales_store import SalesStore, iso

KEEP_DAILY_MONTHS = 13
KEEP_MONTHLY_MONTHS = 60


def months_back(day: date, months: int) -> date:
    """`day`가 속한 달의 1일에서 `months`달 전 1일."""
    index = day.year * 12 + (day.month - 1) - months
    return date(index // 12, index % 12 + 1, 1)


def retention_bounds(as_of: date) -> tuple[date, date]:
    """(일별 행을 두는 첫 날, 달 행을 두는 첫 달의 1일)."""
    return months_back(as_of, KEEP_DAILY_MONTHS - 1), months_back(as_of, KEEP_MONTHLY_MONTHS - 1)


def month_ends(as_of: date, months: int) -> list[date]:
    """기준일 달 앞의 `months`개 달 마지막 날, 오래된 달부터."""
    return [months_back(as_of, n - 1) - timedelta(days=1) for n in range(months, 0, -1)]


def snapshot_day(  # noqa: PLR0913 - 두 저장소·회사·기준일·카탈로그·계산 기록·시각
    sales: SalesStore,
    agg: AggregateStore,
    tenant_id: str,
    day: date,
    cfg: CatalogSales,
    run: AggRun,
    now: datetime,
) -> dict[str, int]:
    """기준일 하루의 스냅샷 전부(구매자·코호트·떠날 위험·함께 산 상품·즐겨 사는 것·채널 가치)."""
    cur = run.source_cursor
    buyers, cohorts, at_risk = build_snapshots(sales, tenant_id, day, cfg, run.run_id, now,
                                               source_cursor=cur)  # fmt: skip
    agg.replace_snapshots(tenant_id, day, buyers, cohorts, at_risk)
    pairs, profiles, channels = build_more(sales, tenant_id, day, cfg, run.run_id, now,
                                           source_cursor=cur)  # fmt: skip
    agg.replace_more(tenant_id, day, pairs, profiles, channels)
    events = event_base(sales, agg, tenant_id, day, cfg, run, now)
    return {"agg_buyers_period": len(buyers), "agg_cohorts": len(cohorts),
            "agg_at_risk": len(at_risk), "agg_pairs": len(pairs),
            "agg_segment_profile": len(profiles), "agg_channel_value": len(channels),
            "agg_event_base": events}  # fmt: skip


def event_base(  # noqa: PLR0913
    sales: SalesStore,
    agg: AggregateStore,
    tenant_id: str,
    day: date,
    cfg: CatalogSales,
    run: AggRun,
    now: datetime,
) -> int:
    """이벤트 제안 후보(90일, 대상 고객은 기준일 24시까지)를 화면과 같은 함수로 세어 둔다
    (3-2 ⑦). 속성·장르·이벤트 종류 설정이 없으면 화면도 후보를 세지 않으므로 만들지 않는다."""
    from data_agent.skills import event_proposal as ep  # noqa: PLC0415 - skill → 집계 순환 import

    if not (cfg.attributes and cfg.genres and cfg.event_kinds):
        return 0
    w = next(x for x in windows(day) if x.period == ep.ANALYSIS_PERIOD)
    keys = [a.key for a in cfg.attributes]
    with sales.segment_session(tenant_id, w.start, w.end, keys) as s:
        found = ep.count_candidates(s, cfg, w.end)
    agg.replace_event_base(tenant_id, day, run.run_id, now, cfg.version or "",
                           [ep.candidate_dump(c) for c in found],
                           source_cursor=run.source_cursor)  # fmt: skip
    return len(found)


def source_cursor(sales: SalesStore, tenant_id: str) -> str | None:
    """원본 기준 (병행 기간): 판매 출처 sync 커서와 마지막 수집 시각."""
    state = sales.sync_state(tenant_id)
    if state.cursor is None and state.last_synced_at is None:
        return None
    synced = iso(state.last_synced_at) if state.last_synced_at else ""
    return f"{state.cursor or ''}@{synced}"


def backfill(  # noqa: PLR0913 - 두 저장소·회사·카탈로그·시각·계산 번호
    sales: SalesStore,
    agg: AggregateStore,
    tenant_id: str,
    cfg: CatalogSales | None,
    now: datetime,
    run_id: str | None = None,
) -> AggRun:
    """그 회사의 일별 행을 처음부터 채운다. 주문이 없으면 아무 날도 만들지 않고 끝난다."""
    as_of = kst_day(now) - timedelta(days=1)
    run = AggRun(
        run_id=run_id or f"agg_{uuid.uuid4().hex[:12]}", tenant_id=tenant_id, as_of=as_of,
        input="sales_db", started_at=now, status="running",
        source_cursor=source_cursor(sales, tenant_id),
    )  # fmt: skip
    agg.save_run(run)
    try:
        _, first, _ = sales.order_span(tenant_id)
        days = 0
        if first is not None:
            day = kst_day(first)
            while day <= as_of:
                rows, quality = build_day(
                    sales, tenant_id, day, cfg, run.run_id, now,
                    source_cursor=run.source_cursor, with_current=day == as_of,
                )  # fmt: skip
                agg.replace_day(tenant_id, day, rows, quality)
                day += timedelta(days=1)
                days += 1
        keep_from, drop_before = retention_bounds(as_of)
        merged, dropped = agg.roll_up(tenant_id, keep_from, drop_before)
        rows = {"days": days, "agg_sales_daily": len(agg.days(tenant_id)),
                "rolled_up_daily_rows": merged, "dropped_monthly_rows": dropped}  # fmt: skip
        if cfg is not None and first is not None:  # [sales]가 없으면 k도 속성도 몰라 안 만든다
            # 기준일 + 지난 13개월 월말 기준일 (설계 5.4: 달 행 보관용, 첫 주문 달 뒤만)
            ends = [d for d in month_ends(as_of, KEEP_DAILY_MONTHS) if d >= kst_day(first)]
            for d in ends:  # 월말은 구매자·코호트·떠날 위험만 (함께 산 상품 등은 기준일부터 쌓인다)
                b, c, r = build_snapshots(sales, tenant_id, d, cfg, run.run_id, now,
                                          source_cursor=run.source_cursor)  # fmt: skip
                agg.replace_snapshots(tenant_id, d, b, c, r)
            counts = snapshot_day(sales, agg, tenant_id, as_of, cfg, run, now)
            pruned = agg.prune_snapshots(tenant_id, as_of)
            rows |= counts | {"month_end_snapshots": len(ends), "pruned_snapshot_rows": pruned}
        done = run.model_copy(update={
            "status": "done", "finished_at": datetime.now(now.tzinfo), "rows": rows,
        })  # fmt: skip
    except Exception as exc:  # noqa: BLE001 - 실패도 계산 기록에 남긴다
        done = run.model_copy(update={
            "status": "failed", "finished_at": datetime.now(now.tzinfo),
            "reason": type(exc).__name__,
        })  # fmt: skip
        agg.save_run(done)
        raise
    agg.save_run(done)
    return done
