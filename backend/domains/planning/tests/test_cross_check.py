"""merge_pr 교차검증 근거 모으기 (#104 safety `merge_cross_*`, 10/29 제안 A안).

장애 났던 경로(`pr_linked_incident`)에 걸린 PR은 총괄·데이터분석 근거를 모아 `cross_check=true`로
safety에 보낸다. 근거가 깨끗하면 자동 머지, 걸리면 사람 확인.
"""

from __future__ import annotations

import asyncio
from datetime import timedelta

from ax_contracts.schemas import AgentRequest, AgentResult, ExecuteDecision, TaskStatus
from planning_agent.cross_check import CrossChecker, components_of
from planning_agent.domain.autocheck import WAIT_CROSS_CHECK, WAIT_HUMAN
from planning_agent.domain.service import PlanningService


def run(coro):
    return asyncio.run(coro)


class Gate:
    def __init__(self, hold: bool = False) -> None:
        self.calls: list[tuple[str, dict]] = []
        self.hold = hold

    async def __call__(self, action, params, meta, evidence) -> ExecuteDecision:
        self.calls.append((action, params))
        if self.hold and action == "merge_pr":
            return ExecuteDecision(
                execution_id="exe_hold",
                grade="C",
                status="held",
                hold_reason="over_threshold",
                review_id="rev_7",
            )
        return ExecuteDecision(execution_id=f"exe_{len(self.calls)}", grade="A", status="executed")

    def merge_params(self) -> list[dict]:
        return [p for a, p in self.calls if a == "merge_pr"]


class Agents:
    """총괄·데이터분석 대신 답한다. `incident`가 있으면 그 사건을 진행 중으로 돌려준다."""

    def __init__(
        self,
        incident: str | None = None,
        data_status: str = "normal",
        data_fail: bool = False,
        slow: bool = False,
    ) -> None:
        self.incident = incident
        self.data_status = data_status
        self.data_fail = data_fail
        self.slow = slow
        self.asked: list[tuple[str, str, dict]] = []

    async def __call__(self, target: str, req: AgentRequest) -> AgentResult:
        self.asked.append((target, req.skill, req.input))
        if self.slow:
            await asyncio.sleep(1)
        if target == "orchestrator":
            out = (
                {"found": True, "context_id": self.incident, "component": req.input["component"]}
                if self.incident
                else {"found": False}
            )
            return AgentResult(meta=req.meta, status=TaskStatus.COMPLETED, output=out)
        if self.data_fail:
            return AgentResult(
                meta=req.meta, status=TaskStatus.FAILED, outcome="requester_not_allowed"
            )
        return AgentResult(
            meta=req.meta,
            status=TaskStatus.COMPLETED,
            output={
                "verdicts": [],
                "any_anomaly": False,
                "summary": "결제 실패율 평소 수준",
                "status": self.data_status,
            },
        )


def _service(agents: Agents | None, gate: Gate, timeout: float = 30.0) -> PlanningService:
    checker = CrossChecker(caller=agents, timeout=timeout) if agents else None
    return PlanningService(gate=gate, cross_checker=checker)


def _linked_pr(svc: PlanningService, recent: bool = False):
    """PR #28(SPEC-5 R3, 장애 표시 ctx_inc_0001)과 같은 파일을 바꾼 검수 통과 PR."""
    pr28 = next(p for p in svc.state.prs if p.id == "PR #28")
    pr = pr28.model_copy(
        update={
            "id": "PR #97",
            "number": 97,
            "task": "T-none",
            "merged": False,
            "reviews": ["통과"],
            "auto_review": None,
        }
    )
    svc.state.prs.append(pr)
    for inc in svc.state.incidents:
        if inc.context_id == "ctx_inc_0001":
            inc.opened_at = svc.clock() - (timedelta(days=1) if recent else timedelta(days=30))
    return pr


def test_components_from_paths() -> None:
    assert components_of(["shop-api/payment/config.py", "shop-web/checkout/Pay.tsx"]) == ["payment"]
    assert components_of(["shop-web/search/Box.tsx", "shop-api/payment/x.py"]) == [
        "search",
        "payment",
    ]
    # 떼어 온 레포 경로는 폴더·파일 이름 낱말로 정한다 (CamelCase도 쪼갠다)
    assert components_of(["shop-web/src/pages/Cart.tsx"]) == ["payment"]
    assert components_of(["shop-web/src/pages/OrderDetail.tsx"]) == ["payment"]
    assert components_of(["shop-web/src/pages/ProductDetail.tsx"]) == ["catalog"]
    assert components_of(["shop-web/src/pages/Home.tsx", "docs/a.md"]) == []


def test_clean_evidence_auto_merges_with_cross_check_params() -> None:
    gate, agents = Gate(), Agents()
    svc = _service(agents, gate)
    pr = _linked_pr(svc)

    assert run(svc._try_auto_merge(svc.state, pr)) is True
    params = gate.merge_params()[-1]
    assert params["cross_check"] is True
    assert params["linked_incident_count"] == 1  # safety가 cross_check로 이 규칙을 대신 본다
    assert (params["active_incident_overlap"], params["data_verdict"]) == (0, "normal")
    assert (params["recent_incident_count"], params["evidence_missing"]) == (0, 0)
    assert pr.merged
    # 총괄에는 구성요소로, 데이터분석에는 최근 24시간 창으로 묻는다
    assert (
        "orchestrator",
        "find_active_incident",
        {"component": "payment", "occurred_at": None},
    ) in agents.asked
    assert any(t == "data" and s == "anomaly_detection" for t, s, _ in agents.asked)
    assert any(a.point == "cross_check" and "근거 통과" in a.text for a in svc.state.activity)


