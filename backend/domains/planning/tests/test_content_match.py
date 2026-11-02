"""요청 묶기는 제목이 아니라 내용으로 판단한다 (LLM이 있을 때).

- 제목이 같아도 내용이 다르면 묶지 않는다
- 제목이 달라도 내용이 같으면 묶고, 요청 부서에는 원래 요청의 사건 번호를 돌려준다
- LLM 판단이 실패하면 예전 같은 제목 규칙으로 대신한다
"""

from __future__ import annotations

import asyncio

import pytest

from ax_contracts.schemas import ExecuteDecision
from planning_agent.domain.service import PlanningService
from planning_agent.related import LexicalEmbedder, Relater, _Judged, _Judgement


def run(coro):
    return asyncio.run(coro)


async def _gate(action, params, meta, evidence) -> ExecuteDecision:
    return ExecuteDecision(execution_id="exe_ok", grade="A", status="executed")


class JudgeLLM:
    """두 번째 요청부터 판단한다. relation: 첫 요청과의 관계. fail이면 판단 실패."""

    def __init__(self, relation: str = "none", fail: bool = False) -> None:
        self.relation = relation
        self.fail = fail
        self.first: str | None = None
        self.seen: list[dict] = []

    async def structured(self, prompt, schema, untrusted=None, context_id=None):
        self.seen.append(untrusted)
        if self.fail:
            raise RuntimeError("LLM 꺼짐")
        ids = [c["id"] for c in untrusted["candidates"]]
        items = [
            _Judged(
                id=i,
                relation=self.relation if i == self.first else "none",
                reason="둘 다 같은 대상",
            )
            for i in ids
        ]
        return _Judgement(items=items)


def _svc(llm: JudgeLLM) -> PlanningService:
    return PlanningService(gate=_gate, relater=Relater(LexicalEmbedder(), llm=llm))


def _ticket(svc, title: str, evidence: str, key: str):
    return run(
        svc.create_ticket(
            context_id=f"ctx_{key}",
            requester="marketing",
            title=title,
            evidence=evidence,
            source_ref=None,
            evidence_ids=[],
            idempotency_key=key,
        )
    )


def _req(svc, rid: str):
    return next(r for r in svc.state.requests if r.id == rid)


@pytest.mark.role("planning")
def test_same_title_different_content_is_not_bound() -> None:
    llm = JudgeLLM("related")
    svc = _svc(llm)
    a = _ticket(svc, "가을 프로모션 배너 교체", "메인 상단 배너를 가을 이미지로", "k1")
    llm.first = a.output["request_id"]
    b = _ticket(svc, "가을 프로모션 배너 교체", "앱 푸시 알림 배너 문구를 바꿔 달라", "k2")
    assert b.outcome == "created"
    r = _req(svc, b.output["request_id"])
    assert r.duplicate_of is None and r.context_id != _req(svc, llm.first).context_id
    # 판단에 요청 근거(내용)까지 넣는다
    assert "앱 푸시 알림 배너" in llm.seen[-1]["new"]["text"]


@pytest.mark.role("planning")
def test_different_title_same_content_is_bound() -> None:
    llm = JudgeLLM("same")
    svc = _svc(llm)
    a = _ticket(
        svc, "결제 실패 시 장바구니 유지", "결제 실패 뒤 장바구니가 비워진다는 문의 12건", "k1"
    )
    llm.first = a.output["request_id"]
    b = _ticket(
        svc, "결제 오류 나면 담은 상품 사라짐", "결제 실패하면 장바구니가 비워져요 (문의 5건)", "k2"
    )
    first = _req(svc, llm.first)
    assert b.outcome == "linked_duplicate"
    assert b.output["duplicate_of"] == first.id and b.output["context_id"] == first.context_id
    r = _req(svc, b.output["request_id"])
    assert r.state == "중복 연결" and r.spec is None
    assert any(
        "내용 판단" in x.text for x in svc.state.activity if x.context_id == first.context_id
    )


@pytest.mark.role("planning")
def test_closed_origin_is_not_bound() -> None:
    llm = JudgeLLM("same")
    svc = _svc(llm)
    a = _ticket(svc, "배송 조회 송장 번호", "송장 번호가 안 보인다", "k1")
    llm.first = a.output["request_id"]
    _req(svc, llm.first).state = "완료"
    b = _ticket(svc, "배송 조회에서 송장 번호 보기", "송장 번호가 안 보인다 (재발)", "k2")
    assert b.outcome == "created"


@pytest.mark.role("planning")
def test_llm_failure_falls_back_to_same_title() -> None:
    svc = _svc(JudgeLLM(fail=True))
    a = _ticket(svc, "가을 프로모션 배너 교체", "메인 배너", "k1")
    b = _ticket(svc, "가을 프로모션 배너 교체", "메인 배너 (다시)", "k2")
    assert b.outcome == "linked_duplicate" and b.output["duplicate_of"] == a.output["request_id"]


@pytest.mark.role("planning")
def test_follow_up_from_dev_is_not_bound() -> None:
    """개발이 장애 연결로 만든 재발 방지 요청은 내용이 같아도 묶지 않는다 (사건 번호 유지)."""
    from ax_workflow.models import RelatedItem, Request

    llm = JudgeLLM("same")
    svc = _svc(llm)
    a = _ticket(
        svc, "결제 실패 시 장바구니 유지", "결제 실패 뒤 장바구니가 비워진다는 문의 12건", "k1"
    )
    llm.first = a.output["request_id"]
    req = Request(
        id="REQ-90",
        context_id="ctx_inc_0007",
        from_="dev",
        via="link_incident (재발 방지)",
        title="결제 실패 시 장바구니 비워짐 재발 방지",
        evidence="INC-7 장바구니 초기화 장애",
        state="접수",
    )
    # LLM이 "같은 일"로 판단한 상태를 만든다 (막지 않으면 첫 요청에 묶인다)
    first = next(r for r in svc.state.requests if r.id == llm.first)
    req.related = [
        RelatedItem(
            id=first.id, title=first.title, score=0.9, relation="same", reason="같음", by="llm"
        )
    ]
    svc.state.requests.append(req)
    svc._bind_by_content(svc.state, req)
    assert req.duplicate_of is None and req.context_id == "ctx_inc_0007"
