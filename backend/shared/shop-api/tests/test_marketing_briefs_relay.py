"""관리자 화면용 마케팅 받은 기획안 읽기 중계."""

import pytest
from fastapi.testclient import TestClient

from ax_agent_template import events
from ax_contracts.schemas import AgentRequest, AgentResult, TaskStatus
from ax_shop_api import marketing_client
from ax_shop_api.db import Db
from ax_shop_api.main import create_app

ADMIN = {"Authorization": "Bearer mock:admin"}
INFRA = {"Authorization": "Bearer mock:infra"}
ITEM = {"proposal_id": "PROP-1", "campaign_id": "cmp_PROP-1", "title": "11월 쿠폰"}


@pytest.fixture(autouse=True)
def _mock_mode(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_TEMPLATE_MOCK", "1")
    monkeypatch.setenv("SHOP_MONITOR_INTERVAL_S", "0")
    events.reset_mock_bus()


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app(Db(":memory:")))


def _upstream(monkeypatch, status=TaskStatus.COMPLETED, output=None, outcome=None):
    seen: list[tuple[str, AgentRequest]] = []

    async def send(target: str, request: AgentRequest) -> AgentResult:
        seen.append((target, request))
        return AgentResult(meta=request.meta, status=status, output=output or {}, outcome=outcome)

    monkeypatch.setattr(marketing_client, "SEND", send)
    return seen


def test_lists_briefs(client, monkeypatch) -> None:
    out = {"items": [ITEM], "total": 1, "unknown_reason": None}
    seen = _upstream(monkeypatch, output=out)
    response = client.get("/api/shop/admin/marketing/briefs?limit=10", headers=ADMIN)
    assert response.status_code == 200
    assert response.json()["data"] == out
    target, request = seen[0]
    assert (target, request.skill, request.input) == (
        "marketing",
        "list_campaign_briefs",
        {"limit": 10},
    )


def test_gets_one_brief(client, monkeypatch) -> None:
    seen = _upstream(monkeypatch, output={"found": True, "item": ITEM})
    response = client.get("/api/shop/admin/marketing/briefs/PROP-1", headers=ADMIN)
    assert response.status_code == 200
    assert response.json()["data"] == ITEM
    assert seen[0][1].skill == "get_campaign_brief"
    assert seen[0][1].input == {"proposal_id": "PROP-1"}


def test_missing_brief_is_404(client, monkeypatch) -> None:
    _upstream(monkeypatch, output={"found": False, "item": None})
    assert client.get("/api/shop/admin/marketing/briefs/PROP-9", headers=ADMIN).status_code == 404


@pytest.mark.parametrize("path", ["/briefs?limit=0", "/briefs?limit=101", "/briefs/a%20b"])
def test_invalid_input_never_reaches_marketing(client, monkeypatch, path) -> None:
    seen = _upstream(monkeypatch, output={})
    assert client.get(f"/api/shop/admin/marketing{path}", headers=ADMIN).status_code == 400
    assert seen == []


def test_marketing_down_does_not_crash(client, monkeypatch) -> None:
    _upstream(monkeypatch, status=TaskStatus.FAILED, outcome="unreachable")
    assert client.get("/api/shop/admin/marketing/briefs", headers=ADMIN).status_code == 502


def test_timeout_is_504_and_rejection_is_403(client, monkeypatch) -> None:
    _upstream(monkeypatch, status=TaskStatus.CANCELED, outcome="timeout")
    assert client.get("/api/shop/admin/marketing/briefs", headers=ADMIN).status_code == 504
    _upstream(monkeypatch, status=TaskStatus.REJECTED, outcome="FORBIDDEN_SKILL")
    assert client.get("/api/shop/admin/marketing/briefs", headers=ADMIN).status_code == 403


def test_other_agents_cannot_read(client, monkeypatch) -> None:
    seen = _upstream(monkeypatch, output={})
    assert client.get("/api/shop/admin/marketing/briefs", headers=INFRA).status_code == 403
    assert client.get("/api/shop/admin/marketing/briefs").status_code == 401
    assert seen == []
