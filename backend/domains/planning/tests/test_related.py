"""비슷한 요청·충돌하는 요청 찾기 (related.py).

새 요청이 들어오면 의미가 가까운 기존 요청을 찾고, LLM이 후보 안에서만 관계를 정한다.
"""

from __future__ import annotations

import asyncio

from ax_contracts.schemas import ExecuteDecision
from planning_agent.domain.service import PlanningService
from planning_agent.related import LexicalEmbedder, Relater, _Judged, _Judgement


def run(coro):
    return asyncio.run(coro)


async def _gate(action, params, meta, evidence) -> ExecuteDecision:
    return ExecuteDecision(execution_id="exe_ok", grade="A", status="executed")


class FakeLLM:
    def __init__(self, items: list[dict] | None = None, fail: bool = False) -> None:
        self.items = items or []
        self.fail = fail
        self.data: dict | None = None

    async def structured(self, prompt, schema, untrusted=None, context_id=None):
        self.data = untrusted
        if self.fail:
            raise RuntimeError("LLM 꺼짐")
        return _Judgement(items=[_Judged(**x) for x in self.items])


class Broken:
    method = "embed"

    async def embed(self, texts):
        raise RuntimeError("임베딩 한도")


def _ticket(svc: PlanningService, title: str, key: str):
    return run(
        svc.create_ticket(
            context_id="ctx_x",
            requester="support",
            title=title,
            evidence="고객 문의",
            source_ref=None,
            evidence_ids=[],
            idempotency_key=key,
        )
    )


def _req(svc: PlanningService, title: str):
    return next(r for r in svc.state.requests if r.title == title)


def test_lexical_search_finds_similar_without_llm() -> None:
    svc = PlanningService(gate=_gate, relater=Relater(LexicalEmbedder()))
    _ticket(svc, "결제 실패하면 장바구니가 비워져요", "k1")
    req = _req(svc, "결제 실패하면 장바구니가 비워져요")
    ids = [x.id for x in req.related]
    # 시드의 "결제 실패 뒤 장바구니가 비워진다"와 가깝다
    assert ids and all(x.relation == "related" and x.by == "lexical" for x in req.related)
    assert any("장바구니" in x.title for x in req.related)
    assert any(a.point == "request.related" and req.id in a.text for a in svc.state.activity)


def test_unrelated_request_has_no_candidates() -> None:
    svc = PlanningService(gate=_gate, relater=Relater(LexicalEmbedder()))
    _ticket(svc, "회의실 예약 화면 글꼴 크기", "k2")
    assert _req(svc, "회의실 예약 화면 글꼴 크기").related == []


def test_llm_decides_relation_only_within_candidates() -> None:
    svc = PlanningService(gate=_gate, relater=None)
    target = _req(svc, "결제 실패 뒤 장바구니가 비워진다")
    llm = FakeLLM(
        [
            {"id": target.id, "relation": "same", "reason": "둘 다 결제 실패 뒤 장바구니 유지"},
            {"id": "REQ-999", "relation": "conflict", "reason": "지어낸 후보"},
        ]
    )
    svc._relater = Relater(LexicalEmbedder(), llm=llm)
    _ticket(svc, "결제 실패하면 장바구니가 비워져요", "k3")
    req = _req(svc, "결제 실패하면 장바구니가 비워져요")
    assert [(x.id, x.relation, x.by) for x in req.related] == [(target.id, "same", "llm")]
    # LLM에는 후보만 데이터로 보낸다
    assert {c["id"] for c in llm.data["candidates"]} >= {target.id}
    assert "REQ-999" not in {c["id"] for c in llm.data["candidates"]}


def test_conflict_is_kept_and_none_dropped() -> None:
    svc = PlanningService(gate=_gate, relater=None)
    pool = [r.id for r in svc.state.requests]
    llm = FakeLLM([{"id": i, "relation": "none"} for i in pool])
    llm.items[0] = {"id": pool[0], "relation": "conflict", "reason": "반대 방향"}
    svc._relater = Relater(LexicalEmbedder(), llm=llm)
    _ticket(svc, "결제 실패하면 장바구니가 비워져요", "k4")
    rel = _req(svc, "결제 실패하면 장바구니가 비워져요").related
    assert all(x.relation == "conflict" for x in rel)


def test_llm_failure_falls_back_to_search_only() -> None:
    svc = PlanningService(gate=_gate, relater=Relater(LexicalEmbedder(), llm=FakeLLM(fail=True)))
    _ticket(svc, "결제 실패하면 장바구니가 비워져요", "k5")
    rel = _req(svc, "결제 실패하면 장바구니가 비워져요").related
    assert rel and all(x.by == "lexical" and x.relation == "related" for x in rel)


def test_embedding_failure_falls_back_to_lexical() -> None:
    svc = PlanningService(gate=_gate, relater=Relater(Broken()))
    _ticket(svc, "결제 실패하면 장바구니가 비워져요", "k6")
    rel = _req(svc, "결제 실패하면 장바구니가 비워져요").related
    assert rel and all(x.by == "lexical" for x in rel)


def test_no_relater_keeps_requests_untouched() -> None:
    svc = PlanningService(gate=_gate, relater=None)
    _ticket(svc, "결제 실패하면 장바구니가 비워져요", "k7")
    assert _req(svc, "결제 실패하면 장바구니가 비워져요").related == []
