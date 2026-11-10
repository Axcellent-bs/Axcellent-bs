"""기획 → 요청 서비스 되묻기 (`answer_planning_question`·`receive_planning_answer`).

호출자를 바꿔 끼워 (가) 서비스 id 저장, (나) 바로 답함, (다) 사람 답 대기 → 나중 답,
(라) 창구 없음·응답 없음이면 질문 없이 진행, (마) 목 모드·근거 충분이면 묻지 않음을 본다.
"""

from __future__ import annotations

import asyncio

import pytest
from fastapi.testclient import TestClient

from ax_contracts.schemas import AgentRequest, AgentResult, TaskStatus
from planning_agent.ask_requester import Asker
from planning_agent.domain.service import PlanningService
from planning_agent.main import app
from planning_agent.runtime import set_service

pytestmark = pytest.mark.role("planning")  # 분리 3단계: 기획 서비스 역할로 돈다

TITLE = "품절 상품 재입고 알림 기능 추가"


def run(coro):
    return asyncio.run(coro)


class Svc:
    """요청 서비스 대신 답한다. mode: answered · needs_human · unknown · forbidden · slow."""

    def __init__(self, mode: str = "answered") -> None:
        self.mode = mode
        self.asked: list[tuple[str, AgentRequest]] = []

    async def __call__(self, target: str, req: AgentRequest) -> AgentResult:
        self.asked.append((target, req))
        if self.mode == "slow":
            await asyncio.sleep(1)
        if self.mode == "forbidden":
            return AgentResult(meta=req.meta, status=TaskStatus.REJECTED, outcome="FORBIDDEN_SKILL")
        out = {"status": self.mode}
        if self.mode == "answered":
            out |= {"answer": "지난주 재입고 문의 37건", "answered_by": "agent"}
            out["evidence_ids"] = ["inq_3", "inq_9"]
        return AgentResult(meta=req.meta, status=TaskStatus.COMPLETED, output=out)


def _service(svc: Svc, timeout: float = 30.0) -> PlanningService:
    return PlanningService(asker=Asker(caller=svc, timeout=timeout))


def _ticket(service: PlanningService, evidence: str = "재입고 알림 요청이 많음", ids=None):
    res = run(
        service.create_ticket(
            context_id="ctx_a2a_1",
            requester="inquiry",
            title=TITLE,
            evidence=evidence,
            source_ref="inq_3",
            evidence_ids=ids or [],
            idempotency_key="k-ticket-1",
        )
    )
    req = next(r for r in service.state.requests if r.id == res.output["request_id"])
    sp = next((x for x in service.state.specs if x.id == req.spec), None)
    return req, sp


def _ask_texts(service: PlanningService, ctx: str) -> list[str]:
    return [
        a.text
        for a in service.state.activity
        if a.point == "answer_planning_question" and a.context_id == ctx
    ]


def test_service_name_is_kept_and_dept_is_mapped(service) -> None:
    req, _ = _ticket(service)
    assert (req.from_, req.from_service) == ("support", "inquiry")


def test_mock_mode_does_not_ask(service) -> None:
    assert service._asker is None
    req, sp = _ticket(service)
    assert sp is not None and sp.question is None
    assert _ask_texts(service, req.context_id) == []


def test_answered_goes_into_basis_and_decision() -> None:
    svc = Svc("answered")
    service = _service(svc)
    req, sp = _ticket(service)

    (target, ask) = svc.asked[0]
    assert target == "inquiry"  # 부서(support)가 아니라 요청을 보낸 서비스
    assert ask.skill == "answer_planning_question"
    assert ask.input["request_id"] == req.id and ask.input["about"] == "evidence"
    assert sp.question is None
    assert any("지난주 재입고 문의 37건" in b for b in sp.basis)
    dec = service.state.decisions[-1]
    assert dec.from_ == f"되묻기 Q-{req.id}-1" and dec.spec == sp.id
    assert any("답 받음" in t for t in _ask_texts(service, req.context_id))


def test_needs_human_waits_then_late_answer_applies() -> None:
    svc = Svc("needs_human")
    service = _service(svc)
    req, sp = _ticket(service)
    qid = f"Q-{req.id}-1"
    assert sp.question and sp.question_id == qid and sp.question_to == "inquiry"
    assert sp.status == "초안" and sp.auto_review is not None

    res = run(
        service.receive_planning_answer(
            question_id=qid,
            request_id=req.id,
            answer="재입고 문의 37건 (10/26~11/1)",
            answered_by="human",
            evidence_ids=["inq_3"],
            idempotency_key="k-ans-1",
        )
    )
    assert res.output == {"outcome": "applied", "spec_id": sp.id}
    sp = next(x for x in service.state.specs if x.id == sp.id)
    assert sp.question is None and sp.question_id is None
    assert any("37건" in b for b in sp.basis)

    again = run(
        service.receive_planning_answer(
            question_id=qid,
            request_id=req.id,
            answer="다시 보냄",
            answered_by="human",
            evidence_ids=[],
            idempotency_key="k-ans-2",
        )
    )
    assert again.output["outcome"] == "duplicate"
    unknown = run(
        service.receive_planning_answer(
            question_id="Q-REQ-99-1",
            request_id="REQ-99",
            answer="?",
            answered_by="human",
            evidence_ids=[],
            idempotency_key="k-ans-3",
        )
    )
    assert unknown.output["outcome"] == "unknown_question"


