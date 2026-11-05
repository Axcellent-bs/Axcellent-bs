"""미디어 비용 장부 (SQLite). 예약 → 확정으로 월 누적을 센다. `briefs.py`와 같은 방식이다.

- 파일은 환경변수 `MARKETING_MEDIA_LEDGER_DB`. 비어 있으면 메모리(`:memory:`)라
  다시 시작하면 월 누적이 0으로 돌아간다(테스트·로컬 기본).
  실제 공급자를 켤 때는 파일을 정해야 한다.
- 열 수 없으면 `LedgerError`로 시작 때 실패한다(조용히 메모리로 넘어가지 않는다).
- 행 상태: `reserved`(호출 전 예약, 추정액) → `settled`(확정, 실제 또는 추정 사용량 기준) |
  `failed`(공급자 오류. 과금 여부를 모르므로 추정액을 그대로 센다) |
  `released`(관문이 막아 호출하지 않음. 세지 않는다).
- 월 누적 = 같은 달의 `settled`(확정액) + `reserved`·`failed`(추정액).
- 호출 직전 예약은 잠금 안에서 월 누적을 보고 한 번에 넣는다(동시에 와도 상한을 넘기지 않는다).
- 프로세스가 예약 뒤에 죽으면 `reserved` 행이 남아 월 누적을 계속 채운다(보수적).
  사람이 확인해 지운다.
- 키·토큰은 저장하지 않는다.
"""

from __future__ import annotations

import os
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal
from typing import Literal

from marketing_agent.media.config import LEDGER_DB_ENV

UsageSource = Literal["actual", "estimate"]

_SCHEMA = """
CREATE TABLE IF NOT EXISTS media_ledger (
    seq           INTEGER PRIMARY KEY AUTOINCREMENT,
    key           TEXT NOT NULL,
    month         TEXT NOT NULL,
    status        TEXT NOT NULL,
    media_kind    TEXT NOT NULL,
    provider      TEXT NOT NULL,
    model_id      TEXT NOT NULL,
    krw_est       INTEGER NOT NULL,
    usd_est       TEXT,
    usd_actual    TEXT,
    krw_actual    INTEGER,
    fx            TEXT,
    fx_as_of      TEXT,
    usage_source  TEXT,
    result_json   TEXT,
    created_at    TEXT NOT NULL,
    settled_at    TEXT
);
CREATE INDEX IF NOT EXISTS media_ledger_key ON media_ledger (key);
CREATE INDEX IF NOT EXISTS media_ledger_month ON media_ledger (month);
"""


class LedgerError(Exception):
    """장부를 열거나 읽거나 쓰지 못했다(디스크·권한·잠금)."""


@dataclass(frozen=True)
class LedgerRow:
    seq: int
    key: str
    month: str
    status: str
    media_kind: str
    provider: str
    model_id: str
    krw_est: int
    usd_est: Decimal | None
    usd_actual: Decimal | None
    krw_actual: int | None
    fx: Decimal | None
    fx_as_of: str | None
    usage_source: str | None
    result_json: str | None


@dataclass(frozen=True)
class Reservation:
    outcome: Literal["reserved", "settled", "in_progress", "over_budget"]
    # reserved면 새 행, settled면 이전 확정 행, in_progress면 진행 중인 행
    row: LedgerRow | None = None
    month_total: int = 0


_COLUMNS = (
    "seq, key, month, status, media_kind, provider, model_id, krw_est, usd_est, usd_actual,"
    " krw_actual, fx, fx_as_of, usage_source, result_json"
)


def _dec(value: str | None) -> Decimal | None:
    return Decimal(value) if value is not None else None


def _row(values: tuple) -> LedgerRow:
    seq, key, month, status, kind, provider, model_id, krw_est = values[:8]
    return LedgerRow(
        seq, key, month, status, kind, provider, model_id, krw_est,
        _dec(values[8]), _dec(values[9]), values[10], _dec(values[11]), values[12],
        values[13], values[14],
    )  # fmt: skip