def test_active_incident_goes_to_human_with_evidence() -> None:
    gate = Gate()
    svc = _service(Agents(incident="ctx_inc_0042", data_status="anomaly"), gate)
    pr = _linked_pr(svc, recent=True)

    assert run(svc._try_auto_merge(svc.state, pr)) is False
    assert not pr.merged and gate.merge_params() == []
    hold = pr.auto_review
    assert hold.state == WAIT_HUMAN
    assert hold.rules == [
        "merge_cross_active_incident",
        "merge_cross_data_verdict",
        "merge_cross_recent_incident",
    ]
    assert hold.params["active_incident_overlap"] == 1
    assert hold.params["recent_incident_count"] == 1
    sources = {line["source"]: line for line in hold.evidence}
    assert sources["orchestrator"]["value"] == "ctx_inc_0042" and not sources["orchestrator"]["ok"]
    assert sources["data"]["value"].startswith("이상") and not sources["data"]["ok"]


def test_data_refused_counts_as_missing_not_invented() -> None:
    """데이터분석이 아직 planning 요청을 허용하지 않으면 '확인 못 함'으로 사람 확인."""
    gate = Gate()
    svc = _service(Agents(data_fail=True), gate)
    pr = _linked_pr(svc)

    run(svc._try_auto_merge(svc.state, pr))
    hold = pr.auto_review
    assert hold.state == WAIT_HUMAN
    assert hold.rules == ["merge_cross_data_verdict", "merge_cross_evidence_missing"]
    assert (hold.params["data_verdict"], hold.params["evidence_missing"]) == ("unknown", 1)
    data = next(x for x in hold.evidence if x["source"] == "data")
    assert data["value"].startswith("확인 못 함") and "requester_not_allowed" in data["value"]


def test_slow_answer_times_out_as_missing() -> None:
    gate = Gate()
    svc = _service(Agents(slow=True), gate, timeout=0.05)
    pr = _linked_pr(svc)

    run(svc._try_auto_merge(svc.state, pr))
    assert pr.auto_review.params["evidence_missing"] == 2
    assert all(
        "응답 없음" in x["value"] for x in pr.auto_review.evidence if x["source"] != "planning"
    )


def test_safety_hold_after_evidence_is_human() -> None:
    gate = Gate(hold=True)
    svc = _service(Agents(), gate)
    pr = _linked_pr(svc)

    run(svc._try_auto_merge(svc.state, pr))
    assert gate.merge_params()[-1]["cross_check"] is True
    assert pr.auto_review.state == WAIT_HUMAN and pr.auto_review.gate_status == "held"


def test_no_checker_keeps_old_behavior() -> None:
    """목 모드 기본값: 다른 부서를 부르지 않고 예전처럼 교차검증 대기."""
    gate = Gate()
    svc = _service(None, gate)
    pr = _linked_pr(svc)

    run(svc._try_auto_merge(svc.state, pr))
    assert pr.auto_review.state == WAIT_CROSS_CHECK
    assert pr.auto_review.rules == ["pr_linked_incident"] and pr.auto_review.evidence == []


def test_unlinked_pr_does_not_ask_anyone() -> None:
    agents = Agents()
    svc = _service(agents, Gate())
    pr28 = next(p for p in svc.state.prs if p.id == "PR #28")
    pr = pr28.model_copy(
        update={
            "id": "PR #96",
            "number": 96,
            "task": "T-none",
            "merged": False,
            "reviews": ["통과"],
            "changed_paths": ["docs/notice.md"],
        }
    )
    svc.state.prs.append(pr)
    assert run(svc._try_auto_merge(svc.state, pr)) is True
    assert agents.asked == []


def test_related_prs_find_real_repo_path_by_component() -> None:
    """총괄이 사건 component(payment)로 관련 PR을 물으면 떼어 온 레포 경로도 찾는다."""
    from planning_agent.domain.rules import merged_in_window

    svc = _service(None, Gate())
    pr28 = next(p for p in svc.state.prs if p.id == "PR #28")
    now = svc.clock()
    pr = pr28.model_copy(
        update={
            "id": "PR #95",
            "number": 95,
            "task": "T-none",
            "merged": True,
            "merged_at": now,
            "changed_paths": ["shop-web/src/pages/Cart.tsx"],
        }
    )
    svc.state.prs.append(pr)
    found = merged_in_window(svc.state, now - timedelta(minutes=15), now, "payment")
    assert pr in found


def test_related_prs_include_merge_just_before_symptoms() -> None:
    """총괄은 장애 시작 시각부터 묻는다. 그 직전에 머지된 PR도 후보여야 한다 (30분 앞부터)."""
    svc = _service(None, Gate())
    pr28 = next(p for p in svc.state.prs if p.id == "PR #28")
    start = svc.clock()
    pr = pr28.model_copy(
        update={
            "id": "PR #94",
            "number": 94,
            "task": "T-none",
            "merged": True,
            "merged_at": start - timedelta(seconds=12),
            "changed_paths": ["shop-web/src/pages/Cart.tsx"],
        }
    )
    svc.state.prs.append(pr)
    found = run(
        svc.find_related_prs(
            context_id="ctx_inc_x",
            requester="orchestrator",
            window_from=start,
            window_to=start + timedelta(minutes=15),
            component="payment",
        )
    )
    assert pr in [p for p, _, _ in found]
