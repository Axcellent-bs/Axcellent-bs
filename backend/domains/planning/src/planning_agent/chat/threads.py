"""대화 저장 (기획 SQLite).

`PLANNING_STATE_DIR`가 있으면 그 폴더의 `planning_chat.db`에, 없으면 프로세스 메모리에 둔다(테스트).
표는 처음 쓸 때 만든다.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

MAX_MESSAGES = 60  # 한 대화에 남기는 메시지 수(넘으면 오래된 것부터 버린다)
SUMMARY_CHARS = 200
FILE_NAME = "planning_chat.db"

_DDL = """
CREATE TABLE IF NOT EXISTS planning_chat_threads (
  id TEXT PRIMARY KEY,
  seq INTEGER NOT NULL,
  title TEXT NOT NULL,
  status TEXT NOT NULL,
  who TEXT,
  summary TEXT NOT NULL DEFAULT '',
  messages TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL
)
"""


def _now() -> datetime:
    return datetime.now(UTC)


class ChatStore:
    def __init__(self, path: str = "", clock: Callable[[], datetime] = _now) -> None:
        self.clock = clock
        self._lock = threading.Lock()
        self._db = sqlite3.connect(path or ":memory:", check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        with self._lock:
            self._db.execute(_DDL)
            self._db.commit()

    def _one(self, sql: str, args: tuple = ()) -> dict[str, Any] | None:
        with self._lock:
            row = self._db.execute(sql, args).fetchone()
        return dict(row) if row else None

    def _write(self, sql: str, args: tuple) -> None:
        with self._lock:
            self._db.execute(sql, args)
            self._db.commit()

    @staticmethod
    def _view(row: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": row["id"],
            "title": row["title"],
            "status": row["status"],
            "who": row["who"],
            "summary": row["summary"],
            "messages": json.loads(row["messages"]),
            # 기획 대화는 일을 만들지 않는다(조회·설명만).
            # 화면이 "일이 생긴 대화"를 가릴 때 쓰는 자리
            "task_id": None,
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def get(self, thread_id: str) -> dict[str, Any] | None:
        row = self._one("SELECT * FROM planning_chat_threads WHERE id = ?", (thread_id,))
        return self._view(row) if row else None

    def list_all(self, limit: int = 100) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._db.execute(
                "SELECT * FROM planning_chat_threads ORDER BY updated_at DESC, seq DESC LIMIT ?",
                (limit,),
            ).fetchall()
        return [self._view(dict(r)) for r in rows]

    def create(self, title: str, who: str | None) -> dict[str, Any]:
        row = self._one("SELECT COALESCE(MAX(seq), 0) AS n FROM planning_chat_threads")
        seq = int(row["n"]) + 1 if row else 1
        now = self.clock().isoformat()
        thread_id = f"planchat-{seq}"
        self._write(
            "INSERT INTO planning_chat_threads"
            "(id, seq, title, status, who, summary, messages, created_at, updated_at)"
            " VALUES (?, ?, ?, 'talking', ?, '', '[]', ?, ?)",
            (thread_id, seq, title, who, now, now),
        )
        return self.get(thread_id) or {}

    def append(
        self, thread_id: str, role: str, text: str, summary: str | None = None
    ) -> dict[str, Any]:
        th = self.get(thread_id)
        if th is None:
            raise KeyError(thread_id)
        msgs = th["messages"]
        now = self.clock().isoformat()
        ids = (int(m["id"][1:]) for m in msgs if str(m.get("id", "")).startswith("m"))
        n = max(ids, default=0) + 1
        msgs.append(
            {"id": f"m{n}", "role": role, "text": text, "at": now, "kind": "text",
             "plan": None, "task_id": None}
        )  # fmt: skip
        self._write(
            "UPDATE planning_chat_threads SET messages = ?, summary = ?, updated_at = ?"
            " WHERE id = ?",
            (
                json.dumps(msgs[-MAX_MESSAGES:], ensure_ascii=False),
                (summary if summary is not None else th["summary"])[:SUMMARY_CHARS],
                now,
                thread_id,
            ),
        )
        return self.get(thread_id) or {}


_store: ChatStore | None = None


def get_store() -> ChatStore:
    global _store
    if _store is None:
        base = os.getenv("PLANNING_STATE_DIR", "").strip()
        _store = ChatStore(os.path.join(base, FILE_NAME) if base else "")
    return _store


def set_store(store: ChatStore) -> ChatStore:
    """테스트에서 메모리 저장소를 끼울 때 쓴다."""
    global _store
    _store = store
    return store
