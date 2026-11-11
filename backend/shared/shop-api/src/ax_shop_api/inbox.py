"""문의함(inquiry_inbox): 판매 서버가 고객 문의를 저장하고 AXcellent가 읽어 간다 (예진 님 제안).

흐름: 고객 접수(`POST /api/shop/inquiries`) → 이 서버에 저장 → AXcellent 고객지원이 정해진 간격으로
읽어 가서(`GET /api/shop/admin/inquiries`) 처리하고 → 읽었다고 알린다(`POST .../synced`).
판매 서버는 고객지원을 부르지 않는다. 그래서 중간이 끊겨도 접수된 문의는 여기에 남고, AXcellent가
못 읽으면 "연결 끊김"으로 바로 보인다 (조용히 사라지지 않는다).

- 같은 구매자의 같은 접수(주문·제목·본문·사진, 같은 시간 창)는 `digest`가 같아 한 건이다.
- 읽어 간 문의(`synced_at`)는 사진을 지운다. 사진은 읽어 갈 때까지만 보관한다.
- 고객이 받는 문의 번호는 이 서버의 접수 번호(`seq`)다. 고객지원이 읽어 간 뒤에는
  `support_inquiry_id`로 고객지원 문의에 이어지고, 고객용 목록·상세·평가는 늘 `seq`로 열어
  이 값으로 바꿔 부른다.
- 처리에 실패한 접수는 읽는 쪽이 `report_failure`로 알린다. 실패할 때마다 다시 읽는 시각을
  늦추고(1분부터 두 배, 최대 30분) 정해진 횟수(`SHOP_INBOX_MAX_ATTEMPTS`, 기본 6)에 닿으면
  실패로 빼 둔다. 그래서 계속 실패하는 접수가 문의함 앞을 막지 않는다.
- 원문은 로그·오류 메시지에 싣지 않는다.
"""

from __future__ import annotations

import json
from typing import Any

from ax_shop_api import intake_common
from ax_shop_api.db import TENANT_ID, Db
from ax_shop_api.intake_common import backoff_seconds, max_attempts  # noqa: F401 (밖에서 쓴다)
from ax_shop_api.intake_common import now as _now

READ_LIMIT_DEFAULT = 20
READ_LIMIT_MAX = 100
PREVIEW_CHARS = 80  # 아직 처리 전인 문의의 고객용 미리보기(본인이 쓴 글의 앞부분)
STUCK_WHERE = "failed_at IS NOT NULL AND synced_at IS NULL"  # 막힌 접수(목록·건수 공통)


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


def pending(db: Db, limit: int = READ_LIMIT_DEFAULT) -> tuple[list[dict[str, Any]], int, int]:
    """읽을 차례가 된 문의를 `limit`건까지(실패가 적은 것·오래된 것 먼저), 읽기를 기다리는 전체 수,
    실패로 빼 둔 수.

    다시 읽을 시각이 아직 안 된 문의는 이번에 건너뛴다(전체 수에는 센다).
    """
    limit = max(1, min(limit, READ_LIMIT_MAX))
    with db.tx() as conn:
        rows = conn.execute(
            "SELECT * FROM inquiry_inbox WHERE synced_at IS NULL AND failed_at IS NULL"
            " AND (retry_after IS NULL OR retry_after <= ?) ORDER BY attempts, seq LIMIT ?",
            (_now(), limit),
        ).fetchall()
        total = conn.execute(
            "SELECT COUNT(*) AS n FROM inquiry_inbox WHERE synced_at IS NULL AND failed_at IS NULL"
        ).fetchone()["n"]
        failed = conn.execute(
            f"SELECT COUNT(*) AS n FROM inquiry_inbox WHERE {STUCK_WHERE}"  # noqa: S608
        ).fetchone()["n"]
    items = [
        {
            "seq": r["seq"],
            "key": r["digest"],  # 고객지원이 멱등 키로 쓴다 (접수 번호는 DB를 새로 만들면 겹친다)
            "context_id": r["context_id"],
            "buyer_id": r["buyer_id"],
            "customer_name": r["customer_name"],
            "order_id": r["order_id"],
            "subject": r["subject"],
            "message": r["message"],
            "images": json.loads(r["images"]) if r["images"] else None,
            "attempts": r["attempts"],
            "created_at": r["created_at"],
        }
        for r in rows
    ]
    return items, total, failed


