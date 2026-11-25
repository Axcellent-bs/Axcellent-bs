"""기획이 그리는 SVG 콘티 (11/25 예진).

- 영상 제작안을 쓴 뒤 같은 기획 LLM으로 장면별 첫·끝 화면 SVG를 받아 제작안에 넣는다
- 받은 SVG는 허용 요소·속성만 남긴다(스크립트·이벤트·외부 주소·DOCTYPE 버림, 8KB 상한)
- LLM이 없거나 실패하면 SVG 없이 제작안 그대로
- 대화 "콘티 다시 그리기"(redraw_storyboard): 사람 말은 데이터 블록에만, 장면 하나만 고칠 수 있다
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime
from typing import Any

import pytest

from ax_agent_template.llm import MockLLMClient
from ax_contracts.schemas import ExecuteDecision
from ax_contracts.schemas.planning import VideoPlan
from ax_workflow.core import ActionError, ActionResult
from ax_workflow.models import Proposal
from planning_agent import storyboard_svg as sb
from planning_agent import video_plan as vp
from planning_agent.chat import turn
from planning_agent.domain.service import PlanningService

pytestmark = pytest.mark.role("planning")

GOOD = ('<svg viewBox="0 0 90 160"><text x="45" y="12" font-size="7">지하철에서 졸다</text>'
        '<rect x="4" y="20" width="82" height="22" fill="#e9eef7"/></svg>')  # fmt: skip
BAD = ('<svg viewBox="0 0 90 160" onload="alert(1)"><script>alert(1)</script>'
       '<image href="https://x.example/a.png"/><rect x="1" y="1" width="5" height="5" '
       'fill="url(#g)" stroke="#000"/></svg>')  # fmt: skip


def _prop(**kw: Any) -> Proposal:
    base = {
        "id": "PROP-91", "title": "출근길 상황극", "from": "data", "skill": "trend_report_insight",
        "insight": "30대 여성이 출근길 상황극을 오래 본다", "idea": "출근길 공감 상황극",
        "why": "상황극 쇼츠 조회", "created_at": datetime(2026, 11, 25, tzinfo=UTC),
    }  # fmt: skip
    return Proposal.model_validate({**base, **kw})


def _plan() -> VideoPlan:
    return vp.rule_plan(_prop(), (vp.FUN_FIRST, vp.END_CARD))


def _svg_out(svg: str = GOOD, n: int | None = None) -> sb._SvgOut:
    plan = _plan()
    ns = [n] if n is not None else [s.n for s in plan.scenes]
    return sb._SvgOut.model_validate(
        {"scenes": [{"n": i, "start_svg": svg, "end_svg": svg} for i in ns]}
    )


def test_sanitize_keeps_shapes_and_text_and_drops_danger() -> None:
    good = sb.sanitize_svg(GOOD)
    assert good.startswith("<svg") and "지하철에서 졸다" in good and "<rect" in good
    bad = sb.sanitize_svg(BAD)
    for word in ("script", "onload", "image", "url(", "https://"):
        assert word not in bad
    assert "<rect" in bad and 'stroke="#000"' in bad
    assert sb.sanitize_svg('<!DOCTYPE x [<!ENTITY a "b">]><svg/>') == ""
    assert sb.sanitize_svg("<div>not svg</div>") == "" and sb.sanitize_svg(None) == ""
    big = "<svg>" + "<rect x='1' y='1' width='1' height='1'/>" * 400 + "</svg>"
    assert sb.sanitize_svg(big) == ""


def test_draw_fills_start_and_end_svg_and_sends_only_scene_words() -> None:
    llm = MockLLMClient(responses={sb._SvgOut: _svg_out()})
    plan, changed = asyncio.run(sb.draw(llm, _plan(), "ctx"))
    assert changed == len(plan.scenes)
    assert all(s.start_svg and s.end_svg for s in plan.scenes)
    sent = llm.calls[0]
    assert "콘티 작가" in sent and 'viewBox="0 0 90 160"' in sent
    assert "자막·회사 이름" in sent  # 코드가 얹는 글은 그리지 않는다


def test_draw_without_llm_or_on_failure_keeps_plan() -> None:
    plan = _plan()
    assert asyncio.run(sb.draw(None, plan, "ctx")) == (plan, 0)
    broken = MockLLMClient(responses={})  # 응답 없음 = 실패
    assert asyncio.run(sb.draw(broken, plan, "ctx")) == (plan, 0)
    junk = MockLLMClient(responses={sb._SvgOut: _svg_out("<div>no</div>")})
    assert asyncio.run(sb.draw(junk, plan, "ctx")) == (plan, 0)


def test_write_draws_svg_after_the_plan() -> None:
    scenes = [{"visual": f"장면 {i}", "caption": "", "prompt": "A woman reading on a calm subway"}
              for i in range(4)]  # fmt: skip
    out = vp._PlanOut.model_validate({"concept": "출근길", "script": "훅 → 공감", "scenes": scenes})
    llm = MockLLMClient(responses={vp._PlanOut: out, sb._SvgOut: _svg_out()})
    plan = asyncio.run(vp.write(llm, _prop(), "ctx", (vp.FUN_FIRST, vp.END_CARD)))
    assert plan.written_by == "llm" and len(llm.calls) == 2
    assert all(s.start_svg for s in plan.scenes)


def test_only_one_scene_is_redrawn_with_the_request_in_the_data_block() -> None:
    llm = MockLLMClient(responses={sb._SvgOut: _svg_out(n=2)})
    plan, changed = asyncio.run(sb.draw(llm, _plan(), "ctx", "2번 장면 인물을 왼쪽에", 2))
    assert changed == 1
    assert [bool(s.start_svg) for s in plan.scenes] == [s.n == 2 for s in plan.scenes]
    data = llm.calls[0].split("<untrusted_data", 1)[-1]
    assert "2번 장면 인물을 왼쪽에" in data and '"only": 2' in data


class _Writer:
    def __init__(self, llm: Any) -> None:
        self.llm = llm

    async def draft(self, inp: Any, ctx: Any) -> None:
        return None


async def _gate(*args: Any, **kwargs: Any) -> ExecuteDecision:
    return ExecuteDecision(execution_id="exe_1", grade="A", status="executed")


def _service(llm: Any) -> PlanningService:
    svc = PlanningService(gate=_gate, relater=None, proposal_writer=_Writer(llm))
    svc.state.proposals.append(_prop(video_plan=_plan()))
    return svc


def test_redraw_action_updates_the_saved_plan() -> None:
    llm = MockLLMClient(responses={sb._SvgOut: _svg_out(n=3)})
    svc = _service(llm)
    res = asyncio.run(svc.dispatch({"type": "redrawStoryboard", "proposal": "PROP-91",
                                    "scene": 3, "note": "엔드카드 가운데를 비워"}))  # fmt: skip
    assert res.output["scenes"] == 1 and res.output["sent"] is False
    p = next(x for x in svc.state.proposals if x.id == "PROP-91")
    assert p.video_plan is not None
    drawn = [bool(s.start_svg) for s in p.video_plan.scenes]
    assert drawn == [s.n == 3 for s in p.video_plan.scenes]


def test_redraw_action_refuses_clearly() -> None:
    svc = _service(MockLLMClient(responses={}))
    with pytest.raises(ActionError) as e:
        asyncio.run(svc.dispatch({"type": "redraw_storyboard", "proposal": "PROP-91", "scene": 9}))
    assert e.value.code == "INVALID_ACTION"
    with pytest.raises(ActionError) as e:
        asyncio.run(svc.dispatch({"type": "redraw_storyboard", "proposal": "PROP-91"}))
    assert e.value.code == "SVG_FAILED"
    svc.state.proposals[-1].video_plan = None
    with pytest.raises(ActionError) as e:
        asyncio.run(svc.dispatch({"type": "redraw_storyboard", "proposal": "PROP-91"}))
    assert e.value.code == "NO_VIDEO_PLAN"


def test_chat_passes_scene_and_note_only_for_redraw() -> None:
    seen: list[dict[str, Any]] = []

    async def act(action: dict[str, Any]) -> ActionResult:
        seen.append(action)
        return ActionResult(changed=True,
                            output={"proposal_id": action["proposal"], "scenes": 1, "sent": True})

    actions = [turn.ChatAction(type="redraw_storyboard", proposal="PROP-91", scene=2,
                               note="인물을 왼쪽에"),
               turn.ChatAction(type="make_plan", proposal="PROP-91")]  # fmt: skip
    lines = asyncio.run(turn.run_actions(actions, {"PROP-91"}, act))
    assert seen[0] == {"type": "redraw_storyboard", "proposal": "PROP-91", "scene": 2,
                       "note": "인물을 왼쪽에"}  # fmt: skip
    assert seen[1] == {"type": "make_plan", "proposal": "PROP-91"}
    assert "2번 장면 콘티를 다시 그렸어요" in lines[0] and "다시 넘겨야" in lines[0]
    assert "redraw_storyboard" in turn.SYSTEM_PROMPT
