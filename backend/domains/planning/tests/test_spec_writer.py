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


class LeakLLM(FakeLLM):
    """근거의 원인 후보 설명(공지 시각·채널)을 요구사항으로 옮겨 오는 LLM (11/25 SPEC-30 R2)."""

    async def structured(self, prompt, schema, untrusted=None, images=None, context_id=None):
        self.calls.append({"prompt": prompt, "untrusted": untrusted, "context_id": context_id})
        return schema(
            problem="payment 장애 원인이 확정되지 않았다",
            goal="운영자가 원인을 확인해 확정한다",
            requirements=[
                "장애 상세 화면에 원인 후보를 신뢰도와 함께 표시한다",
                "후보의 근거로 공지 게시 시각(2026-11-13T07:01:44Z)과 게시 채널(banner)을 표시한다",
            ],
            done_criteria=[
                "화면에 공지 게시 시각 2026-11-13T07:01:44Z와 채널 banner가 표시된다",
                "운영자가 원인을 저장하면 확정 상태로 바뀐다",
            ],
        )


EVIDENCE = ("원인 후보 1개 · 사람 확인 필요 / 후보(low): 이상 직전 게시 중이던 공지와 관련 있을 수 "
            "있음 — 공지 게시 2026-11-13T07:01:44Z (채널 banner)")  # fmt: skip


def test_evidence_values_do_not_become_requirements() -> None:
    llm = LeakLLM()
    got = run(SpecWriter(llm).draft(title="payment 장애 원인 확인", evidence=EVIDENCE,
                                    requester="orchestrator", context_id="ctx"))  # fmt: skip
    assert got is not None
    assert got.requirements == ["장애 상세 화면에 원인 후보를 신뢰도와 함께 표시한다"]
    assert got.done_criteria == ["운영자가 원인을 저장하면 확정 상태로 바뀐다"]
    assert "요구사항·완료 기준으로 옮기지 않는다" in llm.calls[0]["prompt"]


def test_time_asked_in_title_is_kept() -> None:
    """제목이 그 시각을 바라면 요청한 것이라 둔다."""
    llm = LeakLLM()
    got = run(SpecWriter(llm).draft(title="2026-11-13T07:01:44Z 공지 시각 표시", evidence=EVIDENCE,
                                    requester="orchestrator", context_id="ctx"))  # fmt: skip
    assert got is not None and len(got.requirements) == 2


class RuleLLM(FakeLLM):
    """제약 문장을 요구사항에 넣는 LLM (11/25 SPEC-26 R1 → 작업 T-28)."""

    async def structured(self, prompt, schema, untrusted=None, images=None, context_id=None):
        self.calls.append({"prompt": prompt, "untrusted": untrusted, "context_id": context_id})
        return schema(
            problem="README 끝에 시험 줄이 없다",
            goal="README 끝에 시험 줄이 생긴다",
            requirements=["다른 파일은 수정하지 않는다.", "README.md 맨 끝에 시험 줄을 추가한다"],
            done_criteria=["README.md 마지막 줄이 시험 줄이다"],
        )


def test_constraint_requirement_moves_to_done_criteria() -> None:
    llm = RuleLLM()
    got = run(SpecWriter(llm).draft(title="README 끝에 시험 줄 추가", evidence="개발 요청",
                                    requester="dev", context_id="ctx"))  # fmt: skip
    assert got is not None
    assert got.requirements == ["README.md 맨 끝에 시험 줄을 추가한다"]
    assert got.done_criteria == [
        "README.md 마지막 줄이 시험 줄이다",
        "다른 파일은 수정하지 않는다.",
    ]
    assert "제약·금지는 요구사항이 아니라 완료 기준에" in llm.calls[0]["prompt"]


def test_is_constraint() -> None:
    from planning_agent.spec_writer import is_constraint

    assert is_constraint("버튼의 동작, 스타일, 레이아웃과 다른 문구는 변경하지 않는다.")
    assert is_constraint("상단 메뉴의 로그인 링크 문구는 대상에서 제외하고 그대로 둔다.")
    assert not is_constraint("로그인 제출 버튼 텍스트를 '시작하기'로 변경한다.")
