"""인사 연동 1단계: 결정 추출·기획서 확정 때 인사 `record_case`를 A2A로 실제 부른다.

호출자를 바꿔 끼워 (가) 정상 저장, (나) 실패·시간 초과에도 본 작업 유지와 "인사 기록 실패",
(다) 멱등 키·첫 근거, (라) 목 모드·관문 보류에서 호출 없음을 본다.
"""

from __future__ import annotations

import asyncio

import pytest

from ax_contracts.schemas import AgentRequest, AgentResult, ExecuteDecision, TaskStatus
from ax_contracts.schemas.record import RecordCase, RecordKind
from planning_agent.domain.service import PlanningService
from planning_agent.hr_link import HrClient

pytestmark = pytest.mark.role("planning")  # 분리 3단계: 기획 서비스 역할로 돈다


def run(coro):
    return asyncio.run(coro)


async def _ok_gate(action, params, meta, evidence) -> ExecuteDecision:
    return ExecuteDecision(execution_id="exe_ok", grade="A", status="executed")


async def _hold_record_gate(action, params, meta, evidence) -> ExecuteDecision:
    if action == "record_case":
        return ExecuteDecision(
            execution_id="exe_hold", grade="C", status="held", hold_reason="over_threshold"
        )
    return ExecuteDecision(execution_id="exe_ok", grade="A", status="executed")


class Hr:
    """인사 대신 답한다. `fail`이면 실패, `slow`면 늦게 답한다."""

    def __init__(self, fail: bool = False, slow: bool = False) -> None:
        self.fail = fail
        self.slow = slow
        self.asked: list[AgentRequest] = []

    async def __call__(self, target: str, req: AgentRequest) -> AgentResult:
        assert target == "hr"
        self.asked.append(req)
        if self.slow:
            await asyncio.sleep(1)
        if self.fail:
            return AgentResult(meta=req.meta, status=TaskStatus.FAILED, outcome="unreachable")
        n = len(self.asked)
        return AgentResult(
            meta=req.meta,
            status=TaskStatus.COMPLETED,
            output={"outcome": "card_created", "card_id": f"card_{n}", "confidence": "high"},
        )

    def records(self) -> list[RecordCase]:
        return [RecordCase.model_validate(r.input) for r in self.asked]


def _service(hr: Hr, gate=_ok_gate, timeout: float = 30.0) -> PlanningService:
    return PlanningService(gate=gate, hr_client=HrClient(caller=hr, timeout=timeout))


def _hr_acts(svc: PlanningService) -> list[str]:
    return [a.text for a in svc.state.activity if a.point == "record_case" and a.to == "hr"]


def test_mock_mode_has_no_recorder(service) -> None:
    assert service._hr is None
    run(service.answer_question("SPEC-7", "품절 상품도 남겨 두고 품절 표시해 주세요"))
    assert service.soft_holds == []
    assert service.state.decisions[-1].sent_to_hr is True
    assert service.state.decisions[-1].hr_record is None  # 목 모드는 부르지 않음


def test_answer_and_confirm_send_record_case() -> None:
    hr = Hr()
    svc = _service(hr)
    run(svc.answer_question("SPEC-7", "품절 상품도 남겨 두고 품절 표시해 주세요"))
    run(svc.confirm_spec("SPEC-7"))

    kinds = [r.kind for r in hr.records()]
    assert kinds == [RecordKind.DECISION_EXTRACTED, RecordKind.SPEC_CONFIRMED]
    dec = svc.state.decisions[-1]
    extracted, confirmed = hr.records()
    # 결정 번호가 첫 근거 (인사 중복 키 context·종류·첫 근거)
    assert extracted.source_refs[0] == f"decision:{dec.id}"
    assert "spec:SPEC-7" in extracted.source_refs
    assert extracted.summary == dec.text
    assert extracted.decision_reason == dec.reason
    assert extracted.alternatives_considered == [dec.alternatives]
    assert extracted.author == "planning"
    assert confirmed.source_refs[0] == "spec:SPEC-7"
    # 멱등 키는 기획 쪽 `<id>:<종류>`
    keys = [r.meta.idempotency_key for r in hr.asked]
    assert keys == [f"{dec.id}:decision_extracted", "SPEC-7:spec_confirmed"]

    assert dec.sent_to_hr is True
    acts = _hr_acts(svc)
    assert any("인사 카드 생성 card_1" in t for t in acts)
    assert any("인사 카드 생성 card_2" in t for t in acts)
    assert svc.soft_holds == []


