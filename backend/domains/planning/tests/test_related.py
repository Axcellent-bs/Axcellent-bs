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


def test_open_prs_on_same_file_mark_each_request_as_conflict() -> None:
    """같은 파일을 고치는 열린 PR이 둘이면 (검색 없이) 두 요청에 서로 충돌 가능으로 표시한다."""
    svc = PlanningService(gate=_gate, relater=None)
    s = svc.state
    base = next(p for p in s.prs if p.task and p.changed_paths)
    # 다른 요청(REQ-12 → SPEC-7)의 작업을 하나 더 둔다
    other_task = next(t for t in s.tasks if t.id == base.task).model_copy(
        update={"id": "T-99", "spec": "SPEC-7", "para": "R1"}
    )
    s.tasks.append(other_task)
    first = base.model_copy(
        update={
            "id": "PR #90",
            "number": 90,
            "merged": False,
            "closed": False,
            "changed_paths": ["shop-web/src/pages/Cart.tsx"],
        }
    )
    s.prs.append(first)

    async def add_pr(st):
        st.prs.append(
            first.model_copy(
                update={
                    "id": "PR #91",
                    "number": 91,
                    "task": other_task.id,
                    "changed_paths": ["shop-web/src/pages/Cart.tsx", "docs/a.md"],
                }
            )
        )

    run(svc._apply(add_pr))
    st = svc.state
    from planning_agent.related import request_of_pr

    mine = request_of_pr(st, next(p for p in st.prs if p.id == "PR #91"))
    theirs = request_of_pr(st, next(p for p in st.prs if p.id == "PR #90"))
    assert mine and theirs and mine.id != theirs.id
    hit = next(x for x in mine.related if x.by == "path")
    assert (hit.id, hit.relation) == (theirs.id, "conflict") and "Cart.tsx" in hit.reason
    back = next(x for x in theirs.related if x.by == "path")
    assert back.id == mine.id and back.reason.startswith("PR #91가")
    assert any(a.point == "request.related" and "PR #91" in a.text for a in st.activity)


def test_update_of_finished_request_is_not_conflict() -> None:
    """앞 요청을 다시 바꾸는 건(3만 원 → 5만 원) update로 남는다. LLM에 상태·요청자를 같이 준다."""
    svc = PlanningService(gate=_gate, relater=None)
    target = _req(svc, "결제 실패 뒤 장바구니가 비워진다")
    llm = FakeLLM([{"id": target.id, "relation": "update", "reason": "앞 요청 값을 다시 바꿈"}])
    svc._relater = Relater(LexicalEmbedder(), llm=llm)
    _ticket(svc, "결제 실패하면 장바구니가 비워져요", "k8")
    rel = _req(svc, "결제 실패하면 장바구니가 비워져요").related
    assert [(x.id, x.relation) for x in rel] == [(target.id, "update")]
    cand = next(c for c in llm.data["candidates"] if c["id"] == target.id)
    assert cand["state"] == target.state and cand["from"] == target.from_
    assert any("이어서 바꿈" in a.text for a in svc.state.activity if a.point == "request.related")
