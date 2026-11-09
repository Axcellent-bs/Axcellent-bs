"""은행에서 내려받은 거래내역 파일(CSV·엑셀 xlsx)을 읽는다.

화면에서 올린 파일과 폴더에 넣은 파일이 같은 길을 쓴다.

은행마다 파일 모양이 다르다. 코드가 정한 규칙으로만 읽는다(AI가 칸을 짐작하지 않는다).
- 글자: UTF-8(BOM 포함) → CP949(EUC-KR, 국내 은행 CSV에 흔함) 순서로 읽는다
- 엑셀: `.xlsx`는 첫 시트를 읽는다. 옛 엑셀(`.xls`)은 읽지 않고 CSV·xlsx로 저장해 달라고 알린다
- 머리줄: 맨 위 몇 줄이 계좌번호·조회기간 같은 안내여도, 위에서 `HEADER_SCAN_ROWS`줄 안에서
  날짜 칸과 입금·출금 칸이 함께 있는 줄을 머리줄로 본다
- 칸 이름: 띄어쓰기·괄호·"원"을 빼고 비교한다(`입금(원)` = `입금`). 흔한 이름을 받는다
- 적요: 적요·기재내용처럼 설명 칸이 여럿이면 이어 붙인다(입금 대조가 적요의 PG 이름을 찾는다)
- 금액 한 칸 + 구분 칸(입금/출금)인 파일도 읽는다
- 출금은 음수 금액으로 넣는다. 출금 칸이 있던 파일이면 그 기간을 출금 대조 기간으로 남긴다
"""

from __future__ import annotations

import csv
import io
import re
import zipfile
from datetime import date, datetime
from typing import Any

MAX_BYTES = 5_000_000
HEADER_SCAN_ROWS = 20

DATE_HEADERS = ("date", "거래일자", "거래일", "일자", "거래일시", "거래날짜", "날짜")
AMOUNT_HEADERS = ("amount", "입금액", "입금", "맡기신금액", "입금금액")
WITHDRAW_HEADERS = ("withdrawal", "출금액", "출금", "찾으신금액", "출금금액")
MEMO_HEADERS = ("memo", "적요", "내용", "거래내용", "기재내용", "받는분", "보낸분", "거래기록사항")
BALANCE_HEADERS = ("balance", "잔액", "거래후잔액", "거래후 잔액")
SIGNED_HEADERS = ("거래금액", "금액")  # 금액 한 칸 + 구분 칸
KIND_HEADERS = ("구분", "입출금구분", "거래구분", "입출구분")


class BankFileError(ValueError):
    """사람에게 그대로 보여 줄 수 있는 읽기 실패 이유."""


def _norm(name: Any) -> str:
    return re.sub(r"[\s()\[\]원]", "", str(name or "")).lower()


def _index(header: list[str], names: tuple[str, ...]) -> int | None:
    wanted = [_norm(n) for n in names]
    normed = [_norm(h) for h in header]
    for w in wanted:  # 앞에 적은 이름이 먼저다
        if w in normed:
            return normed.index(w)
    return None


def _won(value: Any) -> int | None:
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return int(round(value))
    cleaned = re.sub(r"[,\s원₩]", "", str(value))
    if not cleaned or cleaned in ("-", "+"):
        return None
    try:
        return int(cleaned)
    except ValueError:
        try:
            return int(round(float(cleaned)))
        except ValueError:
            return None


def _day(value: Any) -> str | None:
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    text = str(value or "").strip()
    digits = re.match(r"^(\d{4})[-./](\d{1,2})[-./](\d{1,2})", text)
    if digits:
        y, m, d = (int(x) for x in digits.groups())
    elif re.match(r"^\d{8}", text):
        y, m, d = int(text[:4]), int(text[4:6]), int(text[6:8])
    else:
        return None
    try:
        return date(y, m, d).isoformat()
    except ValueError:
        return None


