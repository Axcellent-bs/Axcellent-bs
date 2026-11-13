"""재무 → 기획 개선 요청 거둬들이기 (`POST /api/finance/planning-requests/{source_ref}/withdraw`).

사람이 보낸 요청을 빼면 기획 `withdraw_ticket`이 그 요청을 지우고, 재무 목록에서도 빠진다.
기획이 지우지 않으면(개발로 넘어감 등) 재무 기록은 그대로 둔다.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from fastapi.testclient import TestClient

from ax_contracts.schemas import AgentRequest, AgentResult, TaskStatus
from finance_agent import finance_api, handlers, persist, planning_questions
from finance_agent.main import DECISION_PATHS, app
from finance_agent.sources import MockSources

HUMAN = {"Authorization": "Bearer human:taeyoon"}
BODY = {"title": "PG 정산 파일을 매일 자동으로 받아 오기", "evidence": "수동 업로드 7회"}


@pytest.fixture(autouse=True)
def _mock(monkeypatch: pytest.MonkeyPatch, fixed_now) -> Iterator[None]:
    monkeypatch.setenv("AGENT_TEMPLATE_MOCK", "1")
    monkeypatch.delenv("FINANCE_HUMAN_TOKENS", raising=False)
    monkeypatch.delenv("SAFETY_HUMAN_TOKENS", raising=False)
    monkeypatch.delenv("AX_SAFETY_URL", raising=False)
    handlers.reset_state(MockSources())
    planning_questions.STORE.clear()
    yield
    planning_questions.STORE.clear()
    handlers.reset_state(MockSources())


class FakePlanning:
    """create_ticket은 REQ-(7+n)을 주고, withdraw_ticket은 정해 둔 결과를 돌려준다."""

    def __init__(self, withdraw: str = "withdrawn") -> None:
        self.withdraw = withdraw
        self.calls: list[AgentRequest] = []

    async def __call__(self, dept: str, req: AgentRequest) -> AgentResult:
        self.calls.append(req)
        if req.skill == "create_ticket":
            n = 7 + sum(1 for c in self.calls if c.skill == "create_ticket") - 1
            out = {"request_id": f"REQ-{n}", "context_id": f"ctx_req_{n:04d}", "outcome": "created"}
            return AgentResult(
                meta=req.meta, status=TaskStatus.COMPLETED, outcome="created", output=out
            )
        if self.withdraw == "fail":
            return AgentResult(meta=req.meta, status=TaskStatus.FAILED, outcome="upstream_error")
        out = {"request_id": req.input["request_id"], "outcome": self.withdraw, "message": "m"}
        return AgentResult(
            meta=req.meta, status=TaskStatus.COMPLETED, outcome=self.withdraw, output=out
        )


def _send(client: TestClient) -> dict:
    res = client.post("/api/finance/planning-requests", json=BODY, headers=HUMAN)
    assert res.status_code == 200, res.text
    return res.json()["data"]


def _listed(client: TestClient) -> list[str]:
    rows = client.get("/api/finance/planning-requests").json()["data"]["requests"]
    return [r["request_id"] for r in rows]


def test_withdraw_removes_it_from_planning_and_from_the_finance_list(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakePlanning()
    monkeypatch.setattr(finance_api, "call_agent", fake)
    client = TestClient(app)
    first, second = _send(client), _send(client)
    assert _listed(client) == ["REQ-8", "REQ-7"]
    url = f"/api/finance/planning-requests/{first['source_ref']}/withdraw"
    res = client.post(url, json={"reason": "잘못 보냄"}, headers=HUMAN)
    assert res.status_code == 200, res.text
    data = res.json()["data"]
    assert (data["withdrawn_by"], data["withdraw_outcome"]) == ("taeyoon", "withdrawn")
    call = fake.calls[-1]
    assert call.skill == "withdraw_ticket"
    assert call.input == {
        "request_id": "REQ-7",
        "source_ref": "fin_req_0001",
        "reason": "잘못 보냄",
    }
    assert call.meta.idempotency_key
    assert _listed(client) == ["REQ-8"]
    # 기획이 되물어도 거둬들인 요청에는 답하지 않는다
    assert planning_questions.STORE.find(call.meta.tenant_id, "REQ-7", "ctx_req_0007") is None
    assert planning_questions.STORE.find(call.meta.tenant_id, "REQ-8", "ctx_req_0008")
    # 같은 요청을 다시 빼려 하면 없다 (기록은 남아 번호를 다시 쓰지 않는다)
    assert client.post(url, json={}, headers=HUMAN).status_code == 404
    assert _send(client)["source_ref"] == "fin_req_0003"
    assert second["source_ref"] == "fin_req_0002"


def test_already_gone_in_planning_still_clears_the_finance_row(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(finance_api, "call_agent", FakePlanning("not_found"))
    client = TestClient(app)
    row = _send(client)
    res = client.post(
        f"/api/finance/planning-requests/{row['source_ref']}/withdraw", json={}, headers=HUMAN
    )
    assert res.status_code == 200, res.text
    assert _listed(client) == []


@pytest.mark.parametrize("outcome", ["in_progress", "not_yours"])
def test_planning_refuses_and_the_row_stays(monkeypatch: pytest.MonkeyPatch, outcome: str) -> None:
    monkeypatch.setattr(finance_api, "call_agent", FakePlanning(outcome))
    client = TestClient(app)
    row = _send(client)
    res = client.post(
        f"/api/finance/planning-requests/{row['source_ref']}/withdraw", json={}, headers=HUMAN
    )
    assert res.status_code == 409 and res.json()["error"]["code"] == outcome
    assert _listed(client) == ["REQ-7"]


def test_planning_down_keeps_the_row(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(finance_api, "call_agent", FakePlanning("fail"))
    client = TestClient(app)
    row = _send(client)
    res = client.post(
        f"/api/finance/planning-requests/{row['source_ref']}/withdraw", json={}, headers=HUMAN
    )
    assert res.status_code == 502
    assert _listed(client) == ["REQ-7"]


def test_only_a_person_can_withdraw(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = FakePlanning()
    monkeypatch.setattr(finance_api, "call_agent", fake)
    client = TestClient(app)
    row = _send(client)
    url = f"/api/finance/planning-requests/{row['source_ref']}/withdraw"
    assert client.post(url, json={}).status_code == 401
    assert client.post(url, json={"reason": 3}, headers=HUMAN).status_code == 400
    assert [c.skill for c in fake.calls] == ["create_ticket"]
    assert DECISION_PATHS.match(url)


def test_withdrawn_rows_stay_out_after_a_restart(monkeypatch: pytest.MonkeyPatch, tmp_path) -> None:
    monkeypatch.setattr(finance_api, "call_agent", FakePlanning())
    client = TestClient(app)
    row = _send(client)
    client.post(
        f"/api/finance/planning-requests/{row['source_ref']}/withdraw", json={}, headers=HUMAN
    )
    path = tmp_path / "state.json"
    persist.save(handlers.state(), path)
    handlers.reset_state(MockSources())
    planning_questions.STORE.clear()
    assert persist.load(handlers.state(), path)
    assert _listed(TestClient(app)) == []
    assert planning_questions.STORE.find(row["tenant_id"], "REQ-7", "ctx_req_0007") is None
