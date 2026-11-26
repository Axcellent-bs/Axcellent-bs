"""기획 콘티 = 3D 그레이박스 (11/26 운영 CR-cb113dca245a · 누비투어).

장면의 장소·글·camera_en을 렌더러에 넘겨 방·가구·앵글로 그리고, 소파 장면은 앉고(LLM 경로·규칙
경로 둘 다), 라벨 시각은 장면 시각(첫 칸 start_s, 끝 칸 end_s), 엔드카드 장면은 사람 없이."""

from __future__ import annotations

import asyncio
import re

import pytest

from ax_agent_template import pose_render as pr
from ax_agent_template.llm import MockLLMClient
from ax_contracts.schemas.planning import VideoPlan, VideoScene
from planning_agent import storyboard_svg as sb
from planning_agent import video_plan as vp

pytestmark = pytest.mark.role("planning")

SCENES = [
    {"visual": "거실 소파에 앉아 휴대폰을 보는 여행자", "place_ko": "거실 소파",
     "camera_en": "medium shot, eye level, slow push-in", "start_s": 0, "end_s": 5},
    {"visual": "카페 창가에서 여권을 펼쳐 보는 여행자", "place_ko": "카페 창가",
     "camera_en": "close-up, slightly high angle, static", "start_s": 5, "end_s": 14},
    {"visual": "눈 덮인 거리에서 캐리어를 끄는 여행자", "place_ko": "눈 덮인 거리",
     "camera_en": "full shot, low angle, tracking left", "start_s": 14, "end_s": 18},
    {"visual": "끝에 누비투어 채널 이름", "place_ko": "", "camera_en": "", "start_s": 18,
     "end_s": 20},
]  # fmt: skip


def _plan() -> VideoPlan:
    scenes = [VideoScene(n=i, prompt="A traveler, warm light", **s)
              for i, s in enumerate(SCENES, start=1)]  # fmt: skip
    return VideoPlan(concept="겨울 여행", duration_s=20, script="s", scenes=scenes, style="st",
                     negative="ng", written_by="llm")  # fmt: skip


def _poses(posture: str | None) -> sb._PoseOut:
    pose = {"shot": "medium", "label_ko": "0초 · 동작"} | ({"posture": posture} if posture else {})
    return sb._PoseOut.model_validate(
        {"scenes": [{"n": n, "start": pose, "end": pose} for n in range(1, 5)]}
    )


def _draw(llm: object) -> VideoPlan:
    drawn, _ = asyncio.run(sb.draw(llm, _plan(), "ctx", company="누비투어"))
    return drawn


SOFA_FACES = [pr.shade(pr.SOFA, k) for k in (pr.SIDE_K, pr.TOP_K, pr.FRONT_K)]


@pytest.mark.parametrize("posture", [None, "sit"])
def test_llm_path_sofa_scene_sits_in_a_living_room(posture: str | None) -> None:
    """LLM이 posture를 적었든(sit) 안 적었든(장면 글 "소파" → sit) 소파 장면은 앉는다."""
    plan = _draw(MockLLMClient(responses={sb._PoseOut: _poses(posture)}))
    sofa = plan.scenes[0]
    assert sofa.start_pose is not None and sofa.start_pose.posture == "sit"
    for face in SOFA_FACES:
        assert face in sofa.start_svg  # 소파 상자 면 3개
    assert '<g stroke="#e6dccd"' not in sofa.start_svg  # 격자 대신 방
    if posture is None:  # 적지 않았고 소파·앉 낱말이 없는 장면은 서 있다
        cafe = plan.scenes[1].start_pose
        assert cafe is not None and cafe.posture == "stand"


def test_rule_path_sofa_scene_sits_too() -> None:
    plan = _draw(None)  # LLM 없음 → 4단 규칙 포즈
    sofa = plan.scenes[0]
    assert sofa.start_pose is not None and sofa.start_pose.posture == "sit"
    assert all(face in sofa.start_svg for face in SOFA_FACES)


def test_label_time_is_scene_start_and_end() -> None:
    plan = _draw(MockLLMClient(responses={sb._PoseOut: _poses(None)}))
    for s in plan.scenes[:3]:
        assert s.start_pose is not None and s.end_pose is not None
        assert s.start_pose.label_ko == f"{s.start_s}초 · 동작"  # LLM이 적은 "0초"는 버린다
        assert s.end_pose.label_ko == f"{s.end_s}초 · 동작"
        assert f">{s.start_s}초 · 동작</text>" in s.start_svg
        assert f">{s.end_s}초 · 동작</text>" in s.end_svg


def test_end_card_scene_has_no_person() -> None:
    plan = _draw(MockLLMClient(responses={sb._PoseOut: _poses(None)}))
    last = plan.scenes[3]
    for svg in (last.start_svg, last.end_svg):
        assert svg == sb.sanitize_svg(pr.end_card(plan.aspect))
        assert "url(#sk)" not in svg and "<circle" not in svg and pr.END_CARD_TEXT in svg
    assert "url(#sk)" in plan.scenes[2].start_svg  # 다른 장면은 사람이 있다


def test_angle_and_place_reach_the_renderer() -> None:
    plan = _draw(MockLLMClient(responses={sb._PoseOut: _poses(None)}))
    cafe, snow = plan.scenes[1], plan.scenes[2]
    assert pr.ROOM_COLORS["cafe"][3] in cafe.start_svg  # 카페 바닥
    assert 'id="sky"' in snow.start_svg and pr.OUTDOOR_COLORS["snow"][2] in snow.start_svg
    for s in plan.scenes:
        assert len(s.start_svg.encode()) <= sb.MAX_SVG_BYTES


def test_marketing_revise_redraw_keeps_room_and_end_card() -> None:
    old = _draw(MockLLMClient(responses={sb._PoseOut: _poses(None)}))
    redrawn = vp.redraw(old, vp.retime(old))
    assert all(face in redrawn.scenes[0].start_svg for face in SOFA_FACES)
    assert re.search(r">\d+초 · 동작</text>", redrawn.scenes[0].start_svg)
    # 회사 이름은 지금 회사 설정에서 읽는다 (시험 회사 이름이 누비투어가 아니어도 "채널 이름"은
    # 회사 이름 없이도 엔드카드 낱말)
    assert sb.is_end_card(redrawn.scenes[3], "누비투어")
