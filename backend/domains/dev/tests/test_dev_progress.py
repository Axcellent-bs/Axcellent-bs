"""`list_dev_progress`: 담당자 없이 개발 진행 중 항목 전체(또는 영역만으로 거른 것)를 돌려준다.

총괄이 고객의 "개발 진행 상황 알려 줘"를 처리할 때 쓴다. 채팅 계정과 명부 `member_id`를 잇는 곳이
없어서 `list_in_progress`(owner_id 필수)를 쓸 수 없다. 진행 중 판정은 `list_in_progress`와 같다.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from ax_contracts.schemas import ExecuteDecision
from ax_contracts.schemas.planning import ListInProgressOutput
from ax_workflow_testing import plan
from dev_agent.main import app
from planning_agent.domain.handover import in_progress


async def _hold_merge(action, params, meta, evidence) -> ExecuteDecision:
    """merge_pr만 safety C 보류로 돌려주는 관문 (PR을 열어 둔 채 진행 중 항목으로 남긴다)."""
    if action == "merge_pr":
        return ExecuteDecision(
            execution_id="exe_hold", grade="C", status="held", hold_reason="over_threshold"
        )
    return ExecuteDecision(execution_id="exe_ok", grade="A", status="executed")


def _start_spec7(service) -> None:
    """SPEC-7 확정 + PR 생성 (test_hr_integration과 같은 준비): T-11(백엔드 emp_01)·T-12(프론트
    emp_02), PR #31(T-11)·PR #32(T-12). 자동 머지를 보류시켜 두 PR 모두 열려 있게 한다."""
    plan(service, "answer_question", "SPEC-7", "품절 상품도 남겨 두고 품절 표시해 주세요")
    gate = service._gate
    service._gate = _hold_merge
    try:
        plan(service, "confirm_spec", "SPEC-7")
    finally:
        service._gate = gate


def _domain(service) -> dict:
    """활동 로그·창구를 뺀 도메인 상태 (read skill이 바꾸면 안 되는 부분)."""
    snap = service.snapshot()
    return {k: v for k, v in snap.items() if k not in ("activity", "outbox", "seq")}


def _call(body: dict) -> dict:
    return TestClient(app).post("/a2a", json=body).json()


def _progress(a2a_body, area: str | None = None, token: str = "mock:orchestrator") -> dict:
    payload = {"area": area} if area else {}
    return _call(a2a_body("list_dev_progress", payload, token=token, context_id="ctx_dev_0001"))


def _owner_items(service, *owners: str, area: str | None = None) -> list[str]:
    """list_in_progress와 같은 판정으로 담당자별 항목을 모은 것 (비교 기준)."""
    return sorted(i.item_id for o in owners for i in in_progress(service.state, o, area))


@pytest.mark.role("dev")
def test_list_dev_progress_returns_every_owner(service, a2a_body) -> None:
    _start_spec7(service)
    result = _progress(a2a_body)
    assert result["status"] == "completed"
    out = ListInProgressOutput.model_validate(result["output"])
    # 담당자 두 명(백엔드 emp_01·프론트 emp_02)의 항목이 함께 나온다
    assert [(i.item_id, i.kind, i.owner_id) for i in out.items] == [
        ("tkt_T-11", "ticket", "emp_01"),
        ("tkt_T-12", "ticket", "emp_02"),
        ("dep_pr_28", "deployment", "emp_01"),
        ("pr_31", "pr", "emp_01"),
        ("pr_32", "pr", "emp_02"),
    ]
    # 진행 중 판정은 list_in_progress와 같다 (담당자별 결과를 합친 것과 같다)
    assert sorted(i.item_id for i in out.items) == _owner_items(service, "emp_01", "emp_02")
    assert out.collected_at is not None and out.unavailable is None
    assert result["evidence"]["data_summary"].startswith("진행 중 항목 5건")
    refs = result["evidence"]["ref_ids"]
    for ref in ("item:tkt_T-11", "item:pr_32", "pr:28", "task:T-12"):
        assert ref in refs
    queries = result["evidence"]["queries"]
    assert queries[0] == "tasks WHERE state != '완료'"
    assert all("owner_id" not in q for q in queries)


@pytest.mark.role("dev")
def test_list_dev_progress_area_filter(service, a2a_body) -> None:
    _start_spec7(service)
    result = _progress(a2a_body, area="payment")
    items = ListInProgressOutput.model_validate(result["output"]).items
    assert {i.area for i in items} == {"payment"}
    expected = _owner_items(service, "emp_01", "emp_02", area="payment")
    assert sorted(i.item_id for i in items) == expected and expected
    assert all("area = 'payment'" in q for q in result["evidence"]["queries"])
    # 명부에 있지만 항목이 없는 영역은 빈 목록 + 확인할 수 없음
    empty = _progress(a2a_body, area="inventory")
    assert empty["status"] == "completed" and empty["output"]["items"] == []
    assert empty["evidence"]["data_summary"].startswith("확인할 수 없음")


@pytest.mark.role("dev")
def test_list_dev_progress_unknown_area_is_not_guessed(service, a2a_body) -> None:
    _start_spec7(service)
    for area in ("shop-api/payment", "결제", "Payment"):
        result = _progress(a2a_body, area=area)
        assert result["status"] == "completed"
        out = ListInProgressOutput.model_validate(result["output"])
        assert out.items == [] and out.collected_at is not None
        summary = result["evidence"]["data_summary"]
        assert summary.startswith("확인할 수 없음") and "명부에 없는 영역" in summary


@pytest.mark.role("dev")
def test_list_dev_progress_only_orchestrator(a2a_body) -> None:
    for token in ("mock:hr", "mock:finance", "mock:planning", "mock:data"):
        denied = _progress(a2a_body, token=token)
        assert (denied["status"], denied["outcome"]) == ("rejected", "FORBIDDEN_SKILL"), token
    assert _progress(a2a_body)["status"] == "completed"


@pytest.mark.role("dev")
def test_list_dev_progress_invalid_input(a2a_body) -> None:
    for payload in ({"owner_id": "emp_01"}, {"area": 3}):
        bad = _call(a2a_body("list_dev_progress", payload, token="mock:orchestrator"))
        assert (bad["status"], bad["outcome"]) == ("failed", "invalid_input"), payload


@pytest.mark.role("dev")
def test_list_dev_progress_changes_no_state(service, a2a_body) -> None:
    plan(service, "answer_question", "SPEC-7", "품절 상품도 남겨 두고 품절 표시해 주세요")
    before = _domain(service)
    first = _progress(a2a_body)
    second = _progress(a2a_body)
    assert _domain(service) == before  # 상태를 바꾸지 않는다 (활동 로그만)
    assert first["output"]["items"] == second["output"]["items"]
    assert first["evidence"] == second["evidence"]
    last = service.state.activity[-2:]
    assert [(a.from_, a.to, a.point) for a in last] == [
        ("orchestrator", "planning", "list_dev_progress"),
        ("planning", "orchestrator", "list_dev_progress"),
    ]
