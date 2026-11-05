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
  updated_at TEXT NOT NULL
)
"""


def ensure(svc: Services) -> None:
    svc.store.execute(_DDL)


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
    row = svc.store.one("SELECT * FROM hr_chat_threads WHERE id = ?", (thread_id,))
    return _view(row) if row else None


def list_all(svc: Services, limit: int = 100) -> list[dict[str, Any]]:
    ensure(svc)
    rows = svc.store.all(
        "SELECT * FROM hr_chat_threads ORDER BY updated_at DESC, seq DESC LIMIT ?", (limit,)
    )
    return [_view(r) for r in rows]


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
        "UPDATE hr_chat_threads SET messages = ?, summary = ?, updated_at = ? WHERE id = ?",
        (
            json.dumps(msgs, ensure_ascii=False),
            (summary if summary is not None else th["summary"])[:SUMMARY_CHARS],
            now,
            thread_id,
        ),
    )
    return get(svc, thread_id) or {}
