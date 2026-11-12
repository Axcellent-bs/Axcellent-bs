"""오피스 단톡방 대화 목록 (AI 오피스 단톡방·AI 대화방 전체 대화방).

단톡방에서 부서 AI에게 건 대화는 모두에게 보인다. 1:1 직접 대화는 여기에 올리지 않는다(비공개).
대화 원문은 부서 AI 대화 창구(`/api/dashboard/<부서>-chat`)에 그대로 있고, 여기에는
"어느 부서의 어느 대화를 누가 단톡방에서 걸었는지"와 마지막으로 말을 보낸 시각만 둔다.
화면은 이 목록을 읽고, 새 대화이거나 마지막 시각이 바뀐 대화만 각 창구에서 다시 읽는다.

- `POST /api/dashboard/office-room`: 단톡방에서 말을 보낸 화면이 보낼 때마다 올린다.
  사람 토큰이 필요하다. 같은 부서·같은 대화를 다시 올리면 `updated_at`만 바뀐다
  (보낸 사람은 처음 올린 사람 그대로).
- `GET /api/dashboard/office-room?hours=&room=`: 최근 `hours`시간(기본 24, 최대 168) 안에
  말이 오간 대화. 최근에 말이 오간 것이 먼저다(최대 200줄). `room`을 주면 그 방 대화만 준다.
  읽기는 다른 화면 읽기처럼 열려 있다. 이 읽기는 부서 AI를 부르지 않아 공통 로그에 줄이
  쌓이지 않는다.
- `POST /api/dashboard/office-room/{room_id}/remove` `{from_id}`: 그 방에서 내가 만든 대화를 지운다
  (office_room.db 줄 + 부서 대화 서버의 대화). 내가 만든 대화가 없으면 403, 없는 방이면 404.
- `room_id`: 대화가 오간 방. 전체 대화방은 `all`(기본값), 장애·이슈 방은 총괄 사건 `context_id`다.
  같은 부서·같은 대화를 다른 방으로 다시 올려도 처음 방 그대로 둔다(대화가 방을 옮겨 다니지 않게).
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

from fastapi import APIRouter, Depends, Path, Query, Request
from pydantic import BaseModel, Field

from ax_dashboard_api import chat_remove
from ax_dashboard_api.auth import require_human_for_writes
from ax_dashboard_api.envelope import ApiError, ok

DB_ENV = "AX_OFFICE_ROOM_DB"
DEFAULT_HOURS = 24
MAX_HOURS = 168
MAX_ROWS = 200
ALL_ROOM = "all"  # 전체 대화방 (화면 ALL_ROOM). 장애·이슈 방은 총괄 사건 context_id

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
    room_id    TEXT NOT NULL DEFAULT 'all',
    PRIMARY KEY (agent, thread_id)
);
CREATE INDEX IF NOT EXISTS idx_office_threads_updated ON office_threads (updated_at);
"""
# room_id 칸이 생기기 전 파일에도 칸을 붙인 뒤 만든다
_ROOM_INDEX = "CREATE INDEX IF NOT EXISTS idx_office_threads_room ON office_threads (room_id)"


class OfficeThreadBody(BaseModel):
    agent: Agent
    thread_id: str = Field(min_length=1, max_length=64)
    from_id: str = Field(min_length=1, max_length=64)
    from_name: str = Field(min_length=1, max_length=40)
    room_id: str = Field(default=ALL_ROOM, min_length=1, max_length=64)


