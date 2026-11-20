"""이미지 시트·스토리보드 제작 기록 (2026-11-20).

마케팅 화면에서 이미지 시트(`character_sheet_prompt`)를 만들면 기록 한 줄을 남기고,
같은 기록에 이어서 만든 스토리보드(`storyboard_prompt`)를 붙인다.
기록 페이지(마케팅 › 제작 기록)가 목록·상세로 읽는다.

- 저장소는 SQLite다. 환경변수 `MARKETING_CREATIVE_DB`가 가리키는 파일에 두면 서비스를 다시 시작해도
  남는다(run_all.sh가 `backend/.data/marketing_creative.db`로 준다). 없으면 메모리.
- 올린 원본 이미지는 저장하지 않는다. 화면이 보낸 작은 미리보기(긴 변 256px JPEG, 80KB 이하)만 둔다.
- 기록은 최근 `KEEP`개만 남긴다. 기록이 실패해도 시트·스토리보드 결과는 그대로 돌려준다(부가 기능).
"""

from __future__ import annotations

import base64
import binascii
import json
import logging
import os
import sqlite3
import threading
import uuid
from datetime import UTC, datetime
from typing import Any

logger = logging.getLogger("marketing.creative_records")

DB_ENV = "MARKETING_CREATIVE_DB"
KEEP = 200
MAX_THUMB_BASE64 = 80_000
MAX_NOTE = 200
MAX_STORY = 1000

_SCHEMA = """
CREATE TABLE IF NOT EXISTS marketing_creative_records (
    record_id TEXT PRIMARY KEY,
    created_at TEXT NOT NULL,
    note TEXT,
    story TEXT,
    shots INTEGER,
    thumbnail TEXT,
    sheet TEXT NOT NULL,
    storyboard TEXT,
    storyboard_at TEXT,
    render TEXT
)
"""


def clean_thumbnail(raw: Any) -> str | None:
    """JPEG base64 미리보기만 받는다. 크거나 깨졌으면 버린다(기록은 미리보기 없이 남긴다)."""
    if not isinstance(raw, str) or not raw or len(raw) > MAX_THUMB_BASE64:
        return None
    try:
        data = base64.b64decode(raw, validate=True)
    except (binascii.Error, ValueError):
        return None
    return raw if data.startswith(b"\xff\xd8") else None


class CreativeRecords:
    """`path`가 없으면 환경변수 `MARKETING_CREATIVE_DB`, 그것도 없으면 메모리."""

    def __init__(self, path: str | None = None) -> None:
        self.path = path if path is not None else (os.getenv(DB_ENV, "").strip() or ":memory:")
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(self.path, check_same_thread=False, timeout=5.0)
        self._conn.row_factory = sqlite3.Row
        with self._lock, self._conn:
            self._conn.execute(_SCHEMA)
            info = self._conn.execute("PRAGMA table_info(marketing_creative_records)")
            if "render" not in {r["name"] for r in info}:  # 2026-11-20 Higgsfield 그림·영상 칸
                self._conn.execute("ALTER TABLE marketing_creative_records ADD COLUMN render TEXT")

    def add_sheet(self, sheet: dict[str, Any], note: str | None, thumbnail: str | None) -> str:
        record_id = f"CR-{uuid.uuid4().hex[:12]}"
        now = datetime.now(UTC).isoformat()
        with self._lock, self._conn:
            self._conn.execute(
                "INSERT INTO marketing_creative_records"
                " (record_id, created_at, note, thumbnail, sheet) VALUES (?, ?, ?, ?, ?)",
                (record_id, now, (note or "")[:MAX_NOTE] or None, thumbnail, json.dumps(sheet)),
            )
            self._conn.execute(
                "DELETE FROM marketing_creative_records WHERE record_id NOT IN ("
                " SELECT record_id FROM marketing_creative_records"
                " ORDER BY created_at DESC LIMIT ?)",
                (KEEP,),
            )
        return record_id

    def attach_storyboard(
        self, record_id: str, story: str, shots: int, storyboard: dict[str, Any]
    ) -> bool:
        """기록에 스토리보드를 붙인다(다시 만들면 바꾼다). 기록이 없으면 False."""
        now = datetime.now(UTC).isoformat()
        with self._lock, self._conn:
            cur = self._conn.execute(
                "UPDATE marketing_creative_records"
                " SET story = ?, shots = ?, storyboard = ?, storyboard_at = ? WHERE record_id = ?",
                (story[:MAX_STORY], shots, json.dumps(storyboard), now, record_id),
            )
        return cur.rowcount == 1

    def list(self, limit: int = 50) -> list[dict[str, Any]]:
        """목록은 가볍게: 요약·미리보기·칸 수만."""
        with self._lock:
            rows = self._conn.execute(
                "SELECT * FROM marketing_creative_records ORDER BY created_at DESC LIMIT ?",
                (max(1, min(limit, KEEP)),),
            ).fetchall()
        out = []
        for r in rows:
            sheet = json.loads(r["sheet"])
            board = json.loads(r["storyboard"]) if r["storyboard"] else None
            out.append(
                {
                    "record_id": r["record_id"],
                    "created_at": r["created_at"],
                    "summary_ko": sheet.get("summary_ko", ""),
                    "note": r["note"],
                    "story": r["story"],
                    "thumbnail_base64": r["thumbnail"],
                    "storyboard_title_ko": board.get("title_ko") if board else None,
                    "shot_count": len(board.get("shots", [])) if board else 0,
                    "storyboard_at": r["storyboard_at"],
                    "render": _statuses(r["render"]),
                }
            )
        return out

    def get(self, record_id: str) -> dict[str, Any] | None:
        with self._lock:
            r = self._conn.execute(
                "SELECT * FROM marketing_creative_records WHERE record_id = ?", (record_id,)
            ).fetchone()
        if r is None:
            return None
        return {
            "record_id": r["record_id"],
            "created_at": r["created_at"],
            "note": r["note"],
            "story": r["story"],
            "shots": r["shots"],
            "thumbnail_base64": r["thumbnail"],
            "sheet": json.loads(r["sheet"]),
            "storyboard": json.loads(r["storyboard"]) if r["storyboard"] else None,
            "storyboard_at": r["storyboard_at"],
            "render": json.loads(r["render"]) if r["render"] else {},
        }

    def get_render(self, record_id: str) -> dict[str, Any]:
        with self._lock:
            r = self._conn.execute(
                "SELECT render FROM marketing_creative_records WHERE record_id = ?", (record_id,)
            ).fetchone()
        return json.loads(r["render"]) if r is not None and r["render"] else {}

    def set_render(self, record_id: str, render: dict[str, Any]) -> None:
        with self._lock, self._conn:
            self._conn.execute(
                "UPDATE marketing_creative_records SET render = ? WHERE record_id = ?",
                (json.dumps(render, ensure_ascii=False), record_id),
            )


def _statuses(raw: str | None) -> dict[str, str]:
    """목록용: 단계별 상태만(sheet_image·board_image·video)."""
    render = json.loads(raw) if raw else {}
    return {k: str((v or {}).get("status") or "") for k, v in render.items() if isinstance(v, dict)}


_store: CreativeRecords | None = None


def store() -> CreativeRecords:
    global _store
    if _store is None:
        _store = CreativeRecords()
    return _store


def set_store(s: CreativeRecords | None) -> None:
    """테스트용."""
    global _store
    _store = s
