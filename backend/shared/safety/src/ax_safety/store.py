"""실행 기록·멱등성·보류 큐·감사 로그 저장소.

v0은 프로세스 메모리에 둔다(재시작하면 비워진다). TODO: postgres 테이블
(`executions`, `pending_reviews`, `audit_logs`)로 옮긴다. 감사 로그는 추가만 한다.

외부 연결 쓰기 보류만은 `SAFETY_CONNECTION_HOLDS_FILE`이 있으면 파일에도 남긴다. 개발 서비스는
보류를 한 번만 보내므로, 재시작으로 잊으면 취약한 연결의 쓰기가 그대로 나간다.
"""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from ax_contracts.schemas import ConnectionHoldNotice, ExecuteDecision, ExecuteRequest

ReviewStatus = Literal["pending", "approved", "rejected", "expired"]


class ExecutionRecord(BaseModel):
    model_config = ConfigDict(extra="forbid")

    execution_id: str
    created_at: datetime
    request: ExecuteRequest
    decision: ExecuteDecision
    customer: str | None = None
    report: dict[str, Any] = Field(default_factory=dict)


class Review(BaseModel):
    """C 보류 건. 담당자가 대시보드에서 결정하고, 마감까지 결정이 없으면 자동 거절한다."""

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


class Store:
    def __init__(self) -> None:
        self._executions: dict[str, ExecutionRecord] = {}
        self._by_key: dict[tuple[str, str], str] = {}
        self._reviews: dict[str, Review] = {}
        self._audit: list[AuditEntry] = []
        raw_path = os.getenv("SAFETY_CONNECTION_HOLDS_FILE")
        self._holds_file = Path(raw_path) if raw_path else None
        self._holds: dict[str, ConnectionHoldNotice] = {}
        if self._holds_file is not None and self._holds_file.exists():
            # 못 읽으면 켜지 않는다. 보류를 잊은 채 쓰기를 통과시키지 않는다 (fail-closed)
            rows = json.loads(self._holds_file.read_text(encoding="utf-8"))
            self._holds = {
                n.connection_id: n for n in map(ConnectionHoldNotice.model_validate, rows)
            }

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

    def add_execution(self, record: ExecutionRecord) -> None:
        self._executions[record.execution_id] = record
        key = (record.request.tenant_id, record.request.idempotency_key)
        self._by_key[key] = record.execution_id

    def get_execution(self, execution_id: str) -> ExecutionRecord | None:
        return self._executions.get(execution_id)

    def set_decision(self, execution_id: str, decision: ExecuteDecision) -> None:
        self._executions[execution_id].decision = decision

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
        self._audit.append(entry)
        return entry

    def list_audit(self, context_id: str | None = None) -> list[AuditEntry]:
        if context_id is None:
            return list(self._audit)
        return [entry for entry in self._audit if entry.context_id == context_id]
