"""hr 연동: list_in_progress·verify_handover skill, decision_rejected 전송, warn 무시 확정 기록.

계약은 #103 (`contracts/src/ax_contracts/schemas/planning.py`·`hr.py`·`record.py`).
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from ax_contracts.schemas import ExecuteDecision
from ax_contracts.schemas.record import RecordCase, RecordKind
from ax_workflow_testing import plan
from planning_agent.domain.service import ActionError, PlanningService
from planning_agent.main import app
from planning_agent.runtime import get_service, set_service


def run(coro):
    return asyncio.run(coro)


def _call(body: dict) -> dict:
    return TestClient(app).post("/a2a", json=body).json()


def _list(a2a_body, owner_id: str, area: str | None = None, token: str = "mock:hr") -> dict:
    payload = {"owner_id": owner_id} | ({"area": area} if area else {})
    return _call(a2a_body("list_in_progress", payload, token=token, context_id="ctx_hnd_0001"))


def _verify(a2a_body, ids: list[str], owner_id: str = "emp_01", area: str | None = None) -> dict:
    payload = {"handover_id": "hnd_1", "owner_id": owner_id, "handover_item_ids": ids}
    if area:
        payload["area"] = area
    return _call(
        a2a_body("verify_handover", payload, token="mock:safety", context_id="ctx_hnd_0001")
    )


async def _hold_merge(action, params, meta, evidence) -> ExecuteDecision:
    """merge_pr만 safety C 보류로 돌려주는 관문 (PR을 열어 둔 채 진행 중 항목으로 남긴다)."""
    if action == "merge_pr":
        return ExecuteDecision(
            execution_id="exe_hold", grade="C", status="held", hold_reason="over_threshold"
        )
    return ExecuteDecision(execution_id="exe_ok", grade="A", status="executed")


def _start_spec7(service) -> None:
    """SPEC-7 확정 + PR 생성: T-11(백엔드)·T-12(프론트), PR #31(T-11)·PR #32(T-12).

    PR #31의 자동 머지는 safety가 보류한 것으로 두어(교차검증 대기) 두 PR 모두 열려 있게 한다.
    """
    plan(service, "answer_question", "SPEC-7", "품절 상품도 남겨 두고 품절 표시해 주세요")
    # 확정본을 넘겨받으면 개발 서비스가 바로 PR을 만든다 (분리 모드). 그때 머지를 보류시킨다
    gate = service._gate
    service._gate = _hold_merge
    try:
        plan(service, "confirm_spec", "SPEC-7")
    finally:
        service._gate = gate
    pr31 = next(p for p in service.state.prs if p.id == "PR #31")
    assert pr31.auto_review is not None and pr31.auto_review.state == "교차검증 대기"


def _domain(service) -> dict:
    """활동 로그·창구를 뺀 도메인 상태 (read skill이 바꾸면 안 되는 부분)."""
    snap = service.snapshot()
    return {k: v for k, v in snap.items() if k not in ("activity", "outbox", "seq")}


class CaptureGate:
    """execute 호출을 params까지 기록하는 관문 (모두 executed)."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    async def __call__(self, action, params, meta, evidence) -> ExecuteDecision:
        self.calls.append((action, params))
        return ExecuteDecision(execution_id=f"exe_{len(self.calls)}", grade="A", status="executed")

    def params(self, action: str) -> list[dict]:
        return [p for a, p in self.calls if a == action]


@pytest.fixture
def gate() -> CaptureGate:
    capture = CaptureGate()
    set_service(PlanningService(gate=capture))
    return capture


def _svc() -> PlanningService:
    return get_service()


# ---- decision_rejected


def _rejected_cases(gate: CaptureGate) -> list[RecordCase]:
    cases = [RecordCase.model_validate(p["record_case"]) for p in gate.params("record_case")]
    return [c for c in cases if c.kind == RecordKind.DECISION_REJECTED]


