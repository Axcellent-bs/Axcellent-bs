"""팀 예산: 팀 인원 × 인당 월 단가, 지난달 대비 증감, 재무 담당 확정 (2026-11-02 합의)."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient

from ax_contracts.schemas import RequestMeta, TaskStatus, TeamHeadcountOutput
from finance_agent import finance_config as fc
from finance_agent import handlers, sources, team_budget
from finance_agent.main import app
from finance_agent.sources import LiveSources, MockSources

HUMAN = {"Authorization": "Bearer human:taeyoon"}


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch, fixed_now) -> None:
    monkeypatch.setenv("AGENT_TEMPLATE_MOCK", "1")
    monkeypatch.delenv("FINANCE_HUMAN_TOKENS", raising=False)
    monkeypatch.delenv("SAFETY_HUMAN_TOKENS", raising=False)
    handlers.reset_state(MockSources())


def _per_head(monkeypatch: pytest.MonkeyPatch, amount: int) -> None:
    cfg = fc.load_config()
    tb = cfg.team_budget.model_copy(update={"per_head_monthly": amount})
    new = cfg.model_copy(update={"team_budget": tb})
    monkeypatch.setattr(fc, "load_config", lambda: new)


def _todo(client: TestClient) -> list[dict[str, Any]]:
    todo = client.get("/api/finance/overview").json()["data"]["todo"]
    return [t for t in todo if t["kind"] == "team_budget"]


def test_budget_is_headcount_times_monthly_rate_and_a_transfer_keeps_the_total() -> None:
    """멘토링 예시: A 5명·B 3명 × 10만원 → 50만·30만, 1명 A→B 발령이면 40만·40만, 합계 그대로."""
    r = team_budget.report("2026-12", {"A팀": 4, "B팀": 4}, {"A팀": 5, "B팀": 3}, 100000, None)
    rows = {x["team"]: x for x in r["rows"]}
    assert (rows["A팀"]["prev_budget"], rows["B팀"]["prev_budget"]) == (500000, 300000)
    assert (rows["A팀"]["budget"], rows["B팀"]["budget"]) == (400000, 400000)
    assert (rows["A팀"]["change"], rows["B팀"]["change"]) == (-100000, 100000)
    assert (r["total"], r["prev_total"], r["change_total"]) == (800000, 800000, 0)
    assert r["status"] == "pending" and r["actual_spend"] is None  # 실제 지출은 아직 없음


def test_unknown_headcount_is_not_zero_won() -> None:
    r = team_budget.report("2026-11", None, None, 100000, None)
    assert r["status"] == "unavailable"
    assert (r["total"], r["rows"], r["signature"]) == (None, [], None)


def test_month_helpers() -> None:
    assert team_budget.shift_month("2026-01", -1) == "2025-12"
    assert team_budget.shift_month("2026-12", 1) == "2027-01"
    with pytest.raises(ValueError):
        team_budget.parse_month("2026-13", fc.today())


def test_api_shows_next_month_transfer_from_the_hr_headcount() -> None:
    """목 시드: 2026-12-01 발령으로 개발팀 1명 → 기획팀. 그 달 예산에 ±10만원으로 보인다."""
    client = TestClient(app)
    body = client.get("/api/finance/team-budget?month=2026-12").json()
    d = body["data"]
    rows = {r["team"]: r for r in d["rows"]}
    assert (rows["개발팀"]["change"], rows["기획팀"]["change"]) == (-100000, 100000)
    assert d["change_total"] == 0 and d["total"] == d["headcount_total"] * 100000
    assert d["status"] == "pending" and body["meta"]["unavailable"] == []
    assert client.get("/api/finance/team-budget?month=2026-1").status_code == 400


def test_only_a_person_approves_and_only_the_numbers_they_saw() -> None:
    client = TestClient(app)
    d = client.get("/api/finance/team-budget?month=2026-11").json()["data"]
    url = "/api/finance/team-budget/2026-11/approve"
    assert client.post(url, json={"signature": d["signature"]}).status_code == 401
    assert client.post(url, json={"signature": "old"}, headers=HUMAN).status_code == 409
    assert "2026-11" not in handlers.state().records.team_budgets  # 바뀐 숫자는 확정하지 않는다

    ok = client.post(url, json={"signature": d["signature"], "note": "확인"}, headers=HUMAN)
    assert ok.status_code == 200
    data = ok.json()["data"]
    assert data["status"] == "approved"
    assert (data["approved"]["by"], data["approved"]["total"]) == ("taeyoon", d["total"])
    assert [t["action"]["month"] for t in _todo(client)] == ["2026-10"]  # 확정한 달은 빠진다


def test_the_overview_asks_for_this_and_next_month() -> None:
    todo = _todo(TestClient(app))
    assert [t["action"]["month"] for t in todo] == ["2026-10", "2026-11"]  # 오늘 2026-10-23
    assert all(t["action"]["type"] == "team_budget_approve" and t["link"] == "policy" for t in todo)


def test_a_rate_change_after_approval_asks_again(monkeypatch: pytest.MonkeyPatch) -> None:
    client = TestClient(app)
    d = client.get("/api/finance/team-budget?month=2026-11").json()["data"]
    client.post(
        "/api/finance/team-budget/2026-11/approve",
        json={"signature": d["signature"]},
        headers=HUMAN,
    )
    _per_head(monkeypatch, 120000)
    again = client.get("/api/finance/team-budget?month=2026-11").json()["data"]
    assert again["status"] == "changed"
    assert again["approved"]["per_head_monthly"] == 100000  # 확정 당시 숫자는 그대로 남는다
    todo = next(t for t in _todo(client) if t["action"]["month"] == "2026-11")
    assert "다시 확인" in todo["title"]


class _NoHr(MockSources):
    async def team_headcount(self, meta: RequestMeta, month: str) -> TeamHeadcountOutput | None:
        return None


def test_without_hr_numbers_nothing_is_invented_or_approved() -> None:
    handlers.reset_state(_NoHr())
    client = TestClient(app)
    body = client.get("/api/finance/team-budget").json()
    assert body["data"]["status"] == "unavailable" and body["data"]["total"] is None
    assert body["meta"]["unavailable"] == ["team_headcount"]
    post = client.post(
        "/api/finance/team-budget/2026-10/approve", json={"signature": "x"}, headers=HUMAN
    )
    assert post.status_code == 503
    assert _todo(client) == []


def _meta() -> RequestMeta:
    return RequestMeta(
        context_id="ctx_t", token="t", deadline=datetime.now(UTC) + timedelta(seconds=10)
    )


def test_live_does_not_call_hr_until_the_skill_has_a_home(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """인사가 skill을 등록하기 전에는 부르지 않는다 (사람 할 일함으로 넘기지 않게)."""

    async def boom(*a: Any, **k: Any) -> Any:
        raise AssertionError("부르면 안 됨")

    monkeypatch.setattr(sources, "call_agent", boom)
    monkeypatch.setattr(sources, "route_target", lambda target, skill: target)
    assert asyncio.run(LiveSources().team_headcount(_meta(), "2026-11")) is None


def test_live_reads_the_contract_and_caches(monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[str] = []
    replies: list[Any] = [
        {
            "month": "2026-11",
            "rows": [{"team": "개발팀", "headcount": 3}],
            "collected_at": "2026-11-01T00:00:00Z",
        }
    ]

    async def fake(target: str, req: Any) -> Any:
        calls.append(req.skill)
        return SimpleNamespace(status=TaskStatus.COMPLETED, output=replies[0])

    monkeypatch.setattr(sources, "call_agent", fake)
    monkeypatch.setattr(sources, "route_target", lambda target, skill: "handover")
    live = LiveSources()
    out = asyncio.run(live.team_headcount(_meta(), "2026-11"))
    assert out is not None and [(r.team, r.headcount) for r in out.rows] == [("개발팀", 3)]
    asyncio.run(live.team_headcount(_meta(), "2026-11"))
    assert calls == ["team_headcount"]  # 같은 달은 다시 묻지 않는다

    replies[0] = {
        "month": "2026-12",
        "rows": [{"team": "개발팀", "headcount": 3, "name": "x"}],
        "collected_at": "2026-12-01T00:00:00Z",
    }
    assert asyncio.run(live.team_headcount(_meta(), "2026-12")) is None  # 계약과 다르면 받지 않는다


class _ExampleHr(MockSources):
    """인사가 가상 명부(예시) 인원을 준다 (실제 명부·발령 기록이 생기기 전)."""

    async def team_headcount(self, meta: RequestMeta, month: str) -> TeamHeadcountOutput | None:
        out = await super().team_headcount(meta, month)
        return None if out is None else out.model_copy(update={"example": True})


def test_example_headcount_is_shown_but_never_approved() -> None:
    handlers.reset_state(_ExampleHr())
    client = TestClient(app)
    d = client.get("/api/finance/team-budget?month=2026-11").json()["data"]
    assert d["example"] is True and d["total"] == d["headcount_total"] * 100000
    post = client.post(
        "/api/finance/team-budget/2026-11/approve",
        json={"signature": d["signature"]},
        headers=HUMAN,
    )
    assert post.status_code == 409 and post.json()["error"]["code"] == "example_headcount"
    assert "2026-11" not in handlers.state().records.team_budgets
    assert _todo(client) == []  # 예시 인원으로 사람에게 확정을 부탁하지 않는다
