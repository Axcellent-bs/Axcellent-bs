"""이벤트 저장소: 총괄이 다시 켜져도 이벤트 기록과 처리한 `event_id`가 남게 한다.

저장은 다른 서비스(사람 할 일함, 공통 로그)처럼 SQLite 파일 하나다. 위치는 `ORCHESTRATOR_DB`이고,
비어 있으면 프로세스 메모리(`:memory:`)에 둔다(다시 켜면 사라진다. 예전 동작).

저장하는 것은 이벤트 기록(단계 결과·로그·지표)과 처리한 `event_id`뿐이다. 승인 대기(`Hold`)와
사람 할 일함 대기(`HandoffWait`)는 `asyncio.Event`라 저장하지 않는다. 다시 켤 때 진행 중이던
이벤트는 이어 가지 않고 "재시작으로 중단됨"으로 남긴다(`IncidentManager.restore`).
"""

from __future__ import annotations

import json
import logging
import os
import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

from ax_orchestrator.models import Incident, StepRun, now

DB_ENV = "ORCHESTRATOR_DB"
MEMORY = ":memory:"

log = logging.getLogger("orchestrator.store")

_SCHEMA = """
CREATE TABLE IF NOT EXISTS incidents (
    context_id TEXT PRIMARY KEY,
    status     TEXT NOT NULL,
    opened_at  TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    body       TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS seen_events (
    event_id TEXT PRIMARY KEY,
    seen_at  TEXT NOT NULL
);
"""


