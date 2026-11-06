"""재무 파일 자동 만들기·보관, 은행 내역 폴더 읽기.

자동으로 하는 일 (사람 없이)
- 지난달 손익계산서, 분기가 끝났으면 부가세 집계와 판매자 명세, 전문가 확인 목록을
  `FINANCE_EXPORT_DIR`에 만든다
- `FINANCE_BANK_INBOX`에 넣은 은행 거래내역 CSV를 읽어 대조 자료에 더한다

세무사에게는 사람이 내려받아 전달한다. 회사마다 세무사와 전달 방법이 달라서 전달 기능을 두지 않는다.
하지 않는 일: 외부 전송, 은행·홈택스 접속, 신고·제출.
주문 자료를 못 받았으면 파일을 만들지 않는다(틀린 숫자 방지).
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import logging
import os
import zipfile
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from finance_agent import bank, statements, tax
from finance_agent import finance_config as fc
from finance_agent.books import Books
from finance_agent.rules import applied, load_rules, review_checklist_docx

logger = logging.getLogger("ax.finance.exports")

MAX_INBOX_BYTES = 5_000_000
# 은행마다 머리글이 달라 흔한 이름을 받는다
DATE_HEADERS = ("date", "거래일자", "거래일", "일자", "거래일시")
AMOUNT_HEADERS = ("amount", "입금액", "입금", "맡기신금액", "입금금액")
MEMO_HEADERS = ("memo", "적요", "내용", "거래내용", "기재내용")
BALANCE_HEADERS = ("balance", "잔액", "거래후잔액", "거래후 잔액")
WITHDRAW_HEADERS = ("withdrawal", "출금액", "출금", "찾으신금액", "출금금액")


def _dir(env: str) -> Path | None:
    raw = os.getenv(env, "").strip()
    return Path(raw) if raw else None


def export_dir() -> Path | None:
    return _dir("FINANCE_EXPORT_DIR")


def inbox_dir() -> Path | None:
    return _dir("FINANCE_BANK_INBOX")


# ---------------------------------------------------------------- 파일 만들기
def _last_month(today: date) -> tuple[str, date]:
    last_day = today.replace(day=1) - timedelta(days=1)
    return last_day.isoformat()[:7], last_day


def vat_csv(pack: dict[str, Any]) -> str:
    out = io.StringIO()
    w = csv.writer(out)
    w.writerow(["부가세 신고 보조 집계 (신고서가 아님)", pack["quarter"], pack["from"], pack["to"]])
    w.writerow(["구분", "항목", "공급가액", "세액"])
    for kind, rows in (("매출", pack["sales"]), ("매입", pack["purchases"])):
        for row in rows:
            w.writerow([kind, row.get("label"), row.get("supply"), row.get("vat")])
    w.writerow(["합계", "매출세액", "", pack["output_vat"]])
    w.writerow(["합계", "매입세액", "", pack["input_vat"]])
    w.writerow(["합계", "낼 세액 (참고)", "", pack["payable"]])
    for check in pack.get("checks", []):
        w.writerow(
            [
                "확인 필요",
                check if isinstance(check, str) else json.dumps(check, ensure_ascii=False),
            ]
        )
    return out.getvalue()


def build_files(
    books: Books,
    month: str,
    last_day: date,
    closings: dict[tuple[str, str], dict[str, Any]] | None = None,
    filings: dict[str, dict[str, Any]] | None = None,
    other_pnl: dict[str, dict[str, Any]] | None = None,
) -> dict[str, bytes]:
    """파일 이름 → 내용. 이름은 코드가 정한다.

    분기 자료는 분기가 끝난 달의 묶음에만 넣는다. 진행 중인 분기는 주문마다 숫자가 바뀌어
    묶음이 계속 새 판이 되고 승인 요청이 반복된다.
    """
    report = statements.income_statement(books, "month", last_day, closings, other_pnl)
    missing = list(books.unavailable)
    files = {
        f"income-statement-{month}.csv": statements.to_csv(report, missing).encode("utf-8-sig"),
        f"income-statement-{month}.xlsx": statements.to_xlsx(report, missing),
        "expert-review-checklist.docx": review_checklist_docx(),
    }
    if last_day.month % 3 == 0:
        filing = (filings or {}).get(tax.filing_key(last_day))  # 확정한 분기는 확정 숫자를 낸다
        pack = filing["pack"] if filing else tax.vat_pack(books, last_day)
        seller = filing["seller_report"] if filing else tax.seller_report(books, last_day)
        quarter = f"{last_day.year}-Q{last_day.month // 3}"  # 파일 이름은 영문만
        files |= {
            f"vat-summary-{quarter}.csv": vat_csv(pack).encode("utf-8-sig"),
            f"seller-sales-{quarter}.csv": tax.seller_report_csv(seller).encode("utf-8-sig"),
            f"seller-sales-{quarter}.xlsx": tax.seller_report_xlsx(seller),
        }
    return files


def make_package(
    books: Books,
    exports: dict[str, dict[str, Any]],
    today: date | None = None,
    closings: dict[tuple[str, str], dict[str, Any]] | None = None,
    filings: dict[str, dict[str, Any]] | None = None,
    other_pnl: dict[str, dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """지난달 묶음을 만든다. 내용이 같으면 그대로 두고, 달라지면 새 판으로 바꾼다."""
    today = today or fc.today()
    month, last_day = _last_month(today)
    package_id = f"fin_{month}"
    if "orders" in books.unavailable:
        return {
            "package_id": package_id,
            "status": "skipped",
            "reason": "주문 자료를 받지 못해 만들지 않았다",
        }
    root = export_dir()
    if root is None:
        return {
            "package_id": package_id,
            "status": "skipped",
            "reason": "FINANCE_EXPORT_DIR이 없다",
        }
    files = build_files(books, month, last_day, closings, filings, other_pnl)
    manifest = [
        {"name": name, "bytes": len(body), "sha256": hashlib.sha256(body).hexdigest()}
        for name, body in sorted(files.items())
    ]
    # 엑셀은 만들 때마다 내부 시각이 달라진다. 숫자가 같으면 같은 판으로 보려고 CSV만 비교한다
    # 확인 목록은 규칙 버전으로 비교한다. Word 파일도 만들 때마다 내부 시각이 달라진다
    same_content = [f for f in manifest if not f["name"].endswith((".xlsx", ".docx"))] + [
        load_rules().version
    ]
    signature = hashlib.sha256(json.dumps(same_content, sort_keys=True).encode()).hexdigest()
    current = exports.get(package_id)
    if current and current["signature"] == signature:
        return current
    folder = root / package_id
    folder.mkdir(parents=True, exist_ok=True)
    for name, body in files.items():
        (folder / name).write_bytes(body)
    version = (current or {}).get("version", 0) + 1
    package = {
        "package_id": package_id,
        "month": month,
        "version": version,
        "signature": signature,
        "files": manifest,
        "generated_at": fc.iso(fc.now()),
        "source": books.source,
        "mock": books.mock,
        "rules_version": load_rules().version,
        "applied_rules": [applied("TAX-001")],
    }
    exports[package_id] = package
    return package


def read_file(exports: dict[str, dict[str, Any]], package_id: str, name: str) -> bytes | None:
    """목록에 있는 파일만 읽는다. 받은 이름을 경로로 쓰지 않는다."""
    package = exports.get(package_id)
    root = export_dir()
    if package is None or root is None:
        return None
    entry = next((f for f in package["files"] if f["name"] == name), None)
    if entry is None:
        return None
    path = root / package_id / entry["name"]
    if not path.is_file():
        return None
    body = path.read_bytes()
    return body if hashlib.sha256(body).hexdigest() == entry["sha256"] else None


def bundle(exports: dict[str, dict[str, Any]], package_id: str) -> tuple[str, bytes] | None:
    """묶음 전체를 zip으로. 세무사에게 그대로 전달할 수 있다. 파일이 기록과 다르면 None."""
    package = exports.get(package_id)
    if package is None:
        return None
    out = io.BytesIO()
    with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED) as archive:
        for entry in package["files"]:
            body = read_file(exports, package_id, entry["name"])
            if body is None:
                return None
            archive.writestr(entry["name"], body)
    return f"{package_id}-v{package['version']}.zip", out.getvalue()


# ---------------------------------------------------------------- 은행 내역 폴더
def _pick(row: dict[str, str], names: tuple[str, ...]) -> str | None:
    for name in names:
        value = row.get(name)
        if value not in (None, ""):
            return value
    return None


def _won(text: str | None) -> int | None:
    if text is None:
        return None
    cleaned = text.replace(",", "").replace("원", "").strip()
    try:
        return int(cleaned)
    except ValueError:
        try:
            return int(float(cleaned))
        except ValueError:
            return None


def parse_csv(body: bytes) -> list[dict[str, Any]]:
    """CSV → `bank.parse_rows`가 받는 줄. 출금은 음수 금액으로 넣는다."""
    for encoding in ("utf-8-sig", "cp949"):
        try:
            text = body.decode(encoding)
            break
        except UnicodeDecodeError:
            continue
    else:
        raise ValueError("글자 인코딩을 읽지 못했다")
    reader = csv.DictReader(io.StringIO(text))
    headers = {h.strip() for h in (reader.fieldnames or []) if h}
    if not headers & set(DATE_HEADERS) or not headers & (
        set(AMOUNT_HEADERS) | set(WITHDRAW_HEADERS)
    ):
        raise ValueError("날짜와 입금액·출금액 칸을 찾지 못했다")
    rows = []
    for raw in reader:
        row = {(k or "").strip(): (v or "").strip() for k, v in raw.items() if isinstance(v, str)}
        day = _pick(row, DATE_HEADERS)
        amount = _won(_pick(row, AMOUNT_HEADERS))
        if not amount:
            out = _won(_pick(row, WITHDRAW_HEADERS))
            amount = -abs(out) if out else None
        if not day or not amount:
            continue
        rows.append(
            {
                "date": day[:10].replace(".", "-").replace("/", "-"),
                "amount": amount,
                "memo": _pick(row, MEMO_HEADERS) or "",
                "balance": _won(_pick(row, BALANCE_HEADERS)),
            }
        )
    return rows


def has_withdrawal_column(body: bytes) -> bool:
    """출금 열이 있는 내역인가. 있으면 그 기간은 출금 대조를 한다."""
    for encoding in ("utf-8-sig", "cp949"):
        try:
            first = body.decode(encoding).splitlines()[:1]
            break
        except UnicodeDecodeError:
            continue
    else:
        return False
    head = next(csv.reader(first), []) if first else []
    return bool({h.strip() for h in head} & set(WITHDRAW_HEADERS))


def scan_inbox(
    bank_rows: list[dict[str, Any]],
    imports: list[dict[str, Any]],
    out_periods: list[dict[str, str]] | None = None,
) -> list[dict[str, Any]]:
    """폴더의 CSV를 읽어 더하고 `processed/`·`failed/`로 옮긴다. 이번에 처리한 기록을 돌려준다."""
    folder = inbox_dir()
    if folder is None or not folder.is_dir():
        return []
    done: list[dict[str, Any]] = []
    for path in sorted(p for p in folder.iterdir() if p.is_file() and p.suffix.lower() == ".csv"):
        record: dict[str, Any] = {"file": path.name, "at": fc.iso(fc.now())}
        try:
            if path.stat().st_size > MAX_INBOX_BYTES:
                raise ValueError("파일이 너무 크다")
            body = path.read_bytes()
            rows, problems = bank.parse_rows(parse_csv(body))
            if not rows:
                raise ValueError("; ".join(problems[:3]) or "거래 줄이 없다")
            if out_periods is not None and has_withdrawal_column(body):
                bank.add_out_period(out_periods, rows)
            record |= {
                "status": "imported",
                "added": bank.merge_rows(bank_rows, rows),
                "rows": len(rows),
                "skipped": len(problems),
            }
            where = "processed"
        except (OSError, ValueError) as exc:
            record |= {"status": "failed", "reason": str(exc)[:200]}
            where = "failed"
        dest = folder / where
        dest.mkdir(exist_ok=True)
        path.replace(dest / f"{fc.now().strftime('%Y%m%dT%H%M%S')}-{path.name}")
        imports.append(record)
        done.append(record)
    del imports[:-100]
    return done
