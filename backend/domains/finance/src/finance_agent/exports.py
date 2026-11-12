"""재무 파일 자동 만들기·보관, 은행 내역 폴더 읽기.

자동으로 하는 일 (사람 없이)
- 지난달 손익계산서, 분기가 끝났으면 부가세 집계와 판매자 명세, 전문가 확인 목록을
  `FINANCE_EXPORT_DIR`에 만든다
- `FINANCE_BANK_INBOX`에 넣은 은행 거래내역 파일(CSV·xlsx)을 읽어 대조 자료에 더한다.
  화면에서 올린 파일도 같은 방법(`import_statement`, `bank_file.py`)으로 읽는다

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
import uuid
import zipfile
from datetime import date, timedelta
from pathlib import Path
from typing import Any

from finance_agent import bank, bank_file, statements, tax
from finance_agent import finance_config as fc
from finance_agent.books import Books
from finance_agent.rules import applied, load_rules, review_checklist_docx

logger = logging.getLogger("ax.finance.exports")

MAX_INBOX_BYTES = bank_file.MAX_BYTES
INBOX_SUFFIXES = (".csv", ".txt", ".xlsx", ".xls")  # .xls는 읽지 못한다고 failed/로 옮겨 알린다


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


VAT_DRAFT_NOTE = "확정 전 숫자(부가세 집계 미확정)"


def csv_bytes(text: str) -> bytes:
    """CSV 글을 UTF-8 바이트로. 엑셀에서 한글이 깨지지 않게 BOM을 꼭 하나만 붙인다.

    `statements.to_csv`·`tax.seller_report_csv`는 이미 BOM을 붙여 돌려주므로 다시 붙이지 않는다.
    """
    return ("\ufeff" + text.lstrip("\ufeff")).encode("utf-8")


def with_note(text: str, note: str | None) -> str:
    """CSV 맨 위에 한 줄 표시를 붙인다 (BOM은 맨 앞에 둔다)."""
    if not note:
        return text
    return "\ufeff" + note + "\r\n" + text.lstrip("\ufeff")


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
        f"income-statement-{month}.csv": csv_bytes(
            statements.to_csv(report, missing, mark_draft=True)
        ),
        f"income-statement-{month}.xlsx": statements.to_xlsx(report, missing, mark_draft=True),
        "expert-review-checklist.docx": review_checklist_docx(),
    }
    if last_day.month % 3 == 0:
        filing = (filings or {}).get(tax.filing_key(last_day))  # 확정한 분기는 확정 숫자를 낸다
        pack = filing["pack"] if filing else tax.vat_pack(books, last_day)
        seller = filing["seller_report"] if filing else tax.seller_report(books, last_day)
        quarter = f"{last_day.year}-Q{last_day.month // 3}"  # 파일 이름은 영문만
        note = None if filing else VAT_DRAFT_NOTE  # 분기 숫자는 부가세 확정(`vat_filings`)이 기준
        files |= {
            f"vat-summary-{quarter}.csv": csv_bytes(with_note(vat_csv(pack), note)),
            f"seller-sales-{quarter}.csv": csv_bytes(
                with_note(tax.seller_report_csv(seller), note)
            ),
            f"seller-sales-{quarter}.xlsx": tax.seller_report_xlsx(seller, note),
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
    # 그 달 결산을 확정했나. 아니면 파일 맨 위에 "확정 전 숫자(결산 미확정)"가 붙는다
    closing_confirmed = statements.frozen_column(closings, last_day.replace(day=1)) is not None
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
        "closing_confirmed": closing_confirmed,
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
def import_statement(
    body: bytes,
    filename: str,
    bank_rows: list[dict[str, Any]],
    out_periods: list[dict[str, str]] | None,
) -> dict[str, Any]:
    """은행 거래내역 파일 하나를 읽어 대조 자료에 더한다. 기록(읽은 줄·더한 줄)을 돌려준다.

    화면에서 올린 파일과 폴더에 넣은 파일이 같이 쓴다. 못 읽으면 `BankFileError`.
    """
    raw, has_withdrawals = bank_file.read_statement(body, filename)
    rows, problems = bank.parse_rows(raw)
    if not rows:
        raise bank_file.BankFileError("; ".join(problems[:3]) or "거래 줄이 없습니다.")
    import_id = new_import_id()
    rows = [r | {"import_id": import_id} for r in rows]  # 이 기록으로 더한 줄 (빼기용)
    if out_periods is not None and has_withdrawals:
        bank.add_out_period(out_periods, rows, import_id)
    return {
        "status": "imported",
        "import_id": import_id,
        "added": bank.merge_rows(bank_rows, rows),
        "rows": len(rows),
        "withdrawals": sum(1 for r in rows if r["amount"] < 0),
        "has_withdrawals": has_withdrawals,
        "skipped": len(problems),
    }


def new_import_id() -> str:
    return f"bimp_{uuid.uuid4().hex[:12]}"


class RemoveError(Exception):
    """은행 내역 기록을 뺄 수 없다. `code`는 API 오류 코드다."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


