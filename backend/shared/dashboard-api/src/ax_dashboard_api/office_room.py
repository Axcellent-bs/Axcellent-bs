"""오피스 단톡방 대화 목록 (AI 오피스 단톡방·AI 대화방 전체 대화방).

단톡방에서 부서 AI에게 건 대화는 모두에게 보인다. 1:1 직접 대화는 여기에 올리지 않는다(비공개).
대화 원문은 부서 AI 대화 창구(`/api/dashboard/<부서>-chat`)에 그대로 있고, 여기에는
"어느 부서의 어느 대화를 누가 단톡방에서 걸었는지"와 마지막으로 말을 보낸 시각만 둔다.
화면은 이 목록을 읽고, 새 대화이거나 마지막 시각이 바뀐 대화만 각 창구에서 다시 읽는다.

- `POST /api/dashboard/office-room`: 단톡방에서 말을 보낸 화면이 보낼 때마다 올린다.
  사람 토큰이 필요하다. 같은 부서·같은 대화를 다시 올리면 `updated_at`만 바뀐다
  (보낸 사람·건 사람은 처음 올린 사람 그대로).
  11/20: 처음 올릴 때 건 사람의 사람 토큰 이름(`owner_who`)을 같이 남긴다.
  중계 부서 8곳은 그 대화가 올리는 사람 것인지 부서 대화 창구로 먼저 확인한다
  (아니면 403 NOT_THREAD_OWNER, 부서가 응답하지 않으면 503).
  단톡방 대화의 상세는 로그인한 누구나 원문을 읽는다(`chat_read`, 건 사람 이름으로 묻는다)
- `GET /api/dashboard/office-room?hours=&room=`: 최근 `hours`시간(기본 24, 최대 168) 안에
  말이 오간 대화. 최근에 말이 오간 것이 먼저다(최대 200줄). `room`을 주면 그 방 대화만 준다.
  읽기는 다른 화면 읽기처럼 열려 있다. 이 읽기는 부서 AI를 부르지 않아 공통 로그에 줄이
  쌓이지 않는다.
- `POST /api/dashboard/office-room/{room_id}/remove` `{from_id}`: 그 방에서 내가 만든 대화를
  목록에서 지운다(office_room.db 줄 + 부서 대화 서버의 대화). 줄을 지우지 않고
  removed_at·removed_by를 남기는 숨김 처리라 기록은 보관된다.
  내가 만든 대화가 없으면 403, 보이는 줄이 없는 방이면 404.
- `room_id`: 대화가 오간 방. 전체 대화방은 `all`(기본값), 장애·이슈 방은 총괄 사건 `context_id`다.
  같은 부서·같은 대화를 다른 방으로 다시 올려도 처음 방 그대로 둔다(대화가 방을 옮겨 다니지 않게).
- `from_id`·`from_name`은 화면 로그인 계정(데모 계정)이다. 사람 토큰은 화면이 같이 쓰는 값이라
  보낸 사람을 가르지 못한다.

저장은 SQLite 파일 하나다. 위치는 `AX_OFFICE_ROOM_DB`이고, 비어 있으면 프로세스 메모리
(`:memory:`)에 둔다(다시 켜면 사라진다).

11/23 대화방 정리 (회사 칸 `tenant_id`, 요청 회사 = 로그인 세션의 회사, 세션이 없으면 캣북스):
- 장애·이슈 방 삭제 = 모두에게 숨김(`hidden_rooms`). 기록은 남고 되돌릴 수 있다. 장애 대응 탭·총괄
  기록은 그대로이고 대화 기록의 방 목록에서만 빠진다.
  - `POST /office-room/{room_id}/hide`: room_id는 `ctx_inc_`로 시작해야 한다(아니면 422).
    총괄 `GET /api/incidents/{room_id}`로 상태를 확인한다. 로그인한 사람의 Authorization 헤더만
    그대로 넘겨 총괄이 같은 회사 기록을 본다(토큰은 로그·오류·응답에 넣지 않고, 다른 서비스로
    넘기지 않는다). 해결됨이면 숨기고, open·degraded는 409, 총괄 404(우리 회사 사건이 아님·없는
    방)는 404 그대로, 401·403·5xx·시간 초과는 확인하지 못한 것이라 막는다(503, fail-closed).
  - `POST /office-room/{room_id}/unhide`: 되돌리기. `GET /office-room/hidden`: 숨긴 방 목록
- `/clear` = 나만 지우기(`room_clears`). 그 방에서 지금까지의 대화를 내 화면에서만 뺀다
  (서버 기록·다른 사람 화면은 그대로). 사람(사람 토큰 이름)마다 방마다 한 줄이다.
  - `POST /office-room/{room_id}/clear`, `POST /office-room/{room_id}/clear/undo`,
    `GET /office-room/clears`(내 것만)
"""

