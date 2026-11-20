"""미디어 사용량 장부 (SQLite). 예약 → 확정으로 월 사용량을 센다. `briefs.py`와 같은 방식이다.

- 센 것은 원화가 아니라 사용량이다(`usage.py`): 영상 초, 이미지 장, BGM 곡. 종류별로 따로 센다.
  토큰이 응답에 있으면 입력·출력 토큰도 같이 남긴다. 예전 원화 칸(`krw_*`·`usd_*`·`fx*`)은
  옛 장부 파일 호환용으로 남아 있고, 새 행에는 0·빈 값이 들어간다.
- 파일은 환경변수 `MARKETING_MEDIA_LEDGER_DB`. 비어 있으면 메모리(`:memory:`)라
  다시 시작하면 월 사용량이 0으로 돌아간다(테스트·로컬 기본).
  실제 공급자를 켤 때는 파일을 정해야 한다.
- 열 수 없으면 `LedgerError`로 시작 때 실패한다(조용히 메모리로 넘어가지 않는다).
- 행 상태: `reserved`(호출 전 예약, 추정 사용량) → `settled`(확정) |
  `failed`(공급자 오류. 과금 여부를 모르므로 추정 사용량을 그대로 센다) |
  `released`(관문이 막아 호출하지 않음. 세지 않는다).
- 월 사용량 = 같은 달 같은 종류의 `settled`·`reserved`·`failed` 사용량 합.
- 호출 직전 예약은 잠금 안에서 월 사용량을 보고 한 번에 넣는다(동시에 와도 예산을 넘기지 않는다).
  예산은 우리 키(서버 키)에만 걸고, 고객사 키는 `monthly_cap=None`으로 부른다.
- 프로세스가 예약 뒤에 죽으면 `reserved` 행이 남아 월 사용량을 계속 채운다(보수적).
  사람이 확인해 지운다.
- 키·토큰은 저장하지 않는다.
"""

from __future__ import annotations

import os
import sqlite3
import threading
from dataclasses import dataclass
from datetime import datetime
from typing import Literal

