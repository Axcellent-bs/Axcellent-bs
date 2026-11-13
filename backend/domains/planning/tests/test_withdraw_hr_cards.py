"""요청 철회 뒤 인사 `retire_request_cards`로 그 요청의 인사 카드를 내린다 (11/13).

인사가 실패하면 실패로 남기고 다음에 다시 부르며, 예전에 철회한 요청(활동 기록만 있는 것)도 잡는다.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest

from ax_contracts.schemas import AgentRequest, AgentResult, ExecuteDecision, TaskStatus
from ax_contracts.schemas.hr import RetireRequestCardsInput
from ax_workflow.models import Activity
from planning_agent.domain.service import PlanningService
from planning_agent.hr_link import HrClient

pytestmark = pytest.mark.role("planning")

TITLE = "쿠폰 적용 후 결제 금액이 두 번 계산된다"


async def _ok_gate(action, params, meta, evidence) -> ExecuteDecision:
    return ExecuteDecision(execution_id="exe_ok", grade="A", status="executed")


class Hr:
    """인사 대신 답한다. `fail`이면 retire_request_cards를 실패로 돌려준다."""

    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.asked: list[AgentRequest] = []

    def retires(self) -> list[RetireRequestCardsInput]:
        return [
            RetireRequestCardsInput.model_validate(r.input)
            for r in self.asked
            if r.skill == "retire_request_cards"
        ]

    async def __call__(self, target: str, req: AgentRequest) -> AgentResult:
        assert target == "hr"
        self.asked.append(req)
        if req.skill != "retire_request_cards":
            return AgentResult(
                meta=req.meta,
                status=TaskStatus.COMPLETED,
                output={"outcome": "record_only", "card_id": None, "confidence": "high"},
            )
        if self.fail:
            return AgentResult(meta=req.meta, status=TaskStatus.FAILED, outcome="unreachable")
        return AgentResult(
            meta=req.meta,
            status=TaskStatus.COMPLETED,
            outcome="retired",
            output={"retired_card_ids": ["card_1"], "already_retired_card_ids": []},
        )


def _svc(hr: Hr) -> PlanningService:
    svc = PlanningService(gate=_ok_gate, hr_client=HrClient(caller=hr, timeout=1))
    svc.state.auto_policy.level = 0  # 자동 처리 없이 접수에 머문다
    return svc


async def _ticket_and_withdraw(svc: PlanningService) -> str:
    made = await svc.create_ticket(
        context_id="ctx_req_finance_0001", requester="finance", title=TITLE,
        evidence="수동 업로드 7회", source_ref="fin_req_0001", evidence_ids=[],
        idempotency_key="ct-1",
    )  # fmt: skip
    req_id = made.output["request_id"]
    out = await svc.withdraw_ticket(
        requester="finance", request_id=req_id, source_ref="fin_req_0001",
        reason="잘못 보냄", idempotency_key="wd-1",
    )  # fmt: skip
    assert out.outcome == "withdrawn"
    return req_id


def _retire_acts(svc: PlanningService) -> list[Activity]:
    return [a for a in svc.state.activity if a.point == "retire_request_cards"]


def test_withdraw_asks_hr_to_retire_the_request_cards() -> None:
    hr = Hr()
    svc = _svc(hr)
    req_id = asyncio.run(_ticket_and_withdraw(svc))
    [asked] = hr.retires()
    ctx = next(a.context_id for a in svc.state.activity if a.point == "withdraw_ticket")
    assert (asked.context_id, asked.request_id, asked.spec_id) == (ctx, req_id, None)
    assert asked.reason
    [act] = _retire_acts(svc)
    assert act.grade == "A" and "인사 카드 1장 내림" in act.text and act.context_id == ctx
    # 이미 내렸으면 다시 부르지 않는다
    assert asyncio.run(svc.retire_withdrawn_cards()) == []
    assert len(hr.retires()) == 1


def test_hr_failure_is_recorded_and_retried_later() -> None:
    hr = Hr(fail=True)
    svc = _svc(hr)
    asyncio.run(_ticket_and_withdraw(svc))
    [failed] = _retire_acts(svc)
    assert failed.grade is None and "인사 카드 내리기 실패" in failed.text
    # 요청 철회 자체는 되돌리지 않는다
    assert all(r.source_ref != "fin_req_0001" for r in svc.state.requests)
    hr.fail = False
    texts = asyncio.run(svc.retire_withdrawn_cards())
    assert len(texts) == 1 and "1장 내림" in texts[0]
    assert len(hr.retires()) == 2
    assert asyncio.run(svc.retire_withdrawn_cards()) == []


def test_an_old_withdrawal_with_only_an_activity_line_is_picked_up() -> None:
    """이 기능 전에 철회한 요청 (251의 REQ-35처럼 활동 기록만 남은 것)."""
    hr = Hr()
    svc = _svc(hr)
    svc.state.activity.append(
        Activity(
            at=datetime(2026, 11, 13, 6, 15, tzinfo=UTC), context_id="ctx_req_0032",
            from_="finance", to="planning", point="withdraw_ticket", kind="A2A", grade="A",
            text="REQ-35 요청 부서가 철회해 목록에서 지움, 기획서 SPEC-12도 지움 · 시험",
        )
    )  # fmt: skip
    asyncio.run(svc.retire_withdrawn_cards())
    [asked] = hr.retires()
    assert (asked.context_id, asked.request_id, asked.spec_id) == (
        "ctx_req_0032",
        "REQ-35",
        "SPEC-12",
    )


def test_mock_mode_does_not_call_hr() -> None:
    svc = PlanningService(gate=_ok_gate, hr_client=None)
    svc.state.auto_policy.level = 0
    asyncio.run(_ticket_and_withdraw(svc))
    assert _retire_acts(svc) == []
