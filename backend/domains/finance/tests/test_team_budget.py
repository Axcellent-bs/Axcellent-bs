"""팀 예산: 팀 인원 × 인당 월 단가, 지난달 대비 증감 (2026-11-02 합의).

확정은 인사 AI(사람)가 하고 재무는 `record_team_budget`으로 기록만 한다 (11/13).
"""

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


def _a2a(client: TestClient, token: str, month: str, signature: str, note: str | None) -> dict:
    body = {
        "meta": {
            "context_id": f"ctx_team_budget_{month}",
            "token": token,
            "path": [],
            "deadline": (datetime.now(UTC) + timedelta(seconds=30)).isoformat(),
        },
        "skill": "record_team_budget",
        "input": {"month": month, "signature": signature, "confirmed_by": "준하"}
        | ({"note": note} if note else {}),
    }
    res = client.post("/a2a", json=body)
    assert res.status_code == 200
    return res.json()


def _hr_confirm(client: TestClient, month: str, signature: str, note: str | None = None) -> dict:
    """인사 AI가 사람이 확정한 팀 예산을 재무에 보낸다 (A2A `record_team_budget`)."""
    return _a2a(client, "mock:hr", month, signature, note)


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


def test_only_hr_confirms_and_only_the_numbers_they_saw() -> None:
    """확정은 인사가 한다. 재무 화면의 예전 확정 길은 막혀 있고, 인사가 본 숫자만 기록한다."""
    client = TestClient(app)
    d = client.get("/api/finance/team-budget?month=2026-11").json()["data"]
    url = "/api/finance/team-budget/2026-11/approve"
    old = client.post(url, json={"signature": d["signature"]}, headers=HUMAN)
    assert old.status_code == 403 and old.json()["error"]["code"] == "decided_by_hr"
    stale = _hr_confirm(client, "2026-11", "old")
    assert (stale["status"], stale["outcome"]) == ("failed", "stale")
    assert "2026-11" not in handlers.state().records.team_budgets  # 바뀐 숫자는 기록하지 않는다

    ok = _hr_confirm(client, "2026-11", d["signature"], note="확인")
    assert (ok["status"], ok["outcome"]) == ("completed", "recorded")
    assert ok["output"]["total"] == d["total"]
    data = client.get("/api/finance/team-budget?month=2026-11").json()["data"]
    assert data["status"] == "approved"
    assert (data["approved"]["by"], data["approved"]["total"]) == ("준하", d["total"])
    assert handlers.state().records.team_budgets["2026-11"]["decided_by"] == "hr"
    assert _hr_confirm(client, "2026-11", d["signature"])["outcome"] == "already_recorded"


def test_only_hr_may_send_the_confirmation() -> None:
    client = TestClient(app)
    d = client.get("/api/finance/team-budget?month=2026-11").json()["data"]
    res = _a2a(client, "mock:marketing", "2026-11", d["signature"], None)
    assert res["status"] != "completed"
    assert "2026-11" not in handlers.state().records.team_budgets


def test_the_overview_does_not_ask_finance_to_confirm() -> None:
    """팀 예산 확정은 인사 일이라 재무 할 일에 올리지 않는다 (11/13). ④ 탭에서 상태만 본다."""
    client = TestClient(app)
    assert _todo(client) == []
    for month in ("2026-10", "2026-11"):  # 이번 달·다음 달은 아직 확정 전으로 보인다
        data = client.get(f"/api/finance/team-budget?month={month}").json()["data"]
        assert data["status"] == "pending"


def test_a_rate_change_after_approval_asks_again(monkeypatch: pytest.MonkeyPatch) -> None:
    client = TestClient(app)
    d = client.get("/api/finance/team-budget?month=2026-11").json()["data"]
    assert _hr_confirm(client, "2026-11", d["signature"])["outcome"] == "recorded"
    _per_head(monkeypatch, 120000)
    again = client.get("/api/finance/team-budget?month=2026-11").json()["data"]
    assert again["status"] == "changed"
    assert again["approved"]["per_head_monthly"] == 100000  # 확정 당시 숫자는 그대로 남는다


class _NoHr(MockSources):
    async def team_headcount(self, meta: RequestMeta, month: str) -> TeamHeadcountOutput | None:
        return None


