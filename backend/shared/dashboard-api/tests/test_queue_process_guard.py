"""환불을 지급하는 `POST /queue-entries/process`의 보호: 역할·지급 수 상한·호출자 기록."""

from typing import Any

import pytest
from fastapi.testclient import TestClient

from ax_agent_template import human_auth
from ax_contracts.schemas import AgentRequest, AgentResult, TaskStatus
from ax_dashboard_api import support_client
from ax_dashboard_api.main import app

URL = "/api/dashboard/queue-entries/process"
EMPTY_RUN = {"results": [], "paid": 0, "held": 0, "queue": []}


class Fake:
    def __init__(self) -> None:
        self.sent: list[AgentRequest] = []

    async def __call__(self, target: str, req: AgentRequest) -> AgentResult:
        self.sent.append(req)
        return AgentResult(meta=req.meta, status=TaskStatus.COMPLETED, output=EMPTY_RUN)


@pytest.fixture
def fake(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> Fake:
    monkeypatch.delenv("DASHBOARD_MOCK", raising=False)
    monkeypatch.delenv("AGENT_TEMPLATE_MOCK", raising=False)
    monkeypatch.delenv("DASHBOARD_QUEUE_PROCESS_MAX", raising=False)
    monkeypatch.setenv("DASHBOARD_API_TOKEN", "test-token")
    monkeypatch.setenv(human_auth.SESSION_SECRET_ENV, "s" * 40)  # 테스트용 자리값
    monkeypatch.setenv(human_auth.REVOKED_FILE_ENV, str(tmp_path / "revoked.json"))
    monkeypatch.setenv(human_auth.GENERATIONS_FILE_ENV, str(tmp_path / "gens.json"))
    f = Fake()
    monkeypatch.setattr(support_client, "SEND", f)
    return f


def _session(role: str, company_role: str | None = None) -> dict[str, str]:
    token, _ = human_auth.issue_session(
        account_id=f"acc_{role}",
        name=f"{role}-user",
        role=role,
        company_id="shop-demo" if company_role else None,
        company_role=company_role,
    )
    return {"Authorization": f"Bearer {token}"}


def _post(headers: dict[str, str] | None, json: dict | None = None):
    client = TestClient(app)
    if headers is not None:
        client.headers.update(headers)
    else:
        client.headers.pop("authorization")
    return client.post(URL, json=json if json is not None else {})


def test_member_session_gets_a_json_403_and_support_is_not_called(fake: Fake) -> None:
    res = _post(_session("member", "member"))
    assert res.status_code == 403
    assert res.headers["content-type"].startswith("application/json")
    error = res.json()["error"]
    assert error["code"] == "ROLE_REQUIRED" and error["message"]
    assert fake.sent == []


@pytest.mark.parametrize("role", ["admin", "operator"])
def test_admin_and_operator_sessions_may_process(fake: Fake, role: str) -> None:
    res = _post(_session(role, "admin" if role == "admin" else None))
    assert res.status_code == 200, res.text
    assert len(fake.sent) == 1


def test_no_token_is_401(fake: Fake) -> None:
    res = _post(None)
    assert res.status_code == 401 and res.json()["error"]["code"] == "AUTH_REQUIRED"
    assert fake.sent == []


def test_shared_human_token_still_works_while_it_is_allowed(fake: Fake) -> None:
    assert _post({"Authorization": "Bearer test-human-token"}).status_code == 200


def test_limit_above_the_cap_is_clamped_not_rejected(
    fake: Fake, monkeypatch: pytest.MonkeyPatch
) -> None:
    assert _post(_session("admin", "admin"), {"limit": 5000}).status_code == 200
    assert fake.sent[-1].input["limit"] == 20
    monkeypatch.setenv("DASHBOARD_QUEUE_PROCESS_MAX", "3")
    _post(_session("admin", "admin"), {"limit": 10})
    assert fake.sent[-1].input["limit"] == 3
    _post(_session("admin", "admin"), {"limit": 2})
    assert fake.sent[-1].input["limit"] == 2


def test_bad_cap_env_falls_back_to_default(fake: Fake, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DASHBOARD_QUEUE_PROCESS_MAX", "zero")
    _post(_session("admin", "admin"), {"limit": 999})
    assert fake.sent[-1].input["limit"] == 20


def test_session_caller_is_passed_as_requested_by(fake: Fake) -> None:
    _post(_session("admin", "admin"), {"limit": 1})
    expected = "admin-user (account:acc_admin, company:catbooks)"  # 별칭은 정식 ID로
    assert fake.sent[-1].input["requested_by"] == expected


def test_shared_token_sends_no_requested_by(fake: Fake) -> None:
    _post({"Authorization": "Bearer test-human-token"}, {"limit": 1})
    assert "requested_by" not in fake.sent[-1].input
