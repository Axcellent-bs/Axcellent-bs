"""기획이 그리는 콘티 (11/25 예진, 같은 날 3D 마네킹 콘티로 바꿈).

- 영상 제작안을 쓴 뒤 같은 기획 LLM으로 장면별 첫·끝 포즈 숫자를 받아 코드가 SVG로 그려 넣는다
- SVG는 허용 요소·속성만 남긴다(스크립트·이벤트·외부 주소·DOCTYPE 버림, 16KB 상한)
- LLM이 없거나 실패하면 포즈·SVG 없이 제작안 그대로
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
# 11/25 같은 SVG 안 그라데이션 참조 url(#id)는 이제 남긴다(3D 마네킹 콘티). 버려야 할 것은 바깥 주소
BAD = ('<svg viewBox="0 0 90 160" onload="alert(1)"><script>alert(1)</script>'
       '<image href="https://x.example/a.png"/><rect x="1" y="1" width="5" height="5" '
       'fill="url(https://x.example/g)" stroke="#000"/></svg>')  # fmt: skip
POSE = {"shot": "medium", "yaw": 20, "arm_l": {"raise": 60, "bend": 40}, "label_ko": "돌아봄"}


def _prop(**kw: Any) -> Proposal:
    base = {
        "id": "PROP-91", "title": "출근길 상황극", "from": "data", "skill": "trend_report_insight",
        "insight": "30대 여성이 출근길 상황극을 오래 본다", "idea": "출근길 공감 상황극",
        "why": "상황극 쇼츠 조회", "created_at": datetime(2026, 11, 25, tzinfo=UTC),
    }  # fmt: skip
    return Proposal.model_validate({**base, **kw})


def _plan() -> VideoPlan:
    return vp.rule_plan(_prop(), (vp.FUN_FIRST, vp.END_CARD))


def _pose_out(pose: dict | None = POSE, n: int | None = None) -> sb._PoseOut:
    plan = _plan()
    ns = [n] if n is not None else [s.n for s in plan.scenes]
    return sb._PoseOut.model_validate(
        {"scenes": [{"n": i, "start": pose, "end": pose} for i in ns]}
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


def test_sanitize_keeps_gradients_and_local_url_only() -> None:
    """11/25 3D 마네킹 콘티: 그라데이션과 같은 SVG 안 url(#id)만 남긴다 (마케팅과 같은 규칙)."""
    svg = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 180 320"><defs>'
        '<radialGradient id="sk" cx="35%" cy="30%" r="75%" gradientUnits="objectBoundingBox">'
        '<stop offset="0%" stop-color="#ffe6c7" stop-opacity="1"/></radialGradient>'
        '<linearGradient id="bad id" x1="0" y2="1" gradientUnits="javascript:x"/></defs>'
        '<circle cx="9" cy="9" r="4" fill="url(#sk)" stroke="url( https://evil/x )"/>'
        '<line x1="0" y1="0" x2="5" y2="5" stroke="#000" stroke-linejoin="round"/>'
        '<rect width="3" height="3" opacity="url(#sk)" fill="javascript:alert(1)"/>'
        "<foreignObject><div>x</div></foreignObject><script>alert(1)</script></svg>"
    )  # fmt: skip
    out = sb.sanitize_svg(svg)
    for kept in ("<radialGradient", "<stop", 'stop-color="#ffe6c7"', 'stop-opacity="1"',
                 'id="sk"', 'gradientUnits="objectBoundingBox"', 'fill="url(#sk)"',
                 'stroke-linejoin="round"', "<linearGradient"):  # fmt: skip
        assert kept in out, kept
    for gone in ("evil", "javascript", "foreignObject", "script", 'opacity="url', "bad id"):
        assert gone not in out, gone
    assert sb.MAX_SVG_BYTES == 16384


def test_draw_fills_start_and_end_svg_and_sends_only_scene_words() -> None:
    llm = MockLLMClient(responses={sb._PoseOut: _pose_out()})
    plan, changed = asyncio.run(sb.draw(llm, _plan(), "ctx"))
    assert changed == len(plan.scenes)
    assert all(s.start_svg and s.end_svg for s in plan.scenes)
    assert all(s.start_pose and s.end_pose for s in plan.scenes)
    sent = llm.calls[0]
    assert "콘티 작가" in sent and "포즈" in sent and "SVG·그림 원문은 쓰지 않는다" in sent
    assert "자막·회사 이름" in sent  # 코드가 얹는 글은 그리지 않는다


def test_draw_without_llm_or_on_failure_keeps_plan() -> None:
    plan = _plan()

    # 11/25 5-1: 장면은 그대로, 못 그린 이유만 svg_reason에 남긴다
    def kept(llm: object) -> str:
        drawn, changed = asyncio.run(sb.draw(llm, plan, "ctx"))
        assert changed == 0
        assert drawn.model_copy(update={"svg_reason": "", "svg_detail": ""}) == plan
        return f"{drawn.svg_reason} {drawn.svg_detail}".strip()

    assert kept(None) == "no_llm"
    broken = MockLLMClient(responses={})  # 응답 없음 = 실패
    assert kept(broken) == "error KeyError"  # 가짜 LLM은 KeyError (11/26 U: svg_detail)
    junk = MockLLMClient(responses={sb._PoseOut: _pose_out(None)})  # 포즈 없는 답
    assert kept(junk) == "rejected"  # 예외가 아니라 svg_detail 없음


