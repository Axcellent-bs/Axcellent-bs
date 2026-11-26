"""롤백 요청에 사건이 열린 시각(`incident_opened_at`)을 싣는다.

교차검증(dev `verify_rollback_target`)은 이 시각이 없으면 판정하지 않고(invalid_input) 안전 관문이
보류한다. 총괄이 롤백 요청에 싣지 않으면 infra가 총괄 사건 기록에서 읽어 채운다.
로컬 실측(2026-11-09 16:17): 시각이 빠져 자동 롤백이 사람 승인 대기로 멈췄다.
"""

from __future__ import annotations

import asyncio

import httpx
import pytest
from fastapi.testclient import TestClient

from ax_agent_template import company_scope
from infra_agent import clients
from infra_agent.main import app

ROLLBACK = {"service": "shop-api", "from_version": "v2.4.1", "release_fault_injection": True}
OPENED = "2026-10-21T05:09:30Z"


def _rollback(a2a_body, payload: dict) -> dict:
    return TestClient(app).post("/a2a", json=a2a_body("rollback_deployment", payload)).json()


def test_opened_at_is_read_from_the_orchestrator_when_missing(a2a_body, gate, runtime) -> None:
    asked: list[str] = []

    async def opened_at(context_id: str) -> str | None:
        asked.append(context_id)
        return OPENED

    runtime.incident_opened_at = opened_at
    fake = gate("executed")
    assert _rollback(a2a_body, ROLLBACK)["status"] == "completed"
    assert asked == ["ctx_inc_0001"]
    assert fake.calls[0]["params"]["incident_opened_at"] == OPENED


def test_opened_at_from_the_request_wins(a2a_body, gate, runtime) -> None:
    async def never(context_id: str) -> str | None:
        raise AssertionError("요청에 시각이 있으면 총괄을 부르지 않는다")

    runtime.incident_opened_at = never
    fake = gate("executed")
    _rollback(a2a_body, {**ROLLBACK, "incident_opened_at": "2026-10-21T05:08:00Z"})
    assert fake.calls[0]["params"]["incident_opened_at"] == "2026-10-21T05:08:00Z"


def test_unknown_opened_at_is_left_out_not_made_up(a2a_body, gate, runtime) -> None:
    async def unknown(context_id: str) -> str | None:
        return None

    runtime.incident_opened_at = unknown
    fake = gate("executed")
    _rollback(a2a_body, ROLLBACK)
    assert "incident_opened_at" not in fake.calls[0]["params"]


def test_orchestrator_lookup_reads_the_incident_record(monkeypatch: pytest.MonkeyPatch) -> None:
    rows = [
        {"context_id": "ctx_inc_other", "opened_at": "2026-10-21T04:00:00Z"},
        {"context_id": "ctx_inc_0001", "opened_at": OPENED},
    ]
    sent: list[tuple[str | None, str | None]] = []

    def handler(req: httpx.Request) -> httpx.Response:
        sent.append((req.headers.get("authorization"), req.headers.get("x-ax-company")))
        return httpx.Response(200, json={"data": rows})

    transport = httpx.MockTransport(handler)
    real_client = httpx.AsyncClient
    monkeypatch.setattr(clients, "is_mock", lambda _name=None: False)
    monkeypatch.setattr(
        clients.httpx, "AsyncClient", lambda **kw: real_client(**dict(kw, transport=transport))
    )
    monkeypatch.setenv("INFRA_TOKEN", "tok-infra")
    with company_scope.using("c-cf2f0d"):  # 롤백 요청(A2A)은 그 회사로 걸려 온다
        assert asyncio.run(clients.orchestrator_opened_at("ctx_inc_0001")) == OPENED
        assert asyncio.run(clients.orchestrator_opened_at("ctx_inc_none")) is None
    # 그 회사 사건만 읽게 토큰과 회사를 싣는다(11/26)
    assert sent == [("Bearer tok-infra", "c-cf2f0d")] * 2


def test_orchestrator_lookup_without_company_does_not_call(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """요청 회사가 없으면 총괄을 부르지 않는다(캣북스 사건으로 떨어지지 않는다, 11/26)."""
    called: list[httpx.Request] = []
    transport = httpx.MockTransport(lambda req: called.append(req) or httpx.Response(200))
    real_client = httpx.AsyncClient
    monkeypatch.setattr(clients, "is_mock", lambda _name=None: False)
    monkeypatch.setattr(
        clients.httpx, "AsyncClient", lambda **kw: real_client(**dict(kw, transport=transport))
    )
    monkeypatch.setenv("INFRA_TOKEN", "tok-infra")
    token = company_scope._ACTIVE.set(None)
    try:
        assert asyncio.run(clients.orchestrator_opened_at("ctx_inc_0001")) is None
    finally:
        company_scope._ACTIVE.reset(token)
    assert called == []


def test_orchestrator_lookup_is_off_in_mock_mode() -> None:
    assert asyncio.run(clients.orchestrator_opened_at("ctx_inc_0001")) is None
