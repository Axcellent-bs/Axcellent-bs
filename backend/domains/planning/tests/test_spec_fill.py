"""기획서 자유 작성 → 필수 칸 자동 채우기 (fill_spec), 자유 작성이 판단(related)에 들어가는지."""

from __future__ import annotations

import asyncio

import pytest

from ax_contracts.schemas import ExecuteDecision
from ax_workflow.related import text_of
from planning_agent import spec_fill
from planning_agent.domain.service import ActionError, PlanningService

MD = "\n".join(
    [
        "회의 메모",
        "## 배경",
        "결제 실패 뒤 **장바구니가 비워져** 문의가 늘었다",
        "## 목표",
        "결제 실패 뒤에도 장바구니를 유지한다",
        "## 요구사항",
        "- 실패해도 담은 상품을 지우지 않는다",
        "- 다시 결제 버튼을 보여 준다",
        "## 완료 기준",
        "1. 실패 뒤 장바구니 수가 그대로다",
    ]
)


async def _gate(action, params, meta, evidence) -> ExecuteDecision:
    return ExecuteDecision(execution_id="exe_ok", grade="A", status="executed")


def _svc(writer=None) -> PlanningService:
    return PlanningService(gate=_gate, relater=None, spec_writer=writer)


def _draft(svc: PlanningService):
    return next(x for x in svc.state.specs if x.status != "확정")


@pytest.mark.role("planning")
def test_parse_headings() -> None:
    got = spec_fill.parse(MD)
    assert got.problem == "결제 실패 뒤 장바구니가 비워져 문의가 늘었다"
    assert got.goal == "결제 실패 뒤에도 장바구니를 유지한다"
    assert got.reqs == ["실패해도 담은 상품을 지우지 않는다", "다시 결제 버튼을 보여 준다"]
    assert got.done == ["실패 뒤 장바구니 수가 그대로다"]


@pytest.mark.role("planning")
def test_fill_overwrite_replaces_fields_and_paras() -> None:
    svc = _svc()
    sp = _draft(svc)
    asyncio.run(svc.dispatch({"type": "editSpec", "spec": sp.id, "body_md": MD}))
    out = asyncio.run(svc.dispatch({"type": "fillSpec", "spec": sp.id, "overwrite": True}))
    x = next(y for y in svc.state.specs if y.id == sp.id)
    assert out.output["by"] == "rule" and "문제" in out.output["filled"]
    assert x.goal == "결제 실패 뒤에도 장바구니를 유지한다"
    assert [p.id for p in x.paras] == ["R1", "R2", "C1"]
    assert any(a.point == "fill_spec" for a in svc.state.activity)


@pytest.mark.role("planning")
def test_ai_reads_context_keeps_human_text_and_rule_fills_gaps() -> None:
    seen: dict = {}

    class Filler:
        async def read(self, **kw):
            seen.update(kw)
            return spec_fill.Filled(goal="AI 목표", reqs=["AI 요구"], by="llm")

    svc = PlanningService(gate=_gate, relater=None, spec_filler=Filler())
    sp = _draft(svc)
    asyncio.run(svc.dispatch({"type": "editSpec", "spec": sp.id, "problem": "사람이 쓴 문제"}))
    md = "\n".join(
        [
            "회의 메모: 장바구니를 유지하기로 했다",
            "## 완료 기준",
            "- 실패 뒤 장바구니 수가 그대로다",
        ]
    )
    asyncio.run(svc.dispatch({"type": "editSpec", "spec": sp.id, "body_md": md}))
    out = asyncio.run(svc.dispatch({"type": "fillSpec", "spec": sp.id, "overwrite": False}))
    x = next(y for y in svc.state.specs if y.id == sp.id)
    assert seen["md"] == md and seen["current"]["problem"] == "사람이 쓴 문제"
    assert x.problem == "사람이 쓴 문제"  # 사람이 쓴 칸은 그대로
    assert out.output["by"] == "llm+rule"  # AI가 비운 완료 기준은 소제목에서


@pytest.mark.role("planning")
def test_ai_finds_answer_to_open_question_in_free_text() -> None:
    class Filler:
        async def read(self, **kw):
            assert kw["question"]
            return spec_fill.Filled(answer="화면 문구만 바꾼다", by="llm")

    svc = PlanningService(gate=_gate, relater=None, spec_filler=Filler())
    sp = next(x for x in svc.state.specs if x.question)
    md = "질문 받은 범위는 화면 문구만 바꾸는 걸로 정했다"
    asyncio.run(svc.dispatch({"type": "editSpec", "spec": sp.id, "body_md": md}))
    out = asyncio.run(svc.dispatch({"type": "fillSpec", "spec": sp.id}))
    x = next(y for y in svc.state.specs if y.id == sp.id)
    assert "질문 답" in out.output["filled"] and not x.question


@pytest.mark.role("planning")
def test_fill_needs_free_text_and_free_text_feeds_related() -> None:
    svc = _svc()
    sp = _draft(svc)
    with pytest.raises(ActionError):
        asyncio.run(svc.dispatch({"type": "fillSpec", "spec": sp.id}))
    asyncio.run(svc.dispatch({"type": "editSpec", "spec": sp.id, "body_md": "고유한단어XYZ"}))
    req = next(r for r in svc.state.requests if r.spec == sp.id or r.id == sp.request)
    assert "고유한단어XYZ" in text_of(svc.state, req)


