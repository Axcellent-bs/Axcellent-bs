"""실행 기록·멱등성·보류 큐·감사 로그 저장소.

`SAFETY_STATE_DB`(SQLite 파일 경로)가 있으면 실행 기록·멱등성 키·보류 건·감사 로그를 그 파일에
같이 남긴다(write-through). 다시 켜면 파일에서 읽어 와서 재시작해도 보류 큐와 실행 기록이 남는다.
기다리던 보류 건은 원래 마감(`expires_at`)을 그대로 지닌다. 값이 없으면 v0처럼 메모리에만 둔다
(테스트용). 감사 로그는 추가만 한다.

파일에 못 쓰면 `StoreWriteError`를 낸다. 메모리는 파일에 쓴 뒤에만 바꾼다. 관문은 이 오류면
실행하지 않는다(fail-closed, "감사 로그를 못 남기면 실행하지 않는다"와 같은 원칙).

외부 연결 쓰기 보류만은 `SAFETY_CONNECTION_HOLDS_FILE`이 있으면 파일에도 남긴다. 개발 서비스는
보류를 한 번만 보내므로, 재시작으로 잊으면 취약한 연결의 쓰기가 그대로 나간다.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from ax_contracts.schemas import ConnectionHoldNotice, ExecuteDecision, ExecuteRequest

ReviewStatus = Literal["pending", "approved", "rejected", "expired"]


class StoreWriteError(RuntimeError):
    """상태 파일(`SAFETY_STATE_DB`)에 쓰지 못했다. 관문은 실행하지 않는다."""


class ExecutionRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    execution_id: str
    created_at: datetime
    request: ExecuteRequest
    decision: ExecuteDecision
    customer: str | None = None
    report: dict[str, Any] = Field(default_factory=dict)


class Review(BaseModel):
    """C 보류 건. 담당자가 대시보드에서 결정하고, 마감까지 결정이 없으면 자동 거절한다.

    `decided_by`는 토큰이 가리키는 사람이다. 모두가 같이 쓰는 공용 토큰이면 누가 눌렀는지
    알 수 없어서, 화면이 받은 승인자 이름(자기 신고)을 `decided_as`에 따로 남긴다.
    """

    model_config = ConfigDict(extra="forbid")

    review_id: str
    execution_id: str
    context_id: str
    tenant_id: str
    action: str
    requester: str
    created_at: datetime
    expires_at: datetime
    status: ReviewStatus = "pending"
    decided_by: str | None = None
    decided_as: str | None = None
    decided_at: datetime | None = None
    note: str | None = None
    report: dict[str, Any] = Field(default_factory=dict)


class AuditEntry(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)

    log_id: int
    at: datetime
    context_id: str
    tenant_id: str
    step: str
    execution_id: str | None = None
    detail: dict[str, Any] = Field(default_factory=dict)


_SCHEMA = """
CREATE TABLE IF NOT EXISTS executions (
    execution_id TEXT PRIMARY KEY,
    body TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS idempotency_keys (
    tenant_id TEXT NOT NULL,
    idempotency_key TEXT NOT NULL,
    execution_id TEXT NOT NULL,
    PRIMARY KEY (tenant_id, idempotency_key)
);
CREATE TABLE IF NOT EXISTS reviews (
    review_id TEXT PRIMARY KEY,
    status TEXT NOT NULL,
    expires_at TEXT NOT NULL,
    body TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS audit_logs (
    log_id INTEGER PRIMARY KEY,
    body TEXT NOT NULL
);
"""

_DEFAULT = object()


class _StateDB:
    """SQLite write-through. 한 번 쓰는 묶음은 한 트랜잭션이다."""

    def __init__(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        self.path = path
        # 관문은 이벤트 루프 한 곳에서 쓰지만 조회 엔드포인트는 스레드에서 돈다. 잠금으로 묶는다
        self._conn = sqlite3.connect(str(path), check_same_thread=False, isolation_level=None)
        self._lock = threading.Lock()
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.execute("PRAGMA synchronous=FULL")
        self._conn.executescript(_SCHEMA)

    def rows(self, sql: str) -> list[tuple[Any, ...]]:
        with self._lock:
            return list(self._conn.execute(sql))

    def write(self, statements: list[tuple[str, tuple[Any, ...]]]) -> None:
        try:
            with self._lock:
                self._conn.execute("BEGIN IMMEDIATE")
                try:
                    for sql, params in statements:
                        self._conn.execute(sql, params)
                    self._conn.execute("COMMIT")
                except BaseException:
                    self._conn.execute("ROLLBACK")
                    raise
        except sqlite3.Error as exc:
            raise StoreWriteError(f"상태 파일에 쓰지 못했다: {exc}") from exc

    def close(self) -> None:
        with self._lock:
            self._conn.close()


def _execution_rows(record: ExecutionRecord) -> list[tuple[str, tuple[Any, ...]]]:
    return [
        (
            "INSERT OR REPLACE INTO executions (execution_id, body) VALUES (?, ?)",
            (record.execution_id, record.model_dump_json()),
        )
    ]


def _review_row(review: Review) -> tuple[str, tuple[Any, ...]]:
    return (
        "INSERT OR REPLACE INTO reviews (review_id, status, expires_at, body) VALUES (?, ?, ?, ?)",
        (review.review_id, review.status, review.expires_at.isoformat(), review.model_dump_json()),
    )


class Store:
    def __init__(self, state_db: str | Path | None | object = _DEFAULT) -> None:
        """`state_db`를 주지 않으면 `SAFETY_STATE_DB`를 본다. `None`이면 메모리에만 둔다."""
        self._executions: dict[str, ExecutionRecord] = {}
        self._by_key: dict[tuple[str, str], str] = {}
        self._reviews: dict[str, Review] = {}
        self._audit: list[AuditEntry] = []
        if state_db is _DEFAULT:
            state_db = os.getenv("SAFETY_STATE_DB") or None
        self._db = _StateDB(Path(str(state_db))) if state_db else None
        if self._db is not None:
            # 못 읽으면 켜지 않는다. 보류·멱등성 기록을 잊은 채 실행하지 않는다 (fail-closed)
            self._load(self._db)
        raw_path = os.getenv("SAFETY_CONNECTION_HOLDS_FILE")
        self._holds_file = Path(raw_path) if raw_path else None
        self._holds: dict[str, ConnectionHoldNotice] = {}
        if self._holds_file is not None and self._holds_file.exists():
            # 못 읽으면 켜지 않는다. 보류를 잊은 채 쓰기를 통과시키지 않는다 (fail-closed)
            rows = json.loads(self._holds_file.read_text(encoding="utf-8"))
            self._holds = {
                n.connection_id: n for n in map(ConnectionHoldNotice.model_validate, rows)
            }

    def _load(self, db: _StateDB) -> None:
        for (body,) in db.rows("SELECT body FROM executions"):
            record = ExecutionRecord.model_validate_json(body)
            self._executions[record.execution_id] = record
        for tenant_id, key, execution_id in db.rows(
            "SELECT tenant_id, idempotency_key, execution_id FROM idempotency_keys"
        ):
            self._by_key[(tenant_id, key)] = execution_id
        for (body,) in db.rows("SELECT body FROM reviews"):
            review = Review.model_validate_json(body)
            self._reviews[review.review_id] = review
        self._audit = [
            AuditEntry.model_validate_json(body)
            for (body,) in db.rows("SELECT body FROM audit_logs ORDER BY log_id")
        ]

    @property
    def persistent(self) -> bool:
        return self._db is not None

    def close(self) -> None:
        if self._db is not None:
            self._db.close()

    def _write(self, statements: list[tuple[str, tuple[Any, ...]]]) -> None:
        if self._db is not None:
            self._db.write(statements)

    # ---- 외부 연결 쓰기 보류
    def set_connection_hold(self, notice: ConnectionHoldNotice) -> None:
        if notice.hold:
            self._holds[notice.connection_id] = notice
        else:
            self._holds.pop(notice.connection_id, None)
        if self._holds_file is None:
            return
        rows = [n.model_dump(mode="json") for n in self._holds.values()]
        tmp = self._holds_file.with_suffix(self._holds_file.suffix + ".tmp")
        tmp.parent.mkdir(parents=True, exist_ok=True)
        tmp.write_text(json.dumps(rows, ensure_ascii=False), encoding="utf-8")
        tmp.replace(self._holds_file)

    def connection_hold(self, connection_id: str | None) -> ConnectionHoldNotice | None:
        return self._holds.get(connection_id) if connection_id else None

    def list_connection_holds(self) -> list[ConnectionHoldNotice]:
        return list(self._holds.values())

    # ---- 실행 기록·멱등성
    def by_key(self, tenant_id: str, idempotency_key: str) -> ExecutionRecord | None:
        execution_id = self._by_key.get((tenant_id, idempotency_key))
        return self._executions.get(execution_id) if execution_id else None

    def add_execution(self, record: ExecutionRecord, review: Review | None = None) -> None:
        """실행 기록과 멱등성 키(보류면 보류 건까지)를 한 번에 남긴다."""
        key = (record.request.tenant_id, record.request.idempotency_key)
        statements = _execution_rows(record)
        statements.append(
            (
                "INSERT OR REPLACE INTO idempotency_keys"
                " (tenant_id, idempotency_key, execution_id) VALUES (?, ?, ?)",
                (*key, record.execution_id),
            )
        )
        if review is not None:
            statements.append(_review_row(review))
        self._write(statements)
        self._executions[record.execution_id] = record
        self._by_key[key] = record.execution_id
        if review is not None:
            self._reviews[review.review_id] = review

    def get_execution(self, execution_id: str) -> ExecutionRecord | None:
        return self._executions.get(execution_id)

    def set_decision(self, execution_id: str, decision: ExecuteDecision) -> None:
        record = self._executions[execution_id].model_copy(update={"decision": decision})
        self._write(_execution_rows(record))
        self._executions[execution_id] = record

    def list_executions(self, context_id: str | None = None) -> list[ExecutionRecord]:
        records = list(self._executions.values())
        if context_id is not None:
            records = [r for r in records if r.request.context_id == context_id]
        return sorted(records, key=lambda r: r.created_at)

    def executed_total(
        self,
        tenant_id: str,
        action: str,
        *,
        since: datetime | None = None,
        context_id: str | None = None,
        customer: str | None = None,
    ) -> int:
        """실행된(`executed`) 건의 계산 금액 합계. 보류·거부 건은 넣지 않는다."""
        total = 0
        for record in self._executions.values():
            request, decision = record.request, record.decision
            if decision.status != "executed" or decision.computed_amount is None:
                continue
            if request.tenant_id != tenant_id or request.action != action:
                continue
            if since is not None and record.created_at < since:
                continue
            if context_id is not None and request.context_id != context_id:
                continue
            if customer is not None and record.customer != customer:
                continue
            total += decision.computed_amount
        return total

    def executed_count(self, tenant_id: str, action: str, *, since: datetime) -> int:
        """실행된(`executed`) 건수. 금액이 없는 액션도 센다. 보류·거부 건은 넣지 않는다."""
        return sum(
            record.decision.status == "executed"
            and record.request.tenant_id == tenant_id
            and record.request.action == action
            and record.created_at >= since
            for record in self._executions.values()
        )

    # ---- 보류 큐
    def add_review(self, review: Review) -> None:
        """보류 건을 남기거나(같은 id면) 바꾼다. 파일에 쓴 뒤에만 메모리를 바꾼다."""
        self._write([_review_row(review)])
        self._reviews[review.review_id] = review

    def get_review(self, review_id: str) -> Review | None:
        return self._reviews.get(review_id)

    def list_reviews(self, status: ReviewStatus | None = None) -> list[Review]:
        reviews = list(self._reviews.values())
        if status is not None:
            reviews = [r for r in reviews if r.status == status]
        return sorted(reviews, key=lambda r: r.created_at)

    # ---- 감사 로그 (추가만)
    def append_audit(
        self,
        at: datetime,
        context_id: str,
        tenant_id: str,
        step: str,
        execution_id: str | None = None,
        detail: dict[str, Any] | None = None,
    ) -> AuditEntry:
        entry = AuditEntry(
            log_id=len(self._audit) + 1,
            at=at,
            context_id=context_id,
            tenant_id=tenant_id,
            step=step,
            execution_id=execution_id,
            detail=detail or {},
        )
        self._write(
            [
                (
                    "INSERT INTO audit_logs (log_id, body) VALUES (?, ?)",
                    (entry.log_id, entry.model_dump_json()),
                )
            ]
        )
        self._audit.append(entry)
        return entry

    def list_audit(self, context_id: str | None = None) -> list[AuditEntry]:
        if context_id is None:
            return list(self._audit)
        return [entry for entry in self._audit if entry.context_id == context_id]
