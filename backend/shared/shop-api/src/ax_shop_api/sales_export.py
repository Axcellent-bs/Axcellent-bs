"""판매 기록 내보내기 (`GET /api/shop/sales-export`, 계약 `data_access.shop_sales`). 읽기 전용.

data 수집기가 `(updated_at, order_id)` 커서로 바뀐 주문을 차례로 받아 표준 저장소에 덮어쓴다.

- 커서: 마지막으로 준 주문의 `updated_at|order_id`를 URL-safe base64로 감싼 글자. 모양은 이
  파일만 안다. 받은 것이 없으면 요청한 커서를 그대로 돌려준다(다음 수집이 그 자리부터).
- 아직 끝나지 않았을 수 있는 쓰기를 건너뛰지 않게, `updated_at`이 지금보다 `SETTLE`(5초) 이상 지난
  주문만 준다. 시각이 초 단위라 같은 초에 늦게 저장된 주문이 커서 뒤로 밀려 빠지는 것을 막는다.
- 상태가 바뀐 주문·환불이 생긴 주문은 `updated_at`이 바뀌어 다시 나온다(`refunds.py`가 환불 때
  주문 `updated_at`을 바꾼다).
- 구매자 이름·연락처·주소는 담지 않는다(주문 표에도 없다). 쓰기 없음.
"""

from __future__ import annotations

import base64
import binascii
import re
from collections import defaultdict
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from ax_contracts.data_access.shop_sales import (
    SalesOrderLine,
    SalesOrderRefund,
    SalesOrderRow,
    ShopSalesExportQuery,
    ShopSalesExportResult,
)
from ax_shop_api.db import Db
from ax_shop_api.service import ShopError

SETTLE = timedelta(seconds=5)
_CHUNK = 500  # IN (...) 한 번에 넣는 주문 수
_STAMP = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}Z")


