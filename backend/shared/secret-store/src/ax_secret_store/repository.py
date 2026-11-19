"""저장소 인터페이스와 sqlite 구현. 암호화된 값만 다룬다(평문은 여기까지 오지 않는다).

`SecretRepository`를 바꿔 끼우면 다른 DB(postgres 등)로 옮길 수 있다. 지금 구현은 표준 라이브러리
sqlite 파일이다(`AX_SECRET_DB_FILE`). 고객 지원용 DB와 섞지 않는다.
"""

from __future__ import annotations

import os
import sqlite3
from abc import ABC, abstractmethod
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ax_secret_store import errors
from ax_secret_store.errors import SecretStoreError

DB_FILE_ENV = "AX_SECRET_DB_FILE"

SCHEMA = """
CREATE TABLE IF NOT EXISTS secrets (
  tenant TEXT NOT NULL, name TEXT NOT NULL, kid TEXT NOT NULL,
  nonce BLOB NOT NULL, ciphertext BLOB NOT NULL, hint TEXT NOT NULL,
  version INTEGER NOT NULL, created_at TEXT NOT NULL,
  updated_by TEXT NOT NULL, updated_at TEXT NOT NULL,
  PRIMARY KEY (tenant, name)
);
CREATE TABLE IF NOT EXISTS pending (
  id TEXT PRIMARY KEY, op TEXT NOT NULL, tenant TEXT NOT NULL, name TEXT NOT NULL,
  kid TEXT, nonce BLOB, ciphertext BLOB, hint TEXT NOT NULL DEFAULT '',
  fingerprint TEXT NOT NULL DEFAULT '', reason TEXT NOT NULL DEFAULT '',
  requested_by TEXT NOT NULL, requested_at TEXT NOT NULL, review_id TEXT
);
CREATE TABLE IF NOT EXISTS history (
  id INTEGER PRIMARY KEY AUTOINCREMENT, at TEXT NOT NULL, by TEXT NOT NULL,
  action TEXT NOT NULL, tenant TEXT NOT NULL, name TEXT NOT NULL,
  request_id TEXT, review_id TEXT
);
CREATE INDEX IF NOT EXISTS history_tenant ON history (tenant, id);
"""


@dataclass(frozen=True)
class SecretRow:
    tenant: str
    name: str
    kid: str
    nonce: bytes
    ciphertext: bytes
    hint: str
    version: int
    created_at: str
    updated_by: str
    updated_at: str

    def __repr__(self) -> str:  # 암호문도 로그에 남기지 않는다
        return f"SecretRow({self.tenant}/{self.name} v{self.version})"


@dataclass(frozen=True)
class PendingRow:
    id: str
    op: str  # add | replace | delete
    tenant: str
    name: str
    kid: str | None
    nonce: bytes | None
    ciphertext: bytes | None
    hint: str
    fingerprint: str
    reason: str
    requested_by: str
    requested_at: str
    review_id: str | None

    def __repr__(self) -> str:
        return f"PendingRow({self.id} {self.op} {self.tenant}/{self.name})"


class SecretRepository(ABC):
    """암호화된 레코드 저장소. 한 번의 `commit`은 값·이력·대기 요청을 한 트랜잭션으로 바꾼다."""

    @abstractmethod
    def get(self, tenant: str, name: str) -> SecretRow | None: ...

    @abstractmethod
    def list(self, tenants: list[str] | None = None) -> list[SecretRow]: ...

    @abstractmethod
    def put_pending(self, row: PendingRow, history: dict[str, Any]) -> None: ...

    @abstractmethod
    def get_pending(self, request_id: str) -> PendingRow | None: ...

    @abstractmethod
    def list_pending(self, tenants: list[str] | None = None) -> list[PendingRow]: ...

    @abstractmethod
    def set_review(self, request_id: str, review_id: str) -> None: ...

    @abstractmethod
    def commit(
        self,
        *,
        upsert: SecretRow | None = None,
        delete: tuple[str, str] | None = None,
        drop_pending: str | None = None,
        history: dict[str, Any],
    ) -> None: ...

    @abstractmethod
    def history(self, tenants: list[str] | None, limit: int) -> list[dict[str, Any]]: ...


def db_path() -> Path:
    raw = os.getenv(DB_FILE_ENV, "").strip()
    if not raw:
        raise SecretStoreError(errors.DB_PATH_MISSING)
    return Path(raw)


