"""PG 정산 자료 파일(CSV)을 읽는다. 은행 거래내역 올리기(`bank_file.py`)와 같은 방식이다.

실제 PG 연동(API)은 아직 없다. 사람이 PG 관리자 화면에서 내려받은 정산 내역 파일을 올리거나
폴더(`FINANCE_PG_INBOX`)에 넣으면, 그 파일이 덮는 판매일 기간은 파일과 대사한다.
파일이 덮지 않는 날은 지금처럼 결제 기록으로 만든 모의 자료와 맞춘다(`books.pg_settlements`).

- 한 줄 = 결제 한 건의 PG 정산: 결제 번호, 주문 번호, 금액, 수수료, 판매일, 지급일
- 칸 이름은 코드가 정한 이름만 받는다(AI가 칸을 짐작하지 않는다). 띄어쓰기·괄호·"원"은 무시
- 글자: UTF-8(BOM 포함) → CP949
- 같은 결제 번호를 다시 올리면 새 줄로 바꾼다(PG가 정정한 정산 파일을 다시 받는 경우)
- 기간: 파일에 있는 판매일의 처음~끝. 이 기간의 결제가 파일에 없으면 "PG에 없음"이다
- 지급일이 오늘 이후면 아직 정산 전이다
"""

from __future__ import annotations

import csv
import io
import os
import re
from datetime import date
from pathlib import Path
from typing import Any

MAX_BYTES = 5_000_000
MAX_ROWS = 20_000
HEADER_SCAN_ROWS = 10
INBOX_SUFFIXES = (".csv", ".txt")

PAYMENT_HEADERS = ("payment_id", "paymentkey", "결제키", "결제번호", "거래번호", "승인번호")
ORDER_HEADERS = ("order_id", "orderid", "주문번호")
AMOUNT_HEADERS = ("amount", "결제금액", "거래금액", "매출금액")
FEE_HEADERS = ("fee", "수수료", "결제수수료", "pg수수료")
SOLD_HEADERS = ("sold_date", "solddate", "판매일", "결제일", "거래일", "매출일")
PAYOUT_HEADERS = ("paid_out_date", "paidoutdate", "지급일", "정산일", "지급예정일", "정산예정일")


class PgFileError(ValueError):
    """사람에게 그대로 보여 줄 수 있는 읽기 실패 이유."""


def inbox_dir() -> Path | None:
    raw = os.getenv("FINANCE_PG_INBOX", "").strip()
    return Path(raw) if raw else None


def _norm(name: Any) -> str:
    return re.sub(r"[\s()\[\]원_]", "", str(name or "")).lower()


def _index(header: list[str], names: tuple[str, ...]) -> int | None:
    normed = [_norm(h) for h in header]
    for wanted in (_norm(n) for n in names):
        if wanted in normed:
            return normed.index(wanted)
    return None


def _decode(body: bytes) -> str:
    for enc in ("utf-8-sig", "cp949"):
        try:
            return body.decode(enc)
        except UnicodeDecodeError:
            continue
    raise PgFileError("글자를 읽지 못했습니다. UTF-8 또는 CP949 CSV로 저장해 주세요.")


def _won(value: str) -> int:
    text = value.replace(",", "").replace("원", "").strip()
    if not re.fullmatch(r"-?\d+", text):
        raise ValueError(f"금액이 정수 원이 아님: {value!r}")
    return int(text)


def _day(value: str) -> str:
    text = value.strip().replace(".", "-").replace("/", "-")[:10]
    return date.fromisoformat(text).isoformat()


def read_rows(body: bytes, filename: str = "") -> tuple[list[dict[str, Any]], list[str]]:
    """파일 바이트 → (쓸 줄, 버린 이유). 머리줄을 못 찾으면 `PgFileError`."""
    if len(body) > MAX_BYTES:
        raise PgFileError("파일이 너무 큽니다(5MB까지).")
    if filename and not filename.lower().endswith(INBOX_SUFFIXES):
        raise PgFileError("PG 정산 파일은 CSV로 올려 주세요.")
    lines = [r for r in csv.reader(io.StringIO(_decode(body))) if any(c.strip() for c in r)]
    at, cols = -1, {}
    for i, header in enumerate(lines[:HEADER_SCAN_ROWS]):
        cols = {
            key: _index(header, names)
            for key, names in (
                ("payment_id", PAYMENT_HEADERS),
                ("order_id", ORDER_HEADERS),
                ("amount", AMOUNT_HEADERS),
                ("fee", FEE_HEADERS),
                ("sold_date", SOLD_HEADERS),
                ("paid_out_date", PAYOUT_HEADERS),
            )
        }
        if all(cols[k] is not None for k in ("payment_id", "amount", "fee", "sold_date")):
            at = i
            break
    if at < 0:
        raise PgFileError("머리줄(결제번호·금액·수수료·판매일 칸)을 찾지 못했습니다.")
    body_lines = lines[at + 1 :]
    if len(body_lines) > MAX_ROWS:
        raise PgFileError(f"줄이 너무 많습니다({MAX_ROWS:,}줄까지).")
    rows: list[dict[str, Any]] = []
    problems: list[str] = []

    def cell(line: list[str], key: str) -> str:
        i = cols[key]
        return line[i].strip() if i is not None and i < len(line) else ""

    for n, line in enumerate(body_lines, start=at + 2):
        try:
            payment_id = cell(line, "payment_id")
            if not payment_id:
                raise ValueError("결제 번호가 비어 있음")
            sold = _day(cell(line, "sold_date"))
            payout_raw = cell(line, "paid_out_date")
            amount, fee = _won(cell(line, "amount")), _won(cell(line, "fee"))
            rows.append(
                {
                    "payment_id": payment_id[:80],
                    "order_id": cell(line, "order_id")[:80] or None,
                    "amount": amount,
                    "fee": fee,
                    "sold_date": sold,
                    "paid_out_date": _day(payout_raw) if payout_raw else None,
                }
            )
        except ValueError as exc:
            problems.append(f"{n}번째 줄: {exc}")
    if not rows:
        raise PgFileError("; ".join(problems[:3]) or "정산 줄이 없습니다.")
    return rows, problems


def merge_rows(store: dict[str, dict[str, Any]], rows: list[dict[str, Any]]) -> int:
    """결제 번호로 넣는다. 바뀐(새로 넣거나 내용이 달라진) 줄 수를 돌려준다."""
    changed = 0
    for r in rows:
        if store.get(r["payment_id"]) != r:
            store[r["payment_id"]] = dict(r)
            changed += 1
    return changed


def add_period(periods: list[dict[str, str]], rows: list[dict[str, Any]]) -> dict[str, str]:
    days = sorted(r["sold_date"] for r in rows)
    period = {"from": days[0], "to": days[-1]}
    periods.append(period)
    return period


def covered(periods: list[dict[str, str]], day: str) -> bool:
    return any(p["from"] <= day <= p["to"] for p in periods)


def import_file(
    body: bytes, filename: str, store: dict[str, dict[str, Any]], periods: list[dict[str, str]]
) -> dict[str, Any]:
    """파일 하나를 읽어 더한다. 화면에서 올린 파일과 폴더에 넣은 파일이 같이 쓴다."""
    rows, problems = read_rows(body, filename)
    changed = merge_rows(store, rows)
    period = add_period(periods, rows)
    return {
        "status": "imported",
        "rows": len(rows),
        "changed": changed,
        "skipped": len(problems),
        "problems": problems[:5],
        "period": period,
    }
