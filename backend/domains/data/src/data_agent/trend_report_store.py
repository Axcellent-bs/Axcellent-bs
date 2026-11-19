"""트렌드 분석 의뢰 보고서 저장소 (SQLite, `DATA_TREND_REPORT_DB`. 비어 있으면 메모리).

- `reports`: 보고서 id·같은 의뢰 해시·한국 날짜·의뢰 입력(JSON)·호출자·context_id·상태·
  섹션별 상태(JSON)·결과(TrendReport JSON, 만드는 동안 섹션이 끝날 때마다 덮어씀)·만든/끝난 시각
- `ig_media`: 그날(한국) 계정별 인스타 최근 게시물
  (같은 날 다른 의뢰가 같은 계정을 다시 부르지 않게)

일간 업무 리포트(`report_store.py`)·받은 보고 보관(`idea_reports.py`)과는 다른 저장소다.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Mapping, Sequence
from datetime import date, datetime
from pathlib import Path
from typing import Any

DB_ENV = "DATA_TREND_REPORT_DB"
LIVE = ("queued", "running")  # 아직 끝나지 않은 상태
REUSABLE = ("queued", "running", "done", "partial")  # 같은 의뢰면 이 저장본을 연다
INTERRUPTED_REASON = "서버가 다시 시작돼 멈춤 → 다시 만들기 필요"


def path_from(env: Mapping[str, str]) -> str:
    return env.get(DB_ENV) or ":memory:"


class TrendReportStore:
    def __init__(self, path: str | Path = ":memory:") -> None:
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(str(path), check_same_thread=False, timeout=5.0)
        with self._lock, self._conn:
            self._conn.executescript(
                "CREATE TABLE IF NOT EXISTS reports (id TEXT PRIMARY KEY, brief_hash TEXT NOT NULL,"
                " day TEXT NOT NULL, brief TEXT NOT NULL, caller TEXT NOT NULL, context_id TEXT,"
                " status TEXT NOT NULL, sections TEXT NOT NULL, result TEXT NOT NULL,"
                " created_at TEXT NOT NULL, finished_at TEXT);"
                "CREATE INDEX IF NOT EXISTS reports_same ON reports (brief_hash, day);"
                "CREATE INDEX IF NOT EXISTS reports_created ON reports (created_at);"
                "CREATE TABLE IF NOT EXISTS ig_media (day TEXT NOT NULL, account TEXT NOT NULL,"
                " media TEXT NOT NULL, PRIMARY KEY (day, account));"
            )

    def close(self) -> None:
        self._conn.close()

    # ---------------------------------------------------------------- 보고서

    def create(self, report_id: str, brief_hash: str, day: date, brief: Mapping[str, Any],
               caller: str, context_id: str | None, result: Mapping[str, Any],
               created_at: datetime) -> None:  # fmt: skip
        sections = {k: v.get("status") for k, v in (result.get("sections") or {}).items()}
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO reports VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (report_id, brief_hash, day.isoformat(), json.dumps(brief, ensure_ascii=False),
                 caller, context_id, str(result.get("status") or "queued"),
                 json.dumps(sections), json.dumps(result, ensure_ascii=False),
                 created_at.isoformat(), None),
            )  # fmt: skip

    def same(self, brief_hash: str, day: date) -> str | None:
        """같은 날 같은 의뢰의 가장 최근 저장본 (실패·중단은 빼고). 없으면 None."""
        marks = ",".join("?" * len(REUSABLE))
        with self._lock:
            row = self._conn.execute(
                f"SELECT id FROM reports WHERE brief_hash = ? AND day = ? AND status IN ({marks})"  # noqa: S608
                " ORDER BY created_at DESC LIMIT 1",
                (brief_hash, day.isoformat(), *REUSABLE),
            ).fetchone()
        return row[0] if row else None

    def save(self, report_id: str, result: Mapping[str, Any]) -> None:
        """결과(TrendReport JSON)를 덮어쓴다. 상태·섹션별 상태·끝난 시각도 같이."""
        sections = {k: v.get("status") for k, v in (result.get("sections") or {}).items()}
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE reports SET status = ?, sections = ?, result = ?, finished_at = ?"
                " WHERE id = ?",
                (str(result.get("status")), json.dumps(sections),
                 json.dumps(result, ensure_ascii=False), result.get("finished_at"), report_id),
            )  # fmt: skip

    def get(self, report_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute(
                "SELECT result, brief FROM reports WHERE id = ?", (report_id,)
            ).fetchone()
        if row is None:
            return None
        return {"result": json.loads(row[0]), "input": json.loads(row[1])}

    def statuses(self, report_id: str) -> tuple[str, dict[str, str]] | None:
        """(보고서 상태, 섹션별 상태)."""
        with self._lock:
            row = self._conn.execute(
                "SELECT status, sections FROM reports WHERE id = ?", (report_id,)
            ).fetchone()
        return (row[0], json.loads(row[1])) if row else None

    def recent(self, limit: int) -> list[dict[str, Any]]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT result FROM reports ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [json.loads(r[0]) for r in rows]

    def waiting_ahead(self, report_id: str) -> int:
        """이 보고서보다 먼저 들어와 아직 끝나지 않은 보고서 수."""
        marks = ",".join("?" * len(LIVE))
        with self._lock:
            row = self._conn.execute(
                f"SELECT COUNT(*) FROM reports WHERE status IN ({marks}) AND id != ?"  # noqa: S608
                " AND created_at <= (SELECT created_at FROM reports WHERE id = ?)",
                (*LIVE, report_id, report_id),
            ).fetchone()
        return int(row[0]) if row else 0

    def interrupt_live(self, now: datetime) -> list[str]:
        """다시 켜질 때: 대기·진행으로 남은 보고서를 "중단됨"으로 (작업은 프로세스 안에만 있음)."""
        marks = ",".join("?" * len(LIVE))
        with self._lock:
            rows = self._conn.execute(
                f"SELECT id, result FROM reports WHERE status IN ({marks})", LIVE  # noqa: S608
            ).fetchall()
        done: list[str] = []
        for report_id, raw in rows:
            result = json.loads(raw)
            result["status"] = "interrupted"
            result["finished_at"] = now.isoformat()
            for section in (result.get("sections") or {}).values():
                if section.get("status") in ("waiting", "running"):
                    section["status"] = "failed"
                    section["reason"] = INTERRUPTED_REASON
            limits = list(result.get("limits") or [])
            limits.append(INTERRUPTED_REASON)
            result["limits"] = limits
            self.save(report_id, result)
            done.append(report_id)
        return done

    # ---------------------------------------------------------------- 그날 인스타 게시물

    def media(self, day: date, accounts: Sequence[str]) -> dict[str, list[dict[str, Any]]]:
        if not accounts:
            return {}
        marks = ",".join("?" * len(accounts))
        with self._lock:
            rows = self._conn.execute(
                f"SELECT account, media FROM ig_media WHERE day = ? AND account IN ({marks})",  # noqa: S608
                (day.isoformat(), *accounts),
            ).fetchall()
        return {a: json.loads(m) for a, m in rows}

    def save_media(self, day: date, account: str, media: Sequence[Mapping[str, Any]]) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT OR REPLACE INTO ig_media VALUES (?,?,?)",
                (day.isoformat(), account, json.dumps(list(media), ensure_ascii=False)),
            )
            self._conn.execute("DELETE FROM ig_media WHERE day < ?", (day.isoformat(),))
