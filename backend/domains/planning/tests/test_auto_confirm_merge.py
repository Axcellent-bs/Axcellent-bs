"""기획서 자동 확정·PR 자동 머지 (사람 사전 승인 → 규칙 교차검증 + 사후 감사, 10/23 결정).

execute params는 safety #171 규칙 키(`thresholds.toml`)에 맞춘다.
값은 도메인 상태에서 코드로 계산한다.
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from ax_contracts.schemas import ExecuteDecision
from ax_contracts.schemas.record import RecordCase, RecordKind
from ax_workflow_testing import plan
from dev_agent.service import DevService
from planning_agent.domain.autocheck import (
    IRREVERSIBLE_PREFIXES,
    spec_check,
    spec_params,
)
from planning_agent.domain.service import ActionError, PlanningService
from planning_agent.main import app
from planning_agent.runtime import get_service, set_service

ANSWER = "품절 상품도 남겨 두고 품절 표시해 주세요"
PAYMENT_MIGRATION = next(p for p in IRREVERSIBLE_PREFIXES if "migrations" in p) + "0042_drop.py"
SPEC_KEYS = {"open_questions", "warnings", "basis_count", "irreversible_count"}
PR_KEYS = {
    "changed_lines",
    "changed_files",
    "changed_paths",
    "review_mismatch_count",
    "linked_incident_count",
    "irreversible_count",
}


def run(coro):
    return asyncio.run(coro)


class CaptureGate:
    """execute 호출을 params까지 기록한다. `hold`에 넣은 액션은 safety C 보류로 돌려준다."""

    def __init__(self, hold: tuple[str, ...] = ()) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.hold = hold

    async def __call__(self, action, params, meta, evidence) -> ExecuteDecision:
        self.calls.append((action, params))
        if action in self.hold:
            return ExecuteDecision(
                execution_id="exe_hold",
                grade="C",
                status="held",
                hold_reason="over_threshold",
                review_id="rev_7",
            )
        return ExecuteDecision(execution_id=f"exe_{len(self.calls)}", grade="A", status="executed")

    def params(self, action: str) -> list[dict]:
        return [p for a, p in self.calls if a == action]


@pytest.fixture
def gate(request: pytest.FixtureRequest) -> CaptureGate:
    """기록용 관문을 끼운 서비스 (시험의 역할 표시대로 기획·개발 서비스)."""
    capture = CaptureGate()
    mark = request.node.get_closest_marker("role")
    cls = DevService if mark and mark.args[0] == "dev" else PlanningService
    set_service(cls(gate=capture))
    return capture


@pytest.fixture
def wired(wire) -> tuple:
    """기획·개발 두 서비스가 같은 기록용 관문을 쓴다 (요청 → PR 흐름 시험)."""
    capture = CaptureGate()
    return type(wire)(gate=capture), capture


def _svc() -> PlanningService:
    return get_service()


def _spec(svc: PlanningService, spec_id: str = "SPEC-7"):
    return next(s for s in svc.state.specs if s.id == spec_id)


def _pr(svc: PlanningService, pr_id: str):
    return next(p for p in svc.state.prs if p.id == pr_id)


def _clear_warn(svc: PlanningService) -> None:
    for p in _spec(svc).paras:
        p.warn = None


# ---------------------------------------------------------------- params 계산 (각 키)


@pytest.mark.role("planning")
def test_spec_params_each_key(service) -> None:
    sp = _spec(service)
    # 초기 SPEC-7: 질문 1, R2 인사 경고 1, 근거 3, 되돌릴 수 없는 영역 0
    assert spec_params(sp) == {
        "open_questions": 1,
        "warnings": 1,
        "basis_count": 3,
        "irreversible_count": 0,
    }
    assert spec_check(sp).rules == ["spec_open_question", "spec_rejected_warning"]
    sp.question = None
    sp.paras[1].warn = None
    sp.basis = []
    sp.paras[0].paths = [PAYMENT_MIGRATION, "shop-api/payment/migrations/0043.py"]
    sp.paras[1].paths = ["shop-api/accounts/pii/fields.py"]
    params = spec_params(sp)
    assert (params["open_questions"], params["warnings"]) == (0, 0)
    assert params["basis_count"] == 0
    assert params["irreversible_count"] == 2  # 문단 수 (R1·R2), 경로 수가 아님
    assert spec_check(sp).rules == ["spec_no_basis", "irreversible"]


def test_request_cannot_supply_rule_numbers(wire) -> None:
    """화면·요청이 보낸 숫자는 받지 않는다 (값은 도메인 상태에서만)."""
    with pytest.raises(ActionError) as err:
        wire.run(
            wire.planning.dispatch({"type": "confirmSpec", "spec": "SPEC-7", "open_questions": 0})
        )
    assert err.value.code == "INVALID_ACTION"
    with pytest.raises(ActionError):
        wire.run(wire.dev.dispatch({"type": "mergePr", "pr": "PR #28", "irreversible_count": 0}))


@pytest.mark.role("planning")
def test_human_confirm_sends_171_keys_and_keeps_warn_ignored(gate) -> None:
    svc = _svc()
    run(svc.confirm_spec("SPEC-7"))  # 질문·경고가 남은 채 사람 확정
    [confirm] = gate.params("confirm_spec")
    assert SPEC_KEYS <= set(confirm)
    assert (confirm["open_questions"], confirm["warnings"], confirm["basis_count"]) == (1, 1, 3)
    assert confirm["irreversible_count"] == 0 and confirm["auto"] is False
    # FR-SPEC-006 기록은 그대로, 경고 수는 정수로도 같이
    assert len(confirm["warn_ignored"]) == confirm["warnings"] == 1


def test_simple_flow_sends_171_keys(wired) -> None:
    wire, gate = wired
    wire.run(wire.planning.inbound("support_simple"))
    [confirm] = gate.params("confirm_spec")  # 기획 서비스
    assert {k: confirm[k] for k in SPEC_KEYS} == {
        "open_questions": 0,
        "warnings": 0,
        "basis_count": 2,
        "irreversible_count": 0,
    }
    [merge] = gate.params("merge_pr")  # 개발 서비스
    assert {k: merge[k] for k in PR_KEYS} == {
        "changed_lines": 4,
        "changed_files": 1,
        "changed_paths": ["shop-web/pages/notice.tsx"],
        "review_mismatch_count": 0,
        "linked_incident_count": 0,
        "irreversible_count": 0,
    }
    assert merge["auto"] is True


def test_simple_flow_merge_held_by_safety_waits(wired) -> None:
    wire, gate = wired
    P, D = wire.planning, wire.dev
    gate.hold = ("merge_pr",)
    wire.run(P.inbound("support_simple"))
    r = next(x for x in P.state.requests if x.id == "REQ-21")
    pr = D.state.prs[-1]
    assert r.state == "작업 중" and not pr.merged
    assert pr.auto_review is not None
    assert (pr.auto_review.state, pr.auto_review.gate_status, pr.auto_review.review_id) == (
        "교차검증 대기",
        "held",
        "rev_7",
    )
    assert D.state.auto_policy.streak == 0  # 머지 안 됐으니 연속 정상에 안 센다
    # 판단은 기존 사람 머지 버튼 (개발 서비스)
    gate.hold = ()
    wire.run(D.merge_pr(pr.id))
    assert D.state.prs[-1].merged and D.state.prs[-1].auto_review is None
    assert next(x for x in P.state.requests if x.id == "REQ-21").state == "완료"


# ---------------------------------------------------------------- 기획서 자동 확정 / 대기


@pytest.mark.role("planning")
def test_auto_confirm_when_all_rules_pass(gate) -> None:
    svc = _svc()
    _clear_warn(svc)
    run(svc.answer_question("SPEC-7", ANSWER))  # 질문 0·경고 0·근거 3·되돌릴 수 없음 0
    sp = _spec(svc)
    assert (sp.status, sp.auto, sp.auto_review) == ("확정", True, None)
    tasks = [t for t in svc.state.tasks if t.spec == "SPEC-7"]
    assert [(t.id, t.para, t.state) for t in tasks] == [
        ("T-11", "R1", "진행 중"),
        ("T-12", "R2", "진행 중"),
    ]
    [confirm] = gate.params("confirm_spec")
    assert {k: confirm[k] for k in SPEC_KEYS} == {
        "open_questions": 0,
        "warnings": 0,
        "basis_count": 3,
        "irreversible_count": 0,
    }
    assert confirm["auto"] is True and "warn_ignored" not in confirm
    assert "split_tasks" in [a for a, _ in gate.calls]
    flow = [a for a in svc.state.activity if a.context_id == "ctx_req_0012"]
    assert not [a for a in flow if a.kind == "사람"]  # 사람 클릭 0
    audit = [a for a in flow if a.point == "audit.auto_confirmed"]
    assert len(audit) == 1 and "사후 감사" in audit[0].text
    cases = [
        RecordCase.model_validate(p["record_case"])
        for p in gate.params("record_case")
        if "record_case" in p
    ]
    spec_case = next(c for c in cases if c.kind == RecordKind.SPEC_CONFIRMED)
    # 결정 문장은 내용만, 확정 방식은 이유로 (병합 판정이 틀 문구에 흔들리지 않게)
    assert spec_case.summary == "결제 실패 후 장바구니 유지"
    assert "자동 확정" in (spec_case.decision_reason or "")
    assert next(r for r in svc.state.requests if r.id == "REQ-12").state == "작업 중"


@pytest.mark.role("planning")
def test_open_question_waits_for_answer(gate) -> None:
    svc = _svc()
    result = run(svc.check_spec("SPEC-7"))
    sp = _spec(svc)
    assert sp.status == "초안" and sp.auto_review is not None
    assert sp.auto_review.state == "답 대기"
    assert sp.auto_review.rules == ["spec_open_question", "spec_rejected_warning"]
    assert sp.auto_review.params["open_questions"] == 1
    assert (result.outcome, result.output) == ("waiting", {"spec": "SPEC-7", "state": "답 대기"})
    assert gate.params("confirm_spec") == []  # 걸리면 confirm_spec을 부르지 않는다


@pytest.mark.role("planning")
def test_warning_left_waits_for_cross_check(gate) -> None:
    svc = _svc()
    plan(svc, "answer_question", "SPEC-7", ANSWER)
    sp = _spec(svc)
    assert sp.status == "초안" and sp.question is None
    assert (sp.auto_review.state, sp.auto_review.rules) == (
        "교차검증 대기",
        ["spec_rejected_warning"],
    )
    assert gate.params("confirm_spec") == []
    # 판단은 기존 사람 확정 버튼
    plan(svc, "confirm_spec", "SPEC-7")
    sp = _spec(svc)
    assert (sp.status, sp.auto, sp.auto_review) == ("확정", False, None)


@pytest.mark.role("planning")
def test_no_basis_and_irreversible_wait_for_cross_check(gate) -> None:
    svc = _svc()
    _clear_warn(svc)
    _spec(svc).basis = []
    _spec(svc).paras[0].paths = [PAYMENT_MIGRATION]
    plan(svc, "answer_question", "SPEC-7", ANSWER)
    sp = _spec(svc)
    assert sp.status == "초안"
    assert (sp.auto_review.state, sp.auto_review.rules) == (
        "교차검증 대기",
        ["spec_no_basis", "irreversible"],
    )


@pytest.mark.role("planning")
def test_safety_hold_on_auto_confirm_waits(gate) -> None:
    svc = _svc()
    _clear_warn(svc)
    gate.hold = ("confirm_spec",)
    plan(svc, "answer_question", "SPEC-7", ANSWER)
    sp = _spec(svc)
    assert sp.status == "초안" and sp.question is None  # 질문 답은 반영됨
    assert sp.auto_review.state == "교차검증 대기" and sp.auto_review.rules == []
    assert (sp.auto_review.gate_status, sp.auto_review.review_id) == ("held", "rev_7")
    assert not [t for t in svc.state.tasks if t.spec == "SPEC-7"]


# ---------------------------------------------------------------- PR 자동 머지 / 대기 / 사람 확인


def test_auto_merge_and_close_request(wired) -> None:
    wire, gate = wired
    P, D = wire.planning, wire.dev
    _clear_warn(P)
    _spec(P).paras[1].paths = ["shop-api/cart/notice.py"]  # R2도 허용 경로 안
    # 경고가 없으면 자동 확정 → 개발로 넘김 → 개발은 PR·검수·재검수·머지까지 버튼 없이 간다
    wire.run(P.answer_question("SPEC-7", ANSWER))
    pr31, pr32 = _pr(D, "PR #31"), _pr(D, "PR #32")
    assert pr31.merged and pr31.auto and pr31.merged_at is not None
    assert pr32.reviews == ["불일치", "통과"]  # 검수 불일치 1회(<2) → 수정 후 재검수 통과
    assert pr32.merged and pr32.auto
    assert next(r for r in P.state.requests if r.id == "REQ-12").state == "완료"
    flow = wire.activity("ctx_req_0012")
    assert not [a for a in flow if a.kind == "사람"]  # 요청부터 완료까지 사람 클릭 0
    merges = gate.params("merge_pr")
    assert [m["review_mismatch_count"] for m in merges] == [0, 1]
    assert all(m["auto"] is True for m in merges)
    cases = [
        RecordCase.model_validate(p["record_case"])
        for p in gate.params("record_case")
        if "record_case" in p
    ]
    merged = [c for c in cases if c.kind == RecordKind.PR_MERGED]
    assert [c.source_refs for c in merged] == [["pr:31"], ["pr:32"]]
    assert all("사후 감사" in c.summary for c in merged)
    assert [a.point for a in flow].count("github_merge_pull") == 2


# ---------------------------------------------------------------- 대기열 REST


def test_review_queue_rest(wire) -> None:
    # PR 대기열은 개발 서비스 화면(/api/planning/review-queue, dev 8111)이 보여 준다
    set_service(wire.dev)
    client = TestClient(app)
    assert client.get("/api/planning/review-queue").json()["data"] == []
    wire.run(wire.planning.answer_question("SPEC-7", ANSWER))
    wire.run(wire.planning.confirm_spec("SPEC-7"))  # 확정 → 개발이 PR·재검수까지 잇는다
    body = client.get("/api/planning/review-queue").json()
    rows = body["data"]
    assert body["meta"]["count"] == 1
    assert rows[0]["kind"] == "pr" and rows[0]["id"] == "PR #32"
    # 결제 화면(shop-web/checkout/)이라 사람 확인 (#200)
    assert rows[0]["state"] == "사람 확인" and rows[0]["rules"] == ["irreversible"]
    assert rows[0]["action"] == "merge_pr"
    assert rows[0]["decide_with"] == {"type": "mergePr", "pr": "PR #32"}
    assert PR_KEYS <= set(rows[0]["params"])
    # 사람 버튼으로 판단하면 대기열에서 빠진다
    res = client.post("/api/planning/actions", json=rows[0]["decide_with"])
    assert res.status_code == 200
    assert client.get("/api/planning/review-queue").json()["data"] == []


@pytest.mark.role("planning")
def test_review_queue_spec_and_state_filter(service) -> None:
    client = TestClient(app)
    res = client.post("/api/planning/actions", json={"type": "checkSpec", "spec": "SPEC-7"})
    assert res.json()["meta"]["outcome"] == "waiting"
    rows = client.get("/api/planning/review-queue?state=답 대기").json()["data"]
    assert [(r["kind"], r["id"], r["action"]) for r in rows] == [("spec", "SPEC-7", "confirm_spec")]
    assert rows[0]["decide_with"] == {"type": "confirmSpec", "spec": "SPEC-7"}
    assert SPEC_KEYS <= set(rows[0]["params"])
    assert client.get("/api/planning/review-queue?state=사람 확인").json()["data"] == []
