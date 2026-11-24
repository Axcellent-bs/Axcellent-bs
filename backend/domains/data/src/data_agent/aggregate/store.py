"""일별 집계 저장소 `aggregates.db` (설계 3절·5.5절).

- 표: `agg_sales_daily`(일별 매출, 13개월) · `agg_sales_monthly`(그보다 오래된 달, 5년) ·
  `agg_quality_daily`(품질) · `agg_runs`(계산 기록)
- 어떤 표에도 구매자 id·주문 id·주문 시각 칸이 없다(`SCHEMA` 시험으로 확인).
- 회사는 모든 표의 `tenant_id` 칸으로 나눈다. 모든 읽기·쓰기가 `tenant_id`로 거른다.
- 일별 매출 행에는 k 숨김을 쓰지 않는다(사람 수·속성이 없는 칸, 설계 0.2·결정 필요 3).
- 한 날짜는 통째로 바꿔 넣는다(`replace_day`). 같은 날을 다시 계산해도 행이 쌓이지 않는다.
"""

from __future__ import annotations

import json
import os
import sqlite3
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path

from ax_contracts.data_access.sales_aggregates import AggRun, QualityDailyRow, SalesDailyRow

DB_ENV = "DATA_AGGREGATES_DB"
DEFAULT_DB = Path(__file__).resolve().parents[5] / ".data" / "aggregates.db"

# 일별 매출 행의 키 (날짜·달 칸 빼고)
SALES_KEYS = (
    "grain", "product_id", "category_code", "genre_code", "src_source", "src_medium",
    "src_campaign", "coupon_used",
)  # fmt: skip
SALES_NUMBERS = (
    "orders", "revenue_net", "qty", "coupon_discount", "points_used", "refund_amount",
    "created_orders",
)  # fmt: skip
_COMMON = (
    "tenant_id TEXT NOT NULL, run_id TEXT NOT NULL, computed_at TEXT NOT NULL, source_cursor TEXT"
)
_SALES_COLS = (
    "grain TEXT NOT NULL, product_id TEXT NOT NULL, category_code TEXT NOT NULL, "
    "genre_code TEXT NOT NULL, src_source TEXT NOT NULL, src_medium TEXT NOT NULL, "
    "src_campaign TEXT NOT NULL, coupon_used INTEGER NOT NULL, "
    + ", ".join(f"{n} INTEGER NOT NULL DEFAULT 0" for n in SALES_NUMBERS)
)
_SALES_KEY_SQL = ", ".join(SALES_KEYS)

SCHEMA = f"""
CREATE TABLE IF NOT EXISTS agg_sales_daily (
    {_COMMON}, day TEXT NOT NULL, {_SALES_COLS},
    PRIMARY KEY (tenant_id, day, {_SALES_KEY_SQL})
);
CREATE TABLE IF NOT EXISTS agg_sales_monthly (
    {_COMMON}, month TEXT NOT NULL, {_SALES_COLS},
    PRIMARY KEY (tenant_id, month, {_SALES_KEY_SQL})
);
CREATE TABLE IF NOT EXISTS agg_quality_daily (
    {_COMMON}, day TEXT NOT NULL,
    created_orders INTEGER NOT NULL DEFAULT 0, paid_orders INTEGER NOT NULL DEFAULT 0,
    dropped_by_reason TEXT NOT NULL DEFAULT '{{}}', unknown_codes TEXT NOT NULL DEFAULT '{{}}',
    unmapped_attributes TEXT NOT NULL DEFAULT '{{}}', customers INTEGER,
    PRIMARY KEY (tenant_id, day)
);
CREATE TABLE IF NOT EXISTS agg_runs (
    run_id TEXT NOT NULL PRIMARY KEY, tenant_id TEXT NOT NULL, as_of TEXT NOT NULL,
    input TEXT NOT NULL, started_at TEXT NOT NULL, finished_at TEXT, status TEXT NOT NULL,
    reason TEXT, source_cursor TEXT, rows TEXT NOT NULL DEFAULT '{{}}'
);
"""