def _table(body: bytes, filename: str) -> list[list[Any]]:
    name = filename.lower()
    if name.endswith(".xls") and not body.startswith(b"PK"):
        raise BankFileError(
            "옛 엑셀(.xls)은 읽지 못합니다. 엑셀에서 .xlsx나 CSV로 저장해 올려 주세요."
        )
    if body.startswith(b"PK"):  # xlsx는 zip이다
        try:
            from openpyxl import load_workbook

            book = load_workbook(io.BytesIO(body), read_only=True, data_only=True)
        except (zipfile.BadZipFile, KeyError, OSError, ValueError) as exc:
            raise BankFileError("엑셀 파일을 열지 못했습니다.") from exc
        sheet = book.worksheets[0]
        return [list(r) for r in sheet.iter_rows(values_only=True)]
    for encoding in ("utf-8-sig", "cp949"):
        try:
            text = body.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise BankFileError("글자를 읽지 못했습니다. CSV를 UTF-8이나 CP949로 저장해 주세요.")
    sample = text[:4000]
    delimiter = "\t" if sample.count("\t") > sample.count(",") else ","
    return [row for row in csv.reader(io.StringIO(text), delimiter=delimiter)]


def _header_row(table: list[list[Any]]) -> int | None:
    """위에서 `HEADER_SCAN_ROWS`줄 안에서 날짜 칸과 입금·출금(또는 금액+구분) 칸이 있는 줄."""
    for at, raw in enumerate(table[:HEADER_SCAN_ROWS]):
        header = [str(h).strip() if h is not None else "" for h in raw]
        has_money = any(
            _index(header, names) is not None for names in (AMOUNT_HEADERS, WITHDRAW_HEADERS)
        ) or (
            _index(header, SIGNED_HEADERS) is not None and _index(header, KIND_HEADERS) is not None
        )
        if _index(header, DATE_HEADERS) is not None and has_money:
            return at
    return None


def read_statement(body: bytes, filename: str = "") -> tuple[list[dict[str, Any]], bool]:
    """파일 → (`bank.parse_rows`가 받는 줄, 출금 칸이 있었나). 못 읽으면 `BankFileError`."""
    if len(body) > MAX_BYTES:
        raise BankFileError(f"파일이 너무 큽니다({MAX_BYTES // 1_000_000}MB까지).")
    table = _table(body, filename)
    at = _header_row(table)
    if at is None:
        raise BankFileError(
            "머리줄에서 날짜(거래일자)와 입금액·출금액 칸을 찾지 못했습니다. "
            "은행 화면에서 '거래내역 조회 → 파일 저장(CSV·엑셀)'로 받은 파일인지 확인해 주세요."
        )
    header = [str(h).strip() if h is not None else "" for h in table[at]]
    di = _index(header, DATE_HEADERS)
    ai, oi = _index(header, AMOUNT_HEADERS), _index(header, WITHDRAW_HEADERS)
    si, ki = _index(header, SIGNED_HEADERS), _index(header, KIND_HEADERS)
    bi = _index(header, BALANCE_HEADERS)
    # 적요(거래 종류)와 기재내용(보낸·받는 쪽)이 따로 있는 은행이 많아 있는 칸을 모두 잇는다
    memo_cols = list(
        dict.fromkeys(i for n in MEMO_HEADERS if (i := _index(header, (n,))) is not None)
    )

    def cell(row: list[Any], i: int | None) -> Any:
        return row[i] if i is not None and i < len(row) else None

    rows: list[dict[str, Any]] = []
    for raw in table[at + 1 :]:
        day = _day(cell(raw, di))
        if day is None:
            continue  # 합계 줄·빈 줄
        amount = _won(cell(raw, ai)) or 0
        if amount <= 0 and oi is not None:
            out = _won(cell(raw, oi))
            amount = -abs(out) if out else 0
        if not amount and si is not None and ki is not None:
            signed = _won(cell(raw, si)) or 0
            kind = str(cell(raw, ki) or "")
            amount = -abs(signed) if "출" in kind else abs(signed) if "입" in kind else 0
        if not amount:
            continue
        balance = _won(cell(raw, bi))
        rows.append(
            {
                "date": day,
                "amount": amount,
                "memo": " ".join(
                    dict.fromkeys(m for i in memo_cols if (m := str(cell(raw, i) or "").strip()))
                )[:100],
                **({"balance": balance} if balance is not None else {}),
            }
        )
    if not rows:
        raise BankFileError(
            "거래 줄을 하나도 읽지 못했습니다. 날짜와 금액이 있는 줄이 있는지 확인해 주세요."
        )
    has_withdrawals = oi is not None or (si is not None and ki is not None)
    return rows, has_withdrawals
