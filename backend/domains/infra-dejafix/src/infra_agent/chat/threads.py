"""인프라 AI 직접 대화 저장 (SQLite).

`INFRA_CHAT_DB`(파일 경로)가 있으면 거기에, 없으면 프로세스 메모리에 둔다(테스트).
표는 처음 쓸 때 만든다.

status: `talking`(말 걸 수 있음) · `answering`(AI 답을 만드는 중) · `closed`(끝남, 더는 못 보냄).
기획·마케팅·고객지원 직접 대화와 같다. 30분 동안 말이 없으면 자동으로 끝낸다.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

MAX_MESSAGES = 60  # 한 대화에 남기는 메시지 수(넘으면 오래된 것부터 버린다)
SUMMARY_CHARS = 200
IDLE_CLOSE_MINUTES = 30  # 이 시간 동안 아무 말이 없으면 자동으로 끝낸다
FILE_NAME = "infra_chat.db"
INTERRUPTED = "서버가 다시 켜져서 답을 만들지 못했어요. 다시 보내 주세요."

Status = Literal["talking", "answering", "closed"]
TurnOutcome = Literal["ok", "closed", "busy", "missing"]

_DDL = """
CREATE TABLE IF NOT EXISTS infra_chat_threads (
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
        self._lock = threading.RLock()
        self._db = sqlite3.connect(path or ":memory:", check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        with self._lock:
            self._db.execute(_DDL)
            self._db.commit()

    def _rows(self, sql: str, args: tuple = ()) -> list[dict[str, Any]]:
        with self._lock:
            return [dict(r) for r in self._db.execute(sql, args).fetchall()]

    @staticmethod
    def _view(row: dict[str, Any]) -> dict[str, Any]:
        return {
            "id": row["id"],
            "title": row["title"],
            "status": row["status"],
            "who": row["who"],
            "summary": row["summary"],
            "messages": json.loads(row["messages"]),
            # 인프라 대화는 일을 만들지 않는다(조회·설명만).
            # 화면이 "일이 생긴 대화"를 가릴 때 쓰는 자리
            "task_id": None,
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def get(self, thread_id: str) -> dict[str, Any] | None:
        rows = self._rows("SELECT * FROM infra_chat_threads WHERE id = ?", (thread_id,))
        return self._view(rows[0]) if rows else None

    def list_all(self, limit: int = 100) -> list[dict[str, Any]]:
        rows = self._rows(
            "SELECT * FROM infra_chat_threads ORDER BY updated_at DESC, seq DESC LIMIT ?",
            (limit,),
        )
        return [self._view(r) for r in rows]

    def create(self, title: str, who: str | None) -> dict[str, Any]:
        now = self.clock().isoformat()
        with self._lock:
            row = self._db.execute(
                "SELECT COALESCE(MAX(seq), 0) AS n FROM infra_chat_threads"
            ).fetchone()
            seq = int(row["n"]) + 1
            thread_id = f"infrachat-{seq}"
            self._db.execute(
                "INSERT INTO infra_chat_threads"
                "(id, seq, title, status, who, summary, messages, created_at, updated_at)"
                " VALUES (?, ?, ?, 'talking', ?, '', '[]', ?, ?)",
                (thread_id, seq, title, who, now, now),
            )
            self._db.commit()
        return self.get(thread_id) or {}

    def _update(
        self,
        thread_id: str,
        mutate: Callable[[dict[str, Any], list[dict[str, Any]], str], Status | None],
        summary: str | None = None,
    ) -> dict[str, Any] | None:
        """읽고 → 바꾸고 → 쓰기를 잠금 안에서 한다. `mutate`가 None을 돌려주면 쓰지 않는다."""
        with self._lock:
            th = self.get(thread_id)
            if th is None:
                return None
            now = self.clock().isoformat()
            msgs = th["messages"]
            status = mutate(th, msgs, now)
            if status is None:
                return th
            self._db.execute(
                "UPDATE infra_chat_threads SET status = ?, messages = ?, summary = ?,"
                " updated_at = ? WHERE id = ?",
                (
                    status,
                    json.dumps(msgs[-MAX_MESSAGES:], ensure_ascii=False),
                    (summary if summary is not None else th["summary"])[:SUMMARY_CHARS],
                    now,
                    thread_id,
                ),
            )
            self._db.commit()
            return self.get(thread_id)

    @staticmethod
    def _add(msgs: list[dict[str, Any]], role: str, text: str, kind: str, at: str) -> None:
        ids = (int(m["id"][1:]) for m in msgs if str(m.get("id", "")).startswith("m"))
        n = max(ids, default=0) + 1
        msgs.append(
            {"id": f"m{n}", "role": role, "text": text, "at": at, "kind": kind,
             "plan": None, "task_id": None}
        )  # fmt: skip

    def begin_turn(self, thread_id: str, text: str) -> tuple[TurnOutcome, dict[str, Any] | None]:
        """사람 말을 적고 `answering`으로 바꾼다. 끝난 대화·답하는 중이면 적지 않는다."""
        outcome: TurnOutcome = "ok"

        def mutate(th: dict[str, Any], msgs: list[dict[str, Any]], at: str) -> Status | None:
            nonlocal outcome
            if th["status"] == "closed":
                outcome = "closed"
                return None
            if th["status"] == "answering":
                outcome = "busy"
                return None
            self._add(msgs, "user", text, "text", at)
            return "answering"

        th = self._update(thread_id, mutate)
        return ("missing", None) if th is None else (outcome, th)

    def finish_turn(
        self, thread_id: str, text: str, summary: str | None = None, kind: str = "text"
    ) -> dict[str, Any] | None:
        """AI 답을 붙이고 `talking`으로 돌린다(그 사이 끝났으면 `closed` 그대로)."""

        def mutate(th: dict[str, Any], msgs: list[dict[str, Any]], at: str) -> Status | None:
            self._add(msgs, "assistant", text, kind, at)
            return "closed" if th["status"] == "closed" else "talking"

        return self._update(thread_id, mutate, summary)

    def close(self, thread_id: str) -> dict[str, Any] | None:
        """끝낸다. 요약이 비어 있으면 첫 질문 일부를 요약으로 남긴다."""
        th = self.get(thread_id)
        if th is None:
            return None
        summary = th["summary"] or self._first_question(th["messages"])

        def mutate(th: dict[str, Any], _msgs: list[dict[str, Any]], _at: str) -> Status | None:
            return None if th["status"] == "closed" else "closed"

        return self._update(thread_id, mutate, summary)

    @staticmethod
    def _first_question(msgs: list[dict[str, Any]]) -> str:
        first = next((m["text"] for m in msgs if m["role"] == "user"), "")
        return " ".join(str(first).split())[:SUMMARY_CHARS]

    def expire_idle(self, minutes: int = IDLE_CLOSE_MINUTES) -> list[str]:
        """`minutes` 동안 말이 없는 `talking` 대화를 끝낸다. 끝낸 대화 id를 돌려준다."""
        cut = (self.clock() - timedelta(minutes=minutes)).isoformat()
        rows = self._rows(
            "SELECT id FROM infra_chat_threads WHERE status = 'talking' AND updated_at < ?",
            (cut,),
        )
        for r in rows:
            self.close(r["id"])
        return [r["id"] for r in rows]

    def recover_orphans(self) -> list[str]:
        """켤 때: 답을 만들던 중에 서버가 꺼진 대화는 안내 한 줄을 붙여 `talking`으로 돌린다."""
        rows = self._rows("SELECT id FROM infra_chat_threads WHERE status = 'answering'")
        for r in rows:
            self.finish_turn(r["id"], INTERRUPTED, kind="error")
        return [r["id"] for r in rows]


_store: ChatStore | None = None


def get_store() -> ChatStore:
    global _store
    if _store is None:
        path = os.getenv("INFRA_CHAT_DB", "").strip()
        _store = ChatStore(path)
        _store.recover_orphans()
    return _store


def set_store(store: ChatStore) -> ChatStore:
    """테스트에서 메모리 저장소를 끼울 때 쓴다."""
    global _store
    _store = store
    return store