def test_without_hr_numbers_nothing_is_invented_or_approved() -> None:
    handlers.reset_state(_NoHr())
    client = TestClient(app)
    body = client.get("/api/finance/team-budget").json()
    assert body["data"]["status"] == "unavailable" and body["data"]["total"] is None
    assert body["meta"]["unavailable"] == ["team_headcount"]
    assert _hr_confirm(client, "2026-10", "x")["outcome"] == "team_headcount_unavailable"
    assert "2026-10" not in handlers.state().records.team_budgets


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
    post = _hr_confirm(client, "2026-11", d["signature"])
    assert (post["status"], post["outcome"]) == ("failed", "example_headcount")
    assert "2026-11" not in handlers.state().records.team_budgets


def _hr_reply(month: str, heads: int, *, example: bool = False) -> dict[str, Any]:
    return {
        "month": month,
        "rows": [{"team": "개발팀", "headcount": heads}],
        "basis": f"확정된 발령 기록 1건 기준, {month}-01 소속",
        "example": example,
        "collected_at": "2026-11-01T00:00:00Z",
    }


def _live_hr(monkeypatch: pytest.MonkeyPatch, heads: dict[str, Any]) -> None:
    """인사 `team_headcount` 응답 흉내. 달에 값이 없으면 `NO_HEADCOUNT_RECORD` 같은 실패다."""

    async def fake(target: str, req: Any) -> Any:
        reply = heads.get(req.input["month"])
        if reply is None:
            return SimpleNamespace(status=TaskStatus.FAILED, output=None)
        return SimpleNamespace(status=TaskStatus.COMPLETED, output=reply)

    monkeypatch.setattr(sources, "call_agent", fake)
    monkeypatch.setattr(sources, "route_target", lambda target, skill: "decisions")
    handlers.reset_state(LiveSources())


def test_approve_checks_hr_again_not_the_remembered_headcount(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """화면을 본 뒤 발령이 확정되면, 기억해 둔 5분 안이어도 옛 인원으로 확정하지 않는다."""
    heads: dict[str, Any] = {"2026-11": _hr_reply("2026-11", 5), "2026-10": _hr_reply("2026-10", 5)}
    _live_hr(monkeypatch, heads)
    client = TestClient(app)
    seen = client.get("/api/finance/team-budget?month=2026-11").json()["data"]

    heads["2026-11"] = _hr_reply("2026-11", 6)
    # 화면 조회는 아직 기억해 둔 값을 쓴다
    assert client.get("/api/finance/team-budget?month=2026-11").json()["data"]["total"] == 500000
    assert _hr_confirm(client, "2026-11", seen["signature"])["outcome"] == "stale"
    assert "2026-11" not in handlers.state().records.team_budgets

    now = client.get("/api/finance/team-budget?month=2026-11").json()["data"]
    assert now["total"] == 600000  # 다시 불러오면 새 인원이 보인다
    assert _hr_confirm(client, "2026-11", now["signature"])["output"]["total"] == 600000


def test_month_before_hr_records_is_not_zero_won(monkeypatch: pytest.MonkeyPatch) -> None:
    """기록 시작 전 달은 인사가 실패로 답한다. 0원이 아니라 "받지 못함"이고 확정할 수 없다."""
    _live_hr(monkeypatch, {"2026-11": _hr_reply("2026-11", 5)})
    client = TestClient(app)
    body = client.get("/api/finance/team-budget?month=2026-10").json()
    assert body["data"]["status"] == "unavailable" and body["data"]["total"] is None
    assert _hr_confirm(client, "2026-10", "x")["outcome"] == "team_headcount_unavailable"


def test_first_recorded_month_can_be_approved_without_last_month(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """기록이 시작된 달은 지난달 인원이 없어도 확정된다. 증감만 비운다."""
    _live_hr(monkeypatch, {"2026-11": _hr_reply("2026-11", 5)})
    client = TestClient(app)
    body = client.get("/api/finance/team-budget?month=2026-11").json()
    d = body["data"]
    assert d["example"] is False and d["status"] == "pending" and d["total"] == 500000
    assert d["prev_total"] is None and d["change_total"] is None
    assert body["meta"]["unavailable"] == ["team_headcount_prev"]
    assert _hr_confirm(client, "2026-11", d["signature"])["outcome"] == "recorded"
    data = client.get("/api/finance/team-budget?month=2026-11").json()["data"]
    assert data["status"] == "approved"