_COMMON_COLS = "tenant_id, run_id, computed_at, source_cursor"
_NUMBER_SQL = ", ".join(SALES_NUMBERS)
_MARKS = ", ".join("?" * (5 + len(SALES_KEYS) + len(SALES_NUMBERS)))
_INSERT_DAILY = (
    f"INSERT INTO agg_sales_daily ({_COMMON_COLS}, day, {_SALES_KEY_SQL}, {_NUMBER_SQL}) "
    f"VALUES ({_MARKS})"
)
_INSERT_QUALITY = (
    f"INSERT INTO agg_quality_daily ({_COMMON_COLS}, day, created_orders, paid_orders, "
    "dropped_by_reason, unknown_codes, unmapped_attributes, customers) "
    "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
)
_INSERT_RUN = (
    "INSERT OR REPLACE INTO agg_runs (run_id, tenant_id, as_of, input, started_at, finished_at, "
    "status, reason, source_cursor, rows) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
)
_ROLL_UP = (
    f"INSERT INTO agg_sales_monthly ({_COMMON_COLS}, month, {_SALES_KEY_SQL}, {_NUMBER_SQL}) "
    "SELECT tenant_id, MAX(run_id), MAX(computed_at), MAX(source_cursor), substr(day, 1, 7), "
    f"{_SALES_KEY_SQL}, {', '.join(f'SUM({n})' for n in SALES_NUMBERS)} "
    "FROM agg_sales_daily WHERE tenant_id = ? AND day < ? "
    f"GROUP BY tenant_id, substr(day, 1, 7), {_SALES_KEY_SQL}"
)


def _json(value: dict) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True)


def _daily_values(tenant_id: str, day: str, r: SalesDailyRow) -> tuple:
    keys = [int(r.coupon_used) if k == "coupon_used" else getattr(r, k) for k in SALES_KEYS]
    nums = [getattr(r, n) for n in SALES_NUMBERS]
    return (tenant_id, r.run_id, _iso(r.computed_at), r.source_cursor, day, *keys, *nums)


def db_path(env: dict[str, str] | None = None) -> Path:
    env = os.environ if env is None else env
    return Path(env.get(DB_ENV) or DEFAULT_DB)


def _iso(value: datetime) -> str:
    return value.isoformat()


def month_of(day: date) -> str:
    return f"{day.year:04d}-{day.month:02d}"


