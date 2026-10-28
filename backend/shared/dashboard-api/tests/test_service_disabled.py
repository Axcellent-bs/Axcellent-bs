"""꺼진 서비스의 조회(`service_disabled`)를 받아도 화면 API는 실패하지 않는다.

목록은 빈 목록, 숫자 집계는 `data: null`(0으로 지어내지 않음)이고, 둘 다 `meta.unavailable`에
"확인할 수 없음 (○○ 서비스가 꺼져 있음)"을 남긴다. 보여 줄 것이 없는 단건 조회만 503으로 알린다.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from ax_contracts.schemas import AgentRequest, AgentResult, TaskStatus
from ax_dashboard_api import hr_client, support_client
from ax_dashboard_api.main import app

BASE = "/api/dashboard"


def disabled(service: str):
    async def send(target: str, req: AgentRequest) -> AgentResult:
        return AgentResult(
            meta=req.meta,
            status=TaskStatus.REJECTED,
            outcome="service_disabled",
            output={"to_service": service, "message": f"{service} 서비스가 켜져 있지 않다"},
        )

    return send


@pytest.fixture
def client(monkeypatch: pytest.MonkeyPatch) -> TestClient:
    monkeypatch.delenv("DASHBOARD_MOCK", raising=False)
    monkeypatch.delenv("AGENT_TEMPLATE_MOCK", raising=False)
    monkeypatch.setenv("DASHBOARD_API_TOKEN", "test-token")
    monkeypatch.setattr(support_client, "SEND", disabled("claims"))
    monkeypatch.setattr(hr_client, "SEND", disabled("handover"))
    return TestClient(app)


@pytest.mark.parametrize(
    "path",
    [
        "/claims",
        "/conversations",
        "/customers/at-risk",
        "/change-requests",
        "/handovers",
        "/handover-questions",
        "/candidate-cards",
        "/decision-cards",
        "/hr-activity/ctx_x",
    ],
)
def test_lists_from_a_disabled_service_are_empty_with_a_reason(client, path) -> None:
    res = client.get(BASE + path)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["data"] == []
    assert body["meta"]["total"] == 0
    [reason] = body["meta"]["unavailable"]
    assert reason.startswith("확인할 수 없음 (") and reason.endswith("서비스가 꺼져 있음)")


@pytest.mark.parametrize("path", ["/kpis", "/claims/stats", "/queue-entries", "/roster"])
def test_number_summaries_from_a_disabled_service_are_null_not_zero(client, path) -> None:
    res = client.get(BASE + path)
    assert res.status_code == 200, res.text
    body = res.json()
    assert body["data"] is None
    assert body["meta"]["unavailable"]


def test_the_dashboard_still_loads_and_says_what_is_unknown(client) -> None:
    res = client.get(BASE + "/metrics")
    assert res.status_code == 200, res.text
    assert res.json()["meta"]["unavailable"] == ["확인할 수 없음 (claims 서비스가 꺼져 있음)"]


def test_a_single_item_from_a_disabled_service_says_so(client) -> None:
    res = client.get(BASE + "/claims/7")
    assert res.status_code == 503
    assert res.json()["error"] == {
        "code": "SERVICE_DISABLED",
        "message": "확인할 수 없음 (claims 서비스가 꺼져 있음)",
    }


def test_the_reason_does_not_leak_into_the_next_request(client, monkeypatch) -> None:
    client.get(BASE + "/claims")

    async def works(target: str, req: AgentRequest) -> AgentResult:
        return AgentResult(meta=req.meta, status=TaskStatus.COMPLETED, output={"items": []})

    monkeypatch.setattr(support_client, "SEND", works)
    body = client.get(BASE + "/conversations").json()
    assert "unavailable" not in body["meta"]
