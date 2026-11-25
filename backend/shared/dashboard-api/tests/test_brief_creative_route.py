"""기획안 → 제작 기록 경로 (`POST /api/dashboard/marketing/briefs/{id}/creative-record`).

사람 토큰 필요, 마케팅에는 기획안 번호만 보내고 로그인 회사가 meta에 실린다.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from ax_agent_template import human_auth
from ax_contracts.schemas import AgentRequest, AgentResult, TaskStatus
from ax_dashboard_api import marketing_client
from ax_dashboard_api.main import app

URL = "/api/dashboard/marketing/briefs/PROP-34/creative-record"
OUT = {
    "proposal_id": "PROP-34",
    "record_id": "CR-0123456789ab",
    "character_record_id": None,
    "reused": False,
}


class Fake:
    def __init__(self) -> None:
        self.calls: list[AgentRequest] = []
        self.status = TaskStatus.COMPLETED
        self.outcome: str | None = None

    async def __call__(self, target: str, request: AgentRequest) -> AgentResult:
        assert target == "marketing"
        self.calls.append(request)
        return AgentResult(
            meta=request.meta, status=self.status, outcome=self.outcome, output=OUT
        )


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch) -> Fake:
    f = Fake()
    monkeypatch.delenv("DASHBOARD_MOCK", raising=False)
    monkeypatch.delenv("AGENT_TEMPLATE_MOCK", raising=False)
    monkeypatch.setenv("DASHBOARD_API_TOKEN", "test-token")
    monkeypatch.setenv("AX_SESSION_SECRET", "test-secret-" + "x" * 32)
    monkeypatch.setattr(marketing_client, "SEND", f)
    return f


@pytest.fixture
def client(fake: Fake) -> TestClient:
    return TestClient(app)


def _auth(company: str = "catbooks") -> dict[str, str]:
    token, _ = human_auth.issue_session(
        account_id="acc_1", name="준하", role="admin", company_id=company, company_role="admin"
    )
    return {"Authorization": f"Bearer {token}"}


def test_sends_only_the_proposal_id_and_returns_the_record(
    client: TestClient, fake: Fake
) -> None:
    res = client.post(URL, headers=_auth())
    assert res.status_code == 200, res.text
    assert res.json()["data"] == OUT
    (sent,) = fake.calls
    assert sent.skill == "prepare_brief_creative"
    assert sent.input == {"proposal_id": "PROP-34"}


def test_needs_a_person(client: TestClient, fake: Fake) -> None:
    client.headers.pop("authorization", None)  # conftest가 넣는 기본 사람 토큰을 지운다
    assert client.post(URL).status_code == 401
    assert fake.calls == []


@pytest.mark.parametrize(
    ("outcome", "status", "code"),
    [
        ("brief_not_found", 404, "NOT_FOUND"),
        ("no_video_plan", 409, "NO_VIDEO_PLAN"),
        ("no_scenes", 409, "NO_SCENES"),
        ("storage_error", 500, "STORAGE_ERROR"),
    ],
)
def test_failures_keep_their_code(
    client: TestClient, fake: Fake, outcome: str, status: int, code: str
) -> None:
    fake.status, fake.outcome = TaskStatus.FAILED, outcome
    res = client.post(URL, headers=_auth())
    assert res.status_code == status
    assert res.json()["error"]["code"] == code