class SqliteRepository(SecretRepository):
    def __init__(self, path: Path) -> None:
        self._path = path
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            with self._conn() as conn:
                conn.executescript(SCHEMA)
            try:
                os.chmod(path, 0o600)
            except OSError:
                pass  # Windows 등
        except (OSError, sqlite3.Error):
            raise SecretStoreError(errors.DB_UNAVAILABLE) from None

    @contextmanager
    def _conn(self) -> Iterator[sqlite3.Connection]:
        conn = sqlite3.connect(self._path, timeout=5.0)
        conn.row_factory = sqlite3.Row
        try:
            with conn:  # 트랜잭션: 성공하면 커밋, 예외면 되돌림
                yield conn
        finally:
            conn.close()

    def _run(self, fn: Callable[[sqlite3.Connection], Any]) -> Any:
        try:
            with self._conn() as conn:
                return fn(conn)
        except sqlite3.Error:
            raise SecretStoreError(errors.DB_UNAVAILABLE) from None

    @staticmethod
    def _secret(r: sqlite3.Row) -> SecretRow:
        return SecretRow(
            tenant=r["tenant"],
            name=r["name"],
            kid=r["kid"],
            nonce=bytes(r["nonce"]),
            ciphertext=bytes(r["ciphertext"]),
            hint=r["hint"],
            version=r["version"],
            created_at=r["created_at"],
            updated_by=r["updated_by"],
            updated_at=r["updated_at"],
        )

    @staticmethod
    def _pending(r: sqlite3.Row) -> PendingRow:
        return PendingRow(
            id=r["id"],
            op=r["op"],
            tenant=r["tenant"],
            name=r["name"],
            kid=r["kid"],
            nonce=bytes(r["nonce"]) if r["nonce"] is not None else None,
            ciphertext=bytes(r["ciphertext"]) if r["ciphertext"] is not None else None,
            hint=r["hint"],
            fingerprint=r["fingerprint"],
            reason=r["reason"],
            requested_by=r["requested_by"],
            requested_at=r["requested_at"],
            review_id=r["review_id"],
        )

    @staticmethod
    def _where(tenants: list[str] | None) -> tuple[str, list[str]]:
        if tenants is None:
            return "", []
        marks = ",".join("?" * len(tenants))
        return f" WHERE tenant IN ({marks})", list(tenants)

    def get(self, tenant: str, name: str) -> SecretRow | None:
        def fn(c: sqlite3.Connection) -> SecretRow | None:
            r = c.execute("SELECT * FROM secrets WHERE tenant=? AND name=?", (tenant, name))
            row = r.fetchone()
            return self._secret(row) if row else None

        return self._run(fn)

    def list(self, tenants: list[str] | None = None) -> list[SecretRow]:
        where, args = self._where(tenants)

        def fn(c: sqlite3.Connection) -> list[SecretRow]:
            q = f"SELECT * FROM secrets{where} ORDER BY tenant, name"
            return [self._secret(r) for r in c.execute(q, args)]

        return self._run(fn)

    @staticmethod
    def _log(c: sqlite3.Connection, h: dict[str, Any]) -> None:
        c.execute(
            "INSERT INTO history (at, by, action, tenant, name, request_id, review_id)"
            " VALUES (?,?,?,?,?,?,?)",
            (
                h["at"],
                h["by"],
                h["action"],
                h["tenant"],
                h["name"],
                h.get("request_id"),
                h.get("review_id"),
            ),
        )

    def put_pending(self, row: PendingRow, history: dict[str, Any]) -> None:
        def fn(c: sqlite3.Connection) -> None:
            # 같은 키의 낡은 요청은 새 요청이 대신한다
            c.execute("DELETE FROM pending WHERE tenant=? AND name=?", (row.tenant, row.name))
            c.execute(
                "INSERT INTO pending VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                (
                    row.id,
                    row.op,
                    row.tenant,
                    row.name,
                    row.kid,
                    row.nonce,
                    row.ciphertext,
                    row.hint,
                    row.fingerprint,
                    row.reason,
                    row.requested_by,
                    row.requested_at,
                    row.review_id,
                ),
            )
            self._log(c, history)

        self._run(fn)

    def get_pending(self, request_id: str) -> PendingRow | None:
        def fn(c: sqlite3.Connection) -> PendingRow | None:
            row = c.execute("SELECT * FROM pending WHERE id=?", (request_id,)).fetchone()
            return self._pending(row) if row else None

        return self._run(fn)

    def list_pending(self, tenants: list[str] | None = None) -> list[PendingRow]:
        where, args = self._where(tenants)

        def fn(c: sqlite3.Connection) -> list[PendingRow]:
            q = f"SELECT * FROM pending{where} ORDER BY requested_at DESC"
            return [self._pending(r) for r in c.execute(q, args)]

        return self._run(fn)

    def set_review(self, request_id: str, review_id: str) -> None:
        sql = "UPDATE pending SET review_id=? WHERE id=?"
        self._run(lambda c: c.execute(sql, (review_id, request_id)))

    def commit(
        self,
        *,
        upsert: SecretRow | None = None,
        delete: tuple[str, str] | None = None,
        drop_pending: str | None = None,
        history: dict[str, Any],
    ) -> None:
        def fn(c: sqlite3.Connection) -> None:
            if upsert is not None:
                c.execute(
                    "INSERT INTO secrets VALUES (?,?,?,?,?,?,?,?,?,?)"
                    " ON CONFLICT(tenant,name) DO UPDATE SET kid=excluded.kid,"
                    " nonce=excluded.nonce, ciphertext=excluded.ciphertext, hint=excluded.hint,"
                    " version=excluded.version, updated_by=excluded.updated_by,"
                    " updated_at=excluded.updated_at",
                    (
                        upsert.tenant,
                        upsert.name,
                        upsert.kid,
                        upsert.nonce,
                        upsert.ciphertext,
                        upsert.hint,
                        upsert.version,
                        upsert.created_at,
                        upsert.updated_by,
                        upsert.updated_at,
                    ),
                )
            if delete is not None:
                c.execute("DELETE FROM secrets WHERE tenant=? AND name=?", delete)
            if drop_pending is not None:
                c.execute("DELETE FROM pending WHERE id=?", (drop_pending,))
            self._log(c, history)

        self._run(fn)

    def history(self, tenants: list[str] | None, limit: int) -> list[dict[str, Any]]:
        where, args = self._where(tenants)

        def fn(c: sqlite3.Connection) -> list[dict[str, Any]]:
            q = f"SELECT at, by, action, tenant, name, request_id, review_id FROM history{where}"
            q += " ORDER BY id DESC LIMIT ?"
            return [dict(r) for r in c.execute(q, [*args, limit])]

        return self._run(fn)