def _iso(moment: datetime) -> str:
    return moment.astimezone(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _parse(text: str) -> datetime:
    return datetime.strptime(text, "%Y-%m-%dT%H:%M:%SZ").replace(tzinfo=UTC)


def encode_cursor(updated_at: str, order_id: str) -> str:
    raw = f"{updated_at}|{order_id}".encode()
    return base64.urlsafe_b64encode(raw).decode().rstrip("=")


def decode_cursor(cursor: str) -> tuple[str, str]:
    """커서 → (updated_at, order_id). 모양이 틀리면 400 `INVALID_CURSOR`."""
    try:
        padded = cursor + "=" * (-len(cursor) % 4)
        text = base64.urlsafe_b64decode(padded.encode()).decode()
        stamp, order_id = text.split("|", 1)
    except (ValueError, binascii.Error, UnicodeDecodeError) as error:
        raise ShopError("INVALID_CURSOR", "커서를 읽을 수 없다") from error
    if not _STAMP.fullmatch(stamp) or not order_id:
        raise ShopError("INVALID_CURSOR", "커서를 읽을 수 없다")
    return stamp, order_id


def _payment_status(statuses: list[str]) -> str | None:
    """성공한 결제가 있으면 succeeded, 아니면 마지막 시도의 상태, 시도가 없으면 None."""
    if not statuses:
        return None
    return "succeeded" if "succeeded" in statuses else statuses[-1]


class SalesExport:
    def __init__(self, db: Db, now: Callable[[], datetime] = lambda: datetime.now(UTC)) -> None:
        self.db = db
        self.now = now

    def export(self, query: ShopSalesExportQuery) -> ShopSalesExportResult:
        after = decode_cursor(query.cursor) if query.cursor else None
        collected_at = self.now()
        settled = _iso(collected_at - SETTLE)
        sql = (
            "SELECT order_id, buyer_id, status, created_at, updated_at, shipping_fee, "
            "points_used, coupon_discount, src_source, src_medium, src_campaign "
            "FROM orders WHERE tenant_id = ? AND updated_at <= ?"
        )
        args: list[Any] = [query.tenant_id, settled]
        if after is not None:
            sql += " AND (updated_at, order_id) > (?, ?)"
            args.extend(after)
        sql += " ORDER BY updated_at, order_id LIMIT ?"
        args.append(query.limit + 1)  # 하나 더 읽어 남은 것이 있는지 안다
        with self.db.tx() as conn:
            rows = conn.execute(sql, args).fetchall()
            has_more = len(rows) > query.limit
            rows = rows[: query.limit]
            ids = [r["order_id"] for r in rows]
            lines, payments, refunds = self._details(conn, ids)
        orders = []
        for r in rows:
            order_lines = lines[r["order_id"]]
            orders.append(
                SalesOrderRow(
                    order_id=r["order_id"],
                    buyer_id=r["buyer_id"],
                    status=r["status"],
                    created_at=_parse(r["created_at"]),
                    updated_at=_parse(r["updated_at"]),
                    items_amount=sum(line.qty * line.unit_price for line in order_lines),
                    shipping_fee=r["shipping_fee"],
                    points_used=r["points_used"],
                    coupon_discount=r["coupon_discount"],
                    src_source=r["src_source"],
                    src_medium=r["src_medium"],
                    src_campaign=r["src_campaign"],
                    payment_status=_payment_status(payments[r["order_id"]]),
                    lines=order_lines,
                    refunds=refunds[r["order_id"]],
                    tenant_id=query.tenant_id,
                )
            )
        if rows:
            last = rows[-1]
            next_cursor: str | None = encode_cursor(last["updated_at"], last["order_id"])
        else:
            next_cursor = query.cursor
        return ShopSalesExportResult(
            orders=orders,
            next_cursor=next_cursor,
            has_more=has_more,
            collected_at=collected_at,
            tenant_id=query.tenant_id,
        )

    @staticmethod
    def _details(
        conn: Any, ids: list[str]
    ) -> tuple[
        dict[str, list[SalesOrderLine]], dict[str, list[str]], dict[str, list[SalesOrderRefund]]
    ]:
        lines: dict[str, list[SalesOrderLine]] = defaultdict(list)
        payments: dict[str, list[str]] = defaultdict(list)
        refunds: dict[str, list[SalesOrderRefund]] = defaultdict(list)
        for start in range(0, len(ids), _CHUNK):
            chunk = ids[start : start + _CHUNK]
            marks = ",".join("?" * len(chunk))
            for item in conn.execute(
                "SELECT i.order_id, i.product_id, p.category_code, "
                "COALESCE(i.title, p.name) AS title, i.qty, i.unit_price, i.coupon_share, "
                "i.points_share FROM order_items i "
                "LEFT JOIN products p ON p.product_id = i.product_id "
                f"WHERE i.order_id IN ({marks}) ORDER BY i.rowid",
                chunk,
            ):
                lines[item["order_id"]].append(
                    SalesOrderLine(
                        product_id=item["product_id"],
                        category_code=item["category_code"],
                        title=item["title"],
                        qty=item["qty"],
                        unit_price=item["unit_price"],
                        coupon_share=item["coupon_share"],
                        points_share=item["points_share"],
                    )
                )
            for pay in conn.execute(
                f"SELECT order_id, status FROM payments WHERE order_id IN ({marks}) "
                "ORDER BY created_at, rowid",
                chunk,
            ):
                payments[pay["order_id"]].append(pay["status"])
            for ref in conn.execute(
                "SELECT refund_id, order_id, amount, created_at FROM refunds "
                f"WHERE order_id IN ({marks}) ORDER BY created_at, refund_id",
                chunk,
            ):
                refunds[ref["order_id"]].append(
                    SalesOrderRefund(
                        refund_id=ref["refund_id"],
                        amount=ref["amount"],
                        refunded_at=_parse(ref["created_at"]),
                    )
                )
        return lines, payments, refunds