def _dt(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _parse(value: str | None) -> datetime | None:
    return datetime.fromisoformat(value) if value else None


def step_to_dict(run: StepRun) -> dict[str, Any]:
    return {
        "step_id": run.step_id,
        "skill": run.skill,
        "label": run.label,
        "dept": run.dept,
        "status": run.status,
        "attempts": run.attempts,
        "note": run.note,
        "grade": run.grade,
        "started_at": _dt(run.started_at),
        "finished_at": _dt(run.finished_at),
        "output": run.output,
        "transferred_to": run.transferred_to,
        "reason_code": run.reason_code,
        "handoff_task_id": run.handoff_task_id,
        "approval_wait_seconds": run.approval_wait_seconds,
        "human_wait_seconds": run.human_wait_seconds,
        "refreshed_at": _dt(run.refreshed_at),
    }


def step_from_dict(raw: dict[str, Any]) -> StepRun:
    return StepRun(
        step_id=raw["step_id"],
        skill=raw["skill"],
        label=raw["label"],
        dept=raw.get("dept"),
        status=raw.get("status", "pending"),
        attempts=raw.get("attempts", 0),
        note=raw.get("note", ""),
        grade=raw.get("grade"),
        started_at=_parse(raw.get("started_at")),
        finished_at=_parse(raw.get("finished_at")),
        output=raw.get("output") or {},
        transferred_to=raw.get("transferred_to"),
        reason_code=raw.get("reason_code"),
        handoff_task_id=raw.get("handoff_task_id"),
        approval_wait_seconds=raw.get("approval_wait_seconds"),
        human_wait_seconds=raw.get("human_wait_seconds"),
        refreshed_at=_parse(raw.get("refreshed_at")),
    )


def incident_to_dict(inc: Incident) -> dict[str, Any]:
    """저장할 모양. 승인 대기·사람 할 일함 대기(`holds`·`handoffs`)는 저장하지 않는다."""
    return {
        "context_id": inc.context_id,
        "kind": inc.kind,
        "title": inc.title,
        "component": inc.component,
        "event_type": inc.event_type,
        "status": inc.status,
        "opened_at": _dt(inc.opened_at),
        "since": _dt(inc.since),
        "closed_at": _dt(inc.closed_at),
        "recovered_at": _dt(inc.recovered_at),
        "settled_by_recovery": inc.settled_by_recovery,
        "steps": [step_to_dict(run) for run in inc.steps.values()],
        "events": inc.events,
        "outputs": inc.outputs,
        "summary": inc.summary,
        "metrics": inc.metrics,
        "shared": inc.shared,
        "times_off": inc.times_off,
        "times_sent": inc.times_sent,
        "signal": inc.signal,
        "detected_by": inc.detected_by,
    }


def _detected_by(raw: Any) -> dict[str, str] | None:
    """예전 저장분(칸 없음)이나 모양이 다른 값은 None으로 읽는다."""
    if not isinstance(raw, dict):
        return None
    return {str(k): str(v) for k, v in raw.items() if isinstance(v, str)}


def incident_from_dict(raw: dict[str, Any]) -> Incident:
    inc = Incident(
        context_id=raw["context_id"],
        kind=raw["kind"],
        title=raw["title"],
        component=raw["component"],
        opened_at=_parse(raw["opened_at"]) or now(),
        since=_parse(raw.get("since")),
        closed_at=_parse(raw.get("closed_at")),
        status=raw.get("status", "open"),
        settled_by_recovery=bool(raw.get("settled_by_recovery")),
        events=list(raw.get("events") or []),
        outputs=dict(raw.get("outputs") or {}),
        summary=list(raw.get("summary") or []),
        metrics=dict(raw.get("metrics") or {}),
        shared=dict(raw.get("shared") or {}),
        event_type=raw.get("event_type"),
        times_off=bool(raw.get("times_off")),
        times_sent=dict(raw.get("times_sent") or {}),
        signal=dict(raw.get("signal") or {}),
        detected_by=_detected_by(raw.get("detected_by")),
    )
    inc.recovered_at = _parse(raw.get("recovered_at"))
    for item in raw.get("steps") or []:
        run = step_from_dict(item)
        inc.steps[run.step_id] = run
    return inc


class IncidentStore:
    """이벤트와 처리한 `event_id`를 SQLite에 둔다. 한 연결을 잠금으로 나눠 쓴다."""

    def __init__(self, path: str = MEMORY) -> None:
        self.path = path
        if path != MEMORY:
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._db = sqlite3.connect(path, check_same_thread=False)
        if path != MEMORY:
            self._db.execute("PRAGMA journal_mode=WAL")
        self._db.executescript(_SCHEMA)
        self._db.commit()

    @classmethod
    def from_env(cls) -> IncidentStore:
        return cls(os.getenv(DB_ENV) or MEMORY)

    def save(self, inc: Incident) -> None:
        body = json.dumps(incident_to_dict(inc), ensure_ascii=False, default=str)
        stamp = now().isoformat()
        with self._lock:
            self._db.execute(
                "INSERT INTO incidents (context_id, status, opened_at, updated_at, body)"
                " VALUES (?, ?, ?, ?, ?)"
                " ON CONFLICT(context_id) DO UPDATE SET"
                " status = excluded.status, updated_at = excluded.updated_at, body = excluded.body",
                (inc.context_id, inc.status, inc.opened_at.isoformat(), stamp, body),
            )
            self._db.commit()

    def load_all(self) -> list[Incident]:
        """열린 순서대로 돌려준다. 읽지 못하는 줄은 건너뛰고 경고만 남긴다."""
        with self._lock:
            rows = self._db.execute(
                "SELECT context_id, body FROM incidents ORDER BY opened_at, context_id"
            ).fetchall()
        found: list[Incident] = []
        for context_id, body in rows:
            try:
                found.append(incident_from_dict(json.loads(body)))
            except (ValueError, KeyError, TypeError):
                log.warning("저장된 이벤트를 읽지 못해 건너뜀: %s", context_id)
        return found

    def mark_seen(self, event_id: str) -> bool:
        """처리한 `event_id`로 적는다. 처음 적으면 True, 이미 있으면 False."""
        with self._lock:
            cur = self._db.execute(
                "INSERT OR IGNORE INTO seen_events (event_id, seen_at) VALUES (?, ?)",
                (event_id, now().isoformat()),
            )
            self._db.commit()
            return cur.rowcount == 1

    def seen_ids(self) -> set[str]:
        with self._lock:
            rows = self._db.execute("SELECT event_id FROM seen_events").fetchall()
        return {row[0] for row in rows}

    def close(self) -> None:
        with self._lock:
            self._db.close()
