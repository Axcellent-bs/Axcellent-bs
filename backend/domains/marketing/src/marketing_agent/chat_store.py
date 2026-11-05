"""마케팅 AI 직접 대화 저장소 (SQLite). 받은 기획안 `briefs.BriefStore`와 같은 방식이다.

- 환경변수 `MARKETING_CHAT_DB`가 가리키는 파일에 두면 서비스를 다시 시작해도 대화가 남는다.
  비어 있으면 메모리(`:memory:`)라 다시 시작할 때 사라진다(테스트·로컬 기본).
  열 수 없으면 시작 때 `ChatStorageError`로 실패한다(조용히 메모리로 바꾸지 않는다).
- 대화 하나 = id(`mktchat-N`)·title·status·who·summary·messages[]·시각.
  status: `talking`(말 걸 수 있음) · `answering`(AI 답을 만드는 중) · `closed`(끝남, 더는 못 보냄).
- 마케팅 전용 파일이고 다른 부서 DB는 쓰지 않는다.
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

DB_ENV = "MARKETING_CHAT_DB"
MAX_MESSAGES = 60  # 한 대화에 남기는 메시지 수(넘으면 오래된 것부터 버린다)
SUMMARY_CHARS = 200
TITLE_CHARS = 30
IDLE_CLOSE_MINUTES = 30  # 이 시간 동안 아무 말이 없으면 자동으로 끝낸다
ANSWER_STALE_MINUTES = 5  # 답을 만든다던 대화가 이 시간 넘게 그대로면 답이 안 온 것으로 본다
RESTART_NOTICE = "답을 만들던 중 서비스가 다시 시작됐어요. 다시 보내 주세요."

Status = Literal["talking", "answering", "closed"]
_log = logging.getLogger(__name__)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS marketing_chat_threads (
    seq        INTEGER PRIMARY KEY AUTOINCREMENT,
    id         TEXT NOT NULL UNIQUE,
    title      TEXT NOT NULL,
    status     TEXT NOT NULL,
    who        TEXT,
    summary    TEXT NOT NULL DEFAULT '',
    messages   TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
"""


class ChatStorageError(Exception):
    """저장소를 읽거나 쓰지 못했다(디스크·권한·잠금)."""


def _now() -> datetime:
    return datetime.now(UTC)


def make_title(message: str) -> str:
    one = " ".join(message.split())
    return one if len(one) <= TITLE_CHARS else one[: TITLE_CHARS - 1] + "…"