LEGACY_PREFIX = "bimp_legacy_"


def give_legacy_ids(imports: list[dict[str, Any]]) -> None:
    """id가 생기기 전에 읽은 기록에 id를 붙인다(읽을 때마다 같다). 그때 더한 줄에는 id가 없다."""
    for record in imports:
        if record.get("status") == "imported" and not record.get("import_id"):
            raw = f"{record.get('file')}|{record.get('at')}|{record.get('source')}"
            record["import_id"] = LEGACY_PREFIX + hashlib.sha1(raw.encode("utf-8")).hexdigest()[:10]


def remove_import(
    imports: list[dict[str, Any]],
    bank_rows: list[dict[str, Any]],
    out_periods: list[dict[str, str]],
    import_id: str,
    by: str,
    reason: str,
    *,
    by_label: str | None = None,
) -> dict[str, Any]:
    """은행 내역 기록 하나와 그 기록으로 더한 줄·출금 대조 기간을 뺀다. 뺀 기록을 돌려준다.

    원래 기록은 지우지 않고 `removed`(누가·언제·이유)를 붙이고, 빼기 기록을 따로 하나 더 남긴다.
    id가 생기기 전의 예전 기록은 어떤 줄을 더했는지 모른다. 예전 기록이 하나뿐일 때만 id 없는
    줄을 그 기록 것으로 보고 빼고, 여럿이면 `legacy_ambiguous`로 거절한다.
    """
    target = next(
        (r for r in imports if r.get("import_id") == import_id and r.get("status") == "imported"),
        None,
    )
    if target is None:
        raise RemoveError("not_found", "그 은행 내역 기록이 없습니다.")
    if target.get("removed"):
        raise RemoveError("already_removed", "이미 뺀 기록입니다.")
    if import_id.startswith(LEGACY_PREFIX):
        legacy = [
            r
            for r in imports
            if str(r.get("import_id", "")).startswith(LEGACY_PREFIX)
            and r.get("status") == "imported"
            and not r.get("removed")
        ]
        if len(legacy) != 1:
            raise RemoveError(
                "legacy_ambiguous",
                "예전에 읽은 기록이 여럿이라 이 기록이 더한 줄을 가려낼 수 없습니다.",
            )

        def mine(item: dict[str, Any]) -> bool:
            return not item.get("import_id")
    else:

        def mine(item: dict[str, Any]) -> bool:
            return item.get("import_id") == import_id

    kept = [r for r in bank_rows if not mine(r)]
    kept_periods = [p for p in out_periods if not mine(p)]
    removed_rows = len(bank_rows) - len(kept)
    removed_periods = len(out_periods) - len(kept_periods)
    bank_rows[:] = kept
    out_periods[:] = kept_periods
    at = fc.iso(fc.now())
    label = {"by_label": by_label} if by_label else {}  # 공용 토큰일 때 누가 했나 (human.py)
    target["removed"] = {"by": by, **label, "at": at, "reason": reason, "rows": removed_rows}
    record = {
        "file": target.get("file"),
        "at": at,
        "source": "remove",
        "by": by,
        **label,
        "status": "removed",
        "import_id": import_id,
        "rows": removed_rows,
        "periods": removed_periods,
        "reason": reason,
    }
    imports.append(record)
    del imports[:-100]
    return record


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
    for path in sorted(
        p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in INBOX_SUFFIXES
    ):
        record: dict[str, Any] = {"file": path.name, "at": fc.iso(fc.now()), "source": "folder"}
        try:
            if path.stat().st_size > MAX_INBOX_BYTES:
                raise ValueError("파일이 너무 크다")
            record |= import_statement(path.read_bytes(), path.name, bank_rows, out_periods)
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
