"""기획 콘티가 PROP-42 장면 장소를 그린다 (11/26 J): 거실 탁자 · 현관 · 눈 오는 서점 앞.

운영 wr_6d9d9e6df4: 장면 2 "거실 낮은 탁자"가 소파만, 장면 3 "현관"이 바깥 건물 거리였다."""

from __future__ import annotations

import asyncio

import pytest

from ax_agent_template import pose_render as pr
from ax_agent_template.llm import MockLLMClient
from ax_contracts.schemas.planning import VideoPlan, VideoScene
from planning_agent import storyboard_svg as sb

pytestmark = pytest.mark.role("planning")

SCENES = [
    {"visual": "저녁 거실 소파에 앉아 책을 고르는 회사 캐릭터", "place_ko": "거실 소파",
     "camera_en": "medium shot, eye level", "start_s": 0, "end_s": 4},
    {"visual": "거실 낮은 탁자 앞에 앉아 책을 펼쳐 읽는다", "place_ko": "거실 낮은 탁자",
     "camera_en": "medium shot, slightly high angle", "start_s": 4, "end_s": 8},
    {"visual": "현관 신발장 옆에서 우산을 들고 문틈 너머 눈을 본다", "place_ko": "현관",
     "camera_en": "full shot, eye level", "start_s": 8, "end_s": 11},
    {"visual": "눈 오는 서점 앞에서 손을 흔든다", "place_ko": "눈 오는 서점 앞",
     "camera_en": "full shot, low angle", "start_s": 11, "end_s": 13},
    {"visual": "끝에 캣북스 채널 이름", "place_ko": "", "camera_en": "", "start_s": 13,
     "end_s": 15},
]  # fmt: skip


def _faces(color: str) -> list[str]:
    return [pr.shade(color, k) for k in (pr.SIDE_K, pr.TOP_K, pr.FRONT_K)]


def _has_box(svg: str, color: str) -> bool:
    return all(f'fill="{face}"' in svg for face in _faces(color))


def _draw() -> VideoPlan:
    scenes = [VideoScene(n=i, prompt="A girl, warm light", **s)
              for i, s in enumerate(SCENES, start=1)]  # fmt: skip
    plan = VideoPlan(concept="겨울 독서", duration_s=15, script="s", scenes=scenes, style="st",
                     negative="ng", written_by="llm")  # fmt: skip
    pose = {"shot": "medium", "label_ko": "0초 · 동작"}
    out = sb._PoseOut.model_validate(
        {"scenes": [{"n": n, "start": pose, "end": pose} for n in range(1, 6)]}
    )
    drawn, _ = asyncio.run(sb.draw(MockLLMClient(responses={sb._PoseOut: out}), plan, "ctx",
                                   company="캣북스"))  # fmt: skip
    return drawn


def test_prop42_places_are_drawn() -> None:
    plan = _draw()
    table, entrance, store = plan.scenes[1], plan.scenes[2], plan.scenes[3]
    assert table.start_pose is not None and table.start_pose.posture == "sit"  # "앉아"
    assert _has_box(table.start_svg, pr.TABLE) and _has_box(table.start_svg, pr.SEAT)
    assert _has_box(entrance.start_svg, pr.SHOE_RACK) and "url(#sky)" not in entrance.start_svg
    assert f'fill="{pr.DOOR}"' in entrance.start_svg
    assert _has_box(store.start_svg, pr.GLASS) and "url(#sky)" in store.start_svg
    assert f'fill="{pr.OUTDOOR_COLORS["storefront_snow"][2]}"' in store.start_svg
    for s in plan.scenes:
        assert len(s.start_svg.encode("utf-8")) <= pr.MAX_BYTES
