"""임시 예시 값 (11/26): 은행 입금 대조·팀별 지출. 실제 칸과 섞지 않고 `example` 칸에만 붙는다."""

from __future__ import annotations

from datetime import date

import pytest
from fastapi.testclient import TestClient

from finance_agent import bank, example_data, handlers, team_budget
from finance_agent.books import pg_settlements
from finance_agent.main import app
from finance_agent.sources import MockSources

TODAY = date(2026, 10, 23)
HUMAN = {"Authorization": "Bearer human:taeyoon"}


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch, fixed_now) -> None:
    monkeypatch.setenv("AGENT_TEMPLATE_MOCK", "1")
    monkeypatch.delenv("FINANCE_HUMAN_TOKENS", raising=False)
    monkeypatch.delenv("SAFETY_HUMAN_TOKENS", raising=False)
    monkeypatch.delenv(example_data.ENV, raising=False)
    handlers.reset_state(MockSources())


def _books(kit):
    orders = [
        kit.order("ord_1", kit.at("2026-10-12"), [("sel_a", "book", 1, 10000)]),
        kit.order("ord_2", kit.at("2026-10-13"), [("sel_a", "book", 1, 20000)]),
    ]
    return kit.books(orders=orders)


def test_bank_example_copies_the_pg_payouts_and_says_it_is_an_example(kit) -> None:
    books = _books(kit)
    ex = example_data.bank_example(books, TODAY)
    assert ex is not None and "실제 입금이 아니에요" in ex["note"]
    paid = {
        str(r["paid_out_date"])[:10]: r["pay_out_amount"]
        for r in pg_settlements(books, TODAY).values()
        if r.get("settled")
    }
    for d in ex["days"]:
        assert d["expected"] == d["deposited"] and d["status"] == "matched"
        assert paid[d["date"]] <= d["expected"]
    assert ex["counts"] == {"matched": len(ex["days"])}
    # 실제 대조 함수는 그대로: 올린 거래내역이 없으면 판정하지 않는다
    assert bank.match(books, [], TODAY)["uploaded"] is False


def test_bank_example_is_none_without_payouts(kit) -> None:
    assert example_data.bank_example(kit.books(orders=[]), TODAY) is None


def test_bank_endpoint_adds_the_example_only_before_an_upload() -> None:
    client = TestClient(app)
    before = client.get("/api/finance/bank").json()["data"]
    assert before["uploaded"] is False and "example" in before
    body = {"rows": [{"date": "2026-10-20", "amount": 1000, "memo": "토스"}]}
    client.post("/api/finance/bank/statement", json=body, headers=HUMAN)
    after = client.get("/api/finance/bank").json()["data"]
    assert after["uploaded"] is True and "example" not in after


def test_example_data_can_be_turned_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv(example_data.ENV, "0")
    assert example_data.enabled() is False
    assert "example" not in TestClient(app).get("/api/finance/bank").json()["data"]
    got = team_budget.report("2026-11", {"개발팀": 3}, None, 100_000, None)
    assert got["example_spend"] is None and got["actual_spend"] is None


def test_team_spend_example_is_70_to_105_percent_and_stable() -> None:
    rows = [{"team": "개발팀", "budget": 300_000}, {"team": "마케팅팀", "budget": 0}]
    a = example_data.team_spend_example("2026-11", rows)
    assert a is not None and a == example_data.team_spend_example("2026-11", rows)
    dev, mkt = a["rows"]
    assert 70 <= dev["rate_pct"] <= 105 and dev["spend"] % 1000 == 0
    assert 0 < dev["spend"] <= dev["budget"] * 105 // 100 and mkt["spend"] == 0
    assert a["total"] == dev["spend"] and "실제 지출이 아니에요" in a["note"]
    assert example_data.team_spend_example("2026-11", []) is None


def test_team_budget_report_keeps_actual_spend_empty_and_adds_the_example() -> None:
    got = team_budget.report("2026-11", {"개발팀": 3}, {"개발팀": 2}, 100_000, None)
    assert got["actual_spend"] is None  # 실제 칸은 그대로 비운다
    assert got["example_spend"]["rows"][0]["budget"] == 300_000
    none = team_budget.report("2026-11", None, None, 100_000, None)
    assert none["status"] == "unavailable" and none["example_spend"] is None
