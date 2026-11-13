"""② 매출·PG 대사: 주문 ↔ 결제 ↔ PG 정산을 결제 한 건씩 맞춘다.

상태 (코드 규칙, 위에서부터 먼저 맞는 것):
- `missing_in_pg`: 지급 예정일이 지났는데 PG 정산 기록이 없음
- `pending`: 지급 예정일이 아직 안 됨 (정산 전)
- `mismatch`: PG 금액 ≠ 주문 금액
- `fee_anomaly`: PG 수수료가 기본 수수료율 계산값과 허용 오차(`fee_tolerance`)보다 더 다름
- `matched`: 모두 맞음
- `missing_in_ours`: 올린 PG 정산 파일에는 결제가 있는데 우리 쪽은 결제 성공 기록이 없음
  (결제 실패·시간 초과·기록 없음). 고객 돈이 빠져나갔는데 주문이 처리되지 않았을 수 있어
  환불 또는 확인이 필요하다. 올린 PG 파일에서만 나온다(모의 자료는 우리 결제로 만들어 생기지 않는다)

PG 정산은 올린 PG 정산 파일이 덮는 날은 그 파일, 그 밖의 날은 결제 기록으로 만든 모의 자료
(토스 정산 형식)다(`books.pg_settlements`, 건마다 `pg_basis`). 사람이 남긴 처리 메모(`note`)는
메모리에 둔다.
"""

from __future__ import annotations

from datetime import date, timedelta
from typing import Any

from finance_agent import finance_config as fc
from finance_agent import pg_file
from finance_agent.books import Books, in_range, pg_settlements, pg_upload

STATUSES = ("matched", "mismatch", "missing_in_pg", "pending", "fee_anomaly", "missing_in_ours")


def recon_range(d: date | None, today: date) -> tuple[date, date]:
    """(시작일, 끝 다음 날). 날짜가 없으면 최근 7일(오늘 포함)."""
    if d is not None:
        return d, d + timedelta(days=1)
    return today - timedelta(days=6), today + timedelta(days=1)


def classify(
    order_amount: int, record: dict[str, Any] | None, expected_payout: date, today: date
) -> tuple[str, str | None]:
    cfg = fc.load_config().pg
    if record is None:
        if expected_payout <= today:
            return (
                "missing_in_pg",
                f"지급 예정일({expected_payout.isoformat()})이 지났는데 PG 정산에 없음",
            )
        return "pending", "아직 정산 전"
    if not record["settled"]:
        return "pending", f"{record['paid_out_date']} 지급 예정"
    diff = record["amount"] - order_amount
    if diff:
        side = "많음" if diff > 0 else "적음"
        return "mismatch", f"PG 금액이 주문보다 {abs(diff):,}원 {side}"
    expected = fc.rate_amount(record["amount"], cfg.fee_rate)
    fee_diff = record["fee"] - expected
    if abs(fee_diff) > cfg.fee_tolerance:
        side = "많음" if fee_diff > 0 else "적음"
        pct = round(cfg.fee_rate * 100, 2)
        return (
            "fee_anomaly",
            f"PG 수수료 {record['fee']:,}원이 기본 {pct}% 기준 {expected:,}원보다 "
            f"{abs(fee_diff):,}원 {side}",
        )
    return "matched", None


def reconcile(
    books: Books, d: date | None, notes: dict[str, str], today: date | None = None
) -> dict[str, Any]:
    """기간 안 결제를 한 건씩 맞춘다. 같은 장부·기간이면 계산은 한 번만 하고 메모만 새로 붙인다.

    돌려준 결과는 읽기만 한다(같은 장부로 다시 부르면 같은 품목을 다시 쓴다).
    """
    cfg = fc.load_config().pg
    today = today or fc.today()
    base = books.memo(
        ("recon", d, today, id(cfg), pg_upload(books)[0]), lambda: _reconcile(books, d, today, cfg)
    )
    if not notes:
        return base
    items = [
        i | {"note": notes[i["payment_id"]]} if i["payment_id"] in notes else i
        for i in base["items"]
    ]
    return base | {"items": items}


def _reconcile(books: Books, d: date | None, today: date, cfg: Any) -> dict[str, Any]:
    start_d, end_d = recon_range(d, today)
    start, end = fc.day_start(start_d), fc.day_start(end_d)
    pg = pg_settlements(books, today)
    _, _, periods = pg_upload(books)
    items = []
    for p in books.payments():
        if not in_range(p["created_at"], start, end):
            continue
        order = p["order"]
        record = pg.get(p["payment_id"])
        expected_payout = p["created_at"].date() + timedelta(days=cfg.payout_days)
        order_amount = order.get("total_amount") or p["amount"]
        status, reason = classify(order_amount, record, expected_payout, today)
        base = record["amount"] if record else order_amount
        items.append(
            {
                "order_id": order["order_id"],
                "payment_id": p["payment_id"],
                "paid_at": fc.iso(p["created_at"]),
                "order_amount": order_amount,
                "pg_amount": record["amount"] if record else None,
                "fee": record["fee"] if record else None,
                "expected_fee": fc.rate_amount(base, cfg.fee_rate),
                "pay_out_amount": record["pay_out_amount"] if record else None,
                "paid_out_date": record["paid_out_date"] if record and record["settled"] else None,
                "status": status,
                "reason": reason,
                "note": None,
                # 무엇과 맞췄나: 올린 PG 정산 파일(uploaded) · 결제 기록으로 만든 모의 자료
                "pg_basis": "uploaded"
                if pg_file.covered(periods, p["created_at"].date().isoformat())
                else "generated",
            }
        )
    items.extend(_missing_in_ours(books, start_d, end_d, cfg))
    items.sort(key=lambda i: (i["paid_at"], i["payment_id"]), reverse=True)
    counts = {s: sum(1 for i in items if i["status"] == s) for s in STATUSES}
    totals = {
        "order_amount": sum(i["order_amount"] for i in items),
        "pg_amount": sum(i["pg_amount"] or 0 for i in items),
        "pg_fee": sum(i["fee"] or 0 for i in items),
        "pay_out": sum(i["pay_out_amount"] or 0 for i in items),
    }
    return {
        "from": fc.iso(start),
        "to": fc.iso(end),
        "pg_coverage": {
            "uploaded": sum(1 for i in items if i["pg_basis"] == "uploaded"),
            "generated": sum(1 for i in items if i["pg_basis"] == "generated"),
            "periods": [dict(per) for per in periods][-10:],
        },
        "counts": counts,
        "totals": totals,
        "items": items,
    }