from __future__ import annotations

import os
import sqlite3
import threading
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

from fastapi import APIRouter, Depends, Path, Query, Request
from pydantic import BaseModel, Field

from ax_agent_template.company_scope import scope_id
from ax_dashboard_api import chat_remove, relay
from ax_dashboard_api.auth import human_from_authorization, require_human
from ax_dashboard_api.envelope import ApiError, ok, session_company

DB_ENV = "AX_OFFICE_ROOM_DB"
DEFAULT_HOURS = 24
MAX_HOURS = 168
MAX_ROWS = 200
ALL_ROOM = "all"  # 전체 대화방 (화면 ALL_ROOM). 장애·이슈 방은 총괄 사건 context_id

# 단톡방에서 말을 걸 수 있는 부서 (화면 useOfficeTalk의 OFFICE_TALK_KEYS와 같다).
# safety(안전 처리 로그)는 대화가 안전 처리 로그 서버 HTTP `/api/safety/chat`에 있다
Agent = Literal[
    "dev",
    "data",
    "support",
    "finance",
    "planning",
    "marketing",
    "hr",
    "archive",
    "infra",
    "orchestrator",
    "safety",
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
    removed_at TEXT,
    removed_by TEXT,
    PRIMARY KEY (agent, thread_id)
);
CREATE INDEX IF NOT EXISTS idx_office_threads_updated ON office_threads (updated_at);
CREATE TABLE IF NOT EXISTS hidden_rooms (
    room_id     TEXT NOT NULL,
    tenant_id   TEXT NOT NULL DEFAULT 'catbooks',
    hidden_by   TEXT NOT NULL,
    hidden_at   TEXT NOT NULL,
    restored_at TEXT,
    PRIMARY KEY (room_id, tenant_id)
);
CREATE TABLE IF NOT EXISTS room_clears (
    room_id    TEXT NOT NULL,
    tenant_id  TEXT NOT NULL DEFAULT 'catbooks',
    who        TEXT NOT NULL,
    cleared_at TEXT,
    PRIMARY KEY (room_id, tenant_id, who)
);
"""
INCIDENT_ROOM_PREFIX = "ctx_inc_"  # 장애·이슈 방(총괄 사건 context_id)
OPEN_INCIDENT = frozenset({"open", "degraded"})  # 진행 중: 숨길 수 없다
RESOLVED_INCIDENT = "resolved"
RUNNING_TEXT = "진행 중인 장애 방은 삭제할 수 없어요"
UNCHECKED_TEXT = "장애 상태를 확인하지 못해 삭제하지 않았어요"
# room_id 칸이 생기기 전 파일에도 칸을 붙인 뒤 만든다
_ROOM_INDEX = "CREATE INDEX IF NOT EXISTS idx_office_threads_room ON office_threads (room_id)"
# 예전 파일에 없을 수 있는 칸: 칸 이름 → 붙일 정의.
# 지우기는 줄을 지우지 않고 숨긴 시각·사람(removed_at·removed_by)을 남긴다
_LATE_COLUMNS = {
    "room_id": f"TEXT NOT NULL DEFAULT '{ALL_ROOM}'",
    "removed_at": "TEXT",
    "removed_by": "TEXT",
    # 11/20: 대화를 건 사람의 사람 토큰 이름 (부서 서버가 그 대화에 저장한 who와 같다).
    # 단톡방 대화를 다른 계정이 원문으로 읽을 때 부서에 이 이름으로 묻는다(`read_who`).
    # 예전 줄은 비어 있다(채우지 않음: from_id는 화면 계정 id라 부서가 저장한 who를 확실히 모른다)
    "owner_who": "TEXT",
    # 11/24: 대화를 올린 회사(로그인 세션의 회사). 예전 줄은 캣북스(회사별 전에는 캣북스뿐이었다).
    # 목록·방 읽기·지우기는 요청 회사의 줄만 본다(다른 회사 대화가 보이지 않게)
    "tenant_id": "TEXT NOT NULL DEFAULT 'catbooks'",
}
# 원문을 읽을 때 dashboard-api가 부서 대화 창구(A2A)로 중계하는 부서.
# 단톡방에 처음 올릴 때 그 대화가 올리는 사람 것인지 이 창구로 확인한다.
# hr·safety·archive는 부서 서버가 HTTP로 직접 받아 room_id로 정한다
RELAY_AGENTS = frozenset({"marketing", "planning", "finance", "orchestrator", "data", "infra",
                          "dev", "support"})  # fmt: skip
NOT_OWNER_TEXT = "내가 건 대화만 단톡방에 올릴 수 있어요"
_SHOWN = "removed_at IS NULL"


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
        self._add_late_columns()

    def _add_late_columns(self) -> None:
        """room_id·removed_at·removed_by 칸이 없는 예전 파일이면 붙인다.

        이미 있으면 아무것도 하지 않는다(여러 번 켜도 안전).
        """
        with self._conn:
            cols = {r["name"] for r in self._conn.execute("PRAGMA table_info(office_threads)")}
            for name, spec in _LATE_COLUMNS.items():
                if name not in cols:
                    self._conn.execute(f"ALTER TABLE office_threads ADD COLUMN {name} {spec}")
            self._conn.execute(_ROOM_INDEX)

    def get(self, agent: str, thread_id: str) -> dict[str, Any] | None:
        """그 부서·대화 줄 (숨긴 줄 포함). 없으면 None."""
        with self._lock:
            row = self._conn.execute(
                "SELECT * FROM office_threads WHERE agent = ? AND thread_id = ?",
                (agent, thread_id),
            ).fetchone()
        return dict(row) if row else None

    def touch(
        self,
        body: OfficeThreadBody,
        now: datetime,
        owner_who: str | None = None,
        tenant: str = "catbooks",
    ) -> dict[str, Any]:
        """처음이면 넣고, 있으면 마지막 시각만 바꾼다(보낸 사람·방·건 사람·회사는 처음 그대로)."""
        at = now.isoformat()
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO office_threads (agent, thread_id, from_id, from_name, created_at,"
                " updated_at, room_id, owner_who, tenant_id) VALUES (?,?,?,?,?,?,?,?,?)"
                " ON CONFLICT (agent, thread_id) DO UPDATE SET updated_at = excluded.updated_at",
                (
                    body.agent,
                    body.thread_id,
                    body.from_id,
                    body.from_name,
                    at,
                    at,
                    body.room_id,
                    owner_who,
                    tenant,
                ),
            )
            row = self._conn.execute(
                "SELECT * FROM office_threads WHERE agent = ? AND thread_id = ?",
                (body.agent, body.thread_id),
            ).fetchone()
        return dict(row)

    def recent(
        self, since: datetime, room: str | None = None, tenant: str = "catbooks"
    ) -> list[dict[str, Any]]:
        """그 회사의 `since` 뒤에 말이 오간 대화. `room`을 주면 그 방만. 숨긴(지운) 줄은 뺀다."""
        where = f"updated_at >= ? AND tenant_id = ? AND {_SHOWN}"
        args: list[str] = [since.isoformat(), tenant]
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

    def in_room(self, room: str, tenant: str = "catbooks") -> list[dict[str, Any]]:
        """그 회사·그 방의 보이는 대화 줄 전부 (기간과 상관없이, 숨긴 줄은 뺀다)."""
        with self._lock:
            rows = self._conn.execute(
                f"SELECT * FROM office_threads WHERE room_id = ? AND tenant_id = ? AND {_SHOWN}"
                " ORDER BY created_at, rowid",
                (room, tenant),
            ).fetchall()
        return [dict(r) for r in rows]

    def hide(self, room: str, keys: list[tuple[str, str]], by: str, now: datetime) -> int:
        """그 방의 (부서, 대화 id) 줄을 숨긴다(줄은 남기고 removed_at·removed_by를 적는다).

        이미 숨긴 줄은 그대로 둔다(처음 숨긴 시각·사람 유지). 새로 숨긴 줄 수를 돌려준다.
        """
        with self._lock, self._conn:
            return sum(
                self._conn.execute(
                    "UPDATE office_threads SET removed_at = ?, removed_by = ?"
                    f" WHERE room_id = ? AND agent = ? AND thread_id = ? AND {_SHOWN}",
                    (now.isoformat(), by, room, agent, thread_id),
                ).rowcount
                for agent, thread_id in keys
            )

    # ---- 장애·이슈 방 숨김 (모두에게, 회사별) -------------------------------------------
    def hide_room(self, room: str, tenant: str, by: str, now: datetime) -> dict[str, Any]:
        """숨긴다. 이미 숨겼으면 그대로, 되돌렸던 방이면 다시 숨긴다(숨긴 사람·때를 새로 적는다)."""
        at = now.isoformat()
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO hidden_rooms (room_id, tenant_id, hidden_by, hidden_at)"
                " VALUES (?,?,?,?)"
                " ON CONFLICT (room_id, tenant_id) DO UPDATE SET hidden_by = excluded.hidden_by,"
                " hidden_at = excluded.hidden_at, restored_at = NULL"
                " WHERE hidden_rooms.restored_at IS NOT NULL",
                (room, tenant, by, at),
            )
            row = self._conn.execute(
                "SELECT * FROM hidden_rooms WHERE room_id = ? AND tenant_id = ?", (room, tenant)
            ).fetchone()
        return dict(row)

    def unhide_room(self, room: str, tenant: str, now: datetime) -> bool:
        """되돌린다(줄은 남기고 restored_at을 적는다). 숨긴 방이 아니면 False."""
        with self._lock, self._conn:
            cur = self._conn.execute(
                "UPDATE hidden_rooms SET restored_at = ?"
                " WHERE room_id = ? AND tenant_id = ? AND restored_at IS NULL",
                (now.isoformat(), room, tenant),
            )
        return cur.rowcount == 1

    def hidden_rooms(self, tenant: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT room_id, hidden_by, hidden_at FROM hidden_rooms"
                " WHERE tenant_id = ? AND restored_at IS NULL ORDER BY hidden_at DESC",
                (tenant,),
            ).fetchall()
        return [dict(r) for r in rows]

    # ---- /clear (나만 지우기) -------------------------------------------------------
    def set_clear(self, room: str, tenant: str, who: str, at: datetime | None) -> None:
        """그 사람의 그 방 지운 시각. None이면 되돌리기(줄은 남기고 시각만 비운다)."""
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO room_clears (room_id, tenant_id, who, cleared_at) VALUES (?,?,?,?)"
                " ON CONFLICT (room_id, tenant_id, who)"
                " DO UPDATE SET cleared_at = excluded.cleared_at",
                (room, tenant, who, at.isoformat() if at else None),
            )

    def clears(self, tenant: str, who: str) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT room_id, cleared_at FROM room_clears"
                " WHERE tenant_id = ? AND who = ? AND cleared_at IS NOT NULL ORDER BY room_id",
                (tenant, who),
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


def shared_owner(agent: str, thread_id: str) -> str | None:
    """단톡방에 올라온(숨기지 않은) 대화를 건 사람. 1:1 대화·숨긴 줄·예전 줄(이름 없음)이면 None."""
    row = get_store().get(agent, thread_id)
    if row is None or row.get("removed_at") or not row.get("owner_who"):
        return None
    if (
        scope_id(row.get("tenant_id")) != request_company()
    ):  # 다른 회사 단톡방 대화는 빌려 읽지 않는다
        return None
    return str(row["owner_who"])


def read_who(agent: str, thread_id: str, who: str) -> str:
    """원문을 부서에 물을 때 넘길 who: 단톡방 대화면 건 사람, 아니면 요청한 사람 그대로.

    읽기(상세)에만 쓴다. 이어 쓰기·끝내기·지우기는 요청한 사람 그대로라
    부서의 본인 검사가 막는다."""
    return shared_owner(agent, thread_id) or who


# 쓰기(POST)는 사람 토큰, 읽기는 열려 있다
router = APIRouter(prefix="/api/dashboard", dependencies=[Depends(require_human)])


async def _check_owner(agent: str, thread_id: str, request: Request, who: str) -> None:
    """처음 올리는 대화가 올리는 사람 것인지 부서 대화 창구로 확인한다 (남의 1:1 대화 번호로
    원문을 여는 길을 막는다). 본인 것이 아니면 403, 부서가 응답하지 않으면 503."""
    from ax_dashboard_api import chat_read

    try:
        thread = await chat_read.get_thread(agent, thread_id, who,
                                            request.headers.get("authorization"))  # fmt: skip
    except ApiError as exc:
        if exc.status_code in (403, 404):
            raise ApiError(403, "NOT_THREAD_OWNER", NOT_OWNER_TEXT) from None
        raise ApiError(503, "UPSTREAM_UNAVAILABLE", f"부서 대화를 확인하지 못했어요 ({agent})"
                       ) from None  # fmt: skip
    except Exception as exc:  # noqa: BLE001 - 연결 실패 등: 넣지 않고 다시 시도하게 한다
        why = f"부서 대화를 확인하지 못했어요 ({type(exc).__name__})"
        raise ApiError(503, "UPSTREAM_UNAVAILABLE", why) from None
    if not isinstance(thread, dict) or not thread or thread.get("restricted") is not False:
        raise ApiError(403, "NOT_THREAD_OWNER", NOT_OWNER_TEXT)


@router.post("/office-room", status_code=201)
async def touch_office_thread(body: OfficeThreadBody, request: Request) -> dict[str, Any]:
    """처음 올리면 건 사람(사람 토큰 이름)을 같이 남긴다. 중계 부서 8곳은 그 대화가 올리는 사람
    것인지 먼저 확인한다. 이미 있는 줄은 마지막 시각만 바꾼다."""
    store = get_store()
    who = human_from_authorization(request.headers.get("authorization"))
    tenant = request_company()
    found = store.get(body.agent, body.thread_id)
    if found is not None and scope_id(found.get("tenant_id")) != tenant:
        raise ApiError(404, "NOT_FOUND", "없는 대화예요")  # 다른 회사 대화는 없는 것처럼
    if found is None and body.agent in RELAY_AGENTS:
        await _check_owner(body.agent, body.thread_id, request, who)
    return ok(store.touch(body, _now(), owner_who=who, tenant=tenant))


@router.get("/office-room")
def list_office_threads(
    hours: int = Query(default=DEFAULT_HOURS, ge=1, le=MAX_HOURS),
    room: str | None = Query(default=None, min_length=1, max_length=64),
) -> dict[str, Any]:
    rows = get_store().recent(_now() - timedelta(hours=hours), room, request_company())
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
    """단톡방에서 내가 만든(처음 보낸) 대화를 목록에서 지운다. POST 전용.

    지우기는 숨김 처리다: 줄을 지우지 않고 removed_at·removed_by를 남긴다
    (기록은 보관, 되돌릴 수 있음).
    1. 그 방에 보이는 줄이 없으면 404, 내가 만든 줄이 없으면 403 "만든 사람만 삭제할 수 있어요"
    2. 내가 만든 줄을 office_room.db에서 숨긴다 (다른 사람이 만든 줄은 그대로)
    3. 각 줄의 부서 대화 서버에서 그 대화를 숨긴다(`<부서>_chat_remove`, 인사·safety는 HTTP).
       한 부서가 실패해도 나머지는 계속하고 부서별 결과를 돌려준다.
       이미 없는 대화(404)는 지운 것으로 본다
    다른 화면은 목록(GET)을 다시 읽을 때 사라진 것을 본다.
    """
    store = get_store()
    rows = store.in_room(room_id, request_company())
    if not rows:
        raise ApiError(404, "NOT_FOUND", "없는 단톡방이에요")
    mine = [r for r in rows if r["from_id"] == body.from_id]
    if not mine:
        raise ApiError(403, "FORBIDDEN", FORBIDDEN_TEXT)
    keys = [(r["agent"], r["thread_id"]) for r in mine]
    removed = store.hide(room_id, keys, body.from_id, _now())
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


# ---------------------------------------------------------------- 11/23 대화방 정리
def request_company() -> str:
    """이 요청의 회사: 로그인 세션의 회사(세션이 없으면 캣북스). 총괄도 같은 세션 토큰으로 회사를
    정하므로(`bind_company`) 총괄이 본 회사와 여기 적는 회사가 같다."""
    return scope_id(session_company())


async def _incident_status(room_id: str, authorization: str | None) -> str:
    """총괄에 사건 상태를 묻는다. 로그인한 사람의 Authorization 헤더만 그대로 넘긴다.

    404는 그대로(우리 회사 사건이 아니거나 없는 방), 그 밖에 확인하지 못하면 503(막는다)."""
    headers = {"Authorization": authorization} if authorization else None
    try:
        data = await relay._get("orchestrator", f"/api/incidents/{room_id}", headers=headers)  # noqa: SLF001
    except ApiError as exc:
        if exc.status_code == 404:
            raise ApiError(404, "NOT_FOUND", "없는 장애 방이에요") from None
        raise ApiError(503, "INCIDENT_UNCHECKED", UNCHECKED_TEXT) from None
    status = data.get("status") if isinstance(data, dict) else None
    if not isinstance(status, str):
        raise ApiError(503, "INCIDENT_UNCHECKED", UNCHECKED_TEXT)
    return status


def _incident_room(room_id: str) -> str:
    if not room_id.startswith(INCIDENT_ROOM_PREFIX):
        raise ApiError(422, "NOT_INCIDENT_ROOM", "장애·이슈 방만 삭제할 수 있어요")
    return room_id


@router.post("/office-room/{room_id}/hide")
async def hide_incident_room(
    request: Request, room_id: str = Path(min_length=1, max_length=64)
) -> dict[str, Any]:
    """장애·이슈 방을 모두의 방 목록에서 숨긴다(해결된 사건만, 기록은 남고 되돌릴 수 있다)."""
    who = human_from_authorization(request.headers.get("authorization"))
    room = _incident_room(room_id)
    status = await _incident_status(room, request.headers.get("authorization"))
    if status in OPEN_INCIDENT:
        raise ApiError(409, "INCIDENT_RUNNING", RUNNING_TEXT)
    if status != RESOLVED_INCIDENT:
        raise ApiError(503, "INCIDENT_UNCHECKED", UNCHECKED_TEXT)
    return ok(get_store().hide_room(room, request_company(), who, _now()))


@router.post("/office-room/{room_id}/unhide")
def unhide_incident_room(
    request: Request, room_id: str = Path(min_length=1, max_length=64)
) -> dict[str, Any]:
    """숨긴 장애·이슈 방을 되돌린다. 이 회사에서 숨긴 방이 아니면 404."""
    human_from_authorization(request.headers.get("authorization"))
    room = _incident_room(room_id)
    if not get_store().unhide_room(room, request_company(), _now()):
        raise ApiError(404, "NOT_FOUND", "숨긴 방이 아니에요")
    return ok({"room_id": room, "restored": True})


@router.get("/office-room/hidden")
def list_hidden_rooms(request: Request) -> dict[str, Any]:
    """이 회사에서 숨긴 장애·이슈 방 (최근에 숨긴 것부터). 사람 토큰 필요."""
    human_from_authorization(request.headers.get("authorization"))
    rows = get_store().hidden_rooms(request_company())
    return ok(rows, total=len(rows))


@router.post("/office-room/{room_id}/clear")
def clear_room(
    request: Request, room_id: str = Path(min_length=1, max_length=64)
) -> dict[str, Any]:
    """/clear: 이 방의 지금까지 대화를 내 화면에서 지운다(지금 시각을 적는다).

    서버 기록·다른 사람 화면은 그대로다."""
    who = human_from_authorization(request.headers.get("authorization"))
    at = _now()
    get_store().set_clear(room_id, request_company(), who, at)
    return ok({"room_id": room_id, "cleared_at": at.isoformat()})


@router.post("/office-room/{room_id}/clear/undo")
def undo_clear_room(
    request: Request, room_id: str = Path(min_length=1, max_length=64)
) -> dict[str, Any]:
    """/clear 되돌리기: 지운 대화를 내 화면에 다시 보인다."""
    who = human_from_authorization(request.headers.get("authorization"))
    get_store().set_clear(room_id, request_company(), who, None)
    return ok({"room_id": room_id, "cleared_at": None})


@router.get("/office-room/clears")
def list_room_clears(request: Request) -> dict[str, Any]:
    """내가 /clear 한 방과 시각 (내 것만, 이 회사만)."""
    who = human_from_authorization(request.headers.get("authorization"))
    rows = get_store().clears(request_company(), who)
    return ok(rows, total=len(rows))
