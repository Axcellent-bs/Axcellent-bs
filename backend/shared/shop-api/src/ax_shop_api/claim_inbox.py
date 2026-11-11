"""클레임함(claim_inbox): 판매 서버가 고객의 취소·반품·교환 신청을 저장하고 AXcellent가 읽어 간다.

문의함(`inbox.py`)과 같은 방식이다 (예진 님 제안: 저장 → AXcellent가 읽어 오기, 설계는
`docs/pull-intake-design.md`). 접수하는 순간에는 판정하지 않고 "접수됨"으로 저장한다.

흐름: 고객 신청(`POST /api/shop/claims`) → 클레임함에 저장 → AXcellent 고객지원이 읽어 가서
(`GET /api/shop/admin/claims`) 판정하고 → 결과를 되돌려 쓴다(`POST …/{seq}/result`) → 고객은 이
서버에서 결과를 읽는다(`GET /api/shop/claims`). 판매 서버는 고객지원을 부르지 않는다.

- 같은 구매자의 같은 신청(주문·종류·사유·품목·설명)은 접수 지문이 같아 한 건이다. 다만 앞 신청이
  반려(`rejected`)됐거나 클레임으로 받지 않았으면(`not_accepted`) 같은 내용으로 다시 낸 신청은
  새 건이다. 접수 지문은 세대 번호를 붙여(`지문#2`) 유일하게 저장하고, 고객지원에는 이 값을
  `key`로 준다.
- `status`는 처음 `received`이고, 되돌려 쓸 때마다 고객지원이 정한 상태로 바뀐다.
  `result`는 고객지원이 돌려준 클레임(고객이 볼 수 있는 항목만) 그대로이며
  이 서버는 값을 만들거나 고치지 않는다.
- 처리에 실패한 접수는 문의함처럼 다시 읽는 시각을 늦추다가(`report_failure`) 횟수에 닿으면
  고객에게 "접수하지 못했다"로 닫는다.
- 끝나지 않은 신청(`open_claims`)에는 반려된 지 얼마 안 된 신청도 들어간다. 검토 시간 초과로 반려된
  건은 다시 진행될 수 있어서다.
- 고객에게는 본인 신청만 보이고 신청 원문(설명·사진 수)은 돌려주지 않는다.
- 설명(고객이 쓴 문장)은 로그·오류 메시지에 싣지 않는다.
"""

from __future__ import annotations

import hashlib
import json
from datetime import UTC, datetime, timedelta
from typing import Any

from ax_shop_api import intake_common
from ax_shop_api.db import TENANT_ID, Db
from ax_shop_api.intake_common import now as _now

READ_LIMIT_DEFAULT = 20
READ_LIMIT_MAX = 100
RECEIVED = "received"
NOT_ACCEPTED = "not_accepted"  # 고객지원이 클레임으로 받지 않았다(없는 주문·같은 품목 진행 중 등)
FINAL = ("completed", NOT_ACCEPTED)  # 더 바뀌지 않는 상태
RESUBMITTABLE = (
    NOT_ACCEPTED,
)  # 같은 내용으로 다시 내면 새 신청이 되는 상태(반려는 고객지원이 막는다)
REJECTED_RECHECK_DAYS = 7  # 반려된 신청도 이 기간은 상태를 다시 적을 수 있게 둔다
MAX_RESULT_CHARS = 100_000
INTAKE_FAILED = {
    "message": "접수를 처리하지 못했습니다. 고객센터로 문의해 주세요.",
    "outcome": "intake_failed",
}


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


def result_digest(result: dict[str, Any] | None) -> str | None:
    """적어 둔 결과의 지문. 읽는 쪽이 같은 방식으로 세어 달라졌을 때만 다시 쓴다."""
    if result is None:
        return None
    return hashlib.sha1(canonical(result).encode()).hexdigest()[:16]