@pytest.mark.role("planning")
def test_reject_draft_sends_decision_rejected(gate) -> None:
    svc = _svc()
    result = run(
        svc.reject_draft(
            "REQ-13",
            decision_reason="안내 문구만 바꿔서는 반복 질문이 줄지 않는다",
            alternatives_considered=["운영 매뉴얼에 FAQ 추가", "결제 실패 화면 개편"],
        )
    )
    req = next(r for r in svc.state.requests if r.id == "REQ-13")
    assert req.state == "기각"
    assert result.output["record_kind"] == "decision_rejected"
    [case] = _rejected_cases(gate)
    assert case.context_id == "ctx_req_0013"
    assert case.decision_reason == "안내 문구만 바꿔서는 반복 질문이 줄지 않는다"
    assert case.alternatives_considered == ["운영 매뉴얼에 FAQ 추가", "결제 실패 화면 개편"]
    assert case.author == "planning" and "req:REQ-13" in case.source_refs
    keys = [c.idempotency_key for c in svc.gate_calls if c.action == "record_case"]
    assert keys == ["ctx_req_0013:record_case:REQ-13:decision_rejected"]
    decision = svc.state.decisions[-1]
    assert decision.text.startswith("[기각한 안]") and decision.sent_to_hr
    to_hr = [a for a in svc.state.activity if a.to == "hr" and a.point == "record_case"]
    assert "decision_rejected" in to_hr[-1].text
    assert any(m.to == "hr" and "decision_rejected" in m.text for m in svc.state.outbox)


@pytest.mark.parametrize(
    ("reason", "alternatives"),
    [("", ["대안"]), ("이유", []), ("   ", ["  "])],
)
@pytest.mark.role("planning")
def test_reject_without_reason_or_alternatives_is_not_sent(gate, reason, alternatives) -> None:
    svc = _svc()
    before = svc.snapshot()
    with pytest.raises(ActionError) as err:
        run(
            svc.reject_draft("REQ-13", decision_reason=reason, alternatives_considered=alternatives)
        )
    assert err.value.code == "MISSING_DECISION_REASON"
    with pytest.raises(ActionError):
        plan(svc, "reject_decision", "SPEC-7", "재시도 버튼만 추가", reason, alternatives)
    assert svc.snapshot() == before
    assert gate.params("record_case") == []  # hr에 보내지 않았다


@pytest.mark.role("planning")
def test_reject_decision_records_rejected_alternative(gate) -> None:
    svc = _svc()
    result = run(
        svc.reject_decision(
            "SPEC-7",
            "재시도 버튼만 추가",
            decision_reason="장바구니가 비면 재시도해도 다시 담아야 한다",
            alternatives_considered=["장바구니 보존과 함께 재시도 버튼"],
        )
    )
    [case] = _rejected_cases(gate)
    assert case.context_id == "ctx_req_0012" and case.summary == "재시도 버튼만 추가"
    assert result.output["decision_id"] == svc.state.decisions[-1].id
    assert svc.state.decisions[-1].spec == "SPEC-7"


