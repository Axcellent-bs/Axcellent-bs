"""공용 3D 마네킹 콘티 렌더러 (11/25, `ax_agent_template.pose_render`).

- 옛 기획 렌더러(`planning_agent.pose_render`, #1807)와 출력이 같다: 고정 포즈 4개 + 16:9 한 칸의
  SVG 원문 sha256·길이를 옮기기 전에 찍어 둔 값과 비교한다
- 포즈마다 다른 그림, 샷 크기별 인물 크기, 카메라 쪽일 때만 눈, 소품, 비율, 글자 이스케이프
  (기획 시험에서 옮김)
- LLM 답 파싱(`key_pose`): 모르는 shot·prop은 기본값, 범위는 자름, label 비면 대신 글
- 엔드카드 배경(`backdrop`): 그라데이션만, 인물·글자 없음
"""

from __future__ import annotations

import hashlib
import re
from typing import Any

import pytest

from ax_agent_template import pose_render as pr
from ax_contracts.schemas.planning import KeyPose

# 화면 시안(3D 마네킹 콘티)의 4칸 포즈 (기획 test_pose_storyboard.SAMPLES와 같은 입력)
SAMPLES = [
    {"label_ko": "0초 · 놀라 돌아봄", "shot": "medium", "x": 0, "z": 0, "yaw": 35,
     "head": {"yaw": -40, "pitch": -5}, "arm_l": {"raise": 40, "bend": 60},
     "arm_r": {"raise": 30, "bend": 80}, "step": 0.15},
    {"label_ko": "2초 · 책상에서 한 줄", "shot": "full", "x": -0.5, "z": 0.5, "yaw": -20,
     "head": {"yaw": 0, "pitch": 20}, "arm_l": {"raise": 50, "bend": 70},
     "arm_r": {"raise": 55, "bend": 60}, "step": 0.05, "prop": "desk"},
    {"label_ko": "7초 · 노트 들고 보여 줌", "shot": "medium", "x": 0.3, "z": 0, "yaw": 0,
     "head": {"yaw": 10, "pitch": 0}, "arm_l": {"raise": 70, "bend": 40},
     "arm_r": {"raise": 10, "bend": 10}, "step": 0.1, "prop": "book"},
    {"label_ko": "12초 · 걸어 나감", "shot": "full", "x": 0.8, "z": 1.2, "yaw": -70,
     "head": {"yaw": 20, "pitch": 0}, "arm_l": {"raise": 25, "bend": 20},
     "arm_r": {"raise": -20, "bend": 15}, "step": 0.45},
]  # fmt: skip
# 옮기기 전 기획 렌더러(dev 5ce647db의 planning_agent.pose_render.render)로 찍은 값 (길이, sha256)
OLD_PLANNING = [
    (6875, "bfe291de3533075582a610946d086ec8f4865860302f0173a70e20d6f540c09d"),
    (6973, "f335290dd19ac8e039f5cdacedf162bd005392caf89e60728ed37dbe8721f0e3"),
    (7033, "22ae5b54843b7ed550bfc9fb1588ae2ca7004e24f3d9426dce20f243e9244cc1"),
    (6917, "3d94cb9d24927eb38070329b6e86b89bd9a3a40bacd1e7128da0c7ede258ce08"),
]
OLD_PLANNING_16_9 = (6921, "bebfa4899c1012a9cfb1a323a79095094c02d70da8fea5c7d383e603bc519c77")
PROP_COLORS = {"book": "#e2552f", "desk": "#c9a27a", "phone": "#2b2f36", "shelf": "#b98d63",
               "cup": "#f4f1ea"}  # fmt: skip
EYE = 'fill="#333"'


def _pose(**kw: Any) -> KeyPose:
    return KeyPose.model_validate(kw)


def _sum(svg: str) -> tuple[int, str]:
    return len(svg), hashlib.sha256(svg.encode()).hexdigest()


