"""마케팅 상태 저장(공지·쿠폰)과 이벤트 보낼 곳(outbox) (2026-11-23).

- 공지 게시 목록과 쿠폰 중복 방지 기록은 예전에 메모리에만 있어서 서버를 다시 켜면 사라졌다.
  고객지원·재무가 이 목록을 조회하므로 SQLite에 남긴다(`MARKETING_STATE_DB`, 없으면 메모리)
- 표 `marketing_state(tenant_id, kind, key, value JSON, updated_at)`: 저장소마다 `kind`로 나눈다.
  회사 칸(`tenant_id`)은 다른 마케팅 저장소처럼 지금 회사(기본 캣북스)
- 이벤트 버스(Redis)에 못 보낸 이벤트는 `marketing_outbox`에 쌓고 주기적으로 다시 보낸다
  (최소 한 번 전달. 같은 `event_id`는 버스가 한 번만 쌓는다).
  오래 못 보내면 최근 `OUTBOX_KEEP`건만 둔다
- 값(JSON)에는 구매자 개인정보를 넣지 않는다. 쿠폰 결과는 원래도 결과 모양(AgentResult)만 둔다
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
from datetime import UTC, datetime
from typing import Any

from ax_agent_template.events import publish
from ax_contracts.schemas import EventEnvelope
from marketing_agent import company_scope

log = logging.getLogger(__name__)

DB_ENV = "MARKETING_STATE_DB"
OUTBOX_KEEP = 2000

_SCHEMA = """
CREATE TABLE IF NOT EXISTS marketing_state (
    tenant_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    key TEXT NOT NULL,
    value TEXT,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (tenant_id, kind, key)
);
CREATE TABLE IF NOT EXISTS marketing_outbox (
    event_id TEXT PRIMARY KEY,
    tenant_id TEXT NOT NULL,
    envelope TEXT NOT NULL,
    created_at TEXT NOT NULL,
    attempts INTEGER NOT NULL DEFAULT 0,
    last_error TEXT
);
"""


def _now() -> str:
    return datetime.now(UTC).isoformat()


class StateDb:
    """`path`가 없으면 `MARKETING_STATE_DB`, 그것도 없으면 메모리(시험)."""
    company_id = company_scope.RequestCompany()  # 요청 회사가 먼저(11/24)

    def __init__(self, path: str | None = None, company_id: str | None = None) -> None:
        self.path = path if path is not None else (os.getenv(DB_ENV, "").strip() or ":memory:")
        self.company_id = company_scope.scope_id(company_id)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False, timeout=5.0)
        with self._lock, self._conn:
            self._conn.executescript(_SCHEMA)

    # ---- 상태(kind/key → JSON)
    def put(self, kind: str, key: str, value: Any) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO marketing_state (tenant_id, kind, key, value, updated_at)"
                " VALUES (?, ?, ?, ?, ?) ON CONFLICT(tenant_id, kind, key)"
                " DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
                (self.company_id, kind, key, json.dumps(value, ensure_ascii=False), _now()),
            )

    def delete(self, kind: str, key: str) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "DELETE FROM marketing_state WHERE tenant_id = ? AND kind = ? AND key = ?",
                (self.company_id, kind, key),
            )

    def all(self, kind: str) -> list[tuple[str, Any]]:
        """(key, 값) 목록. 넣은 순서(rowid)대로."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT key, value FROM marketing_state WHERE tenant_id = ? AND kind = ?"
                " ORDER BY rowid",
                (self.company_id, kind),
            ).fetchall()
        return [(key, json.loads(value) if value is not None else None) for key, value in rows]

    # ---- outbox
    def enqueue(self, envelope: EventEnvelope, error: str = "") -> None:
        """못 보낸 이벤트를 쌓는다. 같은 `event_id`는 한 건만."""
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT OR IGNORE INTO marketing_outbox"
                " (event_id, tenant_id, envelope, created_at, last_error) VALUES (?, ?, ?, ?, ?)",
                (envelope.event_id, self.company_id, envelope.model_dump_json(), _now(), error),
            )
            self._conn.execute(
                "DELETE FROM marketing_outbox WHERE event_id NOT IN ("
                " SELECT event_id FROM marketing_outbox ORDER BY created_at DESC LIMIT ?)",
                (OUTBOX_KEEP,),
            )

    def pending(self, limit: int = 100) -> list[EventEnvelope]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT envelope FROM marketing_outbox WHERE tenant_id = ?"
                " ORDER BY created_at LIMIT ?",
                (self.company_id, limit),
            ).fetchall()
        return [EventEnvelope.model_validate_json(raw) for (raw,) in rows]

    def sent(self, event_id: str) -> None:
        with self._lock, self._conn:
            self._conn.execute("DELETE FROM marketing_outbox WHERE event_id = ?", (event_id,))

    def failed(self, event_id: str, error: str) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE marketing_outbox SET attempts = attempts + 1, last_error = ?"
                " WHERE event_id = ?",
                (error[:200], event_id),
            )

    def outbox_count(self) -> int:
        with self._lock:
            (count,) = self._conn.execute(
                "SELECT COUNT(*) FROM marketing_outbox WHERE tenant_id = ?", (self.company_id,)
            ).fetchone()
        return int(count)


async def emit(db: StateDb, envelope: EventEnvelope) -> bool:
    """바로 보내 보고, 못 보내면 outbox에 쌓는다. 보냈으면 True."""
    try:
        await publish(envelope)
    except Exception as error:  # noqa: BLE001 - 버스가 없거나(EventBusUnavailable) 끊겨도 쌓아 둔다
        db.enqueue(envelope, type(error).__name__)
        return False
    return True


async def flush(db: StateDb, limit: int = 100) -> int:
    """outbox를 다시 보낸다. 보낸 건수. 버스가 아직 없으면 바로 멈춘다(다음 주기에 다시)."""
    count = 0
    for envelope in db.pending(limit):
        try:
            await publish(envelope)
        except Exception as error:  # noqa: BLE001
            db.failed(envelope.event_id, type(error).__name__)
            break
        db.sent(envelope.event_id)
        count += 1
    return count
