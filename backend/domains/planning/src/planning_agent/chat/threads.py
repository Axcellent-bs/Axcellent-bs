"""대화 저장 (기획 SQLite).

`PLANNING_STATE_DIR`가 있으면 그 폴더의 `planning_chat.db`에, 없으면 프로세스 메모리에 둔다(테스트).
회사 폴더를 켰으면(`AX_COMPANIES_DIR`, 여러 회사 설계 1-6) `<회사 폴더>/catbooks/planning_chat.db`
(옛 자리에 있으면 처음 열 때 옮긴다, `planning_agent.company_folder`).
표는 처음 쓸 때 만든다.

status: `talking`(말 걸 수 있음) · `answering`(AI 답을 만드는 중) · `closed`(끝남, 더는 못 보냄).
마케팅·고객지원 직접 대화와 같다. 30분 동안 말이 없으면 자동으로 끝낸다.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from ax_agent_template import company_scope as request_scope
from planning_agent import company_folder

MAX_MESSAGES = 60  # 한 대화에 남기는 메시지 수(넘으면 오래된 것부터 버린다)
SUMMARY_CHARS = 200
IDLE_CLOSE_MINUTES = 30  # 이 시간 동안 아무 말이 없으면 자동으로 끝낸다
FILE_NAME = "planning_chat.db"
INTERRUPTED = "서버가 다시 켜져서 답을 만들지 못했어요. 다시 보내 주세요."

Status = Literal["talking", "answering", "closed"]
TurnOutcome = Literal["ok", "closed", "busy", "missing"]

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
  updated_at TEXT NOT NULL,
  removed_at TEXT,
  removed_by TEXT,
  tenant_id TEXT NOT NULL DEFAULT 'catbooks'
)
"""
# 숨김 처리(소프트 삭제): 행은 지우지 않고 removed_at·removed_by만 적는다.
# 옛 DB는 열이 없어 켤 때 붙인다.
_REMOVED_COLUMNS = ("removed_at", "removed_by")


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
            have = {
                r["name"]
                for r in self._db.execute("PRAGMA table_info(planning_chat_threads)").fetchall()
            }
            for col in _REMOVED_COLUMNS:
                if col not in have:
                    self._db.execute(f"ALTER TABLE planning_chat_threads ADD COLUMN {col} TEXT")
            if "tenant_id" not in have:  # 옛 줄은 캣북스(회사별 전에는 캣북스뿐이었다)
                self._db.execute(
                    "ALTER TABLE planning_chat_threads"
                    " ADD COLUMN tenant_id TEXT NOT NULL DEFAULT 'catbooks'"
                )
            self._db.commit()

    @staticmethod
    def _company() -> str:
        """요청 회사. 회사가 안 걸린 요청(캣북스 세션·공용 토큰)은 캣북스."""
        return request_scope.scope_id(request_scope.active())

    def _mine(self, everywhere: bool = False) -> tuple[str, tuple[str, ...]]:
        """요청 회사의 대화만 보는 조건 (11/24 회사 격리: 다른 회사에 같은 이름이 있어도 안 섞이게).

        `everywhere`는 스스로 도는 정리 루틴(오래 쉰 대화 끝내기·켤 때 되살리기)만 쓴다."""
        if everywhere:
            return "1 = 1", ()
        return "tenant_id = ?", (self._company(),)

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
            # 기획 대화는 일을 만들지 않는다(조회·설명만).
            # 화면이 "일이 생긴 대화"를 가릴 때 쓰는 자리
            "task_id": None,
            "created_at": row["created_at"],
            "updated_at": row["updated_at"],
        }

    def get(self, thread_id: str, *, everywhere: bool = False) -> dict[str, Any] | None:
        where, args = self._mine(everywhere)
        rows = self._rows(
            f"SELECT * FROM planning_chat_threads WHERE id = ? AND removed_at IS NULL AND {where}",
            (thread_id, *args),
        )
        return self._view(rows[0]) if rows else None

    def list_all(self, limit: int = 100) -> list[dict[str, Any]]:
        where, args = self._mine()
        rows = self._rows(
            f"SELECT * FROM planning_chat_threads WHERE removed_at IS NULL AND {where}"
            " ORDER BY updated_at DESC, seq DESC LIMIT ?",
            (*args, limit),
        )
        return [self._view(r) for r in rows]

    def create(self, title: str, who: str | None) -> dict[str, Any]:
        now = self.clock().isoformat()
        with self._lock:
            row = self._db.execute(
                "SELECT COALESCE(MAX(seq), 0) AS n FROM planning_chat_threads"
            ).fetchone()
            seq = int(row["n"]) + 1
            thread_id = f"planchat-{seq}"
            self._db.execute(
                "INSERT INTO planning_chat_threads"
                "(id, seq, title, status, who, summary, messages, created_at, updated_at,"
                " tenant_id) VALUES (?, ?, ?, 'talking', ?, '', '[]', ?, ?, ?)",
                (thread_id, seq, title, who, now, now, self._company()),
            )
            self._db.commit()
        return self.get(thread_id) or {}

    def _update(
        self,
        thread_id: str,
        mutate: Callable[[dict[str, Any], list[dict[str, Any]], str], Status | None],
        summary: str | None = None,
        everywhere: bool = False,
    ) -> dict[str, Any] | None:
        """읽고 → 바꾸고 → 쓰기를 잠금 안에서 한다. `mutate`가 None을 돌려주면 쓰지 않는다."""
        with self._lock:
            th = self.get(thread_id, everywhere=everywhere)
            if th is None:
                return None
            now = self.clock().isoformat()
            msgs = th["messages"]
            status = mutate(th, msgs, now)
            if status is None:
                return th
            self._db.execute(
                "UPDATE planning_chat_threads SET status = ?, messages = ?, summary = ?,"
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
            return self.get(thread_id, everywhere=everywhere)

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
        self,
        thread_id: str,
        text: str,
        summary: str | None = None,
        kind: str = "text",
        *,
        everywhere: bool = False,
    ) -> dict[str, Any] | None:
        """AI 답을 붙이고 `talking`으로 돌린다(그 사이 끝났으면 `closed` 그대로)."""

        def mutate(th: dict[str, Any], msgs: list[dict[str, Any]], at: str) -> Status | None:
            self._add(msgs, "assistant", text, kind, at)
            return "closed" if th["status"] == "closed" else "talking"

        return self._update(thread_id, mutate, summary, everywhere)

    def close(self, thread_id: str, *, everywhere: bool = False) -> dict[str, Any] | None:
        """끝낸다. 요약이 비어 있으면 첫 질문 일부를 요약으로 남긴다."""
        th = self.get(thread_id, everywhere=everywhere)
        if th is None:
            return None
        summary = th["summary"] or self._first_question(th["messages"])

        def mutate(th: dict[str, Any], _msgs: list[dict[str, Any]], _at: str) -> Status | None:
            return None if th["status"] == "closed" else "closed"

        return self._update(thread_id, mutate, summary, everywhere)

    def hide(self, thread_id: str, by: str | None) -> bool:
        """대화를 숨긴다(행은 남기고 removed_at·removed_by만 적는다).

        이미 숨겼으면 건드리지 않고 False.

        답하는 중이어도 숨긴다. 뒤늦은 답은 `_update`가 `get`으로 못 찾아 무시한다.
        """
        with self._lock:
            where, args = self._mine()
            cur = self._db.execute(
                "UPDATE planning_chat_threads SET removed_at = ?, removed_by = ?"
                f" WHERE id = ? AND removed_at IS NULL AND {where}",
                (self.clock().isoformat(), by, thread_id, *args),
            )
            self._db.commit()
            return cur.rowcount > 0

    @staticmethod
    def _first_question(msgs: list[dict[str, Any]]) -> str:
        first = next((m["text"] for m in msgs if m["role"] == "user"), "")
        return " ".join(str(first).split())[:SUMMARY_CHARS]

    def expire_idle(self, minutes: int = IDLE_CLOSE_MINUTES) -> list[str]:
        """`minutes` 동안 말이 없는 `talking` 대화를 끝낸다. 끝낸 대화 id를 돌려준다."""
        cut = (self.clock() - timedelta(minutes=minutes)).isoformat()
        rows = self._rows(
            "SELECT id FROM planning_chat_threads"
            " WHERE status = 'talking' AND updated_at < ? AND removed_at IS NULL",
            (cut,),
        )
        for r in rows:
            self.close(r["id"], everywhere=True)  # 모든 회사 (정리 루틴)
        return [r["id"] for r in rows]

    def recover_orphans(self) -> list[str]:
        """켤 때: 답을 만들던 중에 서버가 꺼진 대화는 안내 한 줄을 붙여 `talking`으로 돌린다."""
        rows = self._rows(
            "SELECT id FROM planning_chat_threads WHERE status = 'answering' AND removed_at IS NULL"
        )
        for r in rows:
            self.finish_turn(r["id"], INTERRUPTED, kind="error", everywhere=True)
        return [r["id"] for r in rows]


_store: ChatStore | None = None


def get_store() -> ChatStore:
    global _store
    if _store is None:
        # 회사 폴더(켰을 때) 또는 PLANNING_STATE_DIR, 없으면 메모리 (company_folder)
        _store = ChatStore(
            company_folder.state_path(FILE_NAME, extras=company_folder.SQLITE_EXTRAS)
        )
        _store.recover_orphans()
    return _store


def set_store(store: ChatStore) -> ChatStore:
    """테스트에서 메모리 저장소를 끼울 때 쓴다."""
    global _store
    _store = store
    return store