def canonical(result: dict[str, Any]) -> str:
    return json.dumps(result, ensure_ascii=False, sort_keys=True)


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
    """신청을 저장한다. 같은 지문의 가장 최근 신청이 반려·접수 불가가 아니면 그것을 돌려준다."""
    with db.tx() as conn:
        # 같은 지문과 그 재신청(지문#n)을 UNIQUE 인덱스로 찾는다(LIKE는 표 전체를 훑는다). '#'의
        # 다음 글자가 '$'라 [지문#, 지문$) 범위가 지문#n 전부다
        same = conn.execute(
            "SELECT * FROM claim_inbox WHERE digest = ? UNION ALL"
            " SELECT * FROM claim_inbox WHERE digest >= ? AND digest < ? ORDER BY seq DESC",
            (digest, digest + "#", digest + "$"),
        ).fetchall()
        if same and same[0]["status"] not in RESUBMITTABLE:
            return _receipt(same[0])
        key = digest if not same else f"{digest}#{len(same) + 1}"
        conn.execute(
            "INSERT OR IGNORE INTO claim_inbox(digest, context_id, buyer_id, customer_name,"
            " order_id, claim_type, reason, items, unsealed, evidence_count, description,"
            " status, tenant_id, created_at, updated_at)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (
                key,
                context_id if not same else f"{context_id}{len(same) + 1:02d}",
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
        row = conn.execute("SELECT * FROM claim_inbox WHERE digest = ?", (key,)).fetchone()
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


def pending(db: Db, limit: int = READ_LIMIT_DEFAULT) -> tuple[list[dict[str, Any]], int, int]:
    """읽을 차례가 된 신청을 `limit`건까지(실패가 적은 것·오래된 것 먼저), 읽기를 기다리는 전체 수,
    실패로 빼 둔 수. 다시 읽을 시각이 아직 안 된 신청은 이번에 건너뛴다(전체 수에는 센다)."""
    limit = max(1, min(limit, READ_LIMIT_MAX))
    with db.tx() as conn:
        rows = conn.execute(
            "SELECT * FROM claim_inbox WHERE synced_at IS NULL AND failed_at IS NULL"
            " AND (retry_after IS NULL OR retry_after <= ?) ORDER BY attempts, seq LIMIT ?",
            (_now(), limit),
        ).fetchall()
        total = conn.execute(
            "SELECT COUNT(*) AS n FROM claim_inbox WHERE synced_at IS NULL AND failed_at IS NULL"
        ).fetchone()["n"]
        failed = conn.execute(
            "SELECT COUNT(*) AS n FROM claim_inbox WHERE failed_at IS NOT NULL"
            " AND support_claim_id IS NULL"
        ).fetchone()["n"]
    items = [
        {
            "seq": r["seq"],
            "key": r["digest"],  # 고객지원이 멱등 키로 쓴다 (접수 번호는 DB를 새로 만들면 겹친다)
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
            "attempts": r["attempts"],
            "created_at": r["created_at"],
        }
        for r in rows
    ]
    return items, total, failed


def open_claims(
    db: Db, after: int = 0, limit: int = READ_LIMIT_MAX
) -> tuple[list[dict[str, Any]], int | None]:
    """고객지원이 읽어 갔지만 아직 끝나지 않은 신청을 접수 번호 순으로 `after` 뒤부터 `limit`건.

    반려된 신청은 일주일 동안은 포함한다(검토 시간 초과 반려는 다시 진행될 수 있다).
    돌려주는 두 번째 값은 다음 쪽의 `after`다(더 없으면 None). 각 줄의 `result_digest`는
    적어 둔 결과의 지문이라 읽는 쪽이 달라졌을 때만 다시 쓸 수 있다.
    """
    limit = max(1, min(limit, READ_LIMIT_MAX))
    final = ", ".join("?" for _ in FINAL)
    recent = intake_common.stamp(datetime.now(UTC) - timedelta(days=REJECTED_RECHECK_DAYS))
    with db.tx() as conn:
        rows = conn.execute(
            "SELECT seq, support_claim_id, status, result FROM claim_inbox"
            " WHERE synced_at IS NOT NULL AND support_claim_id IS NOT NULL AND seq > ?"
            f" AND status NOT IN ({final})"  # noqa: S608
            " AND (status != 'rejected' OR updated_at >= ?) ORDER BY seq LIMIT ?",
            (after, *FINAL, recent, limit + 1),
        ).fetchall()
    page = rows[:limit]
    items = [
        {
            "seq": r["seq"],
            "support_claim_id": r["support_claim_id"],
            "status": r["status"],
            "result_digest": result_digest(json.loads(r["result"]) if r["result"] else None),
        }
        for r in page
    ]
    return items, (page[-1]["seq"] if len(rows) > limit else None)


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
        payload = canonical(result)
        if row["status"] != status or row["result"] != payload or row["synced_at"] is None:
            conn.execute(
                "UPDATE claim_inbox SET support_claim_id = ?, status = ?, result = ?,"
                " synced_at = COALESCE(synced_at, ?), retry_after = NULL, updated_at = ?"
                " WHERE seq = ?",
                (support_claim_id, status, payload, _now(), _now(), seq),
            )
        row = conn.execute("SELECT * FROM claim_inbox WHERE seq = ?", (seq,)).fetchone()
    return {
        "seq": row["seq"],
        "status": row["status"],
        "support_claim_id": row["support_claim_id"],
        "updated_at": row["updated_at"],
    }


def _exhausted(moment: str) -> dict[str, Any]:
    """실패로 뺄 때 고객에게는 접수하지 못했다고 닫는다."""
    return {
        "status": NOT_ACCEPTED,
        "result": canonical(INTAKE_FAILED),
        "synced_at": moment,
        "updated_at": moment,
    }


def report_failure(db: Db, seq: int, reason: str) -> dict[str, Any] | None:
    """읽어 간 쪽이 처리하지 못했다고 알린다 (재시도 간격·상한은 intake_common에 있다)."""
    return intake_common.report_failure(db, "claim_inbox", seq, reason, _exhausted)


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


STUCK_WHERE = "failed_at IS NOT NULL AND support_claim_id IS NULL"  # 막힌 접수(목록·건수 공통)


def _base(digest: str) -> str:
    return digest.split("#", 1)[0]


def stuck(db: Db) -> list[dict[str, Any]]:
    """실패로 닫은 신청(사람이 봐야 한다). 고객이 이미 다시 신청한 것은 뺀다(이중 처리 방지)."""
    rows = intake_common.stuck_rows(db, "claim_inbox", "claim", STUCK_WHERE)
    with db.tx() as conn:
        newest = {
            r["base"]: r["seq"]
            for r in conn.execute("SELECT digest, seq FROM claim_inbox ORDER BY seq").fetchall()
            for r in [{"base": _base(r["digest"]), "seq": r["seq"]}]
        }
    return [
        {k: v for k, v in row.items() if k != "digest"}
        for row in rows
        if newest.get(_base(row["digest"])) == row["seq"]
    ]


def retry(db: Db, seq: int) -> dict[str, Any] | None:
    """실패로 닫은 신청을 다시 "접수됨"으로 돌려 읽는 차례에 올린다(고객 화면에도 다시 접수됨).

    실패로 닫힌 것이 아니거나 고객이 이미 다시 신청했으면 그대로 두고, 없는 번호면 None이다.
    """

    def not_replaced(row: Any) -> bool:
        with db.tx() as conn:
            newer = conn.execute(
                "SELECT digest FROM claim_inbox WHERE seq > ?", (row["seq"],)
            ).fetchall()
        return all(_base(n["digest"]) != _base(row["digest"]) for n in newer)

    sets = {"status": RECEIVED, "result": None, "synced_at": None, "updated_at": _now()}
    return intake_common.requeue(db, "claim_inbox", seq, STUCK_WHERE, sets, not_replaced)
