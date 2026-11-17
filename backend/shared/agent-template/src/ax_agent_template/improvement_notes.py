"""부서가 총괄의 개선 노트를 받는 공통 skill `receive_improvement_note`.

에이전트 평가에서 같은 단계의 확인 필요가 반복되면 총괄이 원인을 진단하고(`diagnosed`)
해당 에이전트에 개선 노트로 전달한다. 이 파일은 받는 쪽을 한 번만 만든 것이다. 부서는 Agent Card에
skill 하나를 적으면 `create_agent_app`이 이 핸들러를 자동으로 붙인다(핸들러를 따로 만들지 않는다).

- 노트는 기록·알림이다. 받은 에이전트의 동작을 바꾸지 않는다. 에이전트가 다음 일에서 참고하거나
  (`active_notes`) 담당자가 읽는다.
- 자기 일이 아닌 노트(`agent`가 자기 부서와 다름)는 `rejected`로 돌려보낸다.
- 같은 에이전트·단계의 노트는 최신 하나만 남긴다(새 노트가 오면 옛 노트는 `superseded`).
  `MAX_AGE_DAYS`(30일)가 지난 노트는 참고 목록(`active_notes`)에서 빠진다(기록은 남는다).
- 같은 `note_id`를 다시 받으면 `duplicate`, `revoke`가 참이면 거두고(`revoked`) 더는
  `active_notes`에 나오지 않는다. 거둘 노트가 없으면 `rejected`다.
- 저장: 서비스마다 SQLite 한 파일(`AX_NOTES_DIR/<서비스>.db`). 폴더를 주지 않으면 메모리
  (다시 켜면 사라짐).
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from pydantic import ValidationError

from ax_agent_template.config import get_agent_name
from ax_agent_template.services import department_of
from ax_contracts.schemas import AgentRequest, AgentResult, Evidence, TaskStatus
from ax_contracts.schemas.diagnosis import (
    ReceiveImprovementNoteInput,
    ReceiveImprovementNoteOutput,
)

SKILL = "receive_improvement_note"
NOTES_DIR_ENV = "AX_NOTES_DIR"
MAX_NOTES = 200
MAX_AGE_DAYS = 30

_SCHEMA = """
CREATE TABLE IF NOT EXISTS improvement_notes (
    note_id    TEXT PRIMARY KEY,
    agent      TEXT NOT NULL,
    step_id    TEXT NOT NULL,
    status     TEXT NOT NULL,
    received_at TEXT NOT NULL,
    body       TEXT NOT NULL
);
"""


class ImprovementNoteStore:
    """받은 노트를 저장한다. `status`는 `active`(참고할 수 있음) 또는 `revoked`(거둠)."""

    def __init__(self, path: str | None = None) -> None:
        self._lock = threading.Lock()
        self._db = sqlite3.connect(path or ":memory:", check_same_thread=False)
        self._db.executescript(_SCHEMA)

    @classmethod
    def for_service(cls, service: str) -> ImprovementNoteStore:
        folder = os.getenv(NOTES_DIR_ENV)
        if not folder:
            return cls(None)
        Path(folder).mkdir(parents=True, exist_ok=True)
        return cls(str(Path(folder) / f"{service}.db"))

    def get(self, note_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._db.execute(
                "SELECT status, body FROM improvement_notes WHERE note_id = ?", (note_id,)
            ).fetchone()
        if row is None:
            return None
        return {**json.loads(row[1]), "status": row[0]}

    def add(self, note: ReceiveImprovementNoteInput, received_at: str) -> None:
        body = json.dumps(note.model_dump(mode="json"), ensure_ascii=False)
        with self._lock:
            # 같은 에이전트·단계의 옛 노트는 새 노트로 대체한다(쌓이지 않게)
            self._db.execute(
                "UPDATE improvement_notes SET status = 'superseded'"
                " WHERE agent = ? AND step_id = ? AND status = 'active' AND note_id != ?",
                (note.agent, note.step_id, note.note_id),
            )
            self._db.execute(
                "INSERT OR IGNORE INTO improvement_notes"
                " (note_id, agent, step_id, status, received_at, body)"
                " VALUES (?, ?, ?, 'active', ?, ?)",
                (note.note_id, note.agent, note.step_id, received_at, body),
            )
            self._trim()
            self._db.commit()

    def revoke(self, note_id: str) -> bool:
        with self._lock:
            cur = self._db.execute(
                "UPDATE improvement_notes SET status = 'revoked'"
                " WHERE note_id = ? AND status = 'active'",
                (note_id,),
            )
            self._db.commit()
        return cur.rowcount > 0

    def active_notes(
        self, step_id: str | None = None, limit: int = 3, *, now: datetime | None = None
    ) -> list[dict[str, Any]]:
        """참고할 수 있는 노트(최근 것부터). 거두거나 대체되거나 `MAX_AGE_DAYS`가 지난 것은 뺀다.

        단계를 주면 그 단계 노트만 준다.
        """
        cutoff = ((now or datetime.now(UTC)) - timedelta(days=MAX_AGE_DAYS)).isoformat()
        sql = "SELECT body FROM improvement_notes WHERE status = 'active' AND received_at >= ?"
        args: list[Any] = [cutoff]
        if step_id is not None:
            sql += " AND step_id = ?"
            args.append(step_id)
        sql += " ORDER BY received_at DESC, rowid DESC LIMIT ?"
        args.append(limit)
        with self._lock:
            rows = self._db.execute(sql, args).fetchall()
        return [json.loads(r[0]) for r in rows]

    def notes_for_skill(
        self, skill: str, limit: int = 3, *, now: datetime | None = None
    ) -> list[dict[str, Any]]:
        """이 skill에 걸린 참고할 수 있는 노트(최근 것부터). 거두거나 대체·만료된 것은 뺀다."""
        found = self.active_notes(limit=MAX_NOTES, now=now)
        return [n for n in found if n.get("skill") == skill][:limit]

    def _trim(self) -> None:
        self._db.execute(
            "DELETE FROM improvement_notes WHERE rowid NOT IN"
            " (SELECT rowid FROM improvement_notes ORDER BY received_at DESC, rowid DESC LIMIT ?)",
            (MAX_NOTES,),
        )


CAUTION_HEAD = "주의: 최근 이 단계가 반복해 오래 걸려 총괄 AI가 진단했어요"


def caution_text(notes: list[dict[str, Any]]) -> str:
    """응답 근거에 붙일 주의 사항 한 줄. 원인 미확정이면 그렇다고 적는다."""
    latest = notes[0]
    kind = "원인 확정" if latest.get("cause_known", True) else "원인 미확정"
    return f"{CAUTION_HEAD} ({kind}, 진단 결과 {len(notes)}건): {latest['summary']}"


def with_caution(service: str, skill: str, result: AgentResult) -> AgentResult:
    """완료한 응답의 근거(`evidence.data_summary`)에 주의 사항을 덧붙인다. 노트가 없으면 그대로다.

    코드가 정해진 순서로 도는 단계는 노트를 읽어 스스로 바꿀 수 없다. 대신 응답을 읽는 사람과 AI가
    이 단계가 반복해 느렸다는 걸 알 수 있게 한다. 결과 값(`output`)은 건드리지 않는다.
    """
    if skill == SKILL or result.status != TaskStatus.COMPLETED or result.evidence is None:
        return result
    notes = store_for(service).notes_for_skill(skill)
    if not notes:
        return result
    summary = result.evidence.data_summary
    text = caution_text(notes)
    if text in summary:
        return result
    joined = f"{summary}\n{text}" if summary else text
    return result.model_copy(
        update={"evidence": result.evidence.model_copy(update={"data_summary": joined})}
    )


_stores: dict[str, ImprovementNoteStore] = {}


def store_for(service: str) -> ImprovementNoteStore:
    if service not in _stores:
        _stores[service] = ImprovementNoteStore.for_service(service)
    return _stores[service]


def reset_stores() -> None:
    """시험에서 서비스별 저장소를 비운다."""
    _stores.clear()


def handler_for(service: str):  # type: ignore[no-untyped-def]
    """`service`(서비스 id)가 받는 `receive_improvement_note` 핸들러."""

    async def handler(request: AgentRequest) -> AgentResult:
        try:
            note = ReceiveImprovementNoteInput.model_validate(request.input)
        except ValidationError as error:
            errors = [f"{'.'.join(map(str, e['loc']))}: {e['type']}" for e in error.errors()]
            return AgentResult(
                meta=request.meta,
                status=TaskStatus.FAILED,
                outcome="invalid_input",
                output={"errors": errors},
            )
        mine = department_of(get_agent_name() or service)
        store = store_for(service)
        if note.agent != mine:
            out = ReceiveImprovementNoteOutput(
                note_id=note.note_id,
                status="rejected",
                reason=f"이 진단 결과는 {note.agent}의 일이고 이 서비스는 {mine}의 것이에요",
            )
        elif note.revoke:
            done = store.revoke(note.note_id)
            out = ReceiveImprovementNoteOutput(
                note_id=note.note_id,
                status="revoked" if done else "rejected",
                reason=None if done else "거둘 진단 결과가 없어요(받은 적 없거나 이미 거둠·대체됨)",
            )
        elif store.get(note.note_id) is not None:
            out = ReceiveImprovementNoteOutput(note_id=note.note_id, status="duplicate")
        else:
            store.add(note, datetime.now(UTC).isoformat())
            out = ReceiveImprovementNoteOutput(note_id=note.note_id, status="accepted")
        return AgentResult(
            meta=request.meta,
            status=TaskStatus.COMPLETED,
            outcome=out.status,
            output=out.model_dump(mode="json"),
            evidence=Evidence(
                queries=[f"orchestrator.diagnosis id={note.diagnosis_id}"],
                data_summary=f"{note.agent} {note.step_label} 진단 결과 {out.status}",
                collected_at=datetime.now(UTC),
                ref_ids=[note.note_id],
            ),
        )

    return handler