def test_write_draws_svg_after_the_plan() -> None:
    scenes = [{"visual": f"장면 {i}", "caption": "", "prompt": "A woman reading on a calm subway"}
              for i in range(4)]  # fmt: skip
    out = vp._PlanOut.model_validate({"concept": "출근길", "script": "훅 → 공감", "scenes": scenes})
    llm = MockLLMClient(responses={vp._PlanOut: out, sb._PoseOut: _pose_out()})
    plan = asyncio.run(vp.write(llm, _prop(), "ctx", (vp.FUN_FIRST, vp.END_CARD)))
    assert plan.written_by == "llm" and len(llm.calls) == 2
    assert all(s.start_svg for s in plan.scenes)


def test_only_one_scene_is_redrawn_with_the_request_in_the_data_block() -> None:
    llm = MockLLMClient(responses={sb._PoseOut: _pose_out(n=2)})
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
    llm = MockLLMClient(responses={sb._PoseOut: _pose_out(n=3)})
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
        return ActionResult(
            changed=True, output={"proposal_id": action["proposal"], "scenes": 1, "sent": True}
        )

    actions = [turn.ChatAction(type="redraw_storyboard", proposal="PROP-91", scene=2,
                               note="인물을 왼쪽에"),
               turn.ChatAction(type="make_plan", proposal="PROP-91")]  # fmt: skip
    lines = asyncio.run(turn.run_actions(actions, {"PROP-91"}, act))
    assert seen[0] == {"type": "redraw_storyboard", "proposal": "PROP-91", "scene": 2,
                       "note": "인물을 왼쪽에"}  # fmt: skip
    assert seen[1] == {"type": "make_plan", "proposal": "PROP-91"}
    assert "2번 장면 콘티를 다시 그렸어요" in lines[0] and "다시 넘겨야" in lines[0]
    assert "redraw_storyboard" in turn.SYSTEM_PROMPT


def test_media_hint_category_comes_from_the_company_list(monkeypatch: pytest.MonkeyPatch) -> None:
    """11/25 예진: 분류는 기획이 회사 분류 목록에서 골라 넘긴다 (마케팅 "미정" 방지)."""
    cats = {"categories": ["그림책", "자기계발", "에세이"]}
    monkeypatch.setattr(vp.tenant, "brand_section", lambda name=None: cats)
    # 제안 글에 나온 분류가 먼저
    assert vp.media_hints(_prop(idea="출근길 에세이 한 줄 공감")).category == "에세이"
    # 글에 없으면 상품이 있는 제안만 LLM이 목록에서 고른 값, 목록 밖이면 버린다
    goods = _prop(products=["독서 기록장 A5"])
    assert vp.media_hints(goods, "자기계발").category == "자기계발"
    assert vp.media_hints(goods, "소설").category is None
    # 11/25 재현 님: 상품도 분류 언급도 없는 회사·채널 홍보는 LLM이 골라 와도 비운다
    assert vp.media_hints(_prop(), "자기계발").category is None
    # 회사 목록이 없으면 비운다(지어내지 않는다)
    monkeypatch.setattr(vp.tenant, "brand_section", lambda name=None: {})
    assert vp.media_hints(_prop(), "에세이").category is None


def test_llm_plan_carries_the_picked_category(monkeypatch: pytest.MonkeyPatch) -> None:
    cats = {"categories": ["그림책", "자기계발"]}
    monkeypatch.setattr(vp.tenant, "brand_section", lambda name=None: cats)
    scenes = [{"visual": f"장면 {i}", "caption": "", "prompt": "A woman reading on a calm subway"}
              for i in range(4)]  # fmt: skip
    out = vp._PlanOut.model_validate(
        {"concept": "출근길", "script": "훅 → 공감", "scenes": scenes, "category": "자기계발"}
    )
    llm = MockLLMClient(responses={vp._PlanOut: out})
    goods = _prop(products=["독서 기록장 A5"])
    plan = asyncio.run(vp.write(llm, goods, "ctx", (vp.FUN_FIRST, vp.END_CARD)))
    assert plan.written_by == "llm" and plan.media_hints is not None
    assert plan.media_hints.category == "자기계발"
    assert '"categories"' in llm.calls[0] and "그림책" in llm.calls[0]
    assert "특정 상품이 아니면 반드시 빈 문자열" in llm.calls[0]


def test_solution_ad_never_gets_a_category(monkeypatch: pytest.MonkeyPatch) -> None:
    """솔루션 광고는 상품 분류가 없다. 글·LLM에 분류가 나와도 비운다."""
    monkeypatch.setattr(vp.tenant, "brand_section", lambda name=None: {"categories": ["에세이"]})
    ad = _prop(idea="솔루션 소개 에세이 장면", products=["독서 기록장 A5"])
    assert vp.purpose_of(ad) == vp.SOLUTION_AD
    assert vp.media_hints(ad, "에세이").category is None
