"""재무·세무 규칙 근거, 법인세 참고 추정, 파일 자동화."""

from __future__ import annotations

import asyncio
from datetime import date
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from ax_contracts.schemas import ExecuteDecision, HoldReason
from finance_agent import exports, finance_api, tax
from finance_agent.books import Books
from finance_agent.handlers import reset_state, state
from finance_agent.main import app
from finance_agent.rules import applied, load_rules
from finance_agent.sources import MockSources

HUMAN = {"Authorization": "Bearer human:taeyoon"}


@pytest.fixture(autouse=True)
def _mock(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_TEMPLATE_MOCK", "1")
    reset_state(MockSources())


@pytest.fixture
def client() -> TestClient:
    return TestClient(app)


def _decision(status: str = "held") -> ExecuteDecision:
    return ExecuteDecision(
        execution_id="exe_1",
        grade="C",
        status=status,  # type: ignore[arg-type]
        review_id="rev_1",
        hold_reason=HoldReason.OVER_THRESHOLD if status == "held" else None,
        threshold_version=1,
    )


def _fake_execute(monkeypatch: pytest.MonkeyPatch, decision: ExecuteDecision) -> list[dict]:
    calls: list[dict] = []

    async def fake(action: str, params: dict, context: Any, evidence: Any) -> ExecuteDecision:
        calls.append({"action": action, "params": params, "key": context.idempotency_key})
        return decision

    monkeypatch.setattr(finance_api, "execute", fake)
    return calls


def test_every_rule_has_a_basis_and_unknown_rule_fails() -> None:
    book = load_rules()
    assert book.rules and all(r.basis and r.statement for r in book.rules)
    assert applied("REV-001", "수수료 1,000원")["detail"] == "수수료 1,000원"
    with pytest.raises(KeyError):
        applied("NOPE-1")


@pytest.mark.parametrize(
    ("base", "expected"),
    [
        (-1, 0),
        (0, 0),
        (100_000_000, 10_000_000),
        (200_000_000, 20_000_000),
        (300_000_000, 40_000_000),  # 2억까지 10% + 1억 20%
        (21_000_000_000, 4_200_000_000),  # 2천만 + 39.6억 + 2.2억
    ],
)
def test_income_tax_brackets(base: int, expected: int) -> None:
    assert tax.income_tax_amount(base) == expected


def test_outputs_carry_applied_rules(client: TestClient) -> None:
    for path, wanted in [
        ("/api/finance/closing?period=month", "REV-001"),
        ("/api/finance/statements?period=month", "PL-002"),
        ("/api/finance/tax/vat", "VAT-003"),
        ("/api/finance/tax/seller-report", "VAT-005"),
        ("/api/finance/settlements", "SET-001"),
        ("/api/finance/tax/income-estimate", "CIT-001"),
    ]:
        data = client.get(path).json()["data"]
        assert wanted in {r["rule_id"] for r in data["applied_rules"]}, path
        assert all(r["basis"] for r in data["applied_rules"])


def test_income_estimate_is_reference_only(client: TestClient) -> None:
    data = client.get("/api/finance/tax/income-estimate").json()["data"]
    assert data["status"] == "reference_only" and "세무조정" in data["not_included"]
    assert data["total"] == data["income_tax"] + data["local_income_tax"]


def test_income_estimate_is_empty_when_orders_are_missing() -> None:
    books = Books(source="shop-api", mock=False, unavailable=["orders"])
    data = tax.income_tax_estimate(books, 2026, date(2026, 11, 4))
    assert data["operating_result"] is None and data["total"] is None


def test_rules_are_read_from_a_replaceable_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from importlib import resources

    text = resources.files("finance_agent").joinpath("finance_rules.toml").read_text("utf-8")
    other = tmp_path / "rules.toml"
    other.write_text(text.replace("loss_streak_reject = 2", "loss_streak_reject = 9"), "utf-8")
    monkeypatch.setenv("FINANCE_RULES_PATH", str(other))
    load_rules.cache_clear()
    try:
        assert load_rules().brake.loss_streak_reject == 9
    finally:
        monkeypatch.delenv("FINANCE_RULES_PATH")
        load_rules.cache_clear()


# ---------------------------------------------------------------- 파일
def test_package_is_written_once_and_new_version_on_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FINANCE_EXPORT_DIR", str(tmp_path))
    store: dict[str, dict[str, Any]] = {}
    calls = {"n": 0}

    def fake_files(books: Any, month: str, last_day: date) -> dict[str, bytes]:
        return {f"a-{month}.csv": f"{calls['n']}".encode()}

    monkeypatch.setattr(exports, "build_files", fake_files)
    books = Books(source="shop-api", mock=False)
    first = exports.make_package(books, store, date(2026, 11, 4))
    assert first["package_id"] == "fin_2026-10" and first["version"] == 1
    assert (tmp_path / "fin_2026-10" / "a-2026-10.csv").read_bytes() == b"0"
    assert exports.make_package(books, store, date(2026, 11, 4))["version"] == 1
    calls["n"] = 1
    second = exports.make_package(books, store, date(2026, 11, 4))
    assert second["version"] == 2 and second["handoff"]["status"] == "not_requested"


def test_excel_timestamps_alone_do_not_make_a_new_version(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """실제 모드에서 엑셀 내부 시각 때문에 판이 늘고 승인 요청이 또 나간 버그."""
    monkeypatch.setenv("FINANCE_EXPORT_DIR", str(tmp_path))
    calls = {"n": 0}

    def fake_files(books: Any, month: str, last_day: date) -> dict[str, bytes]:
        calls["n"] += 1
        return {"a.csv": b"same", "a.xlsx": str(calls["n"]).encode()}

    monkeypatch.setattr(exports, "build_files", fake_files)
    store: dict[str, dict[str, Any]] = {}
    books = Books(source="shop-api", mock=False)
    exports.make_package(books, store, date(2026, 11, 4))
    assert exports.make_package(books, store, date(2026, 11, 4))["version"] == 1


def test_no_files_when_orders_are_missing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FINANCE_EXPORT_DIR", str(tmp_path))
    books = Books(source="shop-api", mock=False, unavailable=["orders"])
    result = exports.make_package(books, {}, date(2026, 11, 4))
    assert result["status"] == "skipped" and list(tmp_path.iterdir()) == []


def test_real_files_build_from_seed_books(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("FINANCE_EXPORT_DIR", str(tmp_path))

    async def run() -> dict[str, Any]:
        store: dict[str, dict[str, Any]] = {}
        books = await state().books()
        mid_quarter = exports.make_package(books, store, date(2026, 11, 4))
        quarter_end = exports.make_package(books, store, date(2026, 10, 4))
        return {"mid": mid_quarter, "end": quarter_end}

    made = asyncio.run(run())
    # 진행 중인 분기 자료는 넣지 않는다
    assert {f["name"] for f in made["mid"]["files"]} == {
        "income-statement-2026-10.csv",
        "income-statement-2026-10.xlsx",
    }
    assert {"vat-summary-2026-Q3.csv", "seller-sales-2026-Q3.xlsx"} <= {
        f["name"] for f in made["end"]["files"]
    }
    assert (tmp_path / "fin_2026-10" / "income-statement-2026-10.csv").is_file()


def test_read_file_only_serves_listed_untampered_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FINANCE_EXPORT_DIR", str(tmp_path))
    monkeypatch.setattr(exports, "build_files", lambda *a: {"a.csv": b"x"})
    store: dict[str, dict[str, Any]] = {}
    exports.make_package(Books(source="s", mock=False), store, date(2026, 11, 4))
    assert exports.read_file(store, "fin_2026-10", "a.csv") == b"x"
    assert exports.read_file(store, "fin_2026-10", "../secret.env") is None
    (tmp_path / "fin_2026-10" / "a.csv").write_bytes(b"changed")
    assert exports.read_file(store, "fin_2026-10", "a.csv") is None


def test_handoff_copies_after_approval(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    out, handoff = tmp_path / "out", tmp_path / "handoff"
    monkeypatch.setenv("FINANCE_EXPORT_DIR", str(out))
    monkeypatch.setenv("FINANCE_TAX_HANDOFF_DIR", str(handoff))
    monkeypatch.setattr(exports, "build_files", lambda *a: {"a.csv": b"x"})
    calls = _fake_execute(monkeypatch, _decision("held"))

    async def run() -> None:
        package = (await finance_api.run_export_cycle())["package"]
        assert calls[0]["action"] == "send_tax_package"
        assert package["handoff"]["status"] == "requested" and not handoff.exists()
        again = await finance_api.run_export_cycle()  # 같은 판은 다시 요청하지 않는다
        assert len(calls) == 1 and again["package"]["version"] == 1
        package["handoff"]["status"] = "approved"
        await finance_api.refresh_handoff(package)
        copied = handoff / f"{package['package_id']}-v1" / "a.csv"
        assert copied.read_bytes() == b"x"
        assert package["handoff"]["delivered_to"] == str(copied.parent)

    asyncio.run(run())


def test_handoff_marks_failure_when_gate_is_down(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FINANCE_EXPORT_DIR", str(tmp_path))
    monkeypatch.setattr(exports, "build_files", lambda *a: {"a.csv": b"x"})
    down = _decision("failed").model_copy(
        update={"execution_id": finance_api.UNAVAILABLE_EXECUTION}
    )
    calls = _fake_execute(monkeypatch, down)

    async def run() -> None:
        package = (await finance_api.run_export_cycle())["package"]
        assert package["handoff"]["status"] == "request_failed"
        await finance_api.run_export_cycle()  # 다음 회차에 다시 요청한다
        assert len(calls) == 2

    asyncio.run(run())


def test_export_api_requires_a_person(client: TestClient) -> None:
    assert client.post("/api/finance/exports/run").status_code == 401
    assert client.post("/api/finance/exports/fin_x/handoff", headers=HUMAN).status_code == 404
    assert client.get("/api/finance/exports/fin_x/files/a.csv").status_code == 404


# ---------------------------------------------------------------- 은행 폴더
def test_inbox_reads_korean_bank_csv_and_moves_files(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("FINANCE_BANK_INBOX", str(tmp_path))
    text = (
        "거래일자,적요,출금,입금액,잔액\n"
        '2026.10.05,PG정산,,"1,000","5,000"\n'
        "2026.10.06,이체,300,,4700\n"
    )
    (tmp_path / "a.csv").write_bytes(text.encode("cp949"))
    (tmp_path / "bad.csv").write_text("이름,나이\n가,1\n", "utf-8")
    rows: list[dict[str, Any]] = []
    log: list[dict[str, Any]] = []
    done = exports.scan_inbox(rows, log)
    assert {d["file"]: d["status"] for d in done} == {"a.csv": "imported", "bad.csv": "failed"}
    assert rows == [{"date": "2026-10-05", "amount": 1000, "memo": "PG정산", "balance": 5000}]
    assert len(list((tmp_path / "processed").iterdir())) == 1
    assert len(list((tmp_path / "failed").iterdir())) == 1
    assert not list(tmp_path.glob("*.csv"))

    # 같은 내역을 다시 넣어도 겹치지 않는다
    (tmp_path / "a2.csv").write_bytes(text.encode("cp949"))
    again = exports.scan_inbox(rows, log)
    assert again[0]["added"] == 0 and len(rows) == 1


def test_inbox_is_off_without_a_folder(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("FINANCE_BANK_INBOX", raising=False)
    assert exports.scan_inbox([], []) == []