from marketing_agent import company_scope
from marketing_agent.media.config import LEDGER_DB_ENV
from marketing_agent.media.usage import KINDS, UsageAmount

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
    settled_at    TEXT,
    tenant_id     TEXT NOT NULL DEFAULT 'catbooks'
);
CREATE INDEX IF NOT EXISTS media_ledger_key ON media_ledger (key);
CREATE INDEX IF NOT EXISTS media_ledger_month ON media_ledger (month);
"""

# 사용량 칸. 옛 장부 파일에는 없으니 열 때 한 번 더한다(없는 칸만)
_USAGE_COLUMNS = (
    ("usage_unit", "TEXT"),
    ("usage_est", "INTEGER"),
    ("usage_actual", "INTEGER"),
    ("input_tokens", "INTEGER"),
    ("output_tokens", "INTEGER"),
)


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
    usage_unit: str | None
    usage_est: int | None
    usage_actual: int | None
    input_tokens: int | None
    output_tokens: int | None
    usage_source: str | None
    result_json: str | None


@dataclass(frozen=True)
class Reservation:
    outcome: Literal["reserved", "settled", "in_progress", "over_budget"]
    # reserved면 새 행, settled면 이전 확정 행, in_progress면 진행 중인 행
    row: LedgerRow | None = None
    # 같은 종류의 이번 달 사용량(예약한 건 제외). 단위는 종류의 사용량 단위
    month_used: int = 0


_COLUMNS = (
    "seq, key, month, status, media_kind, provider, model_id, usage_unit, usage_est,"
    " usage_actual, input_tokens, output_tokens, usage_source, result_json"
)
_COUNTED = "status IN ('settled', 'reserved', 'failed')"
_USED_SQL = (
    "COALESCE(SUM(CASE WHEN status = 'settled' THEN COALESCE(usage_actual, usage_est, 0)"
    " ELSE COALESCE(usage_est, 0) END), 0)"
)


def _row(values: tuple) -> LedgerRow:
    return LedgerRow(*values)


class MediaLedger:
    """`path`가 없으면 환경변수 `MARKETING_MEDIA_LEDGER_DB`, 그것도 없으면 메모리."""

    def __init__(self, path: str | None = None, company_id: str | None = None) -> None:
        self.path = (
            path if path is not None else (os.getenv(LEDGER_DB_ENV, "").strip() or ":memory:")
        )
        self.company_id = company_scope.scope_id(company_id)  # 회사 칸(여러 회사 1-6)
        self._lock = threading.Lock()
        try:
            self._conn = sqlite3.connect(self.path, check_same_thread=False, timeout=5.0)
            with self._lock, self._conn:
                self._conn.executescript(_SCHEMA)
                have = {r[1] for r in self._conn.execute("PRAGMA table_info(media_ledger)")}
                for name, kind in _USAGE_COLUMNS:
                    if name not in have:
                        self._conn.execute(f"ALTER TABLE media_ledger ADD COLUMN {name} {kind}")  # noqa: S608
                company_scope.ensure_tenant_column(self._conn, "media_ledger")  # 옛 줄은 캣북스
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

    def month_usage(self, month: str, kind: str) -> int:
        """같은 달 같은 종류의 확정 사용량 + 예약·실패 추정 사용량(종류의 단위)."""
        rows = self._query(
            f"SELECT {_USED_SQL} FROM media_ledger"  # noqa: S608
            f" WHERE month = ? AND media_kind = ? AND tenant_id = ? AND {_COUNTED}",
            (month, kind, self.company_id),
        )
        return int(rows[0][0])

    def month_usage_all(self, month: str) -> dict[str, int]:
        """종류별 월 사용량. 종류마다 단위가 달라 더하지 않는다."""
        return {kind: self.month_usage(month, kind) for kind in KINDS}

    def settled_for(self, key: str) -> LedgerRow | None:
        rows = self._query(
            f"SELECT {_COLUMNS} FROM media_ledger"  # noqa: S608
            " WHERE key = ? AND tenant_id = ? AND status = 'settled'"
            " ORDER BY seq DESC LIMIT 1",
            (key, self.company_id),
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
        usage: UsageAmount,
        monthly_cap: int | None,
        now: datetime,
    ) -> Reservation:
        """키별 1행 예약. 이미 확정된 키는 그 행을, 진행 중이면 `in_progress`를 돌려준다.

        `monthly_cap`(그 종류의 월 예산)이 있고 (월 사용량 + 이번 예상 사용량)이 넘으면
        행을 만들지 않고 `over_budget`. 없으면(고객사 키) 예산 검사를 하지 않는다.
        """
        try:
            with self._lock, self._conn:
                done = self._conn.execute(
                    f"SELECT {_COLUMNS} FROM media_ledger"  # noqa: S608
                    " WHERE key = ? AND tenant_id = ? AND status = 'settled'"
                    " ORDER BY seq DESC LIMIT 1",
                    (key, self.company_id),
                ).fetchall()
                if done:
                    return Reservation("settled", _row(done[0]))
                running = self._conn.execute(
                    f"SELECT {_COLUMNS} FROM media_ledger"  # noqa: S608
                    " WHERE key = ? AND tenant_id = ? AND status = 'reserved' LIMIT 1",
                    (key, self.company_id),
                ).fetchall()
                if running:
                    return Reservation("in_progress", _row(running[0]))
                used = int(
                    self._conn.execute(
                        f"SELECT {_USED_SQL} FROM media_ledger"  # noqa: S608
                        f" WHERE month = ? AND media_kind = ? AND tenant_id = ? AND {_COUNTED}",
                        (month, media_kind, self.company_id),
                    ).fetchone()[0]
                )
                if monthly_cap is not None and used + usage.amount > monthly_cap:
                    return Reservation("over_budget", None, used)
                cursor = self._conn.execute(
                    "INSERT INTO media_ledger (key, month, status, media_kind, provider, model_id,"
                    " krw_est, usage_unit, usage_est, created_at, tenant_id)"
                    " VALUES (?, ?, 'reserved', ?, ?, ?, 0, ?, ?, ?, ?)",
                    (
                        key, month, media_kind, provider, model_id, usage.unit, usage.amount,
                        now.isoformat(), self.company_id,
                    ),
                )  # fmt: skip
                inserted = self._conn.execute(
                    f"SELECT {_COLUMNS} FROM media_ledger WHERE seq = ?",  # noqa: S608
                    (cursor.lastrowid,),
                ).fetchall()
            return Reservation("reserved", _row(inserted[0]), used)
        except sqlite3.Error as error:
            raise LedgerError(f"미디어 장부에 예약할 수 없다: {type(error).__name__}") from error

    def _finish(self, seq: int, sql: str, args: tuple) -> None:
        try:
            with self._lock, self._conn:
                self._conn.execute(sql, (*args, seq))
        except sqlite3.Error as error:
            raise LedgerError(f"미디어 장부를 고칠 수 없다: {type(error).__name__}") from error

    def release(self, seq: int) -> None:
        """관문이 막아 호출하지 않은 예약을 푼다(월 사용량에서 빠진다)."""
        self._finish(seq, "UPDATE media_ledger SET status = 'released' WHERE seq = ?", ())

    def mark_failed(self, seq: int, now: datetime) -> None:
        """공급자 오류. 과금 여부를 모르므로 추정 사용량을 계속 센다."""
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
        usage: UsageAmount,
        usage_source: UsageSource,
        result_json: str,
        now: datetime,
    ) -> None:
        """예약을 확정 사용량으로 바꾼다. 토큰은 응답에 있을 때만 들어간다."""
        self._finish(
            seq,
            "UPDATE media_ledger SET status = 'settled', model_id = ?, usage_unit = ?,"
            " usage_actual = ?, input_tokens = ?, output_tokens = ?, usage_source = ?,"
            " result_json = ?, settled_at = ? WHERE seq = ?",
            (
                model_id,
                usage.unit,
                usage.amount,
                usage.input_tokens,
                usage.output_tokens,
                usage_source,
                result_json,
                now.isoformat(),
            ),
        )
