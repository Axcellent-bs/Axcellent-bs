"""PG 정산 파일 올리기: 읽기 규칙, 올린 기간의 대사, 빼기, 저장."""

from __future__ import annotations

import dataclasses
from collections.abc import Iterator
from typing import Any

import pytest
from fastapi.testclient import TestClient

from ax_agent_template import events
from finance_agent import books as bk
from finance_agent import finance_config as fc
from finance_agent import handlers, persist, pg_file
from finance_agent import reconciliation as rc
from finance_agent.main import app
from finance_agent.sources import MockSources

HUMAN = {"Authorization": "Bearer human:taeyoon"}


@pytest.fixture(autouse=True)
def _mock_mode(monkeypatch: pytest.MonkeyPatch, fixed_now) -> Iterator[None]:
    monkeypatch.setenv("AGENT_TEMPLATE_MOCK", "1")
    monkeypatch.delenv("FINANCE_HUMAN_TOKENS", raising=False)
    monkeypatch.delenv("SAFETY_HUMAN_TOKENS", raising=False)
    monkeypatch.delenv("AX_SAFETY_URL", raising=False)
    monkeypatch.delenv("FINANCE_PG_INBOX", raising=False)
    events.reset_mock_bus()
    handlers.reset_state(MockSources())
    yield
    handlers.reset_state(MockSources())  # 올린 PG 자료가 다른 시험의 대사에 남지 않게


def _live_like() -> bk.Books:
    """시드 주문을 실제 장부처럼(`mock=False`). 올린 PG 자료는 실제 장부에만 쓴다."""
    return dataclasses.replace(bk.seed_books(), mock=False, pg_overrides={})


def _settled_day_payments() -> tuple[str, list[dict[str, Any]]]:
    """시드에서 정산이 끝났고 데모 불일치가 없는 판매일 하나와 그날 결제."""
    books = bk.seed_books()
    pg = bk.pg_settlements(books)
    by_day: dict[str, list[dict[str, Any]]] = {}
    for p in books.payments():
        if p["payment_id"] in books.pg_overrides:
            continue
        rec = pg.get(p["payment_id"])
        if rec and rec["settled"]:
            by_day.setdefault(rec["sold_date"], []).append(rec)
    day = max(d for d, rows in by_day.items() if len(rows) >= 3)
    return day, by_day[day]


def _csv(rows: list[dict[str, Any]], header: str = "") -> bytes:
    lines = ([header] if header else []) + ["결제번호,주문번호,결제금액,수수료,판매일,지급일"]
    for r in rows:
        lines.append(
            f'{r["payment_id"]},{r["order_id"]},"{r["amount"]:,}",{r["fee"]},'
            f"{r['sold_date']},{r['paid_out_date']}"
        )
    return "\n".join(lines).encode("utf-8")


def test_read_rows_header_names_and_problems() -> None:
    body = (
        "# 안내 줄\n"
        "payment_id,order_id,amount,fee,sold_date,paid_out_date\n"
        'pay_1,ord_1,"10,000",310,2026.10.20,2026-10-22\n'
        "pay_2,ord_2,abc,310,2026-10-20,2026-10-22\n"
        ",ord_3,1000,31,2026-10-20,\n"
    ).encode("cp949")
    rows, problems = pg_file.read_rows(body, "pg.csv")
    assert rows == [
        {
            "payment_id": "pay_1",
            "order_id": "ord_1",
            "amount": 10000,
            "fee": 310,
            "sold_date": "2026-10-20",
            "paid_out_date": "2026-10-22",
        }
    ]
    assert len(problems) == 2
    with pytest.raises(pg_file.PgFileError):
        pg_file.read_rows(b"a,b,c\n1,2,3\n", "pg.csv")
    with pytest.raises(pg_file.PgFileError):
        pg_file.read_rows(b"x", "pg.xlsx")


