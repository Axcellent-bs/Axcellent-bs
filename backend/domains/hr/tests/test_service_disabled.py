"""개발·배포(dev) 서비스가 꺼져 진행 중 항목 조회가 `service_disabled`로 막혀도 인수인계는 계속된다.

조회 결과는 "없음"으로 보고 지어내지 않으며, 요약에는 "확인할 수 없음"으로 남긴다.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import httpx
import pytest
from fastapi.testclient import TestClient

from ax_agent_template import config
from ax_contracts.schemas import ListInProgressInput, ListInProgressOutput, RequestMeta
from hr_agent.clients.errors import bind_meta
from hr_agent.clients.real import A2APlanningClient
from hr_agent.main import app

DISABLED = "확인할 수 없음 (dev 서비스가 꺼져 있음)"


def disabled_handler(request: httpx.Request) -> httpx.Response:
    sent = json.loads(request.content)
    return httpx.Response(
        200,
        json={
            "meta": sent["meta"],
            "status": "rejected",
            "outcome": "service_disabled",
            "output": {"to_service": "dev", "message": "dev 서비스가 켜져 있지 않다"},
            "evidence": {},
        },
    )


@pytest.mark.asyncio
async def test_a_disabled_planning_lookup_is_empty_with_a_reason() -> None:
    config.set_agent_name("hr")
    http = httpx.AsyncClient(transport=httpx.MockTransport(disabled_handler))
    planning = A2APlanningClient("http://planning.test", http_client=http)
    bind_meta(
        RequestMeta(
            context_id="ctx_hnd_off",
            token="mock:orchestrator",
            path=["orchestrator"],
            deadline=datetime.now(UTC) + timedelta(seconds=30),
        )
    )
    try:
        out = await planning.list_in_progress(ListInProgressInput(owner_id="emp_01"))
    finally:
        bind_meta(None)
    assert out.items == []
    assert out.unavailable == DISABLED


class OffPlanning:
    """진행 중 항목 조회가 꺼진 서비스로 막힌 planning 대역."""

    async def list_in_progress(self, query: ListInProgressInput) -> ListInProgressOutput:
        return ListInProgressOutput(items=[], collected_at=datetime.now(UTC), unavailable=DISABLED)

    async def verify_handover(self, query):  # noqa: ANN001, ANN201
        raise AssertionError("이 테스트에서는 부르지 않는다")


def test_start_handover_continues_and_says_unknown_instead_of_zero(svc) -> None:
    svc.planning = OffPlanning()
    body = {
        "meta": {
            "context_id": "ctx_hnd_off",
            "token": "mock:orchestrator",
            "path": [],
            "deadline": (datetime.now(UTC) + timedelta(seconds=30)).isoformat(),
            "idempotency_key": "s",
        },
        "skill": "start_handover",
        "input": {"owner_id": "emp_01", "successor_id": "emp_02"},
    }
    result = TestClient(app).post("/a2a", json=body).json()
    assert result["status"] != "failed", result
    summary = result["evidence"]["data_summary"]
    assert "진행 중 확인할 수 없음 (dev 서비스가 꺼져 있음)" in summary
    assert "진행 중 0건" not in summary  # 0건으로 지어내지 않는다
