"""영향 분석 응답의 처리 시작 시각(`started_at`).

재무가 대기(요청~처리 시작)·처리(시작~응답) 시간을 나눠 로그에 남기는 데 쓴다.
분석 결과(analyze_impact)는 같은 입력이면 같게 두고, 시각은 A2A 처리기에서만 붙인다.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest

from ax_contracts.schemas import AgentRequest, Evidence, RequestMeta, TaskStatus
from ax_contracts.schemas.data import ImpactAnalysisOutput
from data_agent import handlers
from data_agent.skills.impact_analysis import ImpactResult

pytestmark = pytest.mark.asyncio


def _request() -> AgentRequest:
    meta = RequestMeta(
        context_id="ctx_inc_0001", token="x", deadline=datetime.now(UTC) + timedelta(seconds=30)
    )
    window = {"window_from": "2026-10-22T05:00:00Z", "window_to": "2026-10-22T05:40:00Z"}
    return AgentRequest(meta=meta, skill="impact_analysis", input=window)


async def test_impact_handler_reports_when_it_started(monkeypatch: pytest.MonkeyPatch) -> None:
    seen: list[datetime] = []

    async def fake_analyze(*args: Any, **kwargs: Any) -> ImpactResult:
        seen.append(datetime.now(UTC))  # 처리 중인 시각
        out = ImpactAnalysisOutput(
            failed_order_count=1,
            affected_customer_count=0,
            damage_amount=0,
            summary="실패 1건",
        )
        evidence = Evidence(queries=["shop_failed_orders"], data_summary="실패 1건")
        return ImpactResult(output=out, evidence=evidence, fallback=False)

    monkeypatch.setattr(handlers, "analyze_impact", fake_analyze)
    monkeypatch.setattr(handlers, "get_llm", lambda: None)
    before = datetime.now(UTC)
    result = await handlers.impact_analysis(_request())
    assert result.status == TaskStatus.COMPLETED
    started = ImpactAnalysisOutput.model_validate(result.output).started_at
    assert started is not None
    assert before <= started <= seen[0]
