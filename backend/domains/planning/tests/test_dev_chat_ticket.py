"""개발 AI 대화가 넘긴 변경 요청은 자동 처리 없이 사람이 기획서를 확정해야 개발로 간다 (11/09)."""

from __future__ import annotations

import asyncio

import pytest

from ax_contracts.schemas import ExecuteDecision
from ax_workflow.dev_handoff import DEV_CHAT_REF
from planning_agent.domain.service import PlanningService

pytestmark = pytest.mark.role("planning")


async def _gate(action, params, meta, evidence) -> ExecuteDecision:
    return ExecuteDecision(execution_id="exe_ok", grade="A", status="executed")


def _ticket(svc: PlanningService, title: str, requester: str, source_ref: str | None, key: str):
    return asyncio.run(
        svc.create_ticket(
            context_id="ctx_chat_1",
            requester=requester,
            title=title,
            evidence="Banner.tsx의 배너 문구를 바꾼다.\n\n개발 AI 대화 chat-1",
            source_ref=source_ref,
            evidence_ids=[],
            idempotency_key=key,
        )
    )


def test_dev_chat_ticket_waits_for_human_confirm() -> None:
    svc = PlanningService(gate=_gate, relater=None)
    # 간단 요청 판정이면 자동으로 끝까지 갈 제목도 개발 대화에서 오면 사람이 확정한다
    out = _ticket(svc, "공지 배너 문구 오타 수정", "dev", f"{DEV_CHAT_REF}chat-1", "k1")
    assert out.outcome == "created" and out.output["auto"] is False
    req = next(r for r in svc.state.requests if r.id == out.output["request_id"])
    assert req.state == "접수" and req.spec is None and not req.flow and req.auto is None
    assert req.from_ == "dev" and req.source_ref == "dev_chat:chat-1"
    assert any("사람이 기획서 확정" in a.text for a in svc.state.activity)
    assert not any(sp.request == req.id for sp in svc.state.specs)


def test_same_title_from_other_department_still_auto() -> None:
    svc = PlanningService(gate=_gate, relater=None)
    out = _ticket(svc, "공지 배너 문구 오타 수정", "support", None, "k2")
    req = next(r for r in svc.state.requests if r.id == out.output["request_id"])
    assert req.auto is not None and req.from_ == "support"
