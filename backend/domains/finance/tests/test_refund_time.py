"""환불 시각: shop-api 주문 응답의 `refunds`(환불 기록)가 있으면 환불을 실제 환불 시각으로 잡는다.

holdout 평가(eval/holdout.py)에서 환불을 주문 시각에 두면 결산 20%·정산 13%만 맞았다(#877).
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from finance_agent import books as bk

CATEGORIES = {"prd_1": "book", "prd_2": "stationery"}


def _raw(refunds: Any = None, refunded: int = 0) -> dict[str, Any]:
    raw = {
        "order_id": "ord_1",
        "buyer_id": "buy_1",
        "status": "paid",
        "total_amount": 30000,
        "refunded_amount": refunded,
        "created_at": "2026-10-20T01:00:00Z",
        "delivered_at": None,
        "items": [
            {
                "product_id": "prd_1",
                "seller_id": "sel_1",
                "qty": 1,
                "unit_price": 18000,
                "points_share": 0,
                "paid_amount": 18000,
            },
            {
                "product_id": "prd_2",
                "seller_id": "sel_2",
                "qty": 1,
                "unit_price": 12000,
                "points_share": 0,
                "paid_amount": 12000,
            },
        ],  # fmt: skip
        "payments": [
            {
                "payment_id": "pay_1",
                "amount": 30000,
                "status": "succeeded",
                "created_at": "2026-10-20T01:00:05Z",
            },
        ],  # fmt: skip
    }
    if refunds is not None:
        raw["refunds"] = refunds
    return raw


def _order(raw: dict[str, Any]) -> dict[str, Any]:
    order = bk.normalize_order(raw, CATEGORIES)
    assert order is not None
    return order


def test_refunds_use_each_refund_time_and_split_per_refund() -> None:
    order = _order(
        _raw(
            [
                {
                    "refund_id": "rfd_1",
                    "payment_id": "pay_1",
                    "amount": 10000,
                    "created_at": "2026-10-22T03:00:00Z",
                },
                {
                    "refund_id": "rfd_2",
                    "payment_id": "pay_1",
                    "amount": 5000,
                    "created_at": "2026-10-27T09:30:00Z",
                },
            ],  # fmt: skip
            15000,
        )
    )
    rows = bk.refunds_from_orders([order])
    got = sorted((r["refund_id"], r["seller_id"], r["amount"], r["created_at"]) for r in rows)
    d1, d2 = datetime(2026, 10, 22, 3, tzinfo=UTC), datetime(2026, 10, 27, 9, 30, tzinfo=UTC)
    assert got == [
        ("rfd_1_0", "sel_1", 6000, d1),
        ("rfd_1_1", "sel_2", 4000, d1),
        ("rfd_2_0", "sel_1", 3000, d2),
        ("rfd_2_1", "sel_2", 2000, d2),
    ]
    assert {r["payment_id"] for r in rows} == {"pay_1"}
    assert bk.refund_basis([order]) == "refund_created_at"


def test_old_shop_api_without_refunds_keeps_order_time() -> None:
    order = _order(_raw(None, 12000))
    rows = bk.refunds_from_orders([order])
    assert sorted((r["refund_id"], r["seller_id"], r["amount"]) for r in rows) == [
        ("rfd_of_ord_1_0", "sel_1", 7200),
        ("rfd_of_ord_1_1", "sel_2", 4800),
    ]
    assert {r["created_at"] for r in rows} == {datetime(2026, 10, 20, 1, tzinfo=UTC)}
    assert bk.refund_basis([order]) == "order_created_at"


def test_unreadable_refund_records_fall_back_to_order_time() -> None:
    """시각을 못 읽거나 합계가 `refunded_amount`와 다르면 지어내지 않고 주문 시각에 둔다."""
    bad = [
        [{"refund_id": "rfd_1", "payment_id": "pay_1", "amount": 12000, "created_at": "x"}],
        [
            {
                "refund_id": "rfd_1",
                "payment_id": "pay_1",
                "amount": 9000,
                "created_at": "2026-10-22T03:00:00Z",
            }
        ],  # fmt: skip
        "not a list",
    ]
    for refunds in bad:
        order = _order(_raw(refunds, 12000))
        rows = bk.refunds_from_orders([order])
        assert sum(r["amount"] for r in rows) == 12000
        assert {r["created_at"] for r in rows} == {datetime(2026, 10, 20, 1, tzinfo=UTC)}


def test_refund_basis_mixed_and_empty() -> None:
    new = _order(
        _raw(
            [
                {
                    "refund_id": "rfd_1",
                    "payment_id": "pay_1",
                    "amount": 1000,
                    "created_at": "2026-10-22T03:00:00Z",
                }
            ],  # fmt: skip
            1000,
        )
    )
    old = _order(_raw(None, 1000)) | {"order_id": "ord_2"}
    assert bk.refund_basis([new, old]) == "mixed"
    assert bk.refund_basis([_order(_raw([], 0))]) == "refund_created_at"
    assert bk.refunds_from_orders([_order(_raw([], 0))]) == []