def mark_synced(db: Db, seq: int, support_inquiry_id: int) -> dict[str, Any] | None:
    """AXcellent가 읽어 처리했다고 표시하고 사진과 본문 사본을 지운다. 이미 표시했으면 그대로 둔다.

    없는 번호면 None이다.
    """
    with db.tx() as conn:
        row = conn.execute("SELECT * FROM inquiry_inbox WHERE seq = ?", (seq,)).fetchone()
        if row is None:
            return None
        if row["synced_at"] is None:
            conn.execute(
                "UPDATE inquiry_inbox SET synced_at = ?, support_inquiry_id = ?, images = NULL,"
                " message = '', retry_after = NULL, failed_at = NULL WHERE seq = ?",
                (_now(), support_inquiry_id, seq),
            )
            row = conn.execute("SELECT * FROM inquiry_inbox WHERE seq = ?", (seq,)).fetchone()
    return {
        "seq": row["seq"],
        "synced_at": row["synced_at"],
        "support_inquiry_id": row["support_inquiry_id"],
    }


def report_failure(db: Db, seq: int, reason: str) -> dict[str, Any] | None:
    """읽어 간 쪽이 처리하지 못했다고 알린다 (재시도 간격·상한은 intake_common에 있다)."""
    return intake_common.report_failure(db, "inquiry_inbox", seq, reason)


def for_buyer(db: Db, buyer_id: str) -> list[dict[str, Any]]:
    """고객 본인의 접수 목록(최근 순). 고객용 목록이 처리 전 문의도 보여 주는 데 쓴다."""
    with db.tx() as conn:
        rows = conn.execute(
            "SELECT * FROM inquiry_inbox WHERE buyer_id = ? AND gone_at IS NULL ORDER BY seq DESC",
            (buyer_id,),
        ).fetchall()
    return [_own(r) for r in rows]


def get_for_buyer(db: Db, seq: int, buyer_id: str) -> dict[str, Any] | None:
    """본인의 접수 하나. 없는 번호와 남의 접수는 똑같이 None이다."""
    with db.tx() as conn:
        row = conn.execute(
            "SELECT * FROM inquiry_inbox WHERE seq = ? AND buyer_id = ? AND gone_at IS NULL",
            (seq, buyer_id),
        ).fetchone()
    return _own(row) if row is not None else None


def _own(row: Any) -> dict[str, Any]:
    return {
        "seq": row["seq"],
        "order_id": row["order_id"],
        "subject": row["subject"],
        "message": row["message"],
        "created_at": row["created_at"],
        "support_inquiry_id": row["support_inquiry_id"],
        "synced": row["synced_at"] is not None,
        "failed": row["failed_at"] is not None and row["synced_at"] is None,
        "view": json.loads(row["view"]) if row["view"] else None,
        "feedback": json.loads(row["feedback"]) if row["feedback"] else None,
    }


def stuck(db: Db) -> list[dict[str, Any]]:
    """실패로 빼 둔 문의(사람이 봐야 한다). 고객이 쓴 글은 싣지 않고 번호·사유·횟수만 돌려준다."""
    rows = intake_common.stuck_rows(db, "inquiry_inbox", "inquiry", STUCK_WHERE)
    return [{k: v for k, v in row.items() if k != "digest"} for row in rows]


def retry(db: Db, seq: int) -> dict[str, Any] | None:
    """실패로 뺀 문의를 다시 읽는 차례로 돌린다. 실패가 아니면 그대로 두고, 없는 번호면 None."""
    return intake_common.requeue(db, "inquiry_inbox", seq, STUCK_WHERE, {}, lambda _row: True)


# ------------------------------------------------------------ 고객이 보는 문의 상태 (되돌려 쓰기)


def open_views(
    db: Db, after: int = 0, limit: int = READ_LIMIT_MAX
) -> tuple[list[dict[str, Any]], int | None]:
    """고객지원이 읽어 갔지만 고객 화면 값이 아직 끝나지 않은 문의 한 쪽.

    `seq`·`support_inquiry_id`·`view_digest`(마지막으로 적은 값의 지문)를 접수 번호 순으로 준다.
    돌려주는 두 번째 값이 다음 쪽의 `after`이고, 더 없으면 None이다. 해결되어 평가까지 끝난
    문의는 더 바뀌지 않아 뺀다.
    """
    limit = max(1, min(limit, READ_LIMIT_MAX))
    with db.tx() as conn:
        rows = conn.execute(
            "SELECT seq, support_inquiry_id, view_digest FROM inquiry_inbox"
            " WHERE synced_at IS NOT NULL AND support_inquiry_id IS NOT NULL AND view_final = 0"
            " AND gone_at IS NULL"
            " AND seq > ? ORDER BY seq LIMIT ?",
            (after, limit + 1),
        ).fetchall()
    more = len(rows) > limit
    items = [
        {
            "seq": r["seq"],
            "support_inquiry_id": r["support_inquiry_id"],
            "view_digest": r["view_digest"],
        }
        for r in rows[:limit]
    ]
    return items, (items[-1]["seq"] if more and items else None)


