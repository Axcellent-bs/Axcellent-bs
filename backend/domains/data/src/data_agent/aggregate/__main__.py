"""`python -m data_agent.aggregate --backfill [--tenant 회사]` (설계 5.4).

`--verify-days 90 [--every 7]`(3단계 B): 원본으로 지난 N일 집계를 임시 파일에 다시 계산해 분석별로
견준다(`verify.py`). 운영 `aggregates.db`·`sales.db`에 쓰지 않는다. 늘 0으로 끝난다.

회사마다 (11/26): `--tenant <회사 id>`면 그 회사 범위(`company_scope.using`)에서 그 회사 카탈로그로
계산한다(예전에는 늘 설치 카탈로그 = 캣북스). 캣북스(`shop-demo`·`catbooks`·생략)는 예전 그대로.
`--all`은 캣북스와 회사 연결이 켜진 회사(판매 수집기 `watched_companies`)를 차례로 돈다.

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
    parser.add_argument("--all", action="store_true",
                        help="캣북스와 회사 연결이 켜진 회사 모두 차례로")  # fmt: skip
    parser.add_argument("--verify-days", type=int, default=0,
                        help="지난 N일을 임시 파일에 다시 계산해 원본과 견주기 (쓰지 않음)")
    parser.add_argument("--every", type=int, default=7, help="비교할 기준일 간격(일)")
    args = parser.parse_args(argv)
    from data_agent.sales_memory import memory_mode

    if memory_mode(os.environ):
        print("[aggregate] 원본이 데이터 AI 메모리에만 있어서(DATA_SALES_STORE=memory) 이 명령은 "
              "보관된 sales.db 기준이에요", flush=True)  # fmt: skip
    if args.all and args.tenant:
        print("[aggregate] --all과 --tenant는 같이 쓰지 않아요", flush=True)
        return 2
    env = dict(os.environ)
    try:
        targets = _targets(args, env)
    except ValueError as error:
        print(f"[aggregate] {error}", flush=True)
        return 2
    if args.verify_days > 0:
        return _each(targets, args, env, _verify)
    if not args.backfill:
        parser.print_help()
        return 2
    return _each(targets, args, env, _backfill)


def _catbooks_tenant(env: dict[str, str]) -> str:
    return (env.get(TENANT_ENV) or TENANT_ID_DEFAULT).strip()


def _targets(args: argparse.Namespace, env: dict[str, str]) -> list[tuple[str, str | None]]:
    """(판매 저장소 tenant, 회사 범위). 캣북스는 범위 없이 `DATA_SALES_TENANT_ID` 그대로.

    다른 회사는 판매 수집기(`collect_company`)처럼 tenant = 범위 = 회사 정식 id.
    """
    from ax_agent_template.companies import canonical_company_id
    from ax_agent_template.company_scope import CATBOOKS

    catbooks = _catbooks_tenant(env)
    if args.all:
        from data_agent.sales_collect import watched_companies

        return [(catbooks, None)] + [(cid, cid) for cid in watched_companies(env)]
    given = (args.tenant or "").strip()
    if not given or given == catbooks:
        return [(catbooks, None)]
    cid = canonical_company_id(given)
    if cid is None:
        raise ValueError(f"회사 id 모양이 아니에요: {given!r}")
    if cid == CATBOOKS:
        return [(catbooks, None)]
    return [(cid, cid)]


def _label(tenant: str, scope: str | None) -> str:
    if scope is None:
        return f"캣북스 ({tenant})"
    from ax_agent_template.companies import get_company

    try:
        found = get_company(scope)
    except Exception:  # noqa: BLE001 - 이름은 머리줄에만 쓴다
        found = None
    name = getattr(found, "name", None) or "이름 모름"
    return f"{name} ({scope})"


def _each(targets, args, env, run) -> int:  # noqa: ANN001
    """회사마다 그 회사 범위에서 `run`. 한 회사가 실패해도 다음 회사는 돈다."""
    from ax_agent_template import company_scope

    worst = 0
    for tenant, scope in targets:
        if len(targets) > 1:
            print(f"== {_label(tenant, scope)} ==", flush=True)
        try:
            if scope is None:
                code = run(tenant, args, env)
            else:
                with company_scope.using(scope):
                    code = run(tenant, args, env)
        except Exception as error:  # noqa: BLE001 - 한 회사 오류가 다른 회사를 막지 않게
            print(f"[aggregate] {_label(tenant, scope)} 실패: {type(error).__name__}: {error}",
                  flush=True)  # fmt: skip
            code = 1
        worst = max(worst, code)
    return worst


def _backfill(tenant: str, args: argparse.Namespace, env: dict[str, str]) -> int:
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


def _verify(tenant: str, args: argparse.Namespace, env: dict[str, str]) -> int:
    from pathlib import Path

    from data_agent.aggregate.verify import verify

    path = Path(env.get(SALES_DB_ENV) or SALES_DEFAULT_DB)
    if not path.is_file():
        print(f"[aggregate] 지난 날짜 비교: 판매 원본이 없음 ({path.name})")
        return 0
    report = verify(path, tenant, catalog.current(env), datetime.now(UTC), args.verify_days,
                    args.every)  # fmt: skip
    for line in report.lines():
        print(line)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
