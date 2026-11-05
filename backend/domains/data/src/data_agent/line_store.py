"""한 줄 문장 재사용 (브리프 인사이트·기회 카드): 같은 대상 id + 같은 근거 숫자(facts)면 LLM에 다시
묻지 않고 저장한 문장을 쓴다. 날짜가 바뀌어도 근거가 같으면 그대로 쓴다.

- 키: 종류(brief·card) + 대상 id(insight_id·하위 주제) + facts JSON(정렬)의 해시
- 검증(`brief_line.problem`)을 통과한 LLM 문장만 저장한다 (고정 문장은 저장하지 않음)
- 기록: SQLite(`DATA_BRIEF_LINES_DB`, run_all.sh가 `backend/.data/brief_lines.db`로 준다,
  서버·수집기가 같은 파일). 환경변수가 없으면 프로세스 메모리(테스트)
"""

from __future__ import annotations

import hashlib
import json
import os
import sqlite3
import threading
from collections.abc import Mapping
from datetime import UTC, datetime, timedelta
from pathlib import Path

DB_ENV = "DATA_BRIEF_LINES_DB"
KEEP_DAYS = 30  # 이보다 오래 안 쓴 문장은 지운다


def line_key(kind: str, about: str, facts: Mapping[str, object]) -> str:
    text = json.dumps({"kind": kind, "about": about, "facts": facts}, ensure_ascii=False,
                      sort_keys=True, default=str)  # fmt: skip
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


class LineStore:
    def __init__(self, path: str | Path | None = None) -> None:
        self._lock = threading.Lock()
        self._mem: dict[str, str] = {}
        self._conn: sqlite3.Connection | None = None
        if path is not None:
            if str(path) != ":memory:":
                Path(path).parent.mkdir(parents=True, exist_ok=True)
            self._conn = sqlite3.connect(str(path), check_same_thread=False, timeout=5.0)
            with self._lock, self._conn:
                self._conn.execute(
                    "CREATE TABLE IF NOT EXISTS lines (key TEXT PRIMARY KEY, line TEXT NOT NULL,"
                    " used_at TEXT NOT NULL)"
                )

    def get(self, key: str) -> str | None:
        with self._lock:
            if self._conn is None:
                return self._mem.get(key)
            row = self._conn.execute("SELECT line FROM lines WHERE key = ?", (key,)).fetchone()
            if row is not None:
                with self._conn:
                    self._conn.execute(
                        "UPDATE lines SET used_at = ? WHERE key = ?",
                        (datetime.now(UTC).isoformat(), key),
                    )
            return row[0] if row else None

    def put(self, key: str, line: str) -> None:
        with self._lock:
            if self._conn is None:
                self._mem[key] = line
                return
            now = datetime.now(UTC)
            with self._conn:
                self._conn.execute(
                    "INSERT OR REPLACE INTO lines VALUES (?,?,?)", (key, line, now.isoformat())
                )
                old = (now - timedelta(days=KEEP_DAYS)).isoformat()
                self._conn.execute("DELETE FROM lines WHERE used_at < ?", (old,))

    def clear(self) -> None:
        with self._lock:
            self._mem.clear()


LINES = LineStore(os.environ.get(DB_ENV) or None)
