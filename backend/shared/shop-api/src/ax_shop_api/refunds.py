"""현금 환불(SHOP-12)과 반품 입고 기록. 웹 프레임워크에 의존하지 않는다.

환불은 고객지원이 `execute("refund_request")`에서 `executed`를 받은 뒤에만 부른다
(API 계층이 `support` 토큰만 받는다). 같은 `idempotency_key`의 재요청은 이전 환불을 돌려주고
(중복 지급 없음), 결제 금액을 넘는 환불은 거부한다. 환불이 반영되면 `payment.refunded`를,
반품이 입고되면 `return.received`를 outbox에 넣는다(이벤트 발행은 호출하는 쪽이 저장 뒤
`outbox.flush`로 한다).
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from ax_contracts.schemas import EventType, PaymentRefundedData, ReturnReceivedData
from ax_shop_api import outbox
from ax_shop_api.db import TENANT_ID, Db
from ax_shop_api.service import ShopError


def _iso(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


class Refunds:
    def __init__(self, db: Db, now: Callable[[], datetime] = lambda: datetime.now(UTC)) -> None:
        self.db = db
        self.now = now

    def refund(
        self, payment_id: str, amount: int, idempotency_key: str, context_id: str | None
    ) -> tuple[dict[str, Any], bool]:
        """결제 하나에 환불을 반영한다. 돌려주는 두 번째 값은 같은 키의 재요청이면 True다."""
        with self.db.tx() as conn:
            existing = conn.execute(
                "SELECT * FROM refunds WHERE idempotency_key = ?", (idempotency_key,)
            ).fetchone()
            if existing is not None:
                if (existing["payment_id"], existing["amount"]) != (payment_id, amount):
                    raise ShopError(
                        "IDEMPOTENCY_MISMATCH", "같은 키로 다른 내용을 요청할 수 없다", 409
                    )
                return self._view(conn, existing), True
            payment = conn.execute(
                "SELECT * FROM payments WHERE payment_id = ?", (payment_id,)
            ).fetchone()
            if payment is None:
                raise ShopError("PAYMENT_NOT_FOUND", "결제를 찾을 수 없다", 404)
            if payment["status"] != "succeeded":
                raise ShopError("PAYMENT_NOT_REFUNDABLE", "성공한 결제만 환불할 수 있다", 409)
            refunded = self._refunded(conn, payment_id)
            if amount > payment["amount"] - refunded:
                raise ShopError(
                    "REFUND_EXCEEDS_PAYMENT",
                    f"환불 가능 금액({payment['amount'] - refunded:,}원)을 넘는다",
                    409,
                )
            refund_id = self.db.next_id(conn, "refund", "rfd_")
            conn.execute(
                "INSERT INTO refunds VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    refund_id,
                    payment_id,
                    payment["order_id"],
                    amount,
                    idempotency_key,
                    TENANT_ID,
                    context_id,
                    _iso(self.now()),
                ),
            )
            data = PaymentRefundedData(
                payment_id=payment_id,
                order_id=payment["order_id"],
                refund_id=refund_id,
                refund_amount=amount,
            )
            outbox.enqueue(
                conn,
                EventType.PAYMENT_REFUNDED,
                data.model_dump(mode="json"),
                f"payment.refunded:{refund_id}",
            )
            row = conn.execute("SELECT * FROM refunds WHERE refund_id = ?", (refund_id,)).fetchone()
            return self._view(conn, row), False

    @staticmethod
    def _refunded(conn: Any, payment_id: str) -> int:
        row = conn.execute(
            "SELECT COALESCE(SUM(amount), 0) AS total FROM refunds WHERE payment_id = ?",
            (payment_id,),
        ).fetchone()
        return int(row["total"])

    def _view(self, conn: Any, row: Any) -> dict[str, Any]:
        payment = conn.execute(
            "SELECT amount FROM payments WHERE payment_id = ?", (row["payment_id"],)
        ).fetchone()
        return {
            "refund_id": row["refund_id"],
            "payment_id": row["payment_id"],
            "order_id": row["order_id"],
            "refund_amount": row["amount"],
            "refundable_left": payment["amount"] - self._refunded(conn, row["payment_id"]),
            "context_id": row["context_id"],
            "created_at": row["created_at"],
        }

    # --- 반품 입고 --------------------------------------------------------------
    def record_return(
        self, claim_id: int, order_id: str, inspection: str | None, context_id: str | None
    ) -> tuple[dict[str, Any], bool]:
        """반품 입고를 기록하고 `return.received`를 남긴다. 같은 클레임은 한 번만 기록한다."""
        with self.db.tx() as conn:
            existing = conn.execute(
                "SELECT * FROM returns WHERE claim_id = ?", (claim_id,)
            ).fetchone()
            if existing is not None:
                return self._return_view(existing), True
            if not conn.execute("SELECT 1 FROM orders WHERE order_id = ?", (order_id,)).fetchone():
                raise ShopError("ORDER_NOT_FOUND", "주문을 찾을 수 없다", 404)
            return_id = self.db.next_id(conn, "return", "ret_")
            received_at = self.now()
            conn.execute(
                "INSERT INTO returns VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    return_id,
                    claim_id,
                    order_id,
                    inspection,
                    _iso(received_at),
                    TENANT_ID,
                    context_id,
                    _iso(received_at),
                ),
            )
            data = ReturnReceivedData(
                return_id=return_id, order_id=order_id, received_at=received_at
            )
            outbox.enqueue(
                conn,
                EventType.RETURN_RECEIVED,
                data.model_dump(mode="json"),
                f"return.received:{return_id}",
            )
            row = conn.execute("SELECT * FROM returns WHERE return_id = ?", (return_id,)).fetchone()
            return self._return_view(row), False

    def pending_returns(self, limit: int = 20) -> tuple[list[dict[str, Any]], int]:
        """고객지원이 아직 읽어 가지 않은 반품 입고(입고 순서)와 남은 전체 수."""
        where = "FROM returns r LEFT JOIN return_sync s ON s.claim_id = r.claim_id"
        with self.db.tx() as conn:
            rows = conn.execute(
                f"SELECT r.* {where} WHERE s.claim_id IS NULL"  # noqa: S608
                " ORDER BY r.created_at, r.claim_id LIMIT ?",
                (max(1, min(limit, 100)),),
            ).fetchall()
            total = conn.execute(
                f"SELECT COUNT(*) AS n {where} WHERE s.claim_id IS NULL"  # noqa: S608
            ).fetchone()["n"]
        return [self._return_view(r) for r in rows], total

    def mark_return_synced(self, claim_id: int, outcome: str) -> dict[str, Any] | None:
        """고객지원이 입고를 읽어 처리했다고 표시한다(결과 코드와 함께). 없는 번호면 None."""
        with self.db.tx() as conn:
            if not conn.execute("SELECT 1 FROM returns WHERE claim_id = ?", (claim_id,)).fetchone():
                return None
            conn.execute(
                "INSERT OR IGNORE INTO return_sync(claim_id, synced_at, outcome) VALUES (?, ?, ?)",
                (claim_id, _iso(self.now()), outcome[:40]),
            )
            row = conn.execute(
                "SELECT * FROM return_sync WHERE claim_id = ?", (claim_id,)
            ).fetchone()
        return {"claim_id": claim_id, "synced_at": row["synced_at"], "outcome": row["outcome"]}

    def requeue_return(self, claim_id: int) -> dict[str, Any] | None:
        """읽어 간 표시를 지워 입고를 다시 읽게 한다(기록·이벤트는 그대로다)."""
        with self.db.tx() as conn:
            if not conn.execute("SELECT 1 FROM returns WHERE claim_id = ?", (claim_id,)).fetchone():
                return None
            conn.execute("DELETE FROM return_sync WHERE claim_id = ?", (claim_id,))
        return {"claim_id": claim_id, "requeued": True}

    def get_return(self, claim_id: int) -> dict[str, Any] | None:
        with self.db.tx() as conn:
            row = conn.execute("SELECT * FROM returns WHERE claim_id = ?", (claim_id,)).fetchone()
        return self._return_view(row) if row else None

    @staticmethod
    def _return_view(row: Any) -> dict[str, Any]:
        keys = ("return_id", "claim_id", "order_id", "inspection", "received_at", "context_id")
        return {key: row[key] for key in keys}
