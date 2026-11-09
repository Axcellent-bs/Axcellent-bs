"""꺼진 서비스의 조회 단계(`service_disabled`)는 실패가 아니라 skipped + 이유로 남는다.

이벤트는 계속된다.
"""

from __future__ import annotations

import pytest

from ax_contracts.schemas import AgentRequest, AgentResult, TaskStatus
from ax_orchestrator.mock.harness import Harness, new_incident
from ax_orchestrator.plans.scenario_a import scenario_a


class DataOff:
    """데이터 AI(data)를 사지 않은 고객사: `kpi_summary` 조회가 라우터에서 막힌 것처럼 답한다."""

    def __init__(self, inner, skill: str = "kpi_summary") -> None:  # noqa: ANN001
        self.inner, self.skill = inner, skill

    async def __call__(self, dept: str, req: AgentRequest) -> AgentResult:
        if req.skill == self.skill:
            self.inner.calls.append((dept, req))
            return AgentResult(
                meta=req.meta,
                status=TaskStatus.REJECTED,
                outcome="service_disabled",
                output={"to_service": "data", "message": "data 서비스가 켜져 있지 않다"},
            )
        return await self.inner(dept, req)


@pytest.mark.asyncio
async def test_a_disabled_kpi_lookup_is_skipped_with_a_reason_and_the_incident_resolves() -> None:
    harness = Harness()
    engine = await harness.start()
    engine.caller = DataOff(harness.fakes)
    inc = await engine.run(new_incident(), scenario_a())

    kpi = inc.steps["kpi"]
    assert kpi.status == "skipped"  # 실패가 아니다
    assert kpi.note == "확인할 수 없음 (data 서비스가 꺼져 있음)"
    assert kpi.reason_code == "service_disabled"
    assert kpi.attempts == 1  # 재시도하지 않는다
    assert inc.steps["record"].status == "completed"  # 뒤 단계는 이어진다
    assert inc.status == "resolved"
    assert not any(s.status == "failed" for s in inc.steps.values())
    assert inc.metrics["auto_rate"] == 1.0  # 사람이 개입한 일로 세지 않는다
    assert any("확인할 수 없음 (data 서비스가 꺼져 있음)" in line for line in inc.summary)
