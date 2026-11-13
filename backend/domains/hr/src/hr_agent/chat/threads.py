"""대화 저장 (hr SQLite). 표는 처음 쓸 때 만든다(스키마를 다른 파일에 끼우지 않는다)."""

from __future__ import annotations

import json
from typing import Any

from ax_contracts.schemas import TENANT_ID_DEFAULT
from hr_agent.services import Services

MAX_MESSAGES = 60  # 한 대화에 남기는 메시지 수(넘으면 오래된 것부터 버린다)
SUMMARY_CHARS = 200

_DDL = f"""
CREATE TABLE IF NOT EXISTS hr_chat_threads (
  id TEXT PRIMARY KEY,
  tenant_id TEXT NOT NULL DEFAULT '{TENANT_ID_DEFAULT}',
  seq INTEGER NOT NULL,
  title TEXT NOT NULL,
  status TEXT NOT NULL,
  who TEXT,
  summary TEXT NOT NULL DEFAULT '',
  messages TEXT NOT NULL,
  created_at TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  removed_at TEXT,
  removed_by TEXT
)
"""
# 숨김 처리(지우지 않고 `removed_at`·`removed_by`만 남긴다)를 더하기 전에 만든 DB에 붙일 칸
_ADDED_COLUMNS = ("removed_at", "removed_by")
_LIVE = "removed_at IS NULL"  # 숨긴 대화는 모든 조회에서 뺀다


def ensure(svc: Services) -> None:
    svc.store.execute(_DDL)
    have = {r["name"] for r in svc.store.all("PRAGMA table_info(hr_chat_threads)")}
    for column in _ADDED_COLUMNS:
        if column not in have:
            svc.store.execute(f"ALTER TABLE hr_chat_threads ADD COLUMN {column} TEXT")


def _view(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "title": row["title"],
        "status": row["status"],
        "who": row["who"],
        "summary": row["summary"],
        "messages": json.loads(row["messages"]),
        # 인사 대화는 일을 만들지 않는다(조회·설명만). 화면이 "일이 생긴 대화"를 가릴 때 쓰는 자리
        "task_id": None,
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
    }


def get(svc: Services, thread_id: str) -> dict[str, Any] | None:
    ensure(svc)
    row = svc.store.one(f"SELECT * FROM hr_chat_threads WHERE id = ? AND {_LIVE}", (thread_id,))
    return _view(row) if row else None


def list_all(svc: Services, limit: int = 100) -> list[dict[str, Any]]:
    ensure(svc)
    rows = svc.store.all(
        f"SELECT * FROM hr_chat_threads WHERE {_LIVE} ORDER BY updated_at DESC, seq DESC LIMIT ?",
        (limit,),
    )
    return [_view(r) for r in rows]


def remove(svc: Services, thread_id: str, by: str) -> bool:
    """대화를 숨긴다(행은 지우지 않고 `removed_at`·`removed_by`만 남긴다).

    보이는 대화를 숨겼으면 True. 없거나 이미 숨겼으면 False이고 먼저 남긴 기록은 그대로다."""
    ensure(svc)
    changed = svc.store.execute(
        f"UPDATE hr_chat_threads SET removed_at = ?, removed_by = ? WHERE id = ? AND {_LIVE}",
        (svc.clock().isoformat(), by, thread_id),
    )
    return changed > 0


def create(svc: Services, title: str, who: str | None) -> dict[str, Any]:
    ensure(svc)
    row = svc.store.one("SELECT COALESCE(MAX(seq), 0) AS n FROM hr_chat_threads")
    seq = int(row["n"]) + 1 if row else 1
    now = svc.clock().isoformat()
    thread_id = f"hrchat-{seq}"
    svc.store.execute(
        "INSERT INTO hr_chat_threads"
        "(id, seq, title, status, who, summary, messages, created_at, updated_at)"
        " VALUES (?, ?, ?, 'talking', ?, '', '[]', ?, ?)",
        (thread_id, seq, title, who, now, now),
    )
    return get(svc, thread_id) or {}


def append(
    svc: Services, thread_id: str, role: str, text: str, summary: str | None = None
) -> dict[str, Any]:
    th = get(svc, thread_id)
    if th is None:
        raise KeyError(thread_id)
    msgs = th["messages"]
    now = svc.clock().isoformat()
    n = max((int(m["id"][1:]) for m in msgs if str(m.get("id", "")).startswith("m")), default=0) + 1
    msgs.append(
        {
            "id": f"m{n}",
            "role": role,
            "text": text,
            "at": now,
            "kind": "text",
            "plan": None,
            "task_id": None,
        }
    )
    msgs = msgs[-MAX_MESSAGES:]
    svc.store.execute(
        "UPDATE hr_chat_threads SET messages = ?, summary = ?, updated_at = ?"
        f" WHERE id = ? AND {_LIVE}",
        (
            json.dumps(msgs, ensure_ascii=False),
            (summary if summary is not None else th["summary"])[:SUMMARY_CHARS],
            now,
            thread_id,
        ),
    )
    return get(svc, thread_id) or {}
