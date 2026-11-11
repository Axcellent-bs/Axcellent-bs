"""아이디어함(idea_inbox): 판매 서버가 고객 아이디어를 저장하고 AXcellent 고객지원이 읽어 간다.

문의함(`inbox.py`)과 같은 방식이다(예진 님 제안: 저장 → AXcellent가 읽어 가기). 접수하는 순간에는
분류·기획 전달을 하지 않고 저장만 한다. 고객지원이 읽어 `submit_idea`로 처리하고 결과 코드를
적는다(`POST …/{seq}/synced`). 고객에게는 접수 사실만 돌려준다.

- 같은 구매자의 같은 종류·글은 `digest`가 같아 한 건이다.
- 글(text)은 로그·오류 메시지에 싣지 않는다.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import Any

from ax_shop_api.db import TENANT_ID, Db

READ_LIMIT_DEFAULT = 20
READ_LIMIT_MAX = 100


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def digest(buyer_id: str, kind: str, text: str) -> str:
    return hashlib.sha256(f"{buyer_id}|{kind}|{text}".encode()).hexdigest()


def add(
    db: Db, *, digest: str, context_id: str, buyer_id: str, kind: str, text: str
) -> dict[str, Any]:
    """아이디어를 저장한다. 같은 `digest`가 이미 있으면 처음 저장한 것을 돌려준다."""
    with db.tx() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO idea_inbox(digest, context_id, buyer_id, kind, text,"
            " tenant_id, created_at) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (digest, context_id, buyer_id, kind, text, TENANT_ID, _now()),
        )
        row = conn.execute("SELECT * FROM idea_inbox WHERE digest = ?", (digest,)).fetchone()
    return {"seq": row["seq"], "context_id": row["context_id"], "created_at": row["created_at"]}


def pending(db: Db, limit: int = READ_LIMIT_DEFAULT) -> tuple[list[dict[str, Any]], int]:
    """아직 읽어 가지 않은 아이디어를 접수 순서로 `limit`건까지, 그리고 남은 전체 수."""
    limit = max(1, min(limit, READ_LIMIT_MAX))
    with db.tx() as conn:
        rows = conn.execute(
            "SELECT * FROM idea_inbox WHERE synced_at IS NULL ORDER BY seq LIMIT ?", (limit,)
        ).fetchall()
        total = conn.execute(
            "SELECT COUNT(*) AS n FROM idea_inbox WHERE synced_at IS NULL"
        ).fetchone()["n"]
    items = [
        {
            "seq": r["seq"],
            "key": r["digest"],  # 고객지원이 멱등 키로 쓴다
            "context_id": r["context_id"],
            "buyer_id": r["buyer_id"],
            "kind": r["kind"],
            "text": r["text"],
            "created_at": r["created_at"],
        }
        for r in rows
    ]
    return items, total


def mark_synced(
    db: Db, seq: int, outcome: str, support_idea_id: str | None
) -> dict[str, Any] | None:
    """AXcellent가 읽어 처리했다고 표시한다(결과 코드와 함께). 이미 표시했으면 그대로 둔다."""
    with db.tx() as conn:
        row = conn.execute("SELECT * FROM idea_inbox WHERE seq = ?", (seq,)).fetchone()
        if row is None:
            return None
        if row["synced_at"] is None:
            conn.execute(
                "UPDATE idea_inbox SET synced_at = ?, outcome = ?, support_idea_id = ?"
                " WHERE seq = ?",
                (_now(), outcome[:40], support_idea_id, seq),
            )
            row = conn.execute("SELECT * FROM idea_inbox WHERE seq = ?", (seq,)).fetchone()
    return {
        "seq": seq,
        "synced_at": row["synced_at"],
        "outcome": row["outcome"],
        "support_idea_id": row["support_idea_id"],
    }
