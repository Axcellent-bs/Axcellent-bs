"""오피스 단톡방 대화 목록 (AI 오피스 단톡방·AI 대화방 전체 대화방).

단톡방에서 부서 AI에게 건 대화는 모두에게 보인다. 1:1 직접 대화는 여기에 올리지 않는다(비공개).
대화 원문은 부서 AI 대화 창구(`/api/dashboard/<부서>-chat`)에 그대로 있고, 여기에는
"어느 부서의 어느 대화를 누가 단톡방에서 걸었는지"와 마지막으로 말을 보낸 시각만 둔다.
화면은 이 목록을 읽고, 새 대화이거나 마지막 시각이 바뀐 대화만 각 창구에서 다시 읽는다.

- `POST /api/dashboard/office-room`: 단톡방에서 말을 보낸 화면이 보낼 때마다 올린다.
  사람 토큰이 필요하다. 같은 부서·같은 대화를 다시 올리면 `updated_at`만 바뀐다
  (보낸 사람은 처음 올린 사람 그대로).
- `GET /api/dashboard/office-room?hours=`: 최근 `hours`시간(기본 24, 최대 168) 안에 말이 오간 대화.
  최근에 말이 오간 것이 먼저다(최대 200줄). 읽기는 다른 화면 읽기처럼 열려 있다.
  이 읽기는 부서 AI를 부르지 않아 공통 로그에 줄이 쌓이지 않는다.
- `from_id`·`from_name`은 화면 로그인 계정(데모 계정)이다. 사람 토큰은 화면이 같이 쓰는 값이라
  보낸 사람을 가르지 못한다.

저장은 SQLite 파일 하나다. 위치는 `AX_OFFICE_ROOM_DB`이고, 비어 있으면 프로세스 메모리
(`:memory:`)에 둔다(다시 켜면 사라진다).
"""

from __future__ import annotations

import os
import sqlite3
import threading
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from fastapi import APIRouter, Depends, Query
from pydantic import BaseModel, Field

from ax_dashboard_api.auth import require_human_for_writes
from ax_dashboard_api.envelope import ok

DB_ENV = "AX_OFFICE_ROOM_DB"
DEFAULT_HOURS = 24
MAX_HOURS = 168
MAX_ROWS = 200

# 단톡방에서 말을 걸 수 있는 부서 (화면 useOfficeTalk의 OFFICE_TALK_KEYS와 같다)
Agent = Literal[
    "dev", "data", "support", "finance", "planning", "marketing", "hr", "infra", "orchestrator"
]

_SCHEMA = """
CREATE TABLE IF NOT EXISTS office_threads (
    agent      TEXT NOT NULL,
    thread_id  TEXT NOT NULL,
    from_id    TEXT NOT NULL,
    from_name  TEXT NOT NULL,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    PRIMARY KEY (agent, thread_id)
);
CREATE INDEX IF NOT EXISTS idx_office_threads_updated ON office_threads (updated_at);
"""


class OfficeThreadBody(BaseModel):
    agent: Agent
    thread_id: str = Field(min_length=1, max_length=64)
    from_id: str = Field(min_length=1, max_length=64)
    from_name: str = Field(min_length=1, max_length=40)


class OfficeRoomStore:
    def __init__(self, path: str) -> None:
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(path, check_same_thread=False, timeout=5.0)
        self._conn.row_factory = sqlite3.Row
        if path != ":memory:":
            self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(_SCHEMA)

    def touch(self, body: OfficeThreadBody, now: datetime) -> dict[str, Any]:
        """처음이면 넣고, 있으면 마지막 시각만 바꾼다."""
        at = now.isoformat()
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO office_threads"
                " (agent, thread_id, from_id, from_name, created_at, updated_at)"
                " VALUES (?,?,?,?,?,?)"
                " ON CONFLICT (agent, thread_id) DO UPDATE SET updated_at = excluded.updated_at",
                (body.agent, body.thread_id, body.from_id, body.from_name, at, at),
            )
            row = self._conn.execute(
                "SELECT * FROM office_threads WHERE agent = ? AND thread_id = ?",
                (body.agent, body.thread_id),
            ).fetchone()
        return dict(row)

    def recent(self, since: datetime) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM office_threads WHERE updated_at >= ?"
                " ORDER BY updated_at DESC, rowid DESC LIMIT ?",
                (since.isoformat(), MAX_ROWS),
            ).fetchall()
        return [dict(r) for r in rows]


_store: OfficeRoomStore | None = None
_store_key: str | None = None
_store_lock = threading.Lock()


def get_store() -> OfficeRoomStore:
    """`AX_OFFICE_ROOM_DB`가 바뀌면 새로 연다. 비어 있으면 메모리 저장소."""
    global _store, _store_key
    path = os.getenv(DB_ENV) or ":memory:"
    with _store_lock:
        if _store is None or _store_key != path:
            _store = OfficeRoomStore(path)
            _store_key = path
        return _store


def reset_store() -> None:
    """테스트 초기화용."""
    global _store, _store_key
    with _store_lock:
        _store, _store_key = None, None


def _now() -> datetime:
    return datetime.now(UTC)


# 쓰기(POST)는 사람 토큰, 읽기는 열려 있다
router = APIRouter(prefix="/api/dashboard", dependencies=[Depends(require_human_for_writes)])


@router.post("/office-room", status_code=201)
def touch_office_thread(body: OfficeThreadBody) -> dict[str, Any]:
    return ok(get_store().touch(body, _now()))


@router.get("/office-room")
def list_office_threads(
    hours: int = Query(default=DEFAULT_HOURS, ge=1, le=MAX_HOURS),
) -> dict[str, Any]:
    rows = get_store().recent(_now() - timedelta(hours=hours))
    return ok(rows, total=len(rows))