class OfficeRoomStore:
    def __init__(self, path: str) -> None:
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(path, check_same_thread=False, timeout=5.0)
        self._conn.row_factory = sqlite3.Row
        if path != ":memory:":
            self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(_SCHEMA)
        self._add_room_column()

    def _add_room_column(self) -> None:
        """room_id 칸이 없는 예전 파일이면 붙인다.

        이미 있으면 아무것도 하지 않는다(여러 번 켜도 안전).
        """
        with self._conn:
            cols = {r["name"] for r in self._conn.execute("PRAGMA table_info(office_threads)")}
            if "room_id" not in cols:
                self._conn.execute(
                    "ALTER TABLE office_threads"
                    f" ADD COLUMN room_id TEXT NOT NULL DEFAULT '{ALL_ROOM}'"
                )
            self._conn.execute(_ROOM_INDEX)

    def touch(self, body: OfficeThreadBody, now: datetime) -> dict[str, Any]:
        """처음이면 넣고, 있으면 마지막 시각만 바꾼다(보낸 사람·방은 처음 그대로)."""
        at = now.isoformat()
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO office_threads"
                " (agent, thread_id, from_id, from_name, created_at, updated_at, room_id)"
                " VALUES (?,?,?,?,?,?,?)"
                " ON CONFLICT (agent, thread_id) DO UPDATE SET updated_at = excluded.updated_at",
                (
                    body.agent,
                    body.thread_id,
                    body.from_id,
                    body.from_name,
                    at,
                    at,
                    body.room_id,
                ),
            )
            row = self._conn.execute(
                "SELECT * FROM office_threads WHERE agent = ? AND thread_id = ?",
                (body.agent, body.thread_id),
            ).fetchone()
        return dict(row)

    def recent(self, since: datetime, room: str | None = None) -> list[dict[str, Any]]:
        """`since` 뒤에 말이 오간 대화. `room`을 주면 그 방만."""
        where, args = "updated_at >= ?", [since.isoformat()]
        if room is not None:
            where += " AND room_id = ?"
            args.append(room)
        with self._lock:
            rows = self._conn.execute(
                f"SELECT * FROM office_threads WHERE {where}"
                " ORDER BY updated_at DESC, rowid DESC LIMIT ?",
                (*args, MAX_ROWS),
            ).fetchall()
        return [dict(r) for r in rows]

    def in_room(self, room: str) -> list[dict[str, Any]]:
        """그 방의 대화 줄 전부 (기간과 상관없이)."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM office_threads WHERE room_id = ? ORDER BY created_at, rowid", (room,)
            ).fetchall()
        return [dict(r) for r in rows]

    def drop(self, room: str, keys: list[tuple[str, str]]) -> int:
        """그 방에서 (부서, 대화 id) 줄을 지운다. 지운 줄 수."""
        with self._lock, self._conn:
            return sum(
                self._conn.execute(
                    "DELETE FROM office_threads WHERE room_id = ? AND agent = ? AND thread_id = ?",
                    (room, agent, thread_id),
                ).rowcount
                for agent, thread_id in keys
            )


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
    room: str | None = Query(default=None, min_length=1, max_length=64),
) -> dict[str, Any]:
    rows = get_store().recent(_now() - timedelta(hours=hours), room)
    return ok(rows, total=len(rows))


class RemoveRoomBody(BaseModel):
    """지우는 사람(화면 로그인 계정 id). 단톡방 줄에 저장한 보낸 사람(from_id)과 맞춰 본다."""

    from_id: str = Field(min_length=1, max_length=64)


FORBIDDEN_TEXT = "만든 사람만 삭제할 수 있어요"


@router.post("/office-room/{room_id}/remove")
async def remove_office_room(
    body: RemoveRoomBody,
    request: Request,
    room_id: str = Path(min_length=1, max_length=64),
) -> dict[str, Any]:
    """단톡방에서 내가 만든(처음 보낸) 대화를 지운다. POST 전용.

    1. 그 방 줄이 하나도 없으면 404, 내가 만든 줄이 없으면 403 "만든 사람만 삭제할 수 있어요"
    2. 내가 만든 줄을 office_room.db에서 지운다 (다른 사람이 만든 줄은 그대로)
    3. 각 줄의 부서 대화 서버에서 그 대화를 지운다(`<부서>_chat_remove`, 인사는 HTTP).
       한 부서가 실패해도 나머지는 계속하고 부서별 결과를 돌려준다.
       이미 없는 대화(404)는 지운 것으로 본다
    다른 화면은 목록(GET)을 다시 읽을 때 사라진 것을 본다.
    """
    store = get_store()
    rows = store.in_room(room_id)
    if not rows:
        raise ApiError(404, "NOT_FOUND", "없는 단톡방이에요")
    mine = [r for r in rows if r["from_id"] == body.from_id]
    if not mine:
        raise ApiError(403, "FORBIDDEN", FORBIDDEN_TEXT)
    removed = store.drop(room_id, [(r["agent"], r["thread_id"]) for r in mine])
    results: list[dict[str, Any]] = []
    for r in mine:
        one: dict[str, Any] = {"agent": r["agent"], "thread_id": r["thread_id"]}
        try:
            await chat_remove.remove(r["agent"], r["thread_id"], request)
            results.append(one | {"ok": True})
        except ApiError as exc:
            if exc.status_code == 404:  # 부서 쪽에 이미 없다 = 지워진 것
                results.append(one | {"ok": True, "note": "이미 없음"})
            else:
                results.append(
                    one | {"ok": False, "status": exc.status_code, "reason": exc.message}
                )
        except Exception as exc:  # noqa: BLE001 - 한 부서 실패가 나머지를 막지 않게 한다
            results.append(one | {"ok": False, "status": 502, "reason": type(exc).__name__})
    return ok(
        {
            "room_id": room_id,
            "removed": removed,
            "kept": len(rows) - len(mine),
            "departments": results,
        }
    )