class MediaLedger:
    """`path`가 없으면 환경변수 `MARKETING_MEDIA_LEDGER_DB`, 그것도 없으면 메모리."""

    def __init__(self, path: str | None = None) -> None:
        self.path = (
            path if path is not None else (os.getenv(LEDGER_DB_ENV, "").strip() or ":memory:")
        )
        self._lock = threading.Lock()
        try:
            self._conn = sqlite3.connect(self.path, check_same_thread=False, timeout=5.0)
            with self._lock, self._conn:
                self._conn.executescript(_SCHEMA)
        except sqlite3.Error as error:
            raise LedgerError(f"미디어 장부를 열 수 없다: {type(error).__name__}") from error

    def close(self) -> None:
        self._conn.close()

    def _query(self, sql: str, args: tuple = ()) -> list[tuple]:
        try:
            with self._lock:
                return self._conn.execute(sql, args).fetchall()
        except sqlite3.Error as error:
            raise LedgerError(f"미디어 장부를 읽을 수 없다: {type(error).__name__}") from error

    def month_total(self, month: str) -> int:
        """같은 달 확정액 + 예약·실패 추정액."""
        rows = self._query(
            "SELECT COALESCE(SUM(CASE WHEN status = 'settled' THEN COALESCE(krw_actual, krw_est)"
            " ELSE krw_est END), 0) FROM media_ledger"
            " WHERE month = ? AND status IN ('settled', 'reserved', 'failed')",
            (month,),
        )
        return int(rows[0][0])

    def settled_for(self, key: str) -> LedgerRow | None:
        rows = self._query(
            f"SELECT {_COLUMNS} FROM media_ledger WHERE key = ? AND status = 'settled'"  # noqa: S608
            " ORDER BY seq DESC LIMIT 1",
            (key,),
        )
        return _row(rows[0]) if rows else None

    def reserve(
        self,
        *,
        key: str,
        month: str,
        media_kind: str,
        provider: str,
        model_id: str,
        krw_est: int,
        usd_est: Decimal | None,
        monthly_cap: int | None,
        now: datetime,
    ) -> Reservation:
        """키별 1행 예약. 이미 확정된 키는 그 행을, 진행 중이면 `in_progress`를 돌려준다.

        `monthly_cap`이 있고 (월 누적 + `krw_est`)가 넘으면 행을 만들지 않고 `over_budget`.
        """
        try:
            with self._lock, self._conn:
                done = self._conn.execute(
                    f"SELECT {_COLUMNS} FROM media_ledger WHERE key = ? AND status = 'settled'"  # noqa: S608
                    " ORDER BY seq DESC LIMIT 1",
                    (key,),
                ).fetchall()
                if done:
                    return Reservation("settled", _row(done[0]))
                running = self._conn.execute(
                    f"SELECT {_COLUMNS} FROM media_ledger WHERE key = ? AND status = 'reserved'"  # noqa: S608
                    " LIMIT 1",
                    (key,),
                ).fetchall()
                if running:
                    return Reservation("in_progress", _row(running[0]))
                total = int(
                    self._conn.execute(
                        "SELECT COALESCE(SUM(CASE WHEN status = 'settled'"
                        " THEN COALESCE(krw_actual, krw_est) ELSE krw_est END), 0)"
                        " FROM media_ledger WHERE month = ?"
                        " AND status IN ('settled', 'reserved', 'failed')",
                        (month,),
                    ).fetchone()[0]
                )
                if monthly_cap is not None and total + krw_est > monthly_cap:
                    return Reservation("over_budget", None, total)
                cursor = self._conn.execute(
                    "INSERT INTO media_ledger (key, month, status, media_kind, provider, model_id,"
                    " krw_est, usd_est, created_at) VALUES (?, ?, 'reserved', ?, ?, ?, ?, ?, ?)",
                    (
                        key, month, media_kind, provider, model_id, krw_est,
                        str(usd_est) if usd_est is not None else None, now.isoformat(),
                    ),
                )  # fmt: skip
                seq = cursor.lastrowid
                inserted = self._conn.execute(
                    f"SELECT {_COLUMNS} FROM media_ledger WHERE seq = ?",  # noqa: S608
                    (seq,),
                ).fetchall()
            return Reservation("reserved", _row(inserted[0]), total)
        except sqlite3.Error as error:
            raise LedgerError(f"미디어 장부에 예약할 수 없다: {type(error).__name__}") from error

    def _finish(self, seq: int, sql: str, args: tuple) -> None:
        try:
            with self._lock, self._conn:
                self._conn.execute(sql, (*args, seq))
        except sqlite3.Error as error:
            raise LedgerError(f"미디어 장부를 고칠 수 없다: {type(error).__name__}") from error

    def release(self, seq: int) -> None:
        """관문이 막아 호출하지 않은 예약을 푼다(월 누적에서 빠진다)."""
        self._finish(seq, "UPDATE media_ledger SET status = 'released' WHERE seq = ?", ())

    def mark_failed(self, seq: int, now: datetime) -> None:
        """공급자 오류. 과금 여부를 모르므로 추정액을 계속 센다."""
        self._finish(
            seq,
            "UPDATE media_ledger SET status = 'failed', settled_at = ? WHERE seq = ?",
            (now.isoformat(),),
        )

    def settle(
        self,
        seq: int,
        *,
        model_id: str,
        usd_actual: Decimal | None,
        krw_actual: int,
        fx: Decimal | None,
        fx_as_of: str | None,
        usage_source: UsageSource,
        result_json: str,
        now: datetime,
    ) -> None:
        """예약을 확정액으로 바꾼다. 사용량이 없으면 호출한 쪽이 추정액을 `krw_actual`로 넘긴다."""
        self._finish(
            seq,
            "UPDATE media_ledger SET status = 'settled', model_id = ?, usd_actual = ?,"
            " krw_actual = ?, fx = ?, fx_as_of = ?, usage_source = ?, result_json = ?,"
            " settled_at = ? WHERE seq = ?",
            (
                model_id,
                str(usd_actual) if usd_actual is not None else None,
                krw_actual,
                str(fx) if fx is not None else None,
                fx_as_of,
                usage_source,
                result_json,
                now.isoformat(),
            ),
        )
