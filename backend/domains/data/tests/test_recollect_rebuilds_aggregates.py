"""다시 받기(--recollect) 뒤 그 회사 일별 집계를 처음부터 다시 만든다 (11/26 누비투어).

실화면: 누비투어 판매 줄은 다시 받아 분류가 모두 채워졌는데(빈 줄 0), 매출 분석(집계 읽기)은
"이름 없는 분류" 98%. 원인: 하루 집계는 기준일마다 한 번만 돌아서, 다시 받기 전 분류 빈 줄로
만든 지난 날 일별 행이 다음 날 계산까지 그대로 읽혔다.

- 다시 받기가 끝까지 끝난 회차에 그 회사 일별 행을 첫 주문 날부터 다시 만든다(오늘 이미 돌았어도)
- 표시는 한 번 쓰면 지운다(다음 회차는 평소대로 하루 한 번)
- 캣북스·다른 회사 집계는 건드리지 않는다
"""

from __future__ import annotations

import asyncio
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from ax_agent_template import companies as company_list
from ax_agent_template import company_connection
from ax_contracts.data_access.shop_sales import SalesOrderLine, SalesOrderRow
from data_agent import sales_collect as sc
from data_agent.aggregate.store import DB_ENV as AGG_DB_ENV
from data_agent.sales_store import SalesStore

NUBI = "c-aaaaaa"
STUDY = "c-bbbbbb"
NOW = datetime(2026, 11, 26, 3, 0, tzinfo=UTC)  # 한국 12:00 (00:30 뒤)


@dataclass
class _Conn:
    enabled: bool = True


@pytest.fixture(autouse=True)
def _companies(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(company_list, "company_ids", lambda: ["catbooks", NUBI, STUDY])
    conns = {NUBI: _Conn(), STUDY: _Conn()}
    monkeypatch.setattr(company_connection, "read_connection", lambda cid=None: conns.get(cid))


def _orders(prefix: str, category: str | None) -> list[SalesOrderRow]:
    out = []
    for i in range(6):
        when = NOW - timedelta(days=1 + i, hours=2)
        ln = SalesOrderLine(product_id=f"{prefix}-p{i % 2}", category_code=category, title="상품",
                            qty=1, unit_price=10000, coupon_share=0, points_share=0)  # fmt: skip
        out.append(SalesOrderRow(
            order_id=f"{prefix}-o{i}", buyer_id=f"{prefix}-b{i}", status="paid", created_at=when,
            updated_at=when, items_amount=10000, payment_status="succeeded", lines=[ln],
        ))  # fmt: skip
    return out


def _env(tmp_path: Path) -> dict[str, str]:
    return {
        sc.DB_ENV: str(tmp_path / "sales.db"),
        AGG_DB_ENV: str(tmp_path / "aggregates.db"),
        sc.AGGREGATE_ENV: "1",
    }


def _categories(env: dict[str, str], tenant: str) -> dict[str, int]:
    """그 회사 상품 줄 일별 행(grain=line)의 분류별 행 수 (빈 분류는 "")."""
    conn = sqlite3.connect(env[AGG_DB_ENV])
    try:
        rows = conn.execute(
            "SELECT category_code, COUNT(*) FROM agg_sales_daily "
            "WHERE tenant_id = ? AND grain = 'line' "
            "GROUP BY category_code",
            (tenant,),
        ).fetchall()
    finally:
        conn.close()
    return {r[0]: r[1] for r in rows}


def _collect(env: dict[str, str], cid: str, rows: list[SalesOrderRow], now: datetime) -> None:
    store = SalesStore(sc.db_path(env))
    try:
        got = asyncio.run(sc.collect_company(store, cid, env, now, {},
                                             lambda e: sc.MockSalesSource(rows)))  # fmt: skip
        assert got.errors == []
    finally:
        store.close()


def test_recollect_rebuilds_that_company_days_even_if_today_already_ran(tmp_path) -> None:  # noqa: ANN001
    env = _env(tmp_path)
    _collect(env, NUBI, _orders("nubi", None), NOW)  # 분류 없이 받은 줄로 오늘 하루 계산이 돎
    _collect(env, STUDY, _orders("study", None), NOW)
    assert set(_categories(env, NUBI)) == {""}  # 이름 없는 분류

    assert sc.recollect(env, NUBI) == 0
    assert sc.rebuild_marker(env, NUBI).is_file()

    later = NOW + timedelta(minutes=5)  # 같은 날 다음 회차 (평소라면 하루 계산은 안 돎)
    _collect(env, NUBI, _orders("nubi", "japan"), later)
    assert set(_categories(env, NUBI)) == {"japan"}  # 지난 날 행까지 다시 만들어 분류가 채워짐
    assert not sc.rebuild_marker(env, NUBI).exists()  # 한 번 쓰면 지움
    assert set(_categories(env, STUDY)) == {""}  # 다른 회사 집계는 그대로


def test_rebuild_waits_until_the_recollect_finished(tmp_path) -> None:  # noqa: ANN001
    """다시 받기가 끝까지 안 끝난 회차(오류)에는 다시 만들지 않고 표시를 남긴다."""
    env = _env(tmp_path)
    _collect(env, NUBI, _orders("nubi", None), NOW)
    assert sc.recollect(env, NUBI) == 0

    def broken(e):  # noqa: ANN001, ANN202
        raise RuntimeError("출처 고장")

    store = SalesStore(sc.db_path(env))
    try:
        got = asyncio.run(sc.collect_company(store, NUBI, env, NOW + timedelta(minutes=5), {},
                                             broken))  # fmt: skip
        assert got.errors
    finally:
        store.close()
    assert sc.rebuild_marker(env, NUBI).is_file()
    assert set(_categories(env, NUBI)) == {""}


def test_without_recollect_a_day_still_runs_once(tmp_path) -> None:  # noqa: ANN001
    """표시가 없으면 평소처럼 기준일마다 한 번만 (같은 날 다음 회차는 다시 만들지 않음)."""
    env = _env(tmp_path)
    _collect(env, NUBI, _orders("nubi", None), NOW)
    _collect(env, NUBI, _orders("nubi", "japan"), NOW + timedelta(minutes=5))
    assert "" in _categories(env, NUBI)
