"""대화 저장 (archive SQLite). 표는 처음 쓸 때 만든다(스키마를 다른 파일에 끼우지 않는다)."""

from __future__ import annotations

import json
from typing import Any

from archive_agent.services import Services
from ax_agent_template import company_scope
from ax_agent_template.companies import company_ids
from ax_contracts.schemas import TENANT_ID_DEFAULT

MAX_MESSAGES = 60  # 한 대화에 남기는 메시지 수(넘으면 오래된 것부터 버린다)
SUMMARY_CHARS = 200

_DDL = f"""
CREATE TABLE IF NOT EXISTS archive_chat_threads (
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
  removed_by TEXT,
  room_id TEXT
)
"""
# 숨김 처리(지우지 않고 `removed_at`·`removed_by`만 남긴다)를 더하기 전에 만든 DB에 붙일 칸.
# room_id(11/20): 단톡방(전체 대화방 "all"·이슈 방 context_id)에서 만든 대화.
# 있으면 누구나 원문을 읽는다
_ADDED_COLUMNS = ("removed_at", "removed_by", "room_id")
_LIVE = "removed_at IS NULL"  # 숨긴 대화는 모든 조회에서 뺀다


def _mine() -> tuple[str, tuple[str, ...]]:
    """요청 회사의 대화만 보는 조건(11/24 회사 격리: 다른 회사에 같은 이름이 있어도 안 섞이게).

    회사를 모르면(공용 토큰·캣북스 세션) 캣북스. 캣북스는 다른 회사로 등록된 줄만 빼고 본다
    (예전 줄은 회사 값이 기본값·형식이 다른 값이라 예전처럼 캣북스 것으로 둔다)."""
    cid = company_scope.scope_id(company_scope.active())
    if cid == company_scope.CATBOOKS:
        others = tuple(c for c in company_ids() if c != company_scope.CATBOOKS)
        if not others:
            return "1 = 1", ()
        return f"tenant_id NOT IN ({', '.join('?' * len(others))})", others
    return "tenant_id = ?", (cid,)


def _tenant_value() -> str:
    """새 대화에 적을 회사 값. 캣북스는 예전 값(shop-demo) 그대로."""
    cid = company_scope.scope_id(company_scope.active())
    return TENANT_ID_DEFAULT if cid == company_scope.CATBOOKS else cid


def ensure(svc: Services) -> None:
    svc.store.execute(_DDL)
    have = {r["name"] for r in svc.store.all("PRAGMA table_info(archive_chat_threads)")}
    for column in _ADDED_COLUMNS:
        if column not in have:
            svc.store.execute(f"ALTER TABLE archive_chat_threads ADD COLUMN {column} TEXT")


def _view(row: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": row["id"],
        "title": row["title"],
        "status": row["status"],
        "who": row["who"],
        "summary": row["summary"],
        "messages": json.loads(row["messages"]),
        # 아카이브 대화는 일을 만들지 않는다(조회·설명만). 화면이 "일이 생긴 대화"를 가르는 자리
        "task_id": None,
        "created_at": row["created_at"],
        "updated_at": row["updated_at"],
        "room_id": row.get("room_id"),  # 단톡방에서 만든 대화면 그 방 (1:1이면 None)
    }


def get(svc: Services, thread_id: str) -> dict[str, Any] | None:
    ensure(svc)
    where, args = _mine()
    row = svc.store.one(
        f"SELECT * FROM archive_chat_threads WHERE id = ? AND {_LIVE} AND {where}",
        (thread_id, *args),
    )
    return _view(row) if row else None


def list_all(svc: Services, limit: int = 100) -> list[dict[str, Any]]:
    ensure(svc)
    where, args = _mine()
    rows = svc.store.all(
        "SELECT * FROM archive_chat_threads"
        f" WHERE {_LIVE} AND {where} ORDER BY updated_at DESC, seq DESC LIMIT ?",
        (*args, limit),
    )
    return [_view(r) for r in rows]


def remove(svc: Services, thread_id: str, by: str) -> bool:
    """대화를 숨긴다(행은 지우지 않고 `removed_at`·`removed_by`만 남긴다).

    보이는 대화를 숨겼으면 True. 없거나 이미 숨겼으면 False이고 먼저 남긴 기록은 그대로다."""
    ensure(svc)
    where, args = _mine()
    changed = svc.store.execute(
        f"UPDATE archive_chat_threads SET removed_at = ?, removed_by = ?"
        f" WHERE id = ? AND {_LIVE} AND {where}",
        (svc.clock().isoformat(), by, thread_id, *args),
    )
    return changed > 0


def _id_suffix() -> str:
    """다른 회사 대화 번호 뒤에 회사를 붙인다(회사마다 파일이 따로라 번호가 겹치지 않게, 11/25).
    캣북스는 예전 번호 그대로."""
    cid = company_scope.scope_id(company_scope.active())
    return "" if cid == company_scope.CATBOOKS else f"-{cid}"


def create(svc: Services, title: str, who: str | None,
           room_id: str | None = None) -> dict[str, Any]:  # fmt: skip
    ensure(svc)
    row = svc.store.one("SELECT COALESCE(MAX(seq), 0) AS n FROM archive_chat_threads")
    seq = int(row["n"]) + 1 if row else 1
    now = svc.clock().isoformat()
    thread_id = f"archchat-{seq}" + _id_suffix()
    svc.store.execute(
        "INSERT INTO archive_chat_threads"
        "(id, seq, title, status, who, summary, messages, created_at, updated_at, room_id,"
        " tenant_id) VALUES (?, ?, ?, 'talking', ?, '', '[]', ?, ?, ?, ?)",
        (thread_id, seq, title, who, now, now, room_id, _tenant_value()),
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
        "UPDATE archive_chat_threads SET messages = ?, summary = ?, updated_at = ?"
        f" WHERE id = ? AND {_LIVE}",
        (
            json.dumps(msgs, ensure_ascii=False),
            (summary if summary is not None else th["summary"])[:SUMMARY_CHARS],
            now,
            thread_id,
        ),
    )
    return get(svc, thread_id) or {}
