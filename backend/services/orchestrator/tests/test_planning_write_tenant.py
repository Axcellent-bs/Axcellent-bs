"""기획에 요청을 넣는 총괄 경로가 그 매니저 회사를 싣는다 (11/25 회사 분리 7번).

캣북스 기획 요청 목록에 누비투어 요청이 들어와 있었다. 장애 처리 엔진의 부서 호출(원인 확인 요청
`create_ticket` 등)과 재무 의견을 기획에 넘기는 `receive_insight`가 회사를 싣지 않아, 받는 쪽이
캣북스로 봤다.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from ax_contracts.schemas import AgentResult, TaskStatus
from ax_contracts.schemas.common import TENANT_ID_DEFAULT
from ax_contracts.schemas.planning import ReceiveInsightInput
from ax_orchestrator import finance_relay
from ax_orchestrator.engine import Engine
from ax_orchestrator.finance_opinions import FinanceOpinions
from ax_orchestrator.guide import DEPTS
from ax_orchestrator.mock.fake_departments import FakeDepartments
from ax_orchestrator.models import Incident
from ax_orchestrator.plans.scenario_a import scenario_a
from ax_orchestrator.registry import Registry

OPENED = datetime(2026, 11, 25, 4, 0, tzinfo=UTC)


async def _run(tenant_id: str | None) -> list:
    fakes = FakeDepartments()
    registry = Registry()
    await registry.load(DEPTS, fakes.fetch_card)
    kw = {} if tenant_id is None else {"tenant_id": tenant_id}
    engine = Engine(registry, fakes, hold_timeout=0.1, **kw)
    inc = Incident("ctx_inc_tenant", "scenario_a", "결제 실패", "payment", opened_at=OPENED)
    await engine.run(inc, scenario_a())
    return fakes.calls


@pytest.mark.asyncio
async def test_engine_calls_carry_the_manager_company() -> None:
    calls = await _run("nubi-tour")
    assert calls
    assert {req.meta.tenant_id for _, req in calls} == {"nubi-tour"}


@pytest.mark.asyncio
async def test_catbooks_engine_keeps_the_old_default() -> None:
    calls = await _run(None)
    assert {req.meta.tenant_id for _, req in calls} == {TENANT_ID_DEFAULT}


INSIGHT = ReceiveInsightInput.model_validate(
    {
        "insight_id": "fin_budget_shift:a:b:t1",
        "title": "조회당 제작비 차이",
        "summary": "다음 회차 제작비를 옮긴다",
        "skill": "finance_insights",
        "facts": [{"label": "조회당 제작비", "value": "5.0원", "source": "finance_insights"}],
    }
)


@pytest.mark.asyncio
async def test_finance_relay_sends_the_opinion_company(monkeypatch) -> None:  # noqa: ANN001
    sent: list = []

    async def fake(target, req):  # noqa: ANN001, ANN202
        sent.append(req)
        return AgentResult(meta=req.meta, status=TaskStatus.COMPLETED, outcome="created",
                           output={"outcome": "created", "proposal_id": "PROP-1"})  # fmt: skip

    monkeypatch.setattr(finance_relay, "call_agent", fake)
    nubi = FinanceOpinions(tenant=lambda: "nubi-tour")
    await nubi.relay_sender(INSIGHT, "ctx_a")
    plain = FinanceOpinions()
    await plain.relay_sender(INSIGHT, "ctx_b")
    assert [r.meta.tenant_id for r in sent] == ["nubi-tour", TENANT_ID_DEFAULT]
