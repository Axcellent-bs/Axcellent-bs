"""일별 집계 저장소 `aggregates.db` (설계 3절·5.5절).

- 표: `agg_sales_daily`(일별 매출, 13개월) · `agg_sales_monthly`(그보다 오래된 달, 5년) ·
  `agg_quality_daily`(품질) · `agg_runs`(계산 기록) · 기간 스냅샷 `agg_buyers_period`·`agg_cohorts`·
  `agg_at_risk`(사람 수 칸은 숨긴 채 저장: 숫자 NULL + `*_hidden` 1)
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
from datetime import date, datetime, timedelta
from pathlib import Path

from ax_contracts.data_access.sales_aggregates import (
    AggCompareRow,
    AggRowBase,
    AggRun,
    AtRiskRow,
    BuyerPeriodRow,
    ChannelValueRow,
    CohortRow,
    HiddenCount,
    PairRow,
    QualityDailyRow,
    SalesDailyRow,
    SegmentProfileRow,
)

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
CREATE TABLE IF NOT EXISTS agg_buyers_period (
    {_COMMON}, as_of TEXT NOT NULL, period TEXT NOT NULL, dim_kind TEXT NOT NULL,
    dim_code TEXT NOT NULL, attr_cell TEXT NOT NULL,
    buyers INTEGER, buyers_hidden INTEGER NOT NULL DEFAULT 0,
    new_buyers INTEGER, new_hidden INTEGER NOT NULL DEFAULT 0,
    repeat_buyers INTEGER, repeat_hidden INTEGER NOT NULL DEFAULT 0,
    new_revenue INTEGER, repeat_revenue INTEGER, title TEXT,
    PRIMARY KEY (tenant_id, as_of, period, dim_kind, dim_code, attr_cell)
);
CREATE TABLE IF NOT EXISTS agg_cohorts (
    {_COMMON}, as_of TEXT NOT NULL, cohort_month TEXT NOT NULL, attr_cell TEXT NOT NULL,
    first_buyers INTEGER, first_hidden INTEGER NOT NULL DEFAULT 0, repeat TEXT NOT NULL,
    PRIMARY KEY (tenant_id, as_of, cohort_month, attr_cell)
);
CREATE TABLE IF NOT EXISTS agg_at_risk (
    {_COMMON}, as_of TEXT NOT NULL, attr_cell TEXT NOT NULL,
    regulars INTEGER, regulars_hidden INTEGER NOT NULL DEFAULT 0,
    at_risk INTEGER, at_risk_hidden INTEGER NOT NULL DEFAULT 0, top_genre TEXT,
    churn_days INTEGER NOT NULL, repeat_min_orders INTEGER NOT NULL,
    PRIMARY KEY (tenant_id, as_of, attr_cell)
);
CREATE TABLE IF NOT EXISTS agg_pairs (
    {_COMMON}, as_of TEXT NOT NULL, period TEXT NOT NULL, level TEXT NOT NULL,
    attr_cell TEXT NOT NULL, code_a TEXT NOT NULL, code_b TEXT NOT NULL,
    together_buyers INTEGER NOT NULL, a_buyers INTEGER NOT NULL, b_buyers INTEGER NOT NULL,
    overall_buyers INTEGER NOT NULL, together_orders INTEGER, hidden_slots INTEGER NOT NULL,
    PRIMARY KEY (tenant_id, as_of, period, level, attr_cell, code_a, code_b)
);
CREATE TABLE IF NOT EXISTS agg_segment_profile (
    {_COMMON}, as_of TEXT NOT NULL, period TEXT NOT NULL, attr_cell TEXT NOT NULL,
    item_level TEXT NOT NULL, item_code TEXT NOT NULL,
    buyers INTEGER, buyers_hidden INTEGER NOT NULL DEFAULT 0, item_buyers INTEGER NOT NULL,
    orders INTEGER, revenue INTEGER, title TEXT,
    PRIMARY KEY (tenant_id, as_of, period, attr_cell, item_level, item_code)
);
CREATE TABLE IF NOT EXISTS agg_channel_value (
    {_COMMON}, as_of TEXT NOT NULL, period TEXT NOT NULL, source_label TEXT NOT NULL,
    new_buyers INTEGER, new_hidden INTEGER NOT NULL DEFAULT 0, horizon_net INTEGER,
    horizon_orders INTEGER, repeaters INTEGER, excluded_incomplete INTEGER NOT NULL,
    horizon_days INTEGER NOT NULL,
    PRIMARY KEY (tenant_id, as_of, period, source_label)
);
CREATE TABLE IF NOT EXISTS agg_event_base (
    {_COMMON}, as_of TEXT NOT NULL, catalog_version TEXT NOT NULL, candidates TEXT NOT NULL,
    PRIMARY KEY (tenant_id, as_of)
);
CREATE TABLE IF NOT EXISTS agg_compare (
    tenant_id TEXT NOT NULL, as_of TEXT NOT NULL, target TEXT NOT NULL, same INTEGER NOT NULL,
    diffs TEXT NOT NULL DEFAULT '[]', compared_at TEXT NOT NULL,
    PRIMARY KEY (tenant_id, as_of, target)
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


SNAPSHOT_TABLES = ("agg_buyers_period", "agg_cohorts", "agg_at_risk")
MORE_TABLES = ("agg_pairs", "agg_segment_profile", "agg_channel_value")
_INSERT_PAIR = (
    f"INSERT INTO agg_pairs ({_COMMON_COLS}, as_of, period, level, attr_cell, code_a, code_b, "
    "together_buyers, a_buyers, b_buyers, overall_buyers, together_orders, hidden_slots) "
    f"VALUES ({', '.join('?' * 16)})"
)
_INSERT_PROFILE = (
    f"INSERT INTO agg_segment_profile ({_COMMON_COLS}, as_of, period, attr_cell, item_level, "
    "item_code, buyers, buyers_hidden, item_buyers, orders, revenue, title) "
    f"VALUES ({', '.join('?' * 15)})"
)
_INSERT_CHANNEL = (
    f"INSERT INTO agg_channel_value ({_COMMON_COLS}, as_of, period, source_label, new_buyers, "
    "new_hidden, horizon_net, horizon_orders, repeaters, excluded_incomplete, horizon_days) "
    f"VALUES ({', '.join('?' * 14)})"
)


def _pair_values(tenant_id: str, as_of: str, r: PairRow) -> tuple:
    return (
        *_common(tenant_id, r), as_of, r.period, r.level, cell_text(r.attr_cell), r.code_a,
        r.code_b, r.together_buyers, r.a_buyers, r.b_buyers, r.overall_buyers, r.together_orders,
        r.hidden_slots,
    )  # fmt: skip


def _profile_values(tenant_id: str, as_of: str, r: SegmentProfileRow) -> tuple:
    return (
        *_common(tenant_id, r), as_of, r.period, cell_text(r.attr_cell), r.item_level,
        r.item_code, *_hc(r.buyers), r.item_buyers, r.orders, r.revenue, r.title,
    )  # fmt: skip


def _channel_values(tenant_id: str, as_of: str, r: ChannelValueRow) -> tuple:
    return (
        *_common(tenant_id, r), as_of, r.period, r.source_label, *_hc(r.new_buyers),
        r.horizon_net, r.horizon_orders, r.repeaters, r.excluded_incomplete, r.horizon_days,
    )  # fmt: skip


SNAPSHOT_DAILY_DAYS = 35
KEEP_SNAPSHOT_MONTHS = 60
_INSERT_BUYERS = (
    f"INSERT INTO agg_buyers_period ({_COMMON_COLS}, as_of, period, dim_kind, dim_code, "
    "attr_cell, buyers, buyers_hidden, new_buyers, new_hidden, repeat_buyers, repeat_hidden, "
    "new_revenue, repeat_revenue, title) VALUES "
    f"({', '.join('?' * 18)})"
)
_INSERT_COHORT = (
    f"INSERT INTO agg_cohorts ({_COMMON_COLS}, as_of, cohort_month, attr_cell, first_buyers, "
    "first_hidden, repeat) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)"
)
_INSERT_AT_RISK = (
    f"INSERT INTO agg_at_risk ({_COMMON_COLS}, as_of, attr_cell, regulars, regulars_hidden, "
    "at_risk, at_risk_hidden, top_genre, churn_days, repeat_min_orders) "
    f"VALUES ({', '.join('?' * 13)})"
)


def cell_text(cell: dict[str, str]) -> str:
    """속성 칸 글: `age_band=20s;gender=female` (넣은 순서 = 카탈로그 순서). 비면 빈 글자."""
    return ";".join(f"{k}={v}" for k, v in cell.items())


def _months_back(day: date, months: int) -> date:
    index = day.year * 12 + (day.month - 1) - months
    return date(index // 12, index % 12 + 1, 1)


def _common(tenant_id: str, r: AggRowBase) -> tuple:
    return (tenant_id, r.run_id, _iso(r.computed_at), r.source_cursor)


def _hc(h: HiddenCount) -> tuple[int | None, int]:
    return (None if h.hidden else h.value), int(h.hidden)


def _buyer_values(tenant_id: str, as_of: str, r: BuyerPeriodRow) -> tuple:
    return (
        *_common(tenant_id, r), as_of, r.period, r.dim_kind, r.dim_code, cell_text(r.attr_cell),
        *_hc(r.buyers), *_hc(r.new_buyers), *_hc(r.repeat_buyers), r.new_revenue,
        r.repeat_revenue, r.title,
    )  # fmt: skip


def _cohort_values(tenant_id: str, as_of: str, r: CohortRow) -> tuple:
    repeat = {d: (None if h is None else h.model_dump()) for d, h in r.repeat.items()}
    return (
        *_common(tenant_id, r), as_of, r.cohort_month, cell_text(r.attr_cell),
        *_hc(r.first_buyers), _json(repeat),
    )  # fmt: skip


def _at_risk_values(tenant_id: str, as_of: str, r: AtRiskRow) -> tuple:
    return (
        *_common(tenant_id, r), as_of, cell_text(r.attr_cell), *_hc(r.regulars),
        *_hc(r.at_risk), r.top_genre, r.churn_days, r.repeat_min_orders,
    )  # fmt: skip


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

    # ------------------------------------------------------------------ 기간 스냅샷 (설계 3.3~3.5)

    def replace_snapshots(
        self,
        tenant_id: str,
        as_of: date,
        buyers: Sequence[BuyerPeriodRow],
        cohorts: Sequence[CohortRow],
        at_risk: Sequence[AtRiskRow],
    ) -> None:
        """그 회사·그 기준일의 스냅샷을 통째로 바꿔 넣는다. 숨김은 행을 만들 때 이미 적용돼 있다
        (숨긴 칸은 숫자 NULL + hidden 1)."""
        a = as_of.isoformat()
        with self._tx() as conn:
            for table in SNAPSHOT_TABLES:
                conn.execute(
                    f"DELETE FROM {table} WHERE tenant_id = ? AND as_of = ?", (tenant_id, a)
                )
            conn.executemany(_INSERT_BUYERS, [_buyer_values(tenant_id, a, r) for r in buyers])
            conn.executemany(_INSERT_COHORT, [_cohort_values(tenant_id, a, r) for r in cohorts])
            conn.executemany(_INSERT_AT_RISK, [_at_risk_values(tenant_id, a, r) for r in at_risk])

    def prune_snapshots(self, tenant_id: str, as_of: date) -> int:
        """스냅샷 보관 (설계 5.5·결정 필요 4): 최근 35일 기준일은 날마다, 그 앞은 달의 마지막
        기준일 하나만 5년(60개월) 둔다. 지운 행 수를 돌려준다."""
        recent = (as_of - timedelta(days=SNAPSHOT_DAILY_DAYS)).isoformat()
        oldest = _months_back(as_of, KEEP_SNAPSHOT_MONTHS).isoformat()
        removed = 0
        with self._tx() as conn:
            for table in (*SNAPSHOT_TABLES, *MORE_TABLES, "agg_event_base"):
                removed += conn.execute(
                    f"DELETE FROM {table} WHERE tenant_id = ? AND (as_of < ? OR (as_of < ? AND "
                    f"as_of != (SELECT MAX(t2.as_of) FROM {table} t2 WHERE t2.tenant_id = "
                    f"{table}.tenant_id AND substr(t2.as_of, 1, 7) = "
                    f"substr({table}.as_of, 1, 7))))",
                    (tenant_id, oldest, recent),
                ).rowcount
        return removed

    def buyer_rows(self, tenant_id: str, as_of: date) -> list[sqlite3.Row]:
        return self._conn.execute(
            "SELECT * FROM agg_buyers_period WHERE tenant_id = ? AND as_of = ? "
            "ORDER BY period, dim_kind, dim_code, attr_cell",
            (tenant_id, as_of.isoformat()),
        ).fetchall()

    def cohort_rows(self, tenant_id: str, as_of: date) -> list[sqlite3.Row]:
        return self._conn.execute(
            "SELECT * FROM agg_cohorts WHERE tenant_id = ? AND as_of = ? "
            "ORDER BY cohort_month, attr_cell",
            (tenant_id, as_of.isoformat()),
        ).fetchall()

    def at_risk_rows(self, tenant_id: str, as_of: date) -> list[sqlite3.Row]:
        return self._conn.execute(
            "SELECT * FROM agg_at_risk WHERE tenant_id = ? AND as_of = ? ORDER BY attr_cell",
            (tenant_id, as_of.isoformat()),
        ).fetchall()

    def replace_more(
        self,
        tenant_id: str,
        as_of: date,
        pairs: Sequence[PairRow],
        profiles: Sequence[SegmentProfileRow],
        channels: Sequence[ChannelValueRow],
    ) -> None:
        """함께 산 상품·즐겨 사는 것·채널 가치 스냅샷을 그 기준일만 통째로 바꿔 넣는다."""
        a = as_of.isoformat()
        with self._tx() as conn:
            for table in MORE_TABLES:
                conn.execute(
                    f"DELETE FROM {table} WHERE tenant_id = ? AND as_of = ?", (tenant_id, a)
                )
            conn.executemany(_INSERT_PAIR, [_pair_values(tenant_id, a, r) for r in pairs])
            conn.executemany(_INSERT_PROFILE, [_profile_values(tenant_id, a, r) for r in profiles])
            conn.executemany(_INSERT_CHANNEL, [_channel_values(tenant_id, a, r) for r in channels])

    def replace_event_base(  # noqa: PLR0913 - 계산 기록 칸
        self,
        tenant_id: str,
        as_of: date,
        run_id: str,
        computed_at: datetime,
        catalog_version: str,
        candidates: Sequence[dict],
        source_cursor: str | None = None,
    ) -> None:
        """이벤트 제안 후보(90일, 사람 수는 k 이상만) 스냅샷을 그 기준일만 바꿔 넣는다 (3-2 ⑦)."""
        a = as_of.isoformat()
        with self._tx() as conn:
            conn.execute(
                "DELETE FROM agg_event_base WHERE tenant_id = ? AND as_of = ?", (tenant_id, a)
            )
            conn.execute(
                f"INSERT INTO agg_event_base ({_COMMON_COLS}, as_of, catalog_version, candidates) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (tenant_id, run_id, _iso(computed_at), source_cursor, a, catalog_version,
                 json.dumps(list(candidates), ensure_ascii=False)),
            )  # fmt: skip

    def event_base(self, tenant_id: str, as_of: date) -> sqlite3.Row | None:
        return self._conn.execute(
            "SELECT * FROM agg_event_base WHERE tenant_id = ? AND as_of = ?",
            (tenant_id, as_of.isoformat()),
        ).fetchone()

    def more_rows(self, table: str, tenant_id: str, as_of: date) -> list[sqlite3.Row]:
        if table not in MORE_TABLES:
            raise ValueError(table)
        return self._conn.execute(
            f"SELECT * FROM {table} WHERE tenant_id = ? AND as_of = ?",
            (tenant_id, as_of.isoformat()),
        ).fetchall()

    def replace_compare(self, tenant_id: str, as_of: date, rows: Sequence[AggCompareRow]) -> None:
        """병행 비교 결과를 그 기준일만 바꿔 넣는다 (설계 6.3)."""
        a = as_of.isoformat()
        with self._tx() as conn:
            conn.execute(
                "DELETE FROM agg_compare WHERE tenant_id = ? AND as_of = ?", (tenant_id, a)
            )
            conn.executemany(
                "INSERT INTO agg_compare (tenant_id, as_of, target, same, diffs, compared_at) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                [(tenant_id, a, r.target, int(r.same), json.dumps(r.diffs, ensure_ascii=False),
                  _iso(r.compared_at)) for r in rows],
            )  # fmt: skip

    def compare_rows(self, tenant_id: str, as_of: date | None = None) -> list[sqlite3.Row]:
        sql = "SELECT * FROM agg_compare WHERE tenant_id = ?"
        args: list[str] = [tenant_id]
        if as_of is not None:
            sql += " AND as_of = ?"
            args.append(as_of.isoformat())
        return self._conn.execute(sql + " ORDER BY as_of, target", args).fetchall()

    def snapshot_days(self, tenant_id: str) -> list[str]:
        sql = "SELECT DISTINCT as_of FROM agg_buyers_period WHERE tenant_id = ? ORDER BY as_of"
        return [r[0] for r in self._conn.execute(sql, (tenant_id,))]

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
