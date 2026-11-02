"""묶인 요청 풀기 (`unlink_duplicate`): 사람이 확인하고 같은 요청 묶음에서 따로 떼어 기획한다."""

from __future__ import annotations

import asyncio

import pytest

from planning_agent.domain.service import ActionError

TITLE = "배송 문의 급증 — 안내·화면 점검 검토"


def run(coro):
    return asyncio.run(coro)


def _ticket(service, key: str, requester: str = "support"):
    res = run(
        service.create_ticket(
            context_id=f"ctx_a2a_{key}",
            requester=requester,
            title=TITLE,
            evidence="최근 60분 배송 문의 7건 (평소의 5배)",
            source_ref=None,
            evidence_ids=["inq_7"],
            idempotency_key=key,
        )
    )
    return next(r for r in service.state.requests if r.id == res.output["request_id"])


def _req(service, rid: str):
    return next(r for r in service.state.requests if r.id == rid)


def test_unlink_makes_its_own_request_and_drafts(service) -> None:
    first = _ticket(service, "k1")
    second = _ticket(service, "k2", requester="marketing")
    assert second.duplicate_of == first.id and second.context_id == first.context_id

    res = run(service.dispatch({"type": "unlinkDuplicate", "request": second.id}))
    r = _req(service, second.id)
    assert r.duplicate_of is None
    assert r.context_id != first.context_id and r.context_id.startswith("ctx_req_")
    assert res.output["context_id"] == r.context_id and res.output["from"] == first.id
    # 사람 클릭 없이 다음 단계(기획서 초안)로 이어 간다
    assert r.spec is not None and r.state != "중복 연결"
    # 원래 요청은 그대로
    assert _req(service, first.id).context_id == first.context_id
    # 요청 부서에 새 사건 번호를 알린다
    notes = [a for a in service.state.activity if a.point == "request_unlinked"]
    assert notes and notes[-1].to == "marketing" and r.context_id in notes[-1].text


def test_unlink_not_linked_is_no_change(service) -> None:
    first = _ticket(service, "k1")
    before = first.context_id
    res = run(service.dispatch({"type": "unlinkDuplicate", "request": first.id}))
    assert not res.changed
    assert _req(service, first.id).context_id == before


def test_unlink_unknown_request(service) -> None:
    with pytest.raises(ActionError):
        run(service.dispatch({"type": "unlinkDuplicate", "request": "REQ-999"}))
