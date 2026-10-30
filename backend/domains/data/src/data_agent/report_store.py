"""부서별 AI 업무 리포트의 일간 저장소 (data 자체 SQLite 파일).

- 위치: 환경변수 `DATA_REPORT_DB` (run_all.sh는 `.data/data_reports.db`). 비어 있으면 저장하지 않고
  리포트는 지금처럼 바로 계산한다. 다른 부서 DB·memory는 쓰지 않는다.
- 한국 시간 하루마다 한 줄: 날짜, 만든 시각, 코드 버전, 그날의 "더할 수 있는 값"(JSON).
  비율·평균은 저장하지 않는다(분자·분모, 합계·개수, 중앙값용 목록). 값 모양은
  `skills.activity_report.PeriodCounts.to_json`이다.
- 확인할 수 없던 칸은 이유와 함께 그대로 저장한다(0으로 바꾸지 않는다). `has_unknown`이면 다음 날
  한 번 다시 만든다(`retried`).
- 코드 버전(`SNAPSHOT_VERSION`)이 다른 줄은 없는 것으로 보고 다시 만든다.
"""

from __future__ import annotations

import json
import os
import sqlite3
import threading
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import date, datetime
from typing import Any

DB_ENV = "DATA_REPORT_DB"
SNAPSHOT_VERSION = 1  # 저장하는 값의 모양을 바꾸면 올린다 (예전 줄은 다시 만든다)

_SCHEMA = """
CREATE TABLE IF NOT EXISTS daily_reports (
    tenant_id   TEXT NOT NULL,
    day         TEXT NOT NULL,
    created_at  TEXT NOT NULL,
    version     INTEGER NOT NULL,
    has_unknown INTEGER NOT NULL,
    retried     INTEGER NOT NULL,
    payload     TEXT NOT NULL,
    PRIMARY KEY (tenant_id, day)
);
"""


@dataclass(frozen=True)
class Snapshot:
    tenant_id: str
    day: date  # 한국 시간 날짜
    created_at: datetime  # UTC
    payload: dict[str, Any]
    has_unknown: bool  # 확인할 수 없던 칸이 있었는지 (재무·shop-api 실패, 조회 한도)
    retried: bool = False  # 확인할 수 없음 때문에 한 번 다시 만든 줄인지
    version: int = SNAPSHOT_VERSION


class ReportStore:
    """SQLite 파일(또는 `:memory:`) 저장소. 읽기·쓰기는 이 data 서버만 한다."""

    def __init__(self, path: str) -> None:
        self.path = path
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(path, check_same_thread=False, timeout=5.0)
        self._conn.row_factory = sqlite3.Row
        if path != ":memory:":
            self._conn.execute("PRAGMA journal_mode=WAL")
        self._conn.executescript(_SCHEMA)

    def get_many(self, tenant_id: str, days: Iterable[date]) -> dict[date, Snapshot]:
        """그 날들 중 지금 코드 버전으로 저장된 줄만 돌려준다."""
        wanted = sorted({d.isoformat() for d in days})
        if not wanted:
            return {}
        marks = ",".join("?" * len(wanted))
        with self._lock:
            rows = self._conn.execute(
                f"SELECT * FROM daily_reports WHERE tenant_id = ? AND version = ?"
                f" AND day IN ({marks})",
                [tenant_id, SNAPSHOT_VERSION, *wanted],
            ).fetchall()
        return {
            date.fromisoformat(r["day"]): Snapshot(
                tenant_id=r["tenant_id"],
                day=date.fromisoformat(r["day"]),
                created_at=datetime.fromisoformat(r["created_at"]),
                payload=json.loads(r["payload"]),
                has_unknown=bool(r["has_unknown"]),
                retried=bool(r["retried"]),
                version=int(r["version"]),
            )
            for r in rows
        }

    def put(self, snapshot: Snapshot) -> None:
        row = (
            snapshot.tenant_id,
            snapshot.day.isoformat(),
            snapshot.created_at.isoformat(),
            snapshot.version,
            int(snapshot.has_unknown),
            int(snapshot.retried),
            json.dumps(snapshot.payload, ensure_ascii=False, sort_keys=True),
        )
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT OR REPLACE INTO daily_reports (tenant_id, day, created_at, version,"
                " has_unknown, retried, payload) VALUES (?,?,?,?,?,?,?)",
                row,
            )

    def close(self) -> None:
        with self._lock:
            self._conn.close()


_stores: dict[str, ReportStore] = {}


def get_report_store(env: Mapping[str, str] = os.environ) -> ReportStore | None:
    """`DATA_REPORT_DB`가 있으면 그 파일 저장소(같은 경로면 다시 열지 않는다), 없으면 None."""
    path = (env.get(DB_ENV) or "").strip()
    if not path:
        return None
    if path not in _stores:
        _stores[path] = ReportStore(path)
    return _stores[path]
