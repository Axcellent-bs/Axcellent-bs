"""문의함(inquiry_inbox): 판매 서버가 고객 문의를 저장하고 AXcellent가 읽어 간다 (예진 님 제안).

흐름: 고객 접수(`POST /api/shop/inquiries`) → 이 서버에 저장 → AXcellent 고객지원이 정해진 간격으로
읽어 가서(`GET /api/shop/admin/inquiries`) 처리하고 → 읽었다고 알린다(`POST .../synced`).
판매 서버는 고객지원을 부르지 않는다. 그래서 중간이 끊겨도 접수된 문의는 여기에 남고, AXcellent가
못 읽으면 "연결 끊김"으로 바로 보인다 (조용히 사라지지 않는다).

- 같은 구매자의 같은 접수(주문·제목·본문·사진, 같은 시간 창)는 `digest`가 같아 한 건이다.
- 읽어 간 문의(`synced_at`)는 사진을 지운다. 사진은 읽어 갈 때까지만 보관한다.
- 원문은 로그·오류 메시지에 싣지 않는다.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Any

from ax_shop_api.db import TENANT_ID, Db

READ_LIMIT_DEFAULT = 20
READ_LIMIT_MAX = 100


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def add(
    db: Db,
    *,
    digest: str,
    context_id: str,
    buyer_id: str,
    customer_name: str,
    order_id: str,
    subject: str,
    message: str,
    images: list[dict] | None,
) -> dict[str, Any]:
    """문의를 저장한다. 같은 `digest`가 이미 있으면 처음 저장한 것을 돌려준다."""
    with db.tx() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO inquiry_inbox(digest, context_id, buyer_id, customer_name,"
            " order_id, subject, message, images, tenant_id, created_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                digest,
                context_id,
                buyer_id,
                customer_name,
                order_id,
                subject,
                message,
                json.dumps(images, ensure_ascii=False) if images else None,
                TENANT_ID,
                _now(),
            ),
        )
        row = conn.execute(
            "SELECT seq, context_id, order_id, created_at FROM inquiry_inbox WHERE digest = ?",
            (digest,),
        ).fetchone()
    return {
        "seq": row["seq"],
        "context_id": row["context_id"],
        "order_id": row["order_id"],
        "created_at": row["created_at"],
    }


def pending(db: Db, limit: int = READ_LIMIT_DEFAULT) -> tuple[list[dict[str, Any]], int]:
    """아직 읽어 가지 않은 문의를 접수 순서(오래된 것이 앞)로 `limit`건까지, 그리고 남은 전체 수."""
    limit = max(1, min(limit, READ_LIMIT_MAX))
    with db.tx() as conn:
        rows = conn.execute(
            "SELECT * FROM inquiry_inbox WHERE synced_at IS NULL ORDER BY seq LIMIT ?", (limit,)
        ).fetchall()
        total = conn.execute(
            "SELECT COUNT(*) AS n FROM inquiry_inbox WHERE synced_at IS NULL"
        ).fetchone()["n"]
    items = [
        {
            "seq": r["seq"],
            "context_id": r["context_id"],
            "buyer_id": r["buyer_id"],
            "customer_name": r["customer_name"],
            "order_id": r["order_id"],
            "subject": r["subject"],
            "message": r["message"],
            "images": json.loads(r["images"]) if r["images"] else None,
            "created_at": r["created_at"],
        }
        for r in rows
    ]
    return items, total


def mark_synced(db: Db, seq: int, support_inquiry_id: int) -> dict[str, Any] | None:
    """AXcellent가 읽어 처리했다고 표시하고 사진을 지운다. 이미 표시했으면 그대로 돌려준다.

    없는 번호면 None이다.
    """
    with db.tx() as conn:
        row = conn.execute("SELECT * FROM inquiry_inbox WHERE seq = ?", (seq,)).fetchone()
        if row is None:
            return None
        if row["synced_at"] is None:
            conn.execute(
                "UPDATE inquiry_inbox SET synced_at = ?, support_inquiry_id = ?, images = NULL"
                " WHERE seq = ?",
                (_now(), support_inquiry_id, seq),
            )
            row = conn.execute("SELECT * FROM inquiry_inbox WHERE seq = ?", (seq,)).fetchone()
    return {
        "seq": row["seq"],
        "synced_at": row["synced_at"],
        "support_inquiry_id": row["support_inquiry_id"],
    }