def test_human_answers_in_planning_screen() -> None:
    service = _service(Svc("needs_human"))
    req, sp = _ticket(service)
    run(service.answer_question(sp.id, "재입고 문의 37건"))
    sp = next(x for x in service.state.specs if x.id == sp.id)
    assert sp.question is None
    assert service.state.decisions[-1].text == "재입고 문의 37건"


def test_no_channel_or_timeout_continues_without_question() -> None:
    for svc, why in ((Svc("forbidden"), "되묻기 창구 없음"), (Svc("slow"), "응답 없음")):
        service = _service(svc, timeout=0.05)
        req, sp = _ticket(service)
        assert sp.question is None  # 지어내지도, 기다리지도 않는다
        assert any(why in t for t in _ask_texts(service, req.context_id))


def test_unknown_continues_without_question() -> None:
    service = _service(Svc("unknown"))
    req, sp = _ticket(service)
    assert sp.question is None
    assert any("기록에 없음" in t for t in _ask_texts(service, req.context_id))


def test_enough_evidence_is_not_asked() -> None:
    svc = Svc("answered")
    service = _service(svc)
    _ticket(service, evidence="재입고 문의 37건")
    assert svc.asked == []
    service2 = _service(svc)
    _ticket(service2, ids=["inq_1"])
    assert svc.asked == []


def test_a2a_receive_planning_answer(a2a_body) -> None:
    service = set_service(_service(Svc("needs_human")))
    req, _ = _ticket(service)
    body = a2a_body(
        "receive_planning_answer",
        {"question_id": f"Q-{req.id}-1", "request_id": req.id, "answer": "37건"},
        token="mock:support",
        context_id=req.context_id,
        idempotency_key="k-a2a-1",
    )
    out = TestClient(app).post("/a2a", json=body).json()
    assert out["status"] == "completed" and out["output"]["outcome"] == "applied"
    no_key = (
        TestClient(app)
        .post("/a2a", json={**body, "meta": {**body["meta"], "idempotency_key": None}})
        .json()
    )
    assert no_key["status"] != "completed"


def test_second_question_on_same_request_gets_new_number() -> None:
    """같은 요청에서 기획서를 다시 쓰다 또 되물으면 번호가 -2로 늘고, 두 질문이 따로 열려
    각각 맞는 기획서로 답이 간다 (요청 서비스 받은 질문함이 question_id로 한 번만 적음, #941)."""
    svc = Svc("needs_human")
    service = _service(svc)
    req, sp1 = _ticket(service)
    assert sp1.question_id == f"Q-{req.id}-1"

    # 같은 요청의 기획서를 다시 써서 또 묻는다 (앞 질문은 아직 열려 있음)
    sp2 = sp1.model_copy(update={"id": "SPEC-RE", "question": None, "question_id": None})
    service.state.specs.append(sp2)
    run(service._ask_requester(service.state, sp2, req))
    sp2 = next(x for x in service.state.specs if x.id == "SPEC-RE")
    assert sp2.question_id == f"Q-{req.id}-2"
    asked = [r.input["question_id"] for _, r in svc.asked]
    assert asked == [f"Q-{req.id}-1", f"Q-{req.id}-2"]

    def answer(qid: str, key: str):
        return run(
            service.receive_planning_answer(
                question_id=qid, request_id=req.id, answer=f"{qid} 답", answered_by="human",
                evidence_ids=[], idempotency_key=key,
            )
        ).output  # fmt: skip

    assert answer(f"Q-{req.id}-2", "k2") == {"outcome": "applied", "spec_id": "SPEC-RE"}
    assert answer(f"Q-{req.id}-1", "k1") == {"outcome": "applied", "spec_id": sp1.id}
    froms = [d.from_ for d in service.state.decisions if d.from_.startswith("되묻기")]
    assert froms[-2:] == [f"되묻기 Q-{req.id}-2", f"되묻기 Q-{req.id}-1"]
    # 답한 뒤 또 물어도 쓴 번호를 피한다 (결정 기록에서 찾음)
    sp3 = sp1.model_copy(update={"id": "SPEC-RE2", "question": None, "question_id": None})
    service.state.specs.append(sp3)
    run(service._ask_requester(service.state, sp3, req))
    assert next(x for x in service.state.specs if x.id == "SPEC-RE2").question_id == f"Q-{req.id}-3"
