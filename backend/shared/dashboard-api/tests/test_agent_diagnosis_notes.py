"""에이전트 화면의 "총괄 AI가 보낸 진단" 칸이 읽는 경로 (읽기 전용)."""

from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from ax_dashboard_api import relay
from ax_dashboard_api.main import app

URL = "/api/dashboard/agent-diagnosis-notes"


def _note(note_id: str, received_at: str, **kw: Any) -> dict[str, Any]:
    return {
        "note_id": note_id,
        "diagnosis_id": "diag-1",
        "agent": "support",
        "step_id": "compensation",
        "step_label": "영향 고객 일괄 보상",
        "summary": "고객지원 AI의 '영향 고객 일괄 보상' 단계가 3건 중 2건에서 확인이 필요했어요",
        "findings": [],
        "evidence_contexts": ["ctx_inc_a"],
        "method": "rules",
        "cause_known": True,
        "issued_at": "2026-11-17T03:00:00Z",
        "received_at": received_at,
        **kw,
    }


class Upstream:
    def __init__(self) -> None:
        self.paths: list[str] = []
        self.notes: dict[str, list[dict[str, Any]]] = {}
        self.down: set[str] = set()

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.paths.append(f"{request.url.host}{request.url.path}")
        host = request.url.host or ""
        if any(name in host for name in self.down):
            raise httpx.ConnectError("꺼져 있음")
        service = next((n for n in self.notes if n in host), None)
        notes = self.notes.get(service or "", [])
        return httpx.Response(
            200, json={"data": {"service": service or "?", "notes": notes}, "meta": {}}
        )


@pytest.fixture
def upstream() -> Upstream:
    fake = Upstream()
    relay.use_client(httpx.AsyncClient(transport=httpx.MockTransport(fake.handle)))
    yield fake
    relay.use_client(None)


@pytest.fixture
def live(monkeypatch: pytest.MonkeyPatch, upstream: Upstream) -> TestClient:
    monkeypatch.delenv("DASHBOARD_MOCK", raising=False)
    monkeypatch.delenv("AGENT_TEMPLATE_MOCK", raising=False)
    return TestClient(app)


def test_the_agents_notes_are_read_from_its_service_newest_first(
    live: TestClient, upstream: Upstream
) -> None:
    upstream.notes["claims"] = [
        _note("note-diag-1", "2026-11-17T04:00:00Z"),
        _note("note-diag-2", "2026-11-17T05:00:00Z", step_id="other", step_label="다른 단계"),
    ]
    body = live.get(URL, params={"agent": "support"}).json()
    ids = [n["note_id"] for n in body["data"]]
    assert ids == ["note-diag-2", "note-diag-1"]
    assert body["meta"]["total"] == 2
    first = body["data"][0]
    assert first["service"] == "claims" and first["service_name"] == "클레임·보상"
    # 진단을 받는 서비스만 부른다(고객지원은 claims 하나)
    assert all("/api/improvement-notes" in p for p in upstream.paths)
    assert len(upstream.paths) == 1


def test_an_agent_without_received_notes_gets_an_empty_list(
    live: TestClient, upstream: Upstream
) -> None:
    body = live.get(URL, params={"agent": "finance"}).json()
    assert body["data"] == [] and body["meta"]["total"] == 0


def test_an_agent_that_does_not_receive_notes_is_not_found(live: TestClient) -> None:
    for agent in ("safety", "planning", "hr", "nobody"):
        res = live.get(URL, params={"agent": agent})
        assert res.status_code == 404 and res.json()["error"]["code"] == "UNKNOWN_AGENT"
    assert live.get(URL).status_code in (400, 422)  # agent를 꼭 줘야 한다


def test_a_service_that_is_down_is_left_out_and_reported(
    live: TestClient, upstream: Upstream
) -> None:
    upstream.down = {"claims"}
    body = live.get(URL, params={"agent": "support"}).json()
    assert body["data"] == []
    assert any("claims" in m for m in body["meta"]["unavailable"])


def test_mock_mode_returns_an_empty_list_not_made_up_notes(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DASHBOARD_MOCK", "1")
    body = TestClient(app).get(URL, params={"agent": "infra"}).json()
    assert body["data"] == []


def test_the_notes_carry_no_customer_fields(live: TestClient, upstream: Upstream) -> None:
    upstream.notes["claims"] = [_note("note-diag-1", "2026-11-17T04:00:00Z")]
    note = live.get(URL, params={"agent": "support"}).json()["data"][0]
    assert not {"buyer", "customer", "email", "phone", "untrusted"} & set(note)