class ChatStore:
    """`path`가 없으면 환경변수 `MARKETING_CHAT_DB`, 그것도 없으면 메모리."""

    def __init__(self, path: str | None = None, now: Callable[[], datetime] = _now) -> None:
        self.path = path if path is not None else (os.getenv(DB_ENV, "").strip() or ":memory:")
        self.now = now
        self._lock = threading.Lock()
        try:
            self._conn = sqlite3.connect(self.path, check_same_thread=False, timeout=5.0)
            self._conn.row_factory = sqlite3.Row
            with self._lock, self._conn:
                self._conn.execute(_SCHEMA)
        except sqlite3.Error as error:
            raise ChatStorageError(f"대화 저장소를 열 수 없다: {type(error).__name__}") from error
        self.recover_orphans()

    def shutdown(self) -> None:
        self._conn.close()

    # ---- 읽기 ----------------------------------------------------------------

    @staticmethod
    def _view(row: sqlite3.Row) -> dict[str, Any]:
        return {
            "id": row["id"],
            "title": row["title"],
            "status": row["status"],
            "who": row["who"],
            "summary": row["summary"],
            "messages": json.loads(row["messages"]),
            # 대화로는 일이 생기지 않는다(조회·설명만). 화면이 "일이 생긴 대화"를 가릴 때 쓰는 자리
            "task_id": None,
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def _query(self, sql: str, args: tuple[Any, ...] = ()) -> list[sqlite3.Row]:
        try:
            with self._lock:
                return self._conn.execute(sql, args).fetchall()
        except sqlite3.Error as error:
            raise ChatStorageError(f"대화를 읽을 수 없다: {type(error).__name__}") from error

    def get(self, thread_id: str) -> dict[str, Any] | None:
        rows = self._query("SELECT * FROM marketing_chat_threads WHERE id = ?", (thread_id,))
        return self._view(rows[0]) if rows else None

    def list(self, limit: int = 100) -> list[dict[str, Any]]:
        rows = self._query(
            "SELECT * FROM marketing_chat_threads ORDER BY updated_at DESC, seq DESC LIMIT ?",
            (limit,),
        )
        return [self._view(r) for r in rows]

    # ---- 쓰기 ----------------------------------------------------------------

    def _write(self, sql: str, args: tuple[Any, ...]) -> int:
        try:
            with self._lock, self._conn:
                return self._conn.execute(sql, args).rowcount
        except sqlite3.Error as error:
            raise ChatStorageError(f"대화를 저장할 수 없다: {type(error).__name__}") from error

    def create(self, title: str, who: str | None) -> dict[str, Any]:
        at = self.now().isoformat()
        try:
            with self._lock, self._conn:
                row = self._conn.execute(
                    "SELECT COALESCE(MAX(seq), 0) AS n FROM marketing_chat_threads"
                ).fetchone()
                seq = int(row["n"]) + 1
                self._conn.execute(
                    "INSERT INTO marketing_chat_threads"
                    "(seq, id, title, status, who, summary, messages, created_at, updated_at)"
                    " VALUES (?, ?, ?, 'talking', ?, '', '[]', ?, ?)",
                    (seq, f"mktchat-{seq}", title, who, at, at),
                )
        except sqlite3.Error as error:
            raise ChatStorageError(f"대화를 저장할 수 없다: {type(error).__name__}") from error
        return self.get(f"mktchat-{seq}") or {}

    @staticmethod
    def _message(msgs: list[dict[str, Any]], role: str, text: str, kind: str, at: str) -> None:
        n = max((int(m["id"][1:]) for m in msgs if str(m.get("id", "")).startswith("m")), default=0)
        msgs.append(
            {
                "id": f"m{n + 1}",
                "role": role,
                "text": text,
                "at": at,
                "kind": kind,
                "plan": None,
                "task_id": None,
            }
        )

    def _update(
        self,
        thread_id: str,
        mutate: Callable[[dict[str, Any], str], Status | None],
    ) -> dict[str, Any] | None:
        """대화 하나를 읽고 고쳐 쓴다(잠금 안에서 한 번에). 없으면 None."""
        at = self.now().isoformat()
        try:
            with self._lock, self._conn:
                row = self._conn.execute(
                    "SELECT * FROM marketing_chat_threads WHERE id = ?", (thread_id,)
                ).fetchone()
                if row is None:
                    return None
                th = self._view(row)
                status = mutate(th, at)
                if status is None:  # 바꿀 것이 없다
                    return th
                th["messages"] = th["messages"][-MAX_MESSAGES:]
                self._conn.execute(
                    "UPDATE marketing_chat_threads SET status = ?, summary = ?, messages = ?,"
                    " updated_at = ? WHERE id = ?",
                    (
                        status,
                        th["summary"][:SUMMARY_CHARS],
                        json.dumps(th["messages"], ensure_ascii=False),
                        at,
                        thread_id,
                    ),
                )
                th["status"], th["updated_at"] = status, at
                return th
        except sqlite3.Error as error:
            raise ChatStorageError(f"대화를 저장할 수 없다: {type(error).__name__}") from error

    def begin_turn(self, thread_id: str, text: str) -> tuple[str, dict[str, Any] | None]:
        """사람 말을 적고 `answering`으로 바꾼다. (`ok`|`closed`|`busy`|`missing`, 대화)."""
        outcome = "missing"

        def mutate(th: dict[str, Any], at: str) -> Status | None:
            nonlocal outcome
            if th["status"] == "closed":
                outcome = "closed"
                return None
            if th["status"] == "answering":
                outcome = "busy"
                return None
            outcome = "ok"
            self._message(th["messages"], "user", text, "text", at)
            return "answering"

        th = self._update(thread_id, mutate)
        return outcome, th

    def finish_turn(
        self, thread_id: str, reply: str, *, summary: str | None = None, kind: str = "text"
    ) -> dict[str, Any] | None:
        """AI 답을 붙이고 `talking`으로 되돌린다. 그 사이 닫혔으면 닫힌 채로 둔다."""

        def mutate(th: dict[str, Any], at: str) -> Status | None:
            self._message(th["messages"], "assistant", reply, kind, at)
            if summary:
                th["summary"] = summary
            return "closed" if th["status"] == "closed" else "talking"

        return self._update(thread_id, mutate)

    def close(self, thread_id: str) -> dict[str, Any] | None:
        """대화를 끝낸다. 요약이 비어 있으면 첫 질문 일부만 남긴다(지어내지 않는다)."""

        def mutate(th: dict[str, Any], at: str) -> Status | None:
            if th["status"] == "closed":
                return None
            if not th["summary"]:
                th["summary"] = self._first_question(th["messages"])
            return "closed"

        return self._update(thread_id, mutate)

    @staticmethod
    def _first_question(msgs: list[dict[str, Any]]) -> str:
        first = next((m["text"] for m in msgs if m["role"] == "user"), "")
        one = " ".join(first.split())
        return f"첫 질문: {one[:80]}" if one else ""

    def expire_idle(self, minutes: int = IDLE_CLOSE_MINUTES) -> list[str]:
        """말이 없는 지 `minutes`분 넘은 대화를 끝낸다. 답이 안 온 채 굳은 대화는 풀어 준다."""
        limit = self.now() - timedelta(minutes=minutes)
        stale = self.now() - timedelta(minutes=ANSWER_STALE_MINUTES)
        closed: list[str] = []
        for th in self.list(limit=1000):
            moved = datetime.fromisoformat(th["updated_at"])
            if th["status"] == "answering" and moved < stale:
                self._release(th["id"])
            elif th["status"] == "talking" and moved < limit:
                self.close(th["id"])
                closed.append(th["id"])
        return closed

    def _release(self, thread_id: str) -> None:
        def mutate(th: dict[str, Any], at: str) -> Status | None:
            if th["status"] != "answering":
                return None
            self._message(th["messages"], "assistant", RESTART_NOTICE, "error", at)
            return "talking"

        self._update(thread_id, mutate)

    def recover_orphans(self) -> None:
        """다시 시작하면 답을 만들던 일은 사라졌으니 `answering`을 풀어 준다."""
        try:
            rows = self._query("SELECT id FROM marketing_chat_threads WHERE status = 'answering'")
        except ChatStorageError:
            raise
        for row in rows:
            _log.warning("답을 만들던 대화 %s를 풀어 줌", row["id"])
            self._release(row["id"])