class AggregateStore:
    """`aggregates.db` 읽기·쓰기. `path`는 파일 경로 또는 ":memory:"."""

    def __init__(self, path: str | Path) -> None:
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(str(path), check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        if str(path) != ":memory:":
            self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(SCHEMA)

    def close(self) -> None:
        self._conn.close()

    @contextmanager
    def _tx(self) -> Iterator[sqlite3.Connection]:
        with self._conn:
            yield self._conn

    def columns(self, table: str) -> list[str]:
        return [r["name"] for r in self._conn.execute(f"PRAGMA table_info({table})")]

    # ------------------------------------------------------------------ 쓰기

    def replace_day(
        self, tenant_id: str, day: date, rows: Sequence[SalesDailyRow], quality: QualityDailyRow
    ) -> None:
        """그 회사·그 날짜의 일별 매출·품질을 통째로 바꿔 넣는다 (한 트랜잭션)."""
        d = day.isoformat()
        with self._tx() as conn:
            conn.execute(
                "DELETE FROM agg_sales_daily WHERE tenant_id = ? AND day = ?", (tenant_id, d)
            )
            conn.execute(
                "DELETE FROM agg_quality_daily WHERE tenant_id = ? AND day = ?", (tenant_id, d)
            )
            conn.executemany(_INSERT_DAILY, [_daily_values(tenant_id, d, r) for r in rows])
            q = quality
            conn.execute(
                _INSERT_QUALITY,
                (
                    tenant_id, q.run_id, _iso(q.computed_at), q.source_cursor, d,
                    q.created_orders, q.paid_orders, _json(q.dropped_by_reason),
                    _json(q.unknown_codes), _json(q.unmapped_attributes), q.customers,
                ),
            )  # fmt: skip

    def save_run(self, run: AggRun) -> None:
        finished = _iso(run.finished_at) if run.finished_at else None
        with self._tx() as conn:
            conn.execute(
                _INSERT_RUN,
                (
                    run.run_id, run.tenant_id, run.as_of.isoformat(), run.input,
                    _iso(run.started_at), finished, run.status, run.reason, run.source_cursor,
                    _json(run.rows),
                ),
            )  # fmt: skip

    def roll_up(self, tenant_id: str, keep_from: date, drop_before: date) -> tuple[int, int]:
        """보관 (설계 5.5): `keep_from`보다 앞 날짜의 일별 행을 달 행으로 합치고 지운다.
        `drop_before`보다 앞 달의 달 행은 지운다. 돌려주는 값: (합친 일별 행 수, 지운 달 행 수).

        달 행이 이미 있는 달에 일별 행이 다시 생기면(다시 채우기) 그 달 행을 새로 만든다.
        """
        k = keep_from.isoformat()
        with self._tx() as conn:
            months = [
                r[0]
                for r in conn.execute(
                    "SELECT DISTINCT substr(day, 1, 7) FROM agg_sales_daily "
                    "WHERE tenant_id = ? AND day < ?",
                    (tenant_id, k),
                )
            ]
            for m in months:
                conn.execute(
                    "DELETE FROM agg_sales_monthly WHERE tenant_id = ? AND month = ?",
                    (tenant_id, m),
                )
            conn.execute(_ROLL_UP, (tenant_id, k))
            merged = conn.execute(
                "DELETE FROM agg_sales_daily WHERE tenant_id = ? AND day < ?", (tenant_id, k)
            ).rowcount
            conn.execute(
                "DELETE FROM agg_quality_daily WHERE tenant_id = ? AND day < ?", (tenant_id, k)
            )
            dropped = conn.execute(
                "DELETE FROM agg_sales_monthly WHERE tenant_id = ? AND month < ?",
                (tenant_id, month_of(drop_before)),
            ).rowcount
        return merged, dropped

    # ------------------------------------------------------------------ 읽기

    def sales_rows(self, tenant_id: str, start: date, end: date) -> list[sqlite3.Row]:
        """[start, end) 날짜의 일별 매출 행 (그 회사만)."""
        return self._conn.execute(
            "SELECT * FROM agg_sales_daily WHERE tenant_id = ? AND day >= ? AND day < ? "
            f"ORDER BY day, {_SALES_KEY_SQL}",
            (tenant_id, start.isoformat(), end.isoformat()),
        ).fetchall()

    def monthly_rows(self, tenant_id: str) -> list[sqlite3.Row]:
        return self._conn.execute(
            f"SELECT * FROM agg_sales_monthly WHERE tenant_id = ? ORDER BY month, {_SALES_KEY_SQL}",
            (tenant_id,),
        ).fetchall()

    def quality(self, tenant_id: str, day: date) -> sqlite3.Row | None:
        return self._conn.execute(
            "SELECT * FROM agg_quality_daily WHERE tenant_id = ? AND day = ?",
            (tenant_id, day.isoformat()),
        ).fetchone()

    def runs(self, tenant_id: str) -> list[sqlite3.Row]:
        return self._conn.execute(
            "SELECT * FROM agg_runs WHERE tenant_id = ? ORDER BY started_at", (tenant_id,)
        ).fetchall()

    def days(self, tenant_id: str) -> list[str]:
        sql = "SELECT DISTINCT day FROM agg_sales_daily WHERE tenant_id = ? ORDER BY day"
        return [r[0] for r in self._conn.execute(sql, (tenant_id,))]
