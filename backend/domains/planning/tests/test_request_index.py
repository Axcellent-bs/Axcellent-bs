"""요청 색인 (memory request 카드): 기획·개발 서버가 같은 색인으로 서로의 요청을 찾는다."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

from ax_contracts.schemas import AgentResult, ExecuteDecision, TaskStatus
from planning_agent import dev_chat, related
from planning_agent.domain.models import Request
from planning_agent.domain.service import PlanningService
from planning_agent.related import LexicalEmbedder, Relater, _Judged, _Judgement
from planning_agent.request_index import Hit, IndexUnavailable, MemoryRequestIndex


def run(coro):
    return asyncio.run(coro)


async def _gate(action, params, meta, evidence) -> ExecuteDecision:
    return ExecuteDecision(execution_id="exe_ok", grade="A", status="executed")


PLANNING_ONLY = Request(
    id="REQ-31",
    context_id="ctx_req_0031",
    **{"from": "support"},
    via="create_ticket",
    title="장바구니 배송비 문구를 5만 원 이상 무료로",
    evidence="고객 문의",
    state="기획 중",
)


class FakeIndex:
    """memory 대신. 기획 쪽에만 있는 요청 하나를 돌려준다."""

    owner = "dev"

    def __init__(self, state: str = "기획 중", fail: bool = False):
        self.state = state
        self.fail = fail
        self.written: list[str] = []

    async def search(self, text, top_k):
        if self.fail:
            raise IndexUnavailable("memory 꺼짐")
        return [Hit("REQ-31", "planning", 0.82)]

    async def peer_requests(self, owner):
        return [PLANNING_ONLY.model_copy(update={"state": self.state})]

    async def write(self, req, text):
        self.written.append(req.id)


class Judge:
    async def structured(self, prompt, schema, untrusted=None, context_id=None):
        return _Judgement(items=[_Judged(id="REQ-31", relation="conflict", reason="같은 문구")])


def _dev_request(svc: PlanningService, title: str):
    return run(
        svc.create_ticket(
            context_id="ctx_x",
            requester="support",
            title=title,
            evidence="개발팀",
            source_ref=None,
            evidence_ids=[],
            idempotency_key=title,
        )
    )


def test_finds_request_only_on_the_other_service() -> None:
    """개발 서버가 기획 서버에만 있는 요청(아직 개발로 안 넘어옴)을 memory 색인으로 찾는다."""
    index = FakeIndex()
    svc = PlanningService(gate=_gate, relater=Relater(LexicalEmbedder(), Judge(), index))
    out = _dev_request(svc, "장바구니 배송비 문구를 3만 원 이상 무료로")
    req = next(r for r in svc.state.requests if r.id == out.output["request_id"])
    hit = next(x for x in req.related if x.id == "REQ-31")
    assert (hit.relation, hit.by, hit.remote_state) == ("conflict", "llm", "기획 중")
    assert related.open_conflicts(svc.state, req) == [hit]  # 상대가 안 끝났으니 충돌로 남음
    assert req.id in index.written  # 다음 요청이 찾도록 색인에 넣음


def test_finished_remote_request_is_not_open_conflict() -> None:
    index = FakeIndex(state="완료")
    svc = PlanningService(gate=_gate, relater=Relater(LexicalEmbedder(), Judge(), index))
    out = _dev_request(svc, "장바구니 배송비 문구를 3만 원 이상 무료로")
    req = next(r for r in svc.state.requests if r.id == out.output["request_id"])
    assert related.open_conflicts(svc.state, req) == []


def test_index_down_falls_back_to_local_search() -> None:
    index = FakeIndex(fail=True)
    svc = PlanningService(gate=_gate, relater=Relater(LexicalEmbedder(), None, index))
    out = _dev_request(svc, "결제 실패하면 장바구니가 비워져요")
    req = next(r for r in svc.state.requests if r.id == out.output["request_id"])
    assert req.related and all(x.by == "lexical" for x in req.related)


def test_settled_now_reads_other_service() -> None:
    svc = PlanningService(gate=_gate, relater=None)
    assert run(related.settled_now(svc.state, "REQ-31", FakeIndex("기획 중"))) is False
    assert run(related.settled_now(svc.state, "REQ-31", FakeIndex("완료"))) is True
    assert run(related.settled_now(svc.state, "REQ-31", None)) is False  # 모르면 안 끝난 것으로


def test_waiting_on_remote_request_resumes_when_it_finishes(monkeypatch) -> None:
    """다른 서비스 요청을 기다리는 대화는 그 요청이 끝났을 때 풀린다."""
    from planning_agent.domain.models import ChatMessage, ChatPlan, ChatThread

    index = FakeIndex(state="기획 중")
    svc = PlanningService(gate=_gate, relater=Relater(LexicalEmbedder(), None, index))
    started: list[str] = []

    async def fake_start(svc_, tid, plan, cid, reply="", skip_queue=False):
        started.append(tid)

    monkeypatch.setattr(dev_chat, "_start", fake_start)
    t = datetime.now(UTC)
    plan = ChatPlan(title="배송비 문구", plan="3만 원으로")
    svc.state.chats.append(
        ChatThread(
            id="chat-9",
            title="배송비",
            status="waiting",
            messages=[ChatMessage(id="m1", role="assistant", text="대기", at=t, plan=plan)],
            connection="default",
            waiting_for=["REQ-31"],
            created_at=t,
            updated_at=t,
        )
    )
    run(dev_chat.resume_waiting(svc))
    assert started == []  # 기획 쪽 요청이 아직 진행 중
    index.state = "완료"
    run(dev_chat.resume_waiting(svc))
    assert started == ["chat-9"]


def test_memory_index_writes_request_card_and_reads_hits() -> None:
    calls = []

    async def caller(target, req):
        calls.append((target, req.skill, req.input, req.meta.idempotency_key))
        if req.skill == "card_search":
            card = {
                "card_id": "card_1",
                "kind": "request",
                "context_id": "ctx_req_0031",
                "title": "배송비",
                "body": "배송비",
                "author": "planning",
                "confidence": "mid",
                "created_at": "2026-10-30T08:00:00Z",
                "status": "active",
                "extra": {"request_id": "REQ-31", "owner": "planning"},
            }
            out = {"hits": [{"card": card, "score": 0.9, "low_confidence": False}]}
            return AgentResult(meta=req.meta, status=TaskStatus.COMPLETED, output=out)
        return AgentResult(
            meta=req.meta,
            status=TaskStatus.COMPLETED,
            output={"card_id": "card_1", "created": True},
        )

    index = MemoryRequestIndex(owner="dev", caller=caller)
    run(index.write(PLANNING_ONLY, "배송비 문구"))
    target, skill, inp, key = calls[0]
    assert (target, skill, key) == ("memory", "card_write", "request_card:REQ-31")
    assert inp["card"]["kind"] == "request" and inp["card"]["extra"]["request_id"] == "REQ-31"
    assert inp["card"]["extra"]["owner"] == "dev"
    hits = run(index.search("배송비", 5))
    assert [(h.request_id, h.owner) for h in hits] == [("REQ-31", "planning")]
    assert calls[1][2]["kind"] == ["request"]
