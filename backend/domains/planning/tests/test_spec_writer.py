"""기획서 초안을 LLM으로 쓰기 (`spec_writer`). 가짜 LLM을 끼워 본다 (실제 LLM은 부르지 않는다)."""

from __future__ import annotations

import asyncio

from planning_agent.domain.service import PlanningService
from planning_agent.spec_writer import SpecWriter


def run(coro):
    return asyncio.run(coro)


class FakeLLM:
    """`structured()`만 흉내 낸다. mode: ok · fail · slow · empty."""

    def __init__(self, mode: str = "ok") -> None:
        self.mode = mode
        self.calls: list[dict] = []

        class _S:
            model = "fake-model"

        self.settings = _S()

    async def structured(self, prompt, schema, untrusted=None, images=None, context_id=None):
        self.calls.append({"prompt": prompt, "untrusted": untrusted, "context_id": context_id})
        if self.mode == "slow":
            await asyncio.sleep(1)
        if self.mode == "fail":
            raise RuntimeError("LLM down")
        if self.mode == "empty":
            return schema(problem="p", goal="g", requirements=["  "], done_criteria=["완료"])
        return schema(
            problem="같은 문의가 반복된다",
            goal="고객이 문의 없이 해결한다",
            requirements=["배송 조회 화면에 송장 번호를 보인다", "송장 번호를 복사할 수 있게 한다"],
            done_criteria=["배송 조회 화면에서 송장 번호가 보인다"],
        )


def _spec_of_flow(svc: PlanningService):
    run(svc.inbound("support_ticket"))
    r = next(x for x in svc.state.requests if x.id == "REQ-21")
    return r, next(x for x in svc.state.specs if x.id == r.spec)


def test_llm_draft_fills_paras() -> None:
    llm = FakeLLM()
    svc = PlanningService(spec_writer=SpecWriter(llm))
    r, sp = _spec_of_flow(svc)
    assert [p.id for p in sp.paras] == ["R1", "R2", "C1"]
    assert sp.paras[0].text == "배송 조회 화면에 송장 번호를 보인다"
    assert sp.problem == "같은 문의가 반복된다" and sp.goal == "고객이 문의 없이 해결한다"
    # 근거는 요청 쪽 사실만 (LLM 문장을 근거로 쓰지 않는다)
    assert sp.basis == [f"{r.id} (고객지원)", r.evidence]
    # 요청 제목·근거는 데이터 블록으로만 넣는다
    assert llm.calls[0]["untrusted"]["title"] == r.title
    assert llm.calls[0]["context_id"] == r.context_id
    write = [
        a.text
        for a in svc.state.activity
        if a.point == "write_spec" and a.context_id == r.context_id
    ]
    assert any("AI 작성 fake-model" in t and "요구사항 2" in t for t in write)


def test_llm_failure_falls_back_to_rule_draft() -> None:
    for mode in ("fail", "empty"):
        svc = PlanningService(spec_writer=SpecWriter(FakeLLM(mode)))
        r, sp = _spec_of_flow(svc)
        assert [p.id for p in sp.paras] == ["R1", "C1"] and sp.paras[0].text == r.title
        write = [a.text for a in svc.state.activity if a.point == "write_spec"]
        assert any("규칙 작성" in t for t in write)


def test_llm_timeout_falls_back() -> None:
    svc = PlanningService(spec_writer=SpecWriter(FakeLLM("slow"), timeout=0.05))
    r, sp = _spec_of_flow(svc)
    assert sp.paras[0].text == r.title


def test_mock_mode_has_no_writer(service) -> None:
    assert service._spec_writer is None