def test_thread_extract_sends_record_case() -> None:
    hr = Hr()
    svc = _service(hr)
    run(svc.dept_chat("data"))
    run(svc.extract_decision("CH-data", 0))
    run(svc.extract_decision("CH-data", 0))  # 이미 추출 → 다시 보내지 않는다

    assert len(hr.asked) == 1
    rec = hr.records()[0]
    dec = svc.state.decisions[-1]
    assert rec.kind == RecordKind.DECISION_EXTRACTED
    assert rec.source_refs[:2] == [f"decision:{dec.id}", "thread:CH-data"]
    assert rec.alternatives_considered == []  # "-"는 대안으로 보내지 않는다
    assert hr.asked[0].meta.idempotency_key == f"{dec.id}:decision_extracted"


def test_hr_failure_keeps_confirm_and_marks_failed() -> None:
    hr = Hr(fail=True)
    svc = _service(hr)
    run(svc.answer_question("SPEC-7", "품절 상품도 남겨 두고 품절 표시해 주세요"))
    run(svc.confirm_spec("SPEC-7"))

    sp = next(x for x in svc.state.specs if x.id == "SPEC-7")
    assert sp.status == "확정"  # 본 작업은 되돌리지 않는다
    assert svc.state.decisions[-1].sent_to_hr is False
    failed = [h for h in svc.soft_holds if h.status == "hr_failed"]
    assert [h.idempotency_key for h in failed] == [
        f"{svc.state.decisions[-1].id}:decision_extracted",
        "SPEC-7:spec_confirmed",
    ]
    assert sum("인사 기록 실패" in t for t in _hr_acts(svc)) == 2


def test_hr_timeout_is_failure_not_made_up() -> None:
    hr = Hr(slow=True)
    svc = _service(hr, timeout=0.05)
    run(svc.answer_question("SPEC-7", "품절 상품도 남겨 두고 품절 표시해 주세요"))

    assert svc.state.decisions[-1].sent_to_hr is False
    assert any("응답 없음" in t for t in _hr_acts(svc))


def test_record_case_skips_planning_gate_in_real_mode() -> None:
    """등록표 owner가 hr이라 기획 관문은 record_case를 보류한다(실서버 11/2). 인사가 자기 관문으로
    실행하므로 실제 모드(인사 연결)에서는 기획 관문에 묻지 않고 인사를 부른다."""
    hr = Hr()
    asked: list[str] = []

    async def gate(action, params, meta, evidence):
        asked.append(action)
        return await _hold_record_gate(action, params, meta, evidence)

    svc = _service(hr, gate=gate)
    run(svc.answer_question("SPEC-7", "품절 상품도 남겨 두고 품절 표시해 주세요"))
    run(svc.confirm_spec("SPEC-7"))

    assert "record_case" not in asked  # 사람 보류 큐에 C 건이 쌓이지 않는다
    assert [r.kind for r in hr.records()] == [
        RecordKind.DECISION_EXTRACTED,
        RecordKind.SPEC_CONFIRMED,
    ]
    assert svc.soft_holds == []


def test_mock_mode_still_records_record_case_gate(service) -> None:
    run(service.answer_question("SPEC-7", "품절 상품도 남겨 두고 품절 표시해 주세요"))
    assert any(c.action == "record_case" for c in service.gate_calls)


# ---- 3단계: 기획서 초안 → 인사 check_plan


class PlanHr(Hr):
    """check_plan에 `verdict`로 답한다 (record_case는 Hr처럼 카드 생성)."""

    def __init__(self, verdict: str = "ok", **kw) -> None:
        super().__init__(**kw)
        self.verdict = verdict

    async def __call__(self, target: str, req: AgentRequest) -> AgentResult:
        if req.skill != "check_plan":
            return await super().__call__(target, req)
        self.asked.append(req)
        if self.fail:
            return AgentResult(meta=req.meta, status=TaskStatus.FAILED, outcome="unreachable")
        if self.verdict == "none":
            out = {"verdict": "ok", "no_related_cards": True}
        elif self.verdict == "ok":
            out = {
                "verdict": "ok",
                "findings": [{"code": "MATCH", "level": "info", "message": "확정 결정과 같음"}],
            }
        else:
            out = {
                "verdict": "warn",
                "findings": [
                    {
                        "code": "REJECTED_BEFORE",
                        "level": "warn",
                        "message": "9/30 기각된 안과 겹침",
                        "card_ids": ["card_rej"],
                    }
                ],
            }
        return AgentResult(meta=req.meta, status=TaskStatus.COMPLETED, output=out)

    def plan_asks(self) -> list[AgentRequest]:
        return [r for r in self.asked if r.skill == "check_plan"]


def _drafted(hr: PlanHr) -> tuple[PlanningService, object]:
    svc = _service(hr)
    run(svc.inbound("support_ticket"))
    r = next(x for x in svc.state.requests if x.id == "REQ-21")
    sp = next(x for x in svc.state.specs if x.id == r.spec)
    return svc, sp


