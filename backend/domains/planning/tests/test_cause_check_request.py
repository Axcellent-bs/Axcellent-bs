"""총괄이 사건에서 보낸 원인 확인 요청은 자동 기획·PR로 넘기지 않는다 (사람이 원인을 본다)."""

from __future__ import annotations

import asyncio

from ax_contracts.schemas import ExecuteDecision
from planning_agent.domain.service import PlanningService


async def _gate(action, params, meta, evidence) -> ExecuteDecision:
    return ExecuteDecision(execution_id="exe_ok", grade="A", status="executed")


def _ticket(svc: PlanningService, title: str, source_ref: str | None, key: str):
    return asyncio.run(
        svc.create_ticket(
            context_id="ctx_inc_test",
            requester="orchestrator",
            title=title,
            evidence="원인 후보(mid): 직전 배포",
            source_ref=source_ref,
            evidence_ids=[],
            idempotency_key=key,
        )
    )


def test_incident_cause_check_is_not_auto_planned() -> None:
    svc = PlanningService(gate=_gate, relater=None)
    out = _ticket(svc, "payment 장애 원인 확인", "ctx_inc_abc123", "k1")
    # 총괄에 주는 응답은 그대로 (요청이 생김)
    assert out.outcome == "created" and out.output["request_id"]
    req = next(r for r in svc.state.requests if r.id == out.output["request_id"])
    assert req.state == "접수" and req.spec is None and not req.flow and req.auto is None
    assert req.source_ref == "ctx_inc_abc123"
    assert any("사람이 원인 확인" in a.text for a in svc.state.activity)
    assert not any(t for t in svc.state.tasks if t.spec and t.spec == req.spec)


def test_normal_ticket_still_flows() -> None:
    svc = PlanningService(gate=_gate, relater=None)
    out = _ticket(svc, "공지 배너 문구 오타 수정", None, "k2")
    req = next(r for r in svc.state.requests if r.id == out.output["request_id"])
    assert req.auto is not None  # 간단 요청 판정을 탄다
