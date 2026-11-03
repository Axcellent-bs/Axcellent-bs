"""조직 변동(발령) 기록 화면 경로: hr skill로 넘기고 결과를 API 오류로 바꾼다."""

from typing import Any

import pytest
from fastapi.testclient import TestClient

from ax_contracts.schemas import AgentRequest, AgentResult, TaskStatus
from ax_dashboard_api import hr_client
from ax_dashboard_api.main import app

BASE = "/api/dashboard"
NOW = "2026-11-03T09:00:00Z"
CHANGE = {
    "change_id": "orgchg_1",
    "member_id": "emp_01",
    "kind": "join",
    "from_team": None,
    "to_team": "기획팀",
    "effective_date": "2026-11-01",
    "counts_from_month": "2026-11",
    "status": "draft",
    "note": None,
    "created_at": NOW,
    "decided_at": None,
}


class Fake:
    def __init__(self) -> None:
        self.sent: list[AgentRequest] = []
        self.outputs: dict[str, dict[str, Any]] = {}
        self.overrides: dict[str, dict[str, Any]] = {}

    async def __call__(self, target: str, req: AgentRequest) -> AgentResult:
        assert target == "hr"
        self.sent.append(req)
        fields = {"status": TaskStatus.COMPLETED, "output": self.outputs.get(req.skill, {})}
        return AgentResult(meta=req.meta, **{**fields, **self.overrides.get(req.skill, {})})

    def last(self, skill: str) -> AgentRequest:
        return [r for r in self.sent if r.skill == skill][-1]


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> Fake:
    monkeypatch.delenv("DASHBOARD_MOCK", raising=False)
    monkeypatch.delenv("AGENT_TEMPLATE_MOCK", raising=False)
    monkeypatch.setenv("DASHBOARD_API_TOKEN", "test-token")
    fake = Fake()
    monkeypatch.setattr(hr_client, "SEND", fake)
    return fake


@pytest.fixture
def client(fake: Fake) -> TestClient:
    return TestClient(app)


def test_list_returns_changes_and_the_roster_state(client: TestClient, fake: Fake) -> None:
    fake.outputs["list_org_changes"] = {
        "changes": [CHANGE],
        "registered_members": 3,
        "expected_members": 10,
        "roster_complete": False,
        "collected_at": NOW,
    }
    data = client.get(BASE + "/org-changes", params={"status": "draft"}).json()["data"]
    assert data["registered_members"] == 3 and data["roster_complete"] is False
    assert data["changes"][0]["counts_from_month"] == "2026-11"
    assert fake.last("list_org_changes").input == {"status": "draft"}


def test_drafting_goes_to_hr_with_a_target_key(client: TestClient, fake: Fake) -> None:
    fake.outputs["record_org_change"] = {"change": CHANGE}
    body = {
        "member_id": "emp_01",
        "kind": "join",
        "to_team": "기획팀",
        "effective_date": "2026-11-01",
    }
    res = client.post(BASE + "/org-changes", json=body)
    assert res.status_code == 200 and res.json()["data"]["change"]["status"] == "draft"
    sent = fake.last("record_org_change")
    assert sent.input == body  # 비어 있는 칸은 보내지 않는다
    assert sent.meta.idempotency_key and "emp_01:join:2026-11-01" in sent.meta.idempotency_key


def test_a_bad_draft_is_rejected_before_hr(client: TestClient, fake: Fake) -> None:
    bad = {"member_id": "emp_01", "kind": "join", "from_team": "기획팀", "to_team": "개발팀"}
    res = client.post(BASE + "/org-changes", json={**bad, "effective_date": "2026-11-01"})
    assert res.status_code == 422
    assert fake.sent == []


def test_deciding_sends_the_decision_and_a_target_key(client: TestClient, fake: Fake) -> None:
    fake.outputs["decide_org_change"] = {"change": {**CHANGE, "status": "confirmed"}}
    res = client.post(BASE + "/org-changes/orgchg_1/decision", json={"decision": "approve"})
    assert res.status_code == 200 and res.json()["data"]["change"]["status"] == "confirmed"
    sent = fake.last("decide_org_change")
    assert sent.input == {"change_id": "orgchg_1", "decision": "approve"}
    assert sent.meta.idempotency_key and "org-change:orgchg_1:approve" in sent.meta.idempotency_key


@pytest.mark.parametrize(
    ("outcome", "status", "code"),
    [
        ("UNKNOWN_CHANGE", 404, "NOT_FOUND"),
        ("NOT_DRAFT", 409, "CONFLICT"),
        ("LEDGER_CONFLICT", 409, "CONFLICT"),
    ],
)
def test_hr_refusals_become_api_errors(client, fake, outcome, status, code) -> None:
    fake.overrides["decide_org_change"] = {
        "outcome": outcome,
        "output": {"message": "안 된다"},
    }
    res = client.post(BASE + "/org-changes/orgchg_1/decision", json={"decision": "approve"})
    assert res.status_code == status and res.json()["error"]["code"] == code


def test_an_unknown_team_is_a_422(client: TestClient, fake: Fake) -> None:
    fake.overrides["record_org_change"] = {"outcome": "UNKNOWN_TEAM", "output": {"message": "x"}}
    body = {"member_id": "e", "kind": "join", "to_team": "없는팀", "effective_date": "2026-11-01"}
    res = client.post(BASE + "/org-changes", json=body)
    assert res.status_code == 422 and res.json()["error"]["code"] == "INVALID_INPUT"


