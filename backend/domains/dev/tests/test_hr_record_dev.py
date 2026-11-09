"""인사 연동 2단계 (개발): PR 머지·요청 완료 때 인사 `record_case`를 A2A로 실제 부른다.

호출자를 바꿔 끼워 (가) 사람 머지·자동 머지·요청 완료가 각각 기록되고, (나) 인사가 실패해도
머지·완료는 그대로이며 "인사 기록 실패"가 남고, (다) 간단 요청 자동 처리도 따로 기록되는지 본다.
목 모드(인사 없음)는 예전 문구 그대로라 test_service의 REQ-12 흐름이 본다.
"""

from __future__ import annotations

import pytest

from ax_contracts.schemas import AgentRequest, AgentResult, ExecuteDecision, TaskStatus
from ax_contracts.schemas.record import RecordCase, RecordKind
from ax_workflow.hr_link import HrClient
from ax_workflow_testing import Wire, plan
from dev_agent.service import DevService

pytestmark = pytest.mark.role("dev")


async def _ok_gate(action, params, meta, evidence) -> ExecuteDecision:
    return ExecuteDecision(execution_id="exe_ok", grade="A", status="executed")


class Hr:
    """인사 대신 답한다. `fail`이면 실패로 답한다."""

    def __init__(self, fail: bool = False) -> None:
        self.fail = fail
        self.asked: list[AgentRequest] = []

    async def __call__(self, target: str, req: AgentRequest) -> AgentResult:
        assert target == "hr"
        self.asked.append(req)
        if self.fail:
            return AgentResult(meta=req.meta, status=TaskStatus.FAILED, outcome="unreachable")
        return AgentResult(
            meta=req.meta,
            status=TaskStatus.COMPLETED,
            output={"outcome": "card_created", "card_id": f"card_{len(self.asked)}"},
        )

    def records(self) -> list[RecordCase]:
        return [RecordCase.model_validate(r.input) for r in self.asked]

    def keys(self) -> list[str | None]:
        return [r.meta.idempotency_key for r in self.asked]


def _dev(hr: Hr) -> DevService:
    return DevService(gate=_ok_gate, hr_client=HrClient(caller=hr))


def _hr_texts(svc: DevService) -> list[str]:
    return [a.text for a in svc.state.activity if a.point == "record_case" and a.to == "hr"]


def _req12_merged(dev: DevService) -> Wire:
    """REQ-12: 답변 → 확정 → PR #31 자동 머지, PR #32 사람 머지 → 요청 완료."""
    wire = Wire(dev=dev, gate=_ok_gate)
    wire.run(wire.planning.answer_question("SPEC-7", "품절 상품도 남겨 두고 품절 표시해 주세요"))
    wire.run(wire.planning.confirm_spec("SPEC-7"))
    wire.run(dev.merge_pr("PR #32"))
    return wire


def test_merge_and_close_send_record_case() -> None:
    hr = Hr()
    dev = _dev(hr)
    _req12_merged(dev)
    recs = hr.records()
    assert [r.kind for r in recs] == [
        RecordKind.PR_MERGED,
        RecordKind.PR_MERGED,
        RecordKind.REQUEST_CLOSED,
    ]
    assert hr.keys() == ["PR #31:pr_merged", "PR #32:pr_merged", "SPEC-7:request_closed"]
    auto, human, closed = recs
    assert "자동 머지" in auto.summary and auto.decision_reason.startswith("merge_pr 규칙 통과")
    assert "사람 머지" in human.summary and human.decision_reason == "사람이 머지 버튼으로 승인"
    assert closed.source_refs == ["request:REQ-12", "spec:SPEC-7"]
    # 영역: PR 경로(shop-api/cart/ 등)로 정함 → 인사 check_plan 겹침 비교에 쓴다
    assert (auto.area, human.area, closed.area) == ("payment", "payment", "payment")
    assert all(r.author == "planning" and r.context_id == "ctx_req_0012" for r in recs)
    texts = _hr_texts(dev)
    assert "① kind pr_merged · PR #31 (자동 머지) → 인사 카드 생성 card_1" in texts
    assert "① kind request_closed · REQ-12 → 인사 카드 생성 card_3" in texts
    assert dev.soft_holds == []


def test_hr_failure_keeps_merge_and_leaves_note() -> None:
    hr = Hr(fail=True)
    dev = _dev(hr)
    _req12_merged(dev)
    assert all(p.merged for p in dev.state.prs if p.task in {"T-11", "T-12"})
    assert next(r for r in dev.state.requests if r.id == "REQ-12").state == "완료"
    assert [c.idempotency_key for c in dev.soft_holds if c.status == "hr_failed"] == [
        "PR #31:pr_merged",
        "PR #32:pr_merged",
        "SPEC-7:request_closed",
    ]
    texts = _hr_texts(dev)
    assert any(t.startswith("① kind pr_merged · PR #32 → 인사 기록 실패") for t in texts)


def test_simple_request_auto_close_sends_two_records() -> None:
    hr = Hr()
    dev = _dev(hr)
    plan(dev, "inbound", "support_simple")
    kinds = [r.kind for r in hr.records()]
    assert kinds == [RecordKind.PR_MERGED, RecordKind.REQUEST_CLOSED]
    merged, closed = hr.records()
    assert "간단 요청" in merged.summary
    assert any(ref.startswith("request:REQ-") for ref in closed.source_refs)


def test_mock_mode_dev_has_no_recorder(service) -> None:
    assert service._hr is None
