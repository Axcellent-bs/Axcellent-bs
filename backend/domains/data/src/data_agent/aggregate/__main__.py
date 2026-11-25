"""`python -m data_agent.aggregate --backfill [--tenant 회사]` (설계 5.4).

`--compare-history N`: 어제까지 지난 N일을 기준일마다 원본(sales.db)과 집계로 견준다(저장 안 함).
날마다 한 줄과 합계를 찍고, 다른 칸이 하나라도 있으면 끝 코드 1(3단계 3-3 전 확인용).

`sales.db`(`DATA_SALES_DB`)에서 읽어 `aggregates.db`(`DATA_AGGREGATES_DB`)에 일별 행을 채운다.
데이터 AI가 시작할 때는 돌리지 않는다. 사람이 명령으로 한 번 돌린다(다시 돌려도 같은 결과).
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import UTC, datetime, timedelta

from ax_contracts.schemas.common import TENANT_ID_DEFAULT
from data_agent import catalog
from data_agent.aggregate.backfill import backfill
from data_agent.aggregate.compare import compare_history
from data_agent.aggregate.store import AggregateStore, db_path
from data_agent.sales_quality import kst_day
from data_agent.sales_store import DB_ENV as SALES_DB_ENV
from data_agent.sales_store import DEFAULT_DB as SALES_DEFAULT_DB
from data_agent.sales_store import SalesStore

TENANT_ENV = "DATA_SALES_TENANT_ID"  # 판매 수집기와 같은 회사


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser(prog="python -m data_agent.aggregate")
    parser.add_argument("--backfill", action="store_true", help="sales.db 전체로 일별 행 채우기")
    parser.add_argument("--tenant", default=None, help="회사 (기본: DATA_SALES_TENANT_ID)")
    parser.add_argument(
        "--compare-history", type=int, default=0, metavar="N",
        help="어제까지 지난 N일을 원본과 집계로 견준다(저장 안 함)",
    )  # fmt: skip
    args = parser.parse_args(argv)
    if args.compare_history:
        return _compare_history(args)
    if not args.backfill:
        parser.print_help()
        return 2
    env = dict(os.environ)
    tenant = args.tenant or env.get(TENANT_ENV) or TENANT_ID_DEFAULT
    sales = SalesStore(env.get(SALES_DB_ENV) or SALES_DEFAULT_DB)
    agg = AggregateStore(db_path(env))
    try:
        cfg = catalog.current(env).sales
        run = backfill(sales, agg, tenant, cfg, datetime.now(UTC))
    finally:
        sales.close()
        agg.close()
    print(f"[aggregate] {tenant} 첫 채우기 {run.status}: 기준일 {run.as_of} · {run.rows}")
    return 0 if run.status == "done" else 1


def _compare_history(args: argparse.Namespace) -> int:
    env = dict(os.environ)
    tenant = args.tenant or env.get(TENANT_ENV) or TENANT_ID_DEFAULT
    days = max(1, min(int(args.compare_history), 400))
    now = datetime.now(UTC)
    sales = SalesStore(env.get(SALES_DB_ENV) or SALES_DEFAULT_DB)
    agg = AggregateStore(db_path(env))
    try:
        results = compare_history(sales, agg, tenant, kst_day(now) - timedelta(days=1), days, now)
    finally:
        sales.close()
        agg.close()
    for r in results:
        print(r.line())
        for row in r.rows:
            if not row.same:
                print(f"  - {row.target}: " + "; ".join(row.diffs[:5]))
    same = sum(r.same for r in results)
    different = sum(r.different for r in results)
    print(f"[aggregate] {tenant} 지난 {days}일 비교 합계: 같음 {same} · 다름 {different}")
    return 0 if different == 0 else 1


if __name__ == "__main__":
    raise SystemExit(main())