def test_uploaded_period_is_reconciled_against_the_file() -> None:
    """올린 기간은 파일과 맞춘다. 금액·수수료가 다르거나 빠진 결제가 예외로 나온다."""
    day, rows = _settled_day_payments()
    changed = [dict(r) for r in rows]
    changed[0]["amount"] -= 2000
    changed[0]["fee"] = fc.rate_amount(changed[0]["amount"], 0.031)
    changed[1]["fee"] = fc.rate_amount(changed[1]["amount"], 0.035)
    held = changed.pop(2)
    records = handlers.state().records
    record = pg_file.import_file(_csv(changed), "pg.csv", records.pg_rows, records.pg_periods)
    records.pg_version += 1
    assert record["period"] == {"from": day, "to": day}

    assert rc.reconcile(bk.seed_books(), None, {})["pg_coverage"]["uploaded"] == 0  # 시드는 안 섞음
    result = rc.reconcile(_live_like(), None, {})
    by = {i["payment_id"]: i for i in result["items"]}
    assert by[changed[0]["payment_id"]]["status"] == "mismatch"
    assert by[changed[1]["payment_id"]]["status"] == "fee_anomaly"
    assert by[held["payment_id"]]["status"] == "missing_in_pg"
    on_day = [i for i in result["items"] if i["paid_at"].startswith(day)]
    assert {i["pg_basis"] for i in on_day} == {"uploaded"}
    others = [i for i in result["items"] if not i["paid_at"].startswith(day)]
    assert others and {i["pg_basis"] for i in others} == {"generated"}
    cov = result["pg_coverage"]
    assert cov["uploaded"] == len(on_day) and cov["generated"] == len(others)


def test_upload_api_clear_and_meta() -> None:
    async def live() -> bk.Books:
        return _live_like()

    handlers.state().books_loader = live
    client = TestClient(app)
    day, rows = _settled_day_payments()
    meta = client.get("/api/finance/reconciliation").json()["meta"]
    assert meta["pg_source"] == "mock_toss_settlement"

    assert client.post("/api/finance/pg/file", content=_csv(rows)).status_code == 401
    bad = client.post(
        "/api/finance/pg/file", params={"name": "x.csv"}, content=b"a\n", headers=HUMAN
    )
    assert bad.status_code == 400
    up = client.post(
        "/api/finance/pg/file",
        params={"name": "pg-demo-scenario.csv"},
        content=_csv(rows, "# 시연 시나리오"),
        headers=HUMAN,
    )
    assert up.status_code == 200, up.text
    assert up.json()["data"]["rows"] == len(rows)
    recon = client.get("/api/finance/reconciliation", params={"status": "all"}).json()
    assert recon["meta"]["pg_source"] == "pg_file+mock_toss_settlement"
    assert recon["data"]["pg_coverage"]["uploaded"] == len(rows)
    listed = client.get("/api/finance/pg/imports").json()["data"]
    assert listed["imports"][0]["file"] == "pg-demo-scenario.csv"
    assert [i["status"] for i in listed["imports"]] == ["imported", "failed"]

    assert client.post("/api/finance/pg/clear", json={}, headers=HUMAN).status_code == 400
    cleared = client.post("/api/finance/pg/clear", json={"reason": "시연 끝"}, headers=HUMAN)
    assert cleared.status_code == 200 and cleared.json()["data"]["rows"] == len(rows)
    meta = client.get("/api/finance/reconciliation").json()["meta"]
    assert meta["pg_source"] == "mock_toss_settlement"
    assert client.get("/api/finance/pg/imports").json()["data"]["imports"][0]["by"] == "taeyoon"


def test_inbox_folder_is_read_and_moved(tmp_path, monkeypatch: pytest.MonkeyPatch) -> None:
    from finance_agent import finance_api

    _, rows = _settled_day_payments()
    monkeypatch.setenv("FINANCE_PG_INBOX", str(tmp_path))
    (tmp_path / "pg.csv").write_bytes(_csv(rows))
    (tmp_path / "broken.csv").write_bytes(b"nothing here")
    records = handlers.state().records
    done = finance_api.scan_pg_inbox(records)
    assert sorted(d["status"] for d in done) == ["failed", "imported"]
    assert len(records.pg_rows) == len(rows)
    assert len(list((tmp_path / "processed").iterdir())) == 1
    assert len(list((tmp_path / "failed").iterdir())) == 1
    assert not list(tmp_path.glob("*.csv"))


def test_uploaded_pg_survives_restart(tmp_path) -> None:
    _, rows = _settled_day_payments()
    s = handlers.state()
    pg_file.import_file(_csv(rows), "pg.csv", s.records.pg_rows, s.records.pg_periods)
    path = tmp_path / "state.json"
    persist.save(s, path)
    handlers.reset_state(MockSources())
    fresh = handlers.state()
    before = fresh.records.pg_version
    assert persist.load(fresh, path)
    assert fresh.records.pg_rows.keys() == {r["payment_id"] for r in rows}
    assert fresh.records.pg_periods == s.records.pg_periods
    assert fresh.records.pg_version > before  # 장부 계산을 다시 하게 한다
