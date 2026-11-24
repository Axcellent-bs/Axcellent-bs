"""`python -m data_agent.aggregate --backfill [--tenant 회사]` (설계 5.4).

`sales.db`(`DATA_SALES_DB`)에서 읽어 `aggregates.db`(`DATA_AGGREGATES_DB`)에 일별 행을 채운다.
데이터 AI가 시작할 때는 돌리지 않는다. 사람이 명령으로 한 번 돌린다(다시 돌려도 같은 결과).
"""

from __future__ import annotations

import argparse
import os
import sys
from datetime import UTC, datetime

from ax_contracts.schemas.common import TENANT_ID_DEFAULT
from data_agent import catalog
from data_agent.aggregate.backfill import backfill
from data_agent.aggregate.store import AggregateStore, db_path
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
    args = parser.parse_args(argv)
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


if __name__ == "__main__":
    raise SystemExit(main())