def _plan_act(svc: PlanningService) -> str:
    """REQ-21 맥락의 check_plan 활동 (시드 활동은 뺀다)."""
    ctx = next(x.context_id for x in svc.state.requests if x.id == "REQ-21")
    return next(
        a.text for a in svc.state.activity if a.point == "check_plan" and a.context_id == ctx
    )


def test_check_plan_ok_auto_confirms() -> None:
    hr = PlanHr("none")
    svc, sp = _drafted(hr)
    (ask,) = hr.plan_asks()
    assert ask.input["draft"]["kind"] == "spec" and ask.input["draft"]["ref"] == sp.id
    assert ask.input["draft"]["area"]  # 영역 표로 정한 값
    assert "요청 근거" in ask.untrusted  # 다른 부서 문구는 untrusted로
    assert sp.hr_check == "ok" and sp.status == "확정"
    assert "기록에 없음" in _plan_act(svc)


def test_check_plan_warn_puts_para_warn_and_waits() -> None:
    hr = PlanHr("warn")
    svc, sp = _drafted(hr)
    r1 = next(p for p in sp.paras if p.id == "R1")
    assert r1.warn is not None and r1.warn.card == "card_rej"
    assert sp.hr_check == "warn" and sp.status == "초안"
    assert sp.auto_review is not None and "spec_rejected_warning" in sp.auto_review.rules
    assert "비슷한 기각 이력 있음" in _plan_act(svc)


def test_check_plan_unavailable_is_not_ok() -> None:
    hr = PlanHr(fail=True)
    svc, sp = _drafted(hr)
    assert sp.hr_check == "unknown" and sp.status == "초안"
    assert sp.auto_review is not None and "spec_hr_unchecked" in sp.auto_review.rules
    assert all(p.warn is None for p in sp.paras)  # 경고를 지어내지 않는다
    assert "확인 못 함" in _plan_act(svc)


def test_check_plan_mock_mode_keeps_old_text(service) -> None:
    run(service.inbound("support_ticket"))
    r = next(x for x in service.state.requests if x.id == "REQ-21")
    sp = next(x for x in service.state.specs if x.id == r.spec)
    assert sp.hr_check is None
    assert _plan_act(service) == "② 인사 기각 이력 확인 → 없음"


# ---- 2단계: 기각 → 인사 record_case(decision_rejected) 실제 호출


def test_reject_draft_calls_hr_with_area() -> None:
    hr = Hr()
    svc = _service(hr)
    run(
        svc.reject_draft(
            "REQ-13",
            decision_reason="안내 문구만 바꿔서는 반복 질문이 줄지 않는다",
            alternatives_considered=["운영 매뉴얼에 FAQ 추가", "결제 실패 화면 개편"],
        )
    )
    [rec] = [r for r in hr.records() if r.kind == RecordKind.DECISION_REJECTED]
    assert rec.source_refs[0] == "req:REQ-13"
    assert rec.area is not None  # 인사 check_plan이 같은 영역 초안과 비교할 수 있게
    assert hr.asked[-1].meta.idempotency_key == "REQ-13:decision_rejected"
    assert svc.state.decisions[-1].sent_to_hr is True
    assert svc.state.decisions[-1].hr_record == "ok"
    assert any("인사에 기각 기록" in t and "인사 카드 생성" in t for t in _hr_acts(svc))


def test_reject_draft_hr_failure_keeps_rejection() -> None:
    svc = _service(Hr(fail=True))
    run(
        svc.reject_draft(
            "REQ-13",
            decision_reason="안내 문구만 바꿔서는 반복 질문이 줄지 않는다",
            alternatives_considered=["운영 매뉴얼에 FAQ 추가"],
        )
    )
    req = next(r for r in svc.state.requests if r.id == "REQ-13")
    assert req.state == "기각"  # 기각은 되돌리지 않는다
    assert svc.state.decisions[-1].sent_to_hr is False
    assert svc.state.decisions[-1].hr_record == "failed"
    assert any(h.status == "hr_failed" for h in svc.soft_holds)


def test_reject_decision_each_rejection_is_its_own_card() -> None:
    hr = Hr()
    svc = _service(hr)
    for text in ("재시도 버튼만 추가", "결제 수단 화면 전체 개편"):
        run(
            svc.dispatch(
                {
                    "type": "reject_decision",
                    "spec": "SPEC-7",
                    "text": text,
                    "decision_reason": "범위가 맞지 않음",
                    "alternatives_considered": ["R1과 함께 진행"],
                }
            )
        )
    recs = [r for r in hr.records() if r.kind == RecordKind.DECISION_REJECTED]
    firsts = [r.source_refs[0] for r in recs]
    assert len(recs) == 2 and len(set(firsts)) == 2  # 인사 중복 키가 겹치지 않는다
    assert all(f.startswith("decision:") for f in firsts)
