"""은행 거래내역 파일 읽기(`bank_file.py`)와 화면에서 파일 그대로 올리기.

`POST /api/finance/bank/file`
"""

from __future__ import annotations

import io
from datetime import datetime

import pytest
from fastapi.testclient import TestClient
from openpyxl import Workbook

from ax_agent_template import events
from finance_agent import handlers
from finance_agent.bank_file import BankFileError, read_statement
from finance_agent.main import app
from finance_agent.sources import MockSources

HUMAN = {"Authorization": "Bearer human:taeyoon"}


@pytest.fixture(autouse=True)
def _mock_mode(monkeypatch: pytest.MonkeyPatch, fixed_now) -> None:
    monkeypatch.setenv("AGENT_TEMPLATE_MOCK", "1")
    monkeypatch.delenv("FINANCE_HUMAN_TOKENS", raising=False)
    monkeypatch.delenv("SAFETY_HUMAN_TOKENS", raising=False)
    monkeypatch.delenv("AX_SAFETY_URL", raising=False)
    events.reset_mock_bus()
    handlers.reset_state(MockSources())


# 국내 은행 CSV처럼: CP949, 맨 위 안내 줄, 찾으신금액·맡기신금액, 점 찍은 날짜와 시각, 합계 줄
KB_LIKE = (
    "거래내역 조회\n"
    "계좌번호,123-456-789\n"
    "조회기간,2026.10.01 ~ 2026.10.31\n"
    "\n"
    "거래일시,적요,기재내용,찾으신금액,맡기신금액,거래후잔액\n"
    '2026.10.20 09:10:11,타행이체,토스페이먼츠,0,"1,234,500","5,000,000"\n'
    "2026.10.21 14:00:00,인터넷뱅킹,책마루 정산,32400,0,4967600\n"
    "합계,,,32400,1234500,\n"
).encode("cp949")


def test_reads_a_korean_bank_csv_with_title_lines() -> None:
    rows, has_out = read_statement(KB_LIKE, "kb.csv")
    assert has_out
    assert rows == [
        {
            "date": "2026-10-20",
            "amount": 1234500,
            "memo": "타행이체 토스페이먼츠",
            "balance": 5000000,
        },
        {
            "date": "2026-10-21",
            "amount": -32400,
            "memo": "인터넷뱅킹 책마루 정산",
            "balance": 4967600,
        },
    ]


def test_reads_xlsx_with_parenthesized_headers_and_real_dates() -> None:
    book = Workbook()
    sheet = book.active
    sheet.append(["신한은행 거래내역"])
    sheet.append([])
    sheet.append(["거래일자", "입금(원)", "출금(원)", "내용", "잔액(원)"])
    sheet.append([datetime(2026, 10, 20, 9, 0), 50000, None, "PG 정산", 150000])
    sheet.append(["2026-10-21", None, "12,000", "카드대금", 138000])
    out = io.BytesIO()
    book.save(out)
    rows, has_out = read_statement(out.getvalue(), "shinhan.xlsx")
    assert has_out
    assert [(r["date"], r["amount"], r["memo"]) for r in rows] == [
        ("2026-10-20", 50000, "PG 정산"),
        ("2026-10-21", -12000, "카드대금"),
    ]


def test_reads_one_amount_column_with_a_kind_column() -> None:
    body = "날짜\t구분\t거래금액\t적요\n20261020\t입금\t7000\tA\n20261021\t출금\t3000\tB\n".encode()
    rows, has_out = read_statement(body, "x.txt")
    assert has_out and [r["amount"] for r in rows] == [7000, -3000]
    assert [r["date"] for r in rows] == ["2026-10-20", "2026-10-21"]


def test_deposit_only_file_does_not_start_outflow_matching() -> None:
    rows, has_out = read_statement("거래일자,입금액,적요\n2026-10-20,1000,a\n".encode(), "d.csv")
    assert rows and not has_out