@pytest.mark.role("planning")
def test_delete_draft_spec_but_not_confirmed() -> None:
    svc = _svc()
    sp = _draft(svc)
    req_id = sp.request
    asyncio.run(svc.dispatch({"type": "deleteSpec", "spec": sp.id}))
    assert all(x.id != sp.id for x in svc.state.specs)
    req = next(r for r in svc.state.requests if r.id == req_id)
    assert req.spec is None and req.state == "접수" and not req.flow
    done = next(x for x in svc.state.specs if x.status == "확정")
    with pytest.raises(ActionError):
        asyncio.run(svc.dispatch({"type": "deleteSpec", "spec": done.id}))


@pytest.mark.role("planning")
def test_preview_does_not_save_and_edit_saves_lists() -> None:
    svc = _svc()
    sp = _draft(svc)
    before = sp.model_copy(deep=True)
    asyncio.run(svc.dispatch({"type": "editSpec", "spec": sp.id, "body_md": MD}))
    pv = asyncio.run(svc.dispatch({"type": "fillSpec", "spec": sp.id, "preview": True}))
    x = next(y for y in svc.state.specs if y.id == sp.id)
    assert pv.outcome == "preview" and pv.output["goal"] == "결제 실패 뒤에도 장바구니를 유지한다"
    assert x.goal == before.goal and x.paras == before.paras  # 미리보기는 저장하지 않는다
    save = {"type": "editSpec", "spec": sp.id, "goal": pv.output["goal"],
            "reqs": pv.output["reqs"], "dones": pv.output["done"]}  # fmt: skip
    asyncio.run(svc.dispatch(save))
    x = next(y for y in svc.state.specs if y.id == sp.id)
    assert x.goal == pv.output["goal"] and [p.id for p in x.paras] == ["R1", "R2", "C1"]


@pytest.mark.role("planning")
def test_spec_fill_route_reads_without_saving(monkeypatch) -> None:
    from fastapi.testclient import TestClient

    from planning_agent.main import app
    from planning_agent.runtime import get_service

    monkeypatch.setenv("SAFETY_HUMAN_TOKENS", "operator:human-test-token")
    svc = get_service()

    class Filler:
        async def read(self, **kw):
            assert kw["question"] == "범위?"
            return spec_fill.Filled(problem="AI 문제", answer="화면만", by="llm")

    monkeypatch.setattr(svc, "_spec_filler", Filler())
    before = svc.state.model_copy(deep=True)
    res = TestClient(app).post(
        "/api/planning/spec-fill",
        json={"body_md": "메모", "title": "t", "question": "범위?", "current": {}},
        headers={"Authorization": "Bearer human-test-token"},
    )
    data = res.json()["data"]
    assert res.status_code == 200 and data["problem"] == "AI 문제" and data["ai"] is True
    assert data["answer"] == "화면만" and svc.state == before


@pytest.mark.role("planning")
def test_templates_and_extra_fields() -> None:
    from planning_agent import spec_template

    assert [x.label for x in spec_template.sections("proposal")][:4] == [
        "배경",
        "목표",
        "타깃",
        "핵심 메시지",
    ]
    assert spec_template.template_of("proposal") == "proposal"
    assert spec_template.template_of(None) == "plan"
    md = "## 타깃\n20대 직장인\n## 핵심 메시지\n출근길 10분이면 한 권\n## 범위\n앱만"
    got = spec_fill.parse(md)
    assert got.extra == {
        "audience": "20대 직장인",
        "message": "출근길 10분이면 한 권",
        "scope": "앱만",
    }
    svc = _svc()
    sp = _draft(svc)
    edit = {"type": "editSpec", "spec": sp.id, "extra": {"scope": "앱만", "nope": "x"}}
    asyncio.run(svc.dispatch(edit))
    x = next(y for y in svc.state.specs if y.id == sp.id)
    assert x.extra == {"scope": "앱만"}  # 모르는 칸은 버린다
    asyncio.run(svc.dispatch({"type": "editSpec", "spec": sp.id, "extra": {"scope": ""}}))
    assert next(y for y in svc.state.specs if y.id == sp.id).extra == {}


@pytest.mark.role("planning")
def test_suggest_mode_only_for_empty_fields(monkeypatch) -> None:
    from fastapi.testclient import TestClient

    from planning_agent.main import app
    from planning_agent.runtime import get_service

    monkeypatch.setenv("SAFETY_HUMAN_TOKENS", "operator:human-test-token")
    svc = get_service()
    seen: dict = {}

    class Filler:
        async def suggest(self, **kw):
            seen.update(kw)
            return spec_fill.Filled(goal="예: 조회수 1,000", extra={"audience": "20대"}, by="llm")

    monkeypatch.setattr(svc, "_spec_filler", Filler())
    res = TestClient(app).post(
        "/api/planning/spec-fill",
        json={"mode": "suggest", "body_md": "", "title": "골프", "template": "proposal",
              "fields": ["goal", "audience"], "basis": ["구글 9위"], "current": {}},
        headers={"Authorization": "Bearer human-test-token"},
    )  # fmt: skip
    data = res.json()["data"]
    assert res.status_code == 200 and data["goal"] == "예: 조회수 1,000"
    assert data["extra"] == {"audience": "20대"} and seen["fields"] == ["goal", "audience"]
    assert seen["template"] == "proposal" and seen["basis"] == ["구글 9위"]
