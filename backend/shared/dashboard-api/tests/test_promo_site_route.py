"""홍보 사이트 경로 (`/api/dashboard/marketing/promo-site`)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from ax_agent_template import human_auth
from ax_contracts.schemas import AgentRequest, AgentResult, TaskStatus
from ax_dashboard_api import marketing_client
from ax_dashboard_api.main import app

URL = "/api/dashboard/marketing/promo-site"
BODY = {"title": "누비의 여행", "customer": "캣북스", "rights_confirmed": True}


class Fake:
    def __init__(self) -> None:
        self.calls: list[AgentRequest] = []
        self.refuse: str | None = None

    async def __call__(self, target: str, request: AgentRequest) -> AgentResult:
        assert target == "marketing"
        self.calls.append(request)
        if self.refuse:
            return AgentResult(
                meta=request.meta, status=TaskStatus.FAILED, outcome=self.refuse, output={}
            )
        return AgentResult(meta=request.meta, status=TaskStatus.COMPLETED, output={"ok": True})


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
    c = TestClient(app)
    c.headers.pop("authorization", None)
    return c


def _auth() -> dict[str, str]:
    token, _ = human_auth.issue_session(
        account_id="acc_1", name="준하", role="admin", company_id="catbooks", company_role="admin"
    )
    return {"Authorization": f"Bearer {token}"}


def test_every_route_needs_a_person(client: TestClient, fake: Fake) -> None:
    assert client.get(URL).status_code == 401
    assert client.post(f"{URL}/spd_1", json=BODY).status_code == 401
    assert client.delete(f"{URL}/spd_1").status_code == 401
    assert fake.calls == []


def test_publish_remove_and_status(client: TestClient, fake: Fake) -> None:
    assert client.get(URL, headers=_auth()).status_code == 200
    res = client.post(f"{URL}/CR-0123456789ab", json=BODY, headers=_auth())
    assert res.status_code == 200, res.text
    assert client.delete(f"{URL}/spd_1", headers=_auth()).status_code == 200
    assert [c.skill for c in fake.calls] == [
        "promo_site_status",
        "promo_site_publish",
        "promo_site_remove",
    ]
    assert fake.calls[1].input == BODY | {"item_id": "CR-0123456789ab", "who": "준하"}
    assert fake.calls[2].input == {"item_id": "spd_1", "who": "준하"}


@pytest.mark.parametrize(
    "body",
    [
        {**BODY, "rights_confirmed": False},
        {**BODY, "title": ""},
        {**BODY, "customer": "가" * 21},
    ],
)
def test_bad_requests_do_not_reach_marketing(client: TestClient, fake: Fake, body: dict) -> None:
    assert client.post(f"{URL}/spd_1", json=body, headers=_auth()).status_code == 422
    assert fake.calls == []


@pytest.mark.parametrize(
    ("outcome", "status", "code"),
    [
        ("limit_reached", 409, "PROMO_LIMIT_REACHED"),
        ("feed_not_configured", 409, "FEED_NOT_CONFIGURED"),
        ("company_not_allowed", 403, "COMPANY_NOT_ALLOWED"),
    ],
)
def test_refusals_keep_their_codes(
    client: TestClient, fake: Fake, outcome: str, status: int, code: str
) -> None:
    fake.refuse = outcome
    res = client.post(f"{URL}/spd_1", json=BODY, headers=_auth())
    assert res.status_code == status and res.json()["error"]["code"] == code
