"""사람 할 일함 (업무 요청 라우터, 계약 `ax_contracts.schemas.handoff`).

켜지 않은 서비스로 가는 요청을 에이전트(`call_agent`)가 여기에 넣고,
콘솔에서 사람이 결과를 적어 끝낸다.

- `POST /api/dashboard/handoffs`: 에이전트가 넣는다. 에이전트 토큰(`Authorization: Bearer`)만
  받고, `from_service`는 토큰의 에이전트 이름으로 다시 채운다.
  같은 `idempotency_key`면 처음 만든 일을 돌려준다.
- `GET /api/dashboard/handoffs?unit=&status=`: 부서·상태로 거른다 (읽기는 열려 있다).
- `POST /api/dashboard/handoffs/{task_id}/complete`: 사람 토큰이 필요하다. 결과를 적고 끝내면
  `handoff.completed` 이벤트를 발행한다. 발행하지 못하면 일은 열린 채로 두고 503을 돌려준다.

저장은 공통 로그처럼 SQLite 파일 하나다. 위치는 `AX_HANDOFF_DB`이고, 비어 있으면 프로세스 메모리
(`:memory:`)에 둔다(다시 켜면 사라진다).
"""

from __future__ import annotations

import os
import sqlite3
import threading
import uuid
from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, Header

from ax_agent_template import events
from ax_agent_template.logging import log_event
from ax_agent_template.server import verify_token
from ax_contracts.schemas import (
    TENANT_ID_DEFAULT,
    EventEnvelope,
    EventType,
    HandoffCompleteBody,
    HandoffCompletedData,
    HandoffCreate,
    HandoffStatus,
    HandoffTask,
)
from ax_dashboard_api.auth import bearer, require_human_for_writes
from ax_dashboard_api.envelope import ApiError, ok

