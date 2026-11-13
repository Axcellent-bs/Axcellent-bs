"""withdraw_ticket skill (11/13): 요청 부서가 자기가 보낸 요청을 거둬들이면 기획 목록에서 지운다."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from ax_contracts.schemas.planning import CreateTicketOutput, WithdrawTicketOutput
from planning_agent.main import app

TITLE = "쿠폰 적용 후 결제 금액이 두 번 계산된다"


def _call(body: dict) -> dict:
    return TestClient(app).post("/a2a", json=body).json()


def _ticket(a2a_body, *, token: str = "mock:finance", key: str = "ct-1", title: str = TITLE):
    body = a2a_body(
        "create_ticket",
        {"title": title, "evidence": "수동 업로드 7회", "source_ref": "fin_req_0001"},
        token=token,
        context_id="ctx_req_finance_0001",
        idempotency_key=key,
    )
    return CreateTicketOutput.model_validate(_call(body)["output"])


def _withdraw(
    a2a_body,
    request_id: str,
    *,
    token: str = "mock:finance",
    source_ref: str | None = "fin_req_0001",
    key: str | None = "wd-1",
) -> dict:
    payload: dict = {"request_id": request_id, "reason": "잘못 보냄"}
    if source_ref is not None:
        payload["source_ref"] = source_ref
    return _call(
        a2a_body(
            "withdraw_ticket",
            payload,
            token=token,
            context_id="ctx_req_finance_0001",
            idempotency_key=key,
        )
    )


@pytest.mark.role("planning")
def test_withdraw_removes_the_request(service, a2a_body) -> None:
    service.state.auto_policy.level = 0  # 자동 처리 없이 접수에 머문다
    ticket = _ticket(a2a_body)
    req = next(r for r in service.state.requests if r.id == ticket.request_id)
    assert req.from_ == "finance"
    result = _withdraw(a2a_body, ticket.request_id)
    assert (result["status"], result["outcome"]) == ("completed", "withdrawn")
    assert WithdrawTicketOutput.model_validate(result["output"]).request_id == ticket.request_id
    assert all(r.id != ticket.request_id for r in service.state.requests)
    assert any(c.action == "withdraw_ticket" for c in service.gate_calls)
    acts = [a for a in service.state.activity if a.point == "withdraw_ticket"]
    assert acts and acts[-1].from_ == "finance" and "잘못 보냄" in acts[-1].text
    # 같은 키로 다시 오면 이전 결과 그대로, 새 키면 이제 없다
    assert _withdraw(a2a_body, ticket.request_id)["outcome"] == "withdrawn"
    assert _withdraw(a2a_body, ticket.request_id, key="wd-2")["outcome"] == "not_found"


@pytest.mark.role("planning")
def test_withdraw_also_removes_its_draft_spec(service, a2a_body) -> None:
    ticket = _ticket(a2a_body)
    req = next(r for r in service.state.requests if r.id == ticket.request_id)
    assert req.spec is not None
    spec_id = req.spec
    # 실제 모드에서 흔한 모양: 기획서 초안에 머물고 아직 개발로 안 넘어간 요청
    sp = next(s for s in service.state.specs if s.id == spec_id)
    sp.status, sp.handed_to_dev = "초안", False
    service.state.tasks = [t for t in service.state.tasks if t.spec != spec_id]
    result = _withdraw(a2a_body, ticket.request_id)
    assert result["outcome"] == "withdrawn"
    assert WithdrawTicketOutput.model_validate(result["output"]).removed_spec == spec_id
    assert all(s.id != spec_id for s in service.state.specs)


@pytest.mark.role("planning")
def test_request_already_with_dev_tasks_is_not_withdrawn(service, a2a_body) -> None:
    ticket = _ticket(a2a_body)  # 목 모드는 자동 확정 → 작업까지 이어 간다
    req = next(r for r in service.state.requests if r.id == ticket.request_id)
    assert any(t.spec == req.spec for t in service.state.tasks)
    assert _withdraw(a2a_body, ticket.request_id)["outcome"] == "in_progress"
    assert any(r.id == ticket.request_id for r in service.state.requests)


@pytest.mark.role("planning")
def test_other_department_or_wrong_source_ref_cannot_withdraw(service, a2a_body) -> None:
    service.state.auto_policy.level = 0
    ticket = _ticket(a2a_body)
    other = _withdraw(a2a_body, ticket.request_id, token="mock:support", key="wd-a")
    assert other["outcome"] == "not_yours"
    wrong = _withdraw(a2a_body, ticket.request_id, source_ref="fin_req_9999", key="wd-b")
    assert wrong["outcome"] == "not_yours"
    assert any(r.id == ticket.request_id for r in service.state.requests)


@pytest.mark.role("planning")
def test_handed_to_dev_is_not_withdrawn(service, a2a_body) -> None:
    ticket = _ticket(a2a_body)
    req = next(r for r in service.state.requests if r.id == ticket.request_id)
    sp = next(s for s in service.state.specs if s.id == req.spec)
    service.state.tasks = [t for t in service.state.tasks if t.spec != sp.id]
    sp.handed_to_dev = True
    result = _withdraw(a2a_body, ticket.request_id)
    assert result["outcome"] == "in_progress"
    assert any(r.id == ticket.request_id for r in service.state.requests)


@pytest.mark.role("planning")
def test_request_with_linked_duplicates_is_not_withdrawn(service, a2a_body) -> None:
    service.state.auto_policy.level = 0
    first = _ticket(a2a_body)
    linked = _ticket(a2a_body, token="mock:support", key="ct-2")
    if linked.duplicate_of != first.request_id:
        pytest.skip("같은 제목 묶기가 꺼진 설정")
    assert _withdraw(a2a_body, first.request_id)["outcome"] == "in_progress"
    # 묶인 쪽(지원)은 자기 요청만 거둬들일 수 있다
    assert _withdraw(a2a_body, first.request_id, token="mock:support", key="x")["outcome"] == (
        "not_yours"
    )


@pytest.mark.role("planning")
def test_withdraw_needs_an_idempotency_key(service, a2a_body) -> None:
    ticket = _ticket(a2a_body)
    result = _withdraw(a2a_body, ticket.request_id, key=None)
    assert result["outcome"] == "missing_idempotency_key"
    assert any(r.id == ticket.request_id for r in service.state.requests)
