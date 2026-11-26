"""장면 칸을 자세히 (11/26 운영 CR-cb113dca245a · 누비투어).

기획 LLM이 place_ko·start_frame·end_frame·camera_en을 비우거나 짧게 써 콘티가 빈약했다.
- 지시문이 장면마다 구체 장소·첫/끝 화면(누가·어디·무엇·시선·표정·배경 소품)·샷+앵글+움직임·
  처음→끝 동작을 반드시 채우라고 한다
- 비어도 거절·다시 쓰기 하지 않고 코드가 채운다 (로그 scene_detail_filled)
- 엔드카드 판정 함수 하나 (콘티가 사람 없이 그린다)"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime

import pytest

from ax_agent_template.llm import MockLLMClient
from ax_workflow.models import Proposal
from planning_agent import plan_checks
from planning_agent import video_plan as vp

pytestmark = pytest.mark.role("planning")


def _prop() -> Proposal:
    return Proposal.model_validate({
        "id": "PROP-9", "title": "겨울 여행 숏츠", "from": "data", "skill": "trend_history",
        "insight": "겨울 여행 검색이 오른다", "idea": "소파에서 여행을 꿈꾸는 숏츠",
        "why": "검색 1.4배", "products": [], "format": "숏폼 15초", "angle": "with_product",
        "created_at": datetime(2026, 11, 26, tzinfo=UTC),
    })  # fmt: skip


def _bare_scene(i: int, visual: str) -> dict:
    """장소·첫/끝 화면·동작·카메라를 모두 비운 장면 (운영에서 본 빈약한 답)."""
    return {"visual": visual, "caption": f"자막 {i}", "prompt": "A traveler daydreams, warm light"}


VISUALS = (
    "거실 소파에 앉아 휴대폰을 보는 여행자", "탁자 위 여권을 집어 드는 여행자",
    "거리에서 캐리어를 끄는 여행자, 눈이 내림", "공항 창가에서 비행기를 보는 여행자",
)  # fmt: skip


def _bare_out() -> vp._PlanOut:
    return vp._PlanOut.model_validate({
        "concept": "겨울 여행", "script": "소파 → 여권 → 거리 → 공항",
        "scenes": [_bare_scene(i, v) for i, v in enumerate(VISUALS, start=1)],
    })  # fmt: skip


def test_prompt_asks_every_scene_detail_field() -> None:
    text = vp.PROMPT
    assert "place_ko·start_frame·end_frame·camera_en·action_en을 반드시 채운다" in text
    assert "샷 크기 + 앵글 + 카메라 움직임" in text and "eye level" in text
    assert "배경 소품 2~3개" in text and "어떤 표정" in text
    assert "처음 동작 → 끝 동작" in text
    assert "거실 소파" in text  # 구체 장소 예


def test_empty_detail_is_filled_by_code_not_rejected(caplog: pytest.LogCaptureFixture) -> None:
    out = _bare_out()
    llm = MockLLMClient(responses={vp._PlanOut: out})
    assert vp.reject_reason(_prop(), out) is None  # 빈 칸은 거절 이유가 아니다
    with caplog.at_level("INFO"):
        plan = asyncio.run(vp.write(llm, _prop(), "ctx"))
    assert plan.written_by == "llm" and plan.fallback_reason == ""
    plan_calls = [c for c in llm.calls if "콘티 작가" not in c]
    assert len(plan_calls) == 1  # 다시 쓰기 없음 (빈 칸을 고칠 점에 넣지 않는다)
    for s, visual in zip(plan.scenes, VISUALS, strict=True):
        assert s.visual == visual
        assert s.start_frame == visual and s.end_frame == visual
        shot = s.start_pose.shot if s.start_pose is not None else "medium"
        assert s.camera_en == f"{vp.SHOT_EN[shot]}, eye level, static"
    assert [s.place_ko for s in plan.scenes] == ["거실", "탁자", "거리", "공항"]  # 조사 뗌
    lines = [r.getMessage() for r in caplog.records if "scene_detail_filled" in r.getMessage()]
    assert lines == ["scene_detail_filled n=16 scenes=4"]  # 한 줄, 장면 글 없음
    assert "여행자" not in lines[0]


def test_filled_fields_do_not_overwrite_llm_values() -> None:
    out = _bare_out()
    out.scenes[0].place_ko = "거실 소파"
    out.scenes[0].camera_en = "medium shot, eye level, slow push-in"
    out.scenes[0].start_frame = "주인공이 소파 가운데 앉아 휴대폰을 봄. 뒤에 스탠드·탁자"
    plan = asyncio.run(vp.write(MockLLMClient(responses={vp._PlanOut: out}), _prop(), "ctx"))
    first = plan.scenes[0]
    assert first.place_ko == "거실 소파"
    assert first.camera_en == "medium shot, eye level, slow push-in"
    assert first.start_frame.startswith("주인공이 소파")
    assert first.end_frame == VISUALS[0]  # 빈 칸만 채운다


def test_forbidden_cleared_scene_and_rule_plan_stay_as_is() -> None:
    out = _bare_out()
    out.scenes[1].end_frame = "여행자가 가게 간판 앞에 선다"  # 글자 → 그 장면 키프레임 칸 비움
    plan = asyncio.run(vp.write(MockLLMClient(responses={vp._PlanOut: out}), _prop(), "ctx"))
    cleared = plan.scenes[1]
    assert (cleared.start_frame, cleared.end_frame, cleared.action_en, cleared.camera_en) == (
        "", "", "", "")  # fmt: skip
    assert plan.scenes[0].start_frame == VISUALS[0]
    rule = vp.rule_plan(_prop(), (vp.PRODUCT, vp.END_CARD))
    assert vp.fill_scene_detail(rule) is rule  # 규칙 제작안은 그대로


def test_end_card_scene_is_left_unfilled() -> None:
    out = _bare_out()
    out.scenes[3].visual = "채널 이름 엔드 카드"
    plan = asyncio.run(vp.write(MockLLMClient(responses={vp._PlanOut: out}), _prop(), "ctx"))
    last = plan.scenes[3]
    assert (last.start_frame, last.camera_en, last.place_ko) == ("", "", "")


@pytest.mark.parametrize(
    ("text", "company", "want"),
    [
        ("끝에 누비투어 채널 이름", "누비투어", True),
        ("채널 이름 엔드 카드", "", True),
        ("로고와 채널 이름", "", True),
        ("End card with channel logo", "", True),
        ("주인공이 손 흔들며 마무리", "누비투어", False),
        ("엔드카드 앞에서 주인공이 손 흔듦", "", False),  # 사람 낱말이 있으면 사람 장면
        ("거실 소파에 앉은 여행자", "", False),
        ("", "", False),
    ],
)
def test_end_card_scene(text: str, company: str, want: bool) -> None:
    assert plan_checks.end_card_scene(text, company) is want