DB_ENV = "AX_HANDOFF_DB"
SOURCE = "dashboard-api"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS handoffs (
    seq             INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id         TEXT NOT NULL UNIQUE,
    tenant_id       TEXT NOT NULL,
    assignee_unit   TEXT NOT NULL,
    status          TEXT NOT NULL,
    idempotency_key TEXT UNIQUE,
    created_at      TEXT NOT NULL,
    body            TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_handoffs_unit ON handoffs (assignee_unit, status);
"""


class HandoffStore:
    """SQLite 파일(또는 `:memory:`) 저장소. 행마다 `HandoffTask` JSON을 그대로 둔다."""

    def __init__(self, path: str) -> None:
        self.path = path
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(path, check_same_thread=False, timeout=5.0)
        if path != ":memory:":
            self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(_SCHEMA)

    def add(self, body: HandoffCreate) -> HandoffTask:
        with self._lock, self._conn:
            if body.idempotency_key:
                row = self._conn.execute(
                    "SELECT body FROM handoffs WHERE idempotency_key = ?", (body.idempotency_key,)
                ).fetchone()
                if row:
                    return HandoffTask.model_validate_json(row[0])
            fields = body.model_dump(exclude={"idempotency_key"})
            task = HandoffTask(
                task_id=f"hof_{uuid.uuid4().hex[:12]}", created_at=datetime.now(UTC), **fields
            )
            self._conn.execute(
                "INSERT INTO handoffs (task_id, tenant_id, assignee_unit, status, idempotency_key,"
                " created_at, body) VALUES (?,?,?,?,?,?,?)",
                (
                    task.task_id,
                    task.tenant_id,
                    task.assignee_unit,
                    task.status.value,
                    body.idempotency_key,
                    task.created_at.isoformat(),
                    task.model_dump_json(),
                ),
            )
            return task

    def get(self, task_id: str) -> HandoffTask | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT body FROM handoffs WHERE task_id = ?", (task_id,)
            ).fetchone()
        return HandoffTask.model_validate_json(row[0]) if row else None

    def list(
        self, tenant_id: str, unit: str | None = None, status: HandoffStatus | None = None
    ) -> list[HandoffTask]:
        where, args = ["tenant_id = ?"], [tenant_id]
        if unit:
            where.append("assignee_unit = ?")
            args.append(unit)
        if status:
            where.append("status = ?")
            args.append(status.value)
        sql = "SELECT body FROM handoffs WHERE " + " AND ".join(where) + " ORDER BY seq DESC"
        with self._lock:
            rows = self._conn.execute(sql, args).fetchall()
        return [HandoffTask.model_validate_json(r[0]) for r in rows]

    def close_task(self, task: HandoffTask) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE handoffs SET status = ?, body = ? WHERE task_id = ?",
                (task.status.value, task.model_dump_json(), task.task_id),
            )


_store: HandoffStore | None = None
# 첫 요청 두 개가 동시에 와도 저장소는 하나만 만든다(메모리 저장소면 둘이 생겨 한쪽 일이 사라졌다)
_store_lock = threading.Lock()
_store_key: str | None = None


def get_store() -> HandoffStore:
    """`AX_HANDOFF_DB`가 바뀌면 새로 연다. 비어 있으면 메모리 저장소."""
    global _store, _store_key
    path = os.getenv(DB_ENV) or ":memory:"
    with _store_lock:
        if _store is None or _store_key != path:
            _store = HandoffStore(path)
            _store_key = path
        return _store


def reset_store() -> None:
    """테스트 초기화용."""
    global _store, _store_key
    with _store_lock:
        _store, _store_key = None, None


def _tenant() -> str:
    return os.getenv("TENANT_ID", TENANT_ID_DEFAULT)


def _agent_from(authorization: str | None) -> str:
    """에이전트 토큰을 검증해 이름을 돌려준다. 사람 토큰·모르는 토큰은 403."""
    token = bearer(authorization)
    if not token:
        raise ApiError(
            401, "AUTH_REQUIRED", "에이전트 토큰이 필요하다 (Authorization: Bearer 토큰)"
        )
    try:
        return verify_token(token)
    except PermissionError:
        raise ApiError(403, "AGENT_TOKEN_INVALID", "에이전트 토큰을 검증할 수 없다") from None


# 에이전트가 넣는 창구: 사람 토큰 대신 에이전트 토큰을 본다
intake = APIRouter(prefix="/api/dashboard")
# 사람이 보는 창구: 쓰기(complete)는 사람 토큰이 필요하다
inbox = APIRouter(prefix="/api/dashboard", dependencies=[Depends(require_human_for_writes)])


@intake.post("/handoffs", status_code=201)
def create_handoff(
    body: HandoffCreate, authorization: str | None = Header(default=None)
) -> dict[str, Any]:
    agent = _agent_from(authorization)
    task = get_store().add(body.model_copy(update={"from_service": agent}))
    return ok(task.model_dump(mode="json"))


@inbox.get("/handoffs")
def list_handoffs(unit: str | None = None, status: HandoffStatus | None = None) -> dict[str, Any]:
    """`unit`은 맡은 부서(조직도의 unit), `status`는 open·done·rejected. 최근 일이 먼저다."""
    tasks = get_store().list(_tenant(), unit, status)
    return ok([t.model_dump(mode="json") for t in tasks], total=len(tasks))


@inbox.post("/handoffs/{task_id}/complete")
async def complete_handoff(task_id: str, body: HandoffCompleteBody) -> dict[str, Any]:
    store = get_store()
    task = store.get(task_id)
    if task is None:
        raise ApiError(404, "NOT_FOUND", f"할 일을 찾을 수 없다: {task_id}")
    if task.status != HandoffStatus.OPEN:
        raise ApiError(409, "ALREADY_CLOSED", f"이미 끝난 일이다: {task_id} ({task.status.value})")
    closed = task.model_copy(
        update={
            "status": HandoffStatus(body.status),
            "result": body.result,
            "closed_at": datetime.now(UTC),
        }
    )
    closed = HandoffTask.model_validate(closed.model_dump())  # 끝난 일 규칙을 다시 확인한다
    envelope = EventEnvelope(
        event_id=f"evt_{uuid.uuid4().hex[:16]}",
        type=EventType.HANDOFF_COMPLETED,
        occurred_at=closed.closed_at,
        tenant_id=closed.tenant_id,
        context_id=closed.context_id,
        source=SOURCE,
        idempotency_key=f"handoff.completed:{closed.task_id}",
        data=HandoffCompletedData(
            task_id=closed.task_id,
            status=body.status,
            from_service=closed.from_service,
            to_service=closed.to_service,
            skill=closed.skill,
        ).model_dump(),
        untrusted={"result": body.result},
    )
    try:
        await events.publish(envelope)
    except Exception as error:  # noqa: BLE001 - 버스가 없으면 끝내지 않고 알린다
        raise ApiError(
            503,
            "EVENT_BUS_UNAVAILABLE",
            f"handoff.completed를 발행하지 못해 일을 끝내지 않았다: {error}",
        ) from None
    store.close_task(closed)
    log_event(
        closed.context_id,
        closed.tenant_id,
        "task_status",
        {
            "task_id": closed.task_id,
            "to_service": closed.to_service,
            "outcome": f"handoff_{body.status}",
            "event_id": envelope.event_id,
        },
        source=SOURCE,
        skill=closed.skill,
        task_status="completed",
    )
    return ok(closed.model_dump(mode="json"))