def _missing_in_ours(books: Books, start_d: date, end_d: date, cfg: Any) -> list[dict[str, Any]]:
    """올린 PG 정산 파일에는 있는데 우리 쪽 결제 성공 기록이 없는 건(판매일이 기간 안).

    PG 파일의 결제 번호는 우리 결제 번호나 토스 paymentKey일 수 있어 둘 다로 찾는다.
    우리 쪽에 실패·시간 초과으로 남은 결제가 있으면 그 주문과 상태를 붙인다.
    """
    _, rows, _ = pg_upload(books)
    if not rows:
        return []
    ok_ids: set[str] = set()
    others: dict[str, tuple[dict[str, Any], dict[str, Any]]] = {}
    for order in books.orders:
        for p in order.get("payments", []):
            keys = [k for k in (p.get("payment_id"), p.get("pg_payment_key")) if k]
            if p.get("status") == "succeeded":
                ok_ids.update(keys)
            else:
                for k in keys:
                    others[k] = (order, p)
    out = []
    for pid, row in rows.items():
        sold = row.get("sold_date")
        if not sold or not (start_d.isoformat() <= sold < end_d.isoformat()) or pid in ok_ids:
            continue
        order, ours = others.get(pid, (None, None))
        our_state = (ours or {}).get("status") or "없음"
        out.append(
            {
                "order_id": (order or {}).get("order_id") or row.get("order_id"),
                "payment_id": pid,
                "paid_at": f"{sold}T00:00:00Z",
                "order_amount": 0,
                "pg_amount": row.get("amount"),
                "fee": row.get("fee"),
                "expected_fee": fc.rate_amount(row.get("amount") or 0, cfg.fee_rate),
                "pay_out_amount": None,
                "paid_out_date": row.get("paid_out_date"),
                "status": "missing_in_ours",
                "reason": (
                    f"PG에는 결제가 있는데 우리 쪽 결제 성공 기록이 없음(우리 상태: {our_state}). "
                    "고객 돈이 빠져나갔을 수 있어 환불 또는 확인 필요"
                ),
                "note": None,
                "pg_basis": "uploaded",
                "our_payment_status": (ours or {}).get("status"),
            }
        )
    return out


EXCEPTION_STATUSES = ("mismatch", "missing_in_pg", "fee_anomaly", "missing_in_ours")


def parse_statuses(value: str | None) -> tuple[str, ...]:
    """`status` 조회값 → 상태 목록. 없으면 사람이 볼 불일치, `all`이면 전부."""
    if value is None or not value.strip():
        return EXCEPTION_STATUSES
    if value.strip() == "all":
        return STATUSES
    picked = tuple(dict.fromkeys(x.strip() for x in value.split(",") if x.strip()))
    unknown = [x for x in picked if x not in STATUSES]
    if unknown or not picked:
        raise ValueError(f"status는 {', '.join(STATUSES)} 중에서 쉼표로 고르거나 all")
    return picked


def pick(result: dict[str, Any], statuses: tuple[str, ...], limit: int) -> dict[str, Any]:
    """화면에 보낼 대사 결과. 고른 상태의 건만 최근 순으로 `limit`건까지.

    `shown.total`은 고른 상태의 전체 건수다(잘렸으면 `limit`보다 크다).
    """
    chosen = [i for i in result["items"] if i["status"] in statuses]
    return result | {
        "items": chosen[:limit],
        "shown": {"statuses": list(statuses), "total": len(chosen), "limit": limit},
    }


def exceptions(result: dict[str, Any]) -> list[dict[str, Any]]:
    """사람이 볼 불일치 건 (matched·pending 제외)."""
    return [i for i in result["items"] if i["status"] not in ("matched", "pending")]


def exception_amount(item: dict[str, Any]) -> int:
    if item["status"] == "missing_in_pg":
        return item["order_amount"]
    if item["status"] == "missing_in_ours":  # 고객이 낸 돈(환불·확인 대상)
        return item["pg_amount"] or 0
    if item["status"] == "mismatch":
        return abs((item["pg_amount"] or 0) - item["order_amount"])
    return abs((item["fee"] or 0) - item["expected_fee"])