@pytest.mark.role("planning")
def test_rest_reject_endpoint(service) -> None:
    client = TestClient(app)
    res = client.post("/api/planning/requests/REQ-14/reject", json={"decision_reason": "x"})
    assert res.status_code == 400
    assert res.json()["error"]["code"] == "MISSING_DECISION_REASON"
    assert next(r for r in service.state.requests if r.id == "REQ-14").state == "접수"
    res = client.post(
        "/api/planning/requests/REQ-14/reject",
        json={"decision_reason": "일시적 지표 흔들림", "alternatives_considered": ["관찰 1주"]},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["meta"]["changed"] is True
    assert body["meta"]["result"]["record_kind"] == "decision_rejected"
    assert next(r for r in body["data"]["requests"] if r["id"] == "REQ-14")["state"] == "기각"
    missing = client.post(
        "/api/planning/requests/REQ-999/reject",
        json={"decision_reason": "a", "alternatives_considered": ["b"]},
    )
    assert missing.status_code == 404
    # /actions의 camelCase도 같은 길
    res = client.post("/api/planning/actions", json={"type": "rejectDraft", "request": "REQ-13"})
    assert res.status_code == 400 and res.json()["error"]["code"] == "MISSING_DECISION_REASON"


# ---- warn 무시 확정 (FR-SPEC-006)


@pytest.mark.role("planning")
def test_confirm_with_warn_records_warn_ignored(gate) -> None:
    svc = _svc()
    run(svc.confirm_spec("SPEC-7"))  # R2에 인사 경고(DC-31)가 남아 있다
    flow = [a for a in svc.state.activity if a.context_id == "ctx_req_0012"]
    audit = [a for a in flow if a.point == "audit.warn_ignored"]
    assert len(audit) == 1 and "warn 무시 진행" in audit[0].text and "R2(DC-31)" in audit[0].text
    assert any(a.to == "hr" and "warn 무시 진행" in a.text for a in flow)
    [confirm] = gate.params("confirm_spec")
    assert confirm["warn_ignored"][0] == {
        "para": "R2",
        "card": "DC-31",
        "text": '"재시도 버튼만 추가"는 9/30 기각된 안. R1과 함께일 때만 진행',
    }
    [case_params] = gate.params("record_case")
    case = RecordCase.model_validate(case_params["record_case"])
    assert case.kind == RecordKind.SPEC_CONFIRMED
    assert case.summary == "결제 실패 후 장바구니 유지"
    assert "warn 무시 진행" in (case.decision_reason or "") and "팀장" in case.decision_reason
    assert {"para:SPEC-7:R2", "card:DC-31"} <= set(case.source_refs)
    assert case_params["warn_ignored"] == confirm["warn_ignored"]


@pytest.mark.role("planning")
def test_confirm_without_warn_has_no_warn_ignored(gate) -> None:
    svc = _svc()
    for p in next(s for s in svc.state.specs if s.id == "SPEC-7").paras:
        p.warn = None
    plan(svc, "confirm_spec", "SPEC-7")
    assert not [a for a in svc.state.activity if a.point == "audit.warn_ignored"]
    assert "warn_ignored" not in gate.params("confirm_spec")[0]
    [case_params] = gate.params("record_case")
    assert "warn_ignored" not in case_params
    assert "warn 무시" not in RecordCase.model_validate(case_params["record_case"]).summary


# ---- verify_handover: safety 교차검증 호출 형식 (#187)


def _review(params: dict, action: str = "confirm_handover") -> dict:
    """safety `Gate._cross_verify`가 verifier skill에 넣는 input과 같은 모양."""
    return {
        "execution_id": "exe_hnd_1",
        "action": action,
        "requester": "hr",
        "params": params,
        "computed_amount": None,
        "threshold_version": "2026-10-23",
    }


def _safety_verdict(result: dict) -> str:
    """safety `call_verifier` 규칙: completed + (decision approved 또는 approved true)만 승인."""
    if result["status"] != "completed":
        return "unavailable"
    out = result["output"]
    return (
        "approved"
        if out.get("decision") == "approved" or out.get("approved") is True
        else "rejected"
    )


def _verify_review(a2a_body, params: dict, action: str = "confirm_handover") -> dict:
    body = a2a_body(
        "verify_handover",
        _review(params, action),
        token="mock:safety",
        context_id="ctx_hnd_0001",
        idempotency_key="verify:exe_hnd_1",
    )
    return _call(body)


HR_PARAMS = {
    "handover_id": "hnd_1",
    "owner_id": "emp_01",
    "area": "payment",
    "completeness_percent": 90,
    "target": "hnd_1",
    "summary": "인수인계 확정 요청 emp_01→emp_02 (payment), 완성도 90%",
}
