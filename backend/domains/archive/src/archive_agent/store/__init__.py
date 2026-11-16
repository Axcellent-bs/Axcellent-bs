"""아카이브가 소유하는 데이터(SQLite). 테이블 작성자는 아카이브 하나다.

- 공통: `audit_logs`, `idempotency`, `records`(결정이 없어 기록만 남긴 것)
- 목 전용(실제에서는 다른 서비스가 가진다): `mock_cards`(memory), `gate_decisions`(안전 레이어)

분리 직후(2단계)에는 인사와 같은 SQLite 파일(`HR_DB_PATH`)을 읽는다. 파일이 갈라지는 것은 4단계다.
모든 테이블에 `tenant_id`를 둔다. 스키마는 전 문장 `IF NOT EXISTS`다.
"""

from __future__ import annotations

import sqlite3
import threading
from contextlib import contextmanager
from typing import Any

from ax_contracts.schemas import TENANT_ID_DEFAULT

SCHEMA = f"""
CREATE TABLE IF NOT EXISTS audit_logs (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  tenant_id TEXT NOT NULL DEFAULT '{TENANT_ID_DEFAULT}',
  context_id TEXT NOT NULL,
  kind TEXT NOT NULL,
  payload TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS idempotency (
  key TEXT PRIMARY KEY,
  tenant_id TEXT NOT NULL DEFAULT '{TENANT_ID_DEFAULT}',
  result TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS records (
  record_id TEXT PRIMARY KEY,
  tenant_id TEXT NOT NULL DEFAULT '{TENANT_ID_DEFAULT}',
  context_id TEXT NOT NULL,
  kind TEXT NOT NULL,
  summary TEXT NOT NULL,
  source_refs TEXT NOT NULL,
  author TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS mock_cards (
  card_id TEXT PRIMARY KEY,
  tenant_id TEXT NOT NULL DEFAULT '{TENANT_ID_DEFAULT}',
  data TEXT NOT NULL,
  created_at TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS gate_decisions (
  idem_key TEXT PRIMARY KEY,
  tenant_id TEXT NOT NULL DEFAULT '{TENANT_ID_DEFAULT}',
  action TEXT NOT NULL,
  context_id TEXT NOT NULL,
  decision TEXT NOT NULL,
  summary TEXT NOT NULL,
  params TEXT NOT NULL,
  status TEXT NOT NULL,
  created_at TEXT NOT NULL,
  decided_at TEXT
);
"""


class Store:
    """SQLite 연결 하나를 스레드 잠금으로 감싼 얇은 래퍼."""

    def __init__(self, path: str = ":memory:") -> None:
        self.path = path
        self._lock = threading.RLock()
        self._conn = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self._conn.row_factory = sqlite3.Row
        with self._lock:
            self._conn.executescript(SCHEMA)

    def execute(self, sql: str, params: tuple[Any, ...] | list[Any] = ()) -> int:
        """쓰기 한 문장을 실행한다(자동 확정, `transaction()` 안에서는 묶음 단위로 확정).

        INSERT는 마지막 행 ID(없으면 영향 행 수), UPDATE·DELETE는 영향 행 수를 돌려준다.
        """
        with self._lock:
            cur = self._conn.execute(sql, params)
            if sql.lstrip().upper().startswith(("UPDATE", "DELETE")):
                return cur.rowcount
            return cur.lastrowid if cur.lastrowid else cur.rowcount

    def one(self, sql: str, params: tuple[Any, ...] | list[Any] = ()) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(sql, params).fetchone()
        return dict(row) if row else None

    def all(self, sql: str, params: tuple[Any, ...] | list[Any] = ()) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(sql, params).fetchall()
        return [dict(r) for r in rows]

    @contextmanager
    def transaction(self):  # type: ignore[no-untyped-def]
        """여러 문장을 한 번에 확정한다. 중간에 예외가 나면 모두 되돌린다."""
        with self._lock:
            self._conn.execute("BEGIN")
            try:
                yield self
            except Exception:
                self._conn.execute("ROLLBACK")
                raise
            else:
                self._conn.execute("COMMIT")

    def close(self) -> None:
        with self._lock:
            self._conn.close()