def _head_r(svg: str) -> float:
    """머리 구(피부 그라데이션 원 중 가장 큰 것)의 반지름."""
    return max(float(r) for r in re.findall(r'<circle [^>]*r="([\d.]+)" fill="url\(#sk\)"', svg))


@pytest.mark.parametrize(("raw", "want"), list(zip(SAMPLES, OLD_PLANNING, strict=True)))
def test_same_output_as_old_planning_renderer(raw: dict[str, Any], want: tuple[int, str]) -> None:
    assert _sum(pr.render(KeyPose.model_validate(raw))) == want


def test_same_output_as_old_planning_renderer_16_9() -> None:
    assert _sum(pr.render(KeyPose.model_validate(SAMPLES[0]), "16:9")) == OLD_PLANNING_16_9


def test_different_pose_different_svg() -> None:
    a, b = KeyPose.model_validate(SAMPLES[0]), KeyPose.model_validate(SAMPLES[2])
    assert pr.render(a) != pr.render(b)


def test_shot_size_close_is_bigger_than_medium_is_bigger_than_full() -> None:
    r = {shot: _head_r(pr.render(_pose(shot=shot))) for shot in ("close", "medium", "full")}
    assert r["close"] > r["medium"] > r["full"]


def test_eyes_only_when_facing_the_camera() -> None:
    assert pr.render(_pose(yaw=0)).count(EYE) == 2
    assert EYE not in pr.render(_pose(yaw=90))
    assert EYE not in pr.render(_pose(yaw=-90))
    assert EYE not in pr.render(_pose(yaw=180))  # 뒷모습엔 눈을 그리지 않는다


@pytest.mark.parametrize("prop", list(PROP_COLORS))
def test_props_are_drawn(prop: str) -> None:
    svg = pr.render(_pose(prop=prop, arm_l={"raise": 60, "bend": 40}, arm_r={"raise": 60}))
    assert PROP_COLORS[prop] in svg
    others = set(PROP_COLORS.values()) - {PROP_COLORS[prop]}
    assert not any(c in svg for c in others)
    assert not any(c in pr.render(_pose()) for c in PROP_COLORS.values())


def test_other_aspects_fit_the_frame() -> None:
    assert 'viewBox="0 0 320 180"' in pr.render(_pose(), "16:9")
    assert 'viewBox="0 0 180 180"' in pr.render(_pose(), "1:1")


def test_label_is_escaped_text() -> None:
    svg = pr.render(_pose(label_ko="<b>책 & 컵</b>"))
    assert "&lt;b&gt;책 &amp; 컵&lt;/b&gt;" in svg and "<b>" not in svg


def test_key_pose_defaults_clamps_and_label_fallback() -> None:
    got = pr.PoseLLM.model_validate({"shot": "extreme wide", "prop": "sword", "x": 99,
                                     "arm_l": {"raise": 400}, "label_ko": " "})  # fmt: skip
    p = pr.key_pose(got, "3초")
    assert p is not None
    assert (p.shot, p.prop, p.x, p.arm_l.raise_, p.label_ko) == ("medium", None, 1.5, 170, "3초")
    same = pr.key_pose(pr.PoseLLM.model_validate(SAMPLES[1]), "x")
    assert same == KeyPose.model_validate(SAMPLES[1])


def test_pose_fields_prompt_lists_every_number() -> None:
    for key in ("x:", "yaw:", "head:", "arm_l·arm_r:", "step:", "prop:", "label_ko:"):
        assert f"  - {key}" in pr.POSE_FIELDS


@pytest.mark.parametrize(("aspect", "box"), [("9:16", "0 0 180 320"), ("16:9", "0 0 320 180")])
def test_backdrop_is_gradient_only(aspect: str, box: str) -> None:
    svg = pr.backdrop(aspect)
    assert svg.startswith(f'<svg xmlns="http://www.w3.org/2000/svg" viewBox="{box}">')
    assert "<linearGradient" in svg and 'fill="url(#bg)"' in svg
    for absent in ("<text", "<circle", "<line ", "<ellipse", "<polygon", "url(#sk)"):
        assert absent not in svg