@pytest.mark.parametrize(
    ("body", "name", "words"),
    [
        (b"\xd0\xcf\x11\xe0old", "old.xls", ".xlsx"),
        ("이름,값\n가,1\n".encode(), "x.csv", "머리줄"),
        ("거래일자,입금액\n합계,1000\n".encode(), "x.csv", "거래 줄"),
    ],
)
def test_unreadable_files_say_why(body: bytes, name: str, words: str) -> None:
    with pytest.raises(BankFileError, match=words.replace(".", r"\.")):
        read_statement(body, name)


def test_people_upload_the_bank_file_as_is() -> None:
    client = TestClient(app)
    url = "/api/finance/bank/file"
    assert client.post(url, params={"name": "kb.csv"}, content=KB_LIKE).status_code == 401
    response = client.post(url, params={"name": "kb.csv"}, content=KB_LIKE, headers=HUMAN)
    assert response.status_code == 200, response.text
    meta = response.json()["meta"]
    assert (meta["added"], meta["withdrawals"], meta["has_withdrawals"]) == (2, 1, True)
    assert meta["import"]["source"] == "upload" and meta["import"]["by"] == "taeyoon"
    # 같은 파일을 다시 올려도 한 번만 센다
    again = client.post(url, params={"name": "kb.csv"}, content=KB_LIKE, headers=HUMAN)
    assert again.json()["meta"]["added"] == 0
    bad = client.post(url, params={"name": "old.xls"}, content=b"\xd0\xcf\x11\xe0", headers=HUMAN)
    assert (bad.status_code, bad.json()["error"]["code"]) == (400, "invalid_file")
    # 올린 기록은 ⑤ 내보내기 화면의 은행 내역 기록에 함께 나온다(최근 것이 먼저)
    imports = client.get("/api/finance/exports").json()["data"]["bank_imports"]
    assert [(i["file"], i["status"], i["source"]) for i in imports[:3]] == [
        ("old.xls", "failed", "upload"),
        ("kb.csv", "imported", "upload"),
        ("kb.csv", "imported", "upload"),
    ]
    assert client.post(url, params={"name": "e.csv"}, content=b"", headers=HUMAN).status_code == 400


@pytest.mark.parametrize(
    ("name", "body"),
    [
        ("probe.bin", b"\x00" * 2_000_000),  # NUL로 찬 2MB: csv 한 칸 한도 초과였다(500)
        ("kb.csv", b"\x00" * 2_000_000),
        ("kb.csv", b"A" * 300_000),  # 줄바꿈 없는 긴 한 줄
        ("probe", b"\x00" * 50),  # 확장자 없음
        ("kb.csv", b"\xb0\xa1\xff\xfe\xfa\xfb"),  # 깨진 CP949
        ("kb.csv", "거래일자,입금\n".encode("utf-16")),  # UTF-16
        ("kb.csv", b"foo,bar\n1,2\n"),  # 알 수 없는 열 이름
        ("kb.xlsx", b"PK\x00\x00junk"),  # 깨진 xlsx
    ],
)
def test_unreadable_files_are_4xx_validation_errors(name: str, body: bytes) -> None:
    client = TestClient(app)
    response = client.post(
        "/api/finance/bank/file", params={"name": name}, content=body, headers=HUMAN
    )
    assert response.status_code == 400, response.text
    error = response.json()["error"]
    assert error["code"] == "invalid_file" and error["message"]


def test_empty_upload_is_invalid_request_and_good_file_still_passes() -> None:
    client = TestClient(app)
    url = "/api/finance/bank/file"
    empty = client.post(url, params={"name": "kb.csv"}, content=b"", headers=HUMAN)
    assert (empty.status_code, empty.json()["error"]["code"]) == (400, "invalid_request")
    big = client.post(url, params={"name": "kb.csv"}, content=b"x" * 5_000_001, headers=HUMAN)
    assert big.status_code == 400 and "5MB" in big.json()["error"]["message"]
    good = client.post(url, params={"name": "kb.csv"}, content=KB_LIKE, headers=HUMAN)
    assert good.status_code == 200, good.text
    with pytest.raises(BankFileError, match="CSV로 읽을 수 없는"):
        read_statement(b"\x00" * 2_000_000, "probe.bin")
