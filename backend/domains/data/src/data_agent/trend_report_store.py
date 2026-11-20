"""트렌드 분석 의뢰 보고서 저장소 (SQLite, `DATA_TREND_REPORT_DB`. 비어 있으면 메모리).

- `reports`: 보고서 id·같은 의뢰 해시·한국 날짜·의뢰 입력(JSON)·호출자·context_id·상태·
  섹션별 상태(JSON)·결과(TrendReport JSON, 만드는 동안 섹션이 끝날 때마다 덮어씀)·만든/끝난 시각·
  대화 카드로 넣었으면 그 대화 id(`chat_thread_id`, 예전 DB는 열 추가)
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
            # 11/19 추가 열: 대화 카드에서 넣은 의뢰의 대화 id (예전 DB에는 없어서 덧붙인다)
            # 11/20 추가 열: 기획에 보낸 결과 (TrendReportHandoffOutput JSON, 보낸 것만)
            have = {r[1] for r in self._conn.execute("PRAGMA table_info(reports)")}
            for col in ("chat_thread_id", "handoff"):
                if col not in have:
                    self._conn.execute(f"ALTER TABLE reports ADD COLUMN {col} TEXT")
            # 11/20: 총괄 긴 작업으로 이 보고서를 기다리는 쪽 (요청마다 한 줄, 토큰은 저장 안 함)
            self._conn.execute(
                "CREATE TABLE IF NOT EXISTS report_waiters (task_id TEXT PRIMARY KEY,"
                " report_id TEXT NOT NULL, tenant_id TEXT, context_id TEXT, skill TEXT NOT NULL,"
                " requested_at TEXT NOT NULL, completed_sent INTEGER NOT NULL DEFAULT 0)"
            )

    def close(self) -> None:
        self._conn.close()

    # ---------------------------------------------------------------- 보고서

    def create(self, report_id: str, brief_hash: str, day: date, brief: Mapping[str, Any],
               caller: str, context_id: str | None, result: Mapping[str, Any],
               created_at: datetime, chat_thread_id: str | None = None) -> None:  # fmt: skip
        sections = {k: v.get("status") for k, v in (result.get("sections") or {}).items()}
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO reports (id, brief_hash, day, brief, caller, context_id, status,"
                " sections, result, created_at, finished_at, chat_thread_id)"
                " VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (report_id, brief_hash, day.isoformat(), json.dumps(brief, ensure_ascii=False),
                 caller, context_id, str(result.get("status") or "queued"),
                 json.dumps(sections), json.dumps(result, ensure_ascii=False),
                 created_at.isoformat(), None, chat_thread_id),
            )  # fmt: skip

    def chat_thread(self, report_id: str) -> str | None:
        """그 보고서를 넣은 대화 id (대화 카드로 넣지 않았으면 None)."""
        with self._lock:
            row = self._conn.execute(
                "SELECT chat_thread_id FROM reports WHERE id = ?", (report_id,)
            ).fetchone()
        return row[0] if row else None

    # ---------------------------------------------------------------- 총괄 긴 작업 (기다리는 쪽)

    _WAITER_COLS = ("task_id", "report_id", "tenant_id", "context_id", "skill", "requested_at",
                    "completed_sent")  # fmt: skip

    def add_waiter(self, report_id: str, task_id: str, tenant_id: str | None,
                   context_id: str | None, skill: str, at: datetime) -> None:  # fmt: skip
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT OR IGNORE INTO report_waiters (task_id, report_id, tenant_id, context_id,"
                " skill, requested_at) VALUES (?,?,?,?,?,?)",
                (task_id, report_id, tenant_id, context_id, skill, at.isoformat()),
            )

    def waiters(self, report_id: str | None = None) -> list[dict[str, Any]]:
        """완료 알림을 아직 못 받은 기다리는 쪽 (report_id가 없으면 전부)."""
        sql = f"SELECT {', '.join(self._WAITER_COLS)} FROM report_waiters WHERE completed_sent = 0"  # noqa: S608
        args: tuple[Any, ...] = ()
        if report_id is not None:
            sql, args = sql + " AND report_id = ?", (report_id,)
        with self._lock:
            rows = self._conn.execute(sql + " ORDER BY requested_at", args).fetchall()
        return [dict(zip(self._WAITER_COLS, r, strict=True)) for r in rows]

    def mark_completed_sent(self, task_id: str) -> None:
        with self._lock, self._conn:
            self._conn.execute("UPDATE report_waiters SET completed_sent = 1 WHERE task_id = ?",
                               (task_id,))  # fmt: skip

    # ---------------------------------------------------------------- 기획에 보낸 결과

    def handoff(self, report_id: str) -> dict[str, Any] | None:
        with self._lock:
            row = self._conn.execute("SELECT handoff FROM reports WHERE id = ?",
                                     (report_id,)).fetchone()  # fmt: skip
        return json.loads(row[0]) if row and row[0] else None

    def save_handoff(self, report_id: str, value: Mapping[str, Any]) -> None:
        with self._lock, self._conn:
            self._conn.execute("UPDATE reports SET handoff = ? WHERE id = ?",
                               (json.dumps(value, ensure_ascii=False), report_id))  # fmt: skip

    def known_names(self, limit: int = 50) -> dict[str, str]:
        """최근 보고서들의 경쟁사 표시 이름 {아이디: 이름} (최근 것이 이긴다, 없는 이름은 뺀다)."""
        out: dict[str, str] = {}
        for raw in reversed(self.recent(limit)):
            names = ((raw.get("brief") or {}).get("competitor_names") or {})
            out.update({k: v for k, v in names.items() if isinstance(v, str) and v})
        return out

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
        return [r for r, _ in self.recent_with_handoff(limit)]

    def recent_with_handoff(self, limit: int) -> list[tuple[dict[str, Any], dict[str, Any] | None]]:
        """최근 보고서 (결과, 기획에 보낸 결과 또는 None)."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT result, handoff FROM reports ORDER BY created_at DESC LIMIT ?", (limit,)
            ).fetchall()
        return [(json.loads(r[0]), json.loads(r[1]) if r[1] else None) for r in rows]

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
