"""클레임함(claim_inbox): 판매 서버가 고객의 취소·반품·교환 신청을 저장하고 AXcellent가 읽어 간다.

문의함(`inbox.py`)과 같은 방식이다 (예진 님 제안: 저장 → AXcellent가 읽어 오기, 설계는
`docs/pull-intake-design.md`). 접수하는 순간에는 판정하지 않고 "접수됨"으로 저장한다.

흐름: 고객 신청(`POST /api/shop/claims`) → 클레임함에 저장 → AXcellent 고객지원이 읽어 가서
(`GET /api/shop/admin/claims`) 판정하고 → 결과를 되돌려 쓴다(`POST …/{seq}/result`) → 고객은 이
서버에서 결과를 읽는다(`GET /api/shop/claims`). 판매 서버는 고객지원을 부르지 않는다.

- 같은 구매자의 같은 신청(주문·종류·사유·품목·설명)은 `digest`가 같아 한 건이다.
- `status`는 처음 `received`이고, 되돌려 쓸 때마다 고객지원이 정한 상태로 바뀐다.
  `result`는 고객지원이
  돌려준 클레임(고객이 예전에 접수 즉시 보던 모양) 그대로이며 이 서버는 값을 만들거나 고치지 않는다.
- 고객에게는 본인 신청만 보이고 신청 원문(설명·사진 수)은 돌려주지 않는다.
- 설명(고객이 쓴 문장)은 로그·오류 메시지에 싣지 않는다.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime
from typing import Any

from ax_shop_api.db import TENANT_ID, Db

READ_LIMIT_DEFAULT = 20
READ_LIMIT_MAX = 100
RECEIVED = "received"
NOT_ACCEPTED = "not_accepted"  # 고객지원이 클레임으로 받지 않았다(없는 주문·같은 품목 진행 중 등)
TERMINAL = ("completed", "rejected", NOT_ACCEPTED)  # 더 바뀌지 않는 상태
MAX_RESULT_CHARS = 100_000


def _now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def digest(
    buyer_id: str,
    order_id: str,
    type_: str,
    reason: str,
    items: list[dict] | None,
    unsealed: bool,
    description: str,
) -> str:
    """같은 신청인지 가리는 해시. 설명 원문은 키에 남기지 않고 해시에만 들어간다."""
    basis = json.dumps(
        [
            buyer_id,
            order_id,
            type_,
            reason,
            sorted(items or [], key=lambda i: str(i.get("order_item_id"))),
            unsealed,
            " ".join(description.split()),
        ],
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha256(basis.encode()).hexdigest()


def add(
    db: Db,
    *,
    digest: str,
    context_id: str,
    buyer_id: str,
    customer_name: str,
    order_id: str,
    type_: str,
    reason: str,
    items: list[dict] | None,
    unsealed: bool,
    evidence_count: int,
    description: str,
) -> dict[str, Any]:
    """신청을 저장한다. 같은 `digest`가 이미 있으면 처음 저장한 것을 돌려준다."""
    with db.tx() as conn:
        conn.execute(
            "INSERT OR IGNORE INTO claim_inbox(digest, context_id, buyer_id, customer_name,"
            " order_id, claim_type, reason, items, unsealed, evidence_count, description,"
            " status, tenant_id, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                digest,
                context_id,
                buyer_id,
                customer_name,
                order_id,
                type_,
                reason,
                json.dumps(items, ensure_ascii=False) if items else None,
                1 if unsealed else 0,
                evidence_count,
                description,
                RECEIVED,
                TENANT_ID,
                _now(),
                _now(),
            ),
        )
        row = conn.execute("SELECT * FROM claim_inbox WHERE digest = ?", (digest,)).fetchone()
    return _receipt(row)


def _receipt(row: Any) -> dict[str, Any]:
    """접수 직후 고객에게 돌려주는 모양. 판정·환불액은 없다."""
    return {
        "id": row["seq"],
        "context_id": row["context_id"],
        "status": row["status"],
        "order_id": row["order_id"],
        "type": row["claim_type"],
        "reason": row["reason"],
        "created_at": row["created_at"],
    }


def pending(db: Db, limit: int = READ_LIMIT_DEFAULT) -> tuple[list[dict[str, Any]], int]:
    """아직 읽어 가지 않은 신청을 접수 순서로 `limit`건까지, 그리고 남은 전체 수."""
    limit = max(1, min(limit, READ_LIMIT_MAX))
    with db.tx() as conn:
        rows = conn.execute(
            "SELECT * FROM claim_inbox WHERE synced_at IS NULL ORDER BY seq LIMIT ?",
            (limit,),
        ).fetchall()
        total = conn.execute(
            "SELECT COUNT(*) AS n FROM claim_inbox WHERE synced_at IS NULL"
        ).fetchone()["n"]
    items = [
        {
            "seq": r["seq"],
            "context_id": r["context_id"],
            "buyer_id": r["buyer_id"],
            "customer_name": r["customer_name"],
            "order_id": r["order_id"],
            "type": r["claim_type"],
            "reason": r["reason"],
            "items": json.loads(r["items"]) if r["items"] else None,
            "unsealed": bool(r["unsealed"]),
            "evidence_count": r["evidence_count"],
            "description": r["description"],
            "created_at": r["created_at"],
        }
        for r in rows
    ]
    return items, total


def open_claims(db: Db, limit: int = READ_LIMIT_MAX) -> list[dict[str, Any]]:
    """고객지원이 읽어 갔지만 아직 끝나지 않은 신청(상태를 다시 적어야 할 수 있는 것)."""
    limit = max(1, min(limit, READ_LIMIT_MAX))
    marks = ", ".join("?" for _ in TERMINAL)
    with db.tx() as conn:
        rows = conn.execute(
            "SELECT seq, support_claim_id, status FROM claim_inbox"
            f" WHERE synced_at IS NOT NULL AND support_claim_id IS NOT NULL"
            f" AND status NOT IN ({marks})"  # noqa: S608
            " ORDER BY seq LIMIT ?",
            (*TERMINAL, limit),
        ).fetchall()
    return [
        {"seq": r["seq"], "support_claim_id": r["support_claim_id"], "status": r["status"]}
        for r in rows
    ]


def write_result(
    db: Db, seq: int, support_claim_id: int | None, status: str, result: dict[str, Any]
) -> dict[str, Any] | None:
    """고객지원이 판정한 결과를 적는다. 같은 값이면 그대로 두고, 없는 번호면 None이다.

    클레임이 만들어지지 않은 신청은 번호 없이(`support_claim_id=None`) 이유만 적는다.
    이미 다른 고객지원 클레임 번호가 적혀 있으면 `ValueError`(한 신청은 한 클레임이다).
    """
    with db.tx() as conn:
        row = conn.execute("SELECT * FROM claim_inbox WHERE seq = ?", (seq,)).fetchone()
        if row is None:
            return None
        have = row["support_claim_id"]
        if have is not None and support_claim_id not in (None, have):
            raise ValueError("support_claim_id mismatch")
        support_claim_id = have if support_claim_id is None else support_claim_id
        payload = json.dumps(result, ensure_ascii=False, sort_keys=True)
        if row["status"] != status or row["result"] != payload or row["synced_at"] is None:
            conn.execute(
                "UPDATE claim_inbox SET support_claim_id = ?, status = ?, result = ?,"
                " synced_at = COALESCE(synced_at, ?), updated_at = ? WHERE seq = ?",
                (support_claim_id, status, payload, _now(), _now(), seq),
            )
        row = conn.execute("SELECT * FROM claim_inbox WHERE seq = ?", (seq,)).fetchone()
    return {
        "seq": row["seq"],
        "status": row["status"],
        "support_claim_id": row["support_claim_id"],
        "updated_at": row["updated_at"],
    }


def for_customer(db: Db, buyer_id: str, order_id: str | None = None) -> list[dict[str, Any]]:
    """고객 본인의 신청 목록(최근 순). 결과가 오기 전에는 `result`가 비어 있다."""
    sql = "SELECT * FROM claim_inbox WHERE buyer_id = ?"
    args: list[Any] = [buyer_id]
    if order_id is not None:
        sql += " AND order_id = ?"
        args.append(order_id)
    with db.tx() as conn:
        rows = conn.execute(sql + " ORDER BY seq DESC", args).fetchall()
    return [
        _receipt(r)
        | {
            "updated_at": r["updated_at"],
            "result": json.loads(r["result"]) if r["result"] else None,
        }
        for r in rows
    ]