def set_view(db: Db, seq: int, view: dict[str, Any], view_digest: str) -> dict[str, Any] | None:
    """고객지원이 정한 고객용 문의 값을 적는다. 같은 지문이면 그대로 두고, 없는 번호면 None.

    해결(`resolved`)이고 평가가 끝난(`rated`) 문의는 더 바뀌지 않아 `view_final`을 켠다.
    """
    final = 1 if view.get("status") == "resolved" and view.get("rated") is True else 0
    with db.tx() as conn:
        row = conn.execute("SELECT * FROM inquiry_inbox WHERE seq = ?", (seq,)).fetchone()
        if row is None:
            return None
        if row["view_digest"] != view_digest or row["view"] is None:
            conn.execute(
                "UPDATE inquiry_inbox SET view = ?, view_digest = ?, view_final = ? WHERE seq = ?",
                (json.dumps(view, ensure_ascii=False, sort_keys=True), view_digest, final, seq),
            )
    return {"seq": seq, "view_digest": view_digest}


# ------------------------------------------------------------ 해결 결과 평가 (저장 → 읽어 가기)


def save_feedback(
    db: Db, seq: int, buyer_id: str, rating: int, resolved: bool, comment: str
) -> str:
    """본인 문의에 평가를 저장한다. `saved`, 이미 있으면 `exists`, 없는 문의·남의 문의면 `missing`.

    평가 글(comment)은 읽어 가는 쪽이 `untrusted`로만 다룬다. 이 서버는 값을 고치지 않는다.
    """
    payload = json.dumps(
        {"rating": rating, "resolved": resolved, "comment": comment}, ensure_ascii=False
    )
    with db.tx() as conn:
        row = conn.execute(
            "SELECT feedback FROM inquiry_inbox WHERE seq = ? AND buyer_id = ?", (seq, buyer_id)
        ).fetchone()
        if row is None:
            return "missing"
        if row["feedback"] is not None:
            return "exists"
        conn.execute(
            "UPDATE inquiry_inbox SET feedback = ?, feedback_at = ? WHERE seq = ?",
            (payload, _now(), seq),
        )
    return "saved"


def pending_feedback(db: Db, limit: int = READ_LIMIT_DEFAULT) -> tuple[list[dict[str, Any]], int]:
    """아직 읽어 가지 않은 평가를 저장 순서로 `limit`건까지, 그리고 남은 전체 수."""
    limit = max(1, min(limit, READ_LIMIT_MAX))
    where = "feedback IS NOT NULL AND feedback_synced_at IS NULL AND support_inquiry_id IS NOT NULL"
    with db.tx() as conn:
        rows = conn.execute(
            f"SELECT * FROM inquiry_inbox WHERE {where} ORDER BY feedback_at, seq LIMIT ?",  # noqa: S608
            (limit,),
        ).fetchall()
        total = conn.execute(
            f"SELECT COUNT(*) AS n FROM inquiry_inbox WHERE {where}"  # noqa: S608
        ).fetchone()["n"]
    items = []
    for r in rows:
        fb = json.loads(r["feedback"])
        items.append(
            {
                "seq": r["seq"],
                "key": r["digest"],
                "buyer_id": r["buyer_id"],
                "support_inquiry_id": r["support_inquiry_id"],
                "rating": fb["rating"],
                "resolved": fb["resolved"],
                "comment": fb.get("comment", ""),
                "created_at": r["feedback_at"],
            }
        )
    return items, total


def mark_feedback_synced(db: Db, seq: int, outcome: str) -> dict[str, Any] | None:
    """고객지원이 평가를 읽어 처리했다고 표시한다(결과 코드와 함께). 없는 번호면 None."""
    with db.tx() as conn:
        row = conn.execute("SELECT * FROM inquiry_inbox WHERE seq = ?", (seq,)).fetchone()
        if row is None or row["feedback"] is None:
            return None
        if row["feedback_synced_at"] is None:
            conn.execute(
                "UPDATE inquiry_inbox SET feedback_synced_at = ?, feedback_outcome = ?"
                " WHERE seq = ?",
                (_now(), outcome[:40], seq),
            )
            row = conn.execute("SELECT * FROM inquiry_inbox WHERE seq = ?", (seq,)).fetchone()
    return {
        "seq": seq,
        "synced_at": row["feedback_synced_at"],
        "outcome": row["feedback_outcome"],
    }


def mark_gone(db: Db, seq: int) -> dict[str, Any] | None:
    """고객지원에서 찾을 수 없는 문의(삭제·재시작)를 고객 화면에서 뺀다. 저장한 값도 지운다.

    이미 뺀 문의는 그대로 둔다. 없는 번호면 None이다.
    """
    with db.tx() as conn:
        row = conn.execute("SELECT * FROM inquiry_inbox WHERE seq = ?", (seq,)).fetchone()
        if row is None:
            return None
        if row["gone_at"] is None:
            conn.execute(
                "UPDATE inquiry_inbox SET gone_at = ?, view = NULL, view_final = 1, message = ''"
                " WHERE seq = ?",
                (_now(), seq),
            )
    return {"seq": seq}