def test_roster_complete_goes_to_hr_and_a_mismatch_is_a_conflict(client, fake) -> None:
    fake.outputs["set_org_roster_complete"] = {
        "roster_complete": True,
        "registered_members": 10,
        "expected_members": 10,
    }
    ok = client.post(BASE + "/org-roster/complete", json={})
    assert ok.status_code == 200 and ok.json()["data"]["roster_complete"] is True
    assert fake.last("set_org_roster_complete").input == {"complete": True}

    fake.overrides["set_org_roster_complete"] = {
        "outcome": "ROSTER_COUNT_MISMATCH",
        "output": {"message": "확정된 명부가 3명이다"},
    }
    res = client.post(BASE + "/org-roster/complete", json={})
    assert res.status_code == 409 and "3명" in res.json()["error"]["message"]


def test_headcount_preview_and_unknown_month(client: TestClient, fake: Fake) -> None:
    fake.outputs["team_headcount"] = {
        "month": "2026-11",
        "rows": [{"team": "기획팀", "headcount": 2}],
        "basis": "가상 명부(예시) 기준, 2026-11-01 소속",
        "collected_at": NOW,
        "example": True,
    }
    data = client.get(BASE + "/org-headcount", params={"month": "2026-11"}).json()["data"]
    assert data["available"] is True and data["example"] is True
    assert data["rows"] == [{"team": "기획팀", "headcount": 2}]

    fake.overrides["team_headcount"] = {
        "status": TaskStatus.FAILED,
        "outcome": "NO_HEADCOUNT_RECORD",
        "output": {"message": "2026-09의 팀 인원을 알 수 없다"},
    }
    gone = client.get(BASE + "/org-headcount", params={"month": "2026-09"}).json()["data"]
    assert gone["available"] is False and gone["month"] == "2026-09"
    assert client.get(BASE + "/org-headcount", params={"month": "2026-13"}).status_code == 422


def test_writes_need_a_human_token(client: TestClient, fake: Fake) -> None:
    client.headers.pop("authorization", None)
    res = client.post(BASE + "/org-changes/orgchg_1/decision", json={"decision": "approve"})
    assert res.status_code == 401 and res.json()["error"]["code"] == "AUTH_REQUIRED"
    res = client.post(BASE + "/org-roster/complete", json={})
    assert res.status_code == 401
    res = client.post(
        BASE + "/org-changes/orgchg_1/decision",
        json={"decision": "approve"},
        headers={"authorization": "Bearer mock:orchestrator"},
    )
    assert res.status_code == 403 and res.json()["error"]["code"] == "HUMAN_TOKEN_REQUIRED"
    assert fake.sent == []


def test_mock_mode_has_no_org_records(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DASHBOARD_MOCK", "1")
    res = TestClient(app).get(BASE + "/org-changes")
    assert res.status_code == 501


def test_basis_search_goes_to_hr_without_a_human_token(client: TestClient, fake: Fake) -> None:
    fake.outputs["find_org_change_basis"] = {
        "candidates": [
            {
                "card_id": "card_1",
                "title": "개발팀 인력 이동",
                "summary": "기획팀 1명을 개발팀으로",
                "similarity": 0.8,
                "rejected": False,
            }
        ],
        "method": "dense",
        "collected_at": NOW,
    }
    client.headers.pop("authorization", None)  # 찾기만 하는 요청이라 사람 토큰이 필요 없다
    res = client.post(BASE + "/org-changes/basis", json={"reason": "개발팀 인력 부족으로 이동"})
    assert res.status_code == 200 and res.json()["data"]["candidates"][0]["card_id"] == "card_1"
    assert fake.last("find_org_change_basis").input == {
        "reason": "개발팀 인력 부족으로 이동",
        "limit": 3,
    }
    assert client.post(BASE + "/org-changes/basis", json={"reason": "가"}).status_code == 422


def test_parse_goes_to_hr_and_an_unavailable_llm_is_not_an_error(client, fake) -> None:
    fake.outputs["parse_org_change_text"] = {
        "available": False,
        "message": "문장을 정리하지 못했다. 직접 입력한다",
    }
    client.headers.pop("authorization", None)
    res = client.post(BASE + "/org-changes/parse", json={"text": "다음 달 1일자 emp_03 이동"})
    assert res.status_code == 200 and res.json()["data"]["available"] is False
    assert fake.last("parse_org_change_text").input == {"text": "다음 달 1일자 emp_03 이동"}


def test_description_is_a_get_and_conflicts_become_409(client: TestClient, fake: Fake) -> None:
    fake.outputs["describe_org_change"] = {
        "change_id": "orgchg_1",
        "month": "2026-12",
        "deltas": [{"team": "기획팀", "delta": -1}],
        "text": "12월 1일부터 기획팀 -1명",
        "fallback": False,
        "llm_used": False,
    }
    res = client.get(BASE + "/org-changes/orgchg_1/description")
    assert res.status_code == 200 and res.json()["data"]["deltas"][0]["delta"] == -1
    fake.overrides["describe_org_change"] = {
        "outcome": "LEDGER_CONFLICT",
        "output": {"message": "그 팀에 없는 직원의 이동이다"},
    }
    assert client.get(BASE + "/org-changes/orgchg_1/description").status_code == 409


def test_a_wrong_basis_card_is_a_409(client: TestClient, fake: Fake) -> None:
    fake.overrides["record_org_change"] = {
        "outcome": "NOT_BASIS_CARD",
        "output": {"message": "확정된 업무 결정 카드만 근거로 쓸 수 있다"},
    }
    body = {
        "member_id": "e",
        "kind": "join",
        "to_team": "기획팀",
        "effective_date": "2026-11-01",
        "basis_card_id": "card_x",
    }
    res = client.post(BASE + "/org-changes", json=body)
    assert (
        res.status_code == 409 and fake.last("record_org_change").input["basis_card_id"] == "card_x"
    )
