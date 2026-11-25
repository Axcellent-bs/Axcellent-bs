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
# 고정 포즈 출력 (길이, sha256). 11/26 바뀐 기대값: 카메라(CAMERAS) 높이·초점을 인물이 칸 높이의
# 55~70%가 되게 바꿔서(예전 45~54%) 다시 찍었다. 그 밖의 그리기는 같다(소품 없는 포즈는
# 새 소품 0개).
# 예전 값(옮기기 전 기획 렌더러 dev 5ce647db): (6875, bfe291de…) (6973, f335290d…) (7033, 22ae5b54…)
# (6917, 3d94cb9d…), 16:9 (6921, bebfa489…)
OLD_PLANNING = [
    (6861, "7db32ac6ae599acc92db48e9ff6fcad302b22aeb917a01ff1b694b9c6a775a51"),
    (7026, "beb6a47794c9219d9c6965f767480184f11087a5fe0a4992eef6bc6a3e0f1608"),
    (7011, "4a41a76f86d5d1dd7462af02dc1947f4e46396ea1d4b7581adca6f9e3a78726b"),
    (6944, "ac6dbc2d2f3bdbb96c0dd46c3c4019d156e68167320a26d657e78f86061f5473"),
]
OLD_PLANNING_16_9 = (6784, "e17b19265f217f19ce0d5a4078433b2b82ce832b90429c81989b8c1a89f0047e")
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


# ---------------------------------------------------------------- 11/26 시각 라벨·소품·인물 크기


def test_llm_time_in_the_label_is_replaced_by_the_panel_time() -> None:
    got = pr.PoseLLM.model_validate({"label_ko": "0초 · 마이크 들고 노래"})
    assert pr.key_pose(got, "x", at_s=4).label_ko == "4초 · 마이크 들고 노래"  # type: ignore[union-attr]
    assert pr.time_label("12.5초 놀라 돌아봄", 8) == "8초 · 놀라 돌아봄"
    assert pr.time_label("손 흔듦", 2.5) == "2.5초 · 손 흔듦"
    assert pr.time_label("0초", 12) == "12초"
    assert pr.time_label("0초 · 돌아봄", None) == "0초 · 돌아봄"  # 시각을 모르면 받은 글 그대로
    assert '"0초' not in pr.POSE_FIELDS and "동작만 짧게" in pr.POSE_FIELDS


def test_extra_props_are_drawn_and_unknown_ones_dropped() -> None:
    got = pr.PoseLLM.model_validate({"extra_props": ["mic", "sword", "mic", "spotlight", "bag"]})
    p = pr.key_pose(got, "x")
    assert p is not None and p.extra_props == ["mic", "spotlight"]  # 목록 밖 버림, 최대 2개
    plain = pr.render(_pose())
    for ex, color in (("mic", "#8a8f99"), ("spotlight", "rgba(255,236,170,.35)"),
                      ("screen", "#5b6fd6"), ("laptop", "#9aa3b2"), ("bag", "#b0703c"),
                      ("sign_blank", 'fill="#fdfbf7" stroke="#8a7a66"')):  # fmt: skip
        svg = pr.render(_pose(extra_props=[ex]))
        assert color in svg and color not in plain
        assert "<text" not in svg  # 소품에 글자 없음 (라벨 없는 포즈)
        assert len(svg.encode()) <= pr.MAX_BYTES


def test_words_in_the_scene_add_a_prop_even_if_the_llm_did_not() -> None:
    got = pr.PoseLLM.model_validate({})
    p = pr.key_pose(got, "x", text="보라 LED 무대에서 마이크를 들고 노래한다")
    assert p is not None and p.extra_props == ["mic"]
    assert pr.props_from_text("a woman opens her laptop under a spotlight") == ["laptop",
                                                                               "spotlight"]
    assert pr.props_from_text("design signage") == []  # 영문은 낱말 경계 (sign ≠ signage)
    assert pr.key_pose(pr.PoseLLM.model_validate({"prop": "book"}), "x").prop == "book"  # type: ignore[union-attr]


def test_old_prop_is_unchanged_and_hands_do_not_collide() -> None:
    both = pr.render(_pose(prop="phone", extra_props=["mic"]))
    assert PROP_COLORS["phone"] in both and "#8a8f99" in both  # 휴대폰 오른손, 마이크는 왼손


@pytest.mark.parametrize("shot", ["close", "medium", "full"])
def test_figure_is_55_to_70_percent_of_the_panel(shot: str) -> None:
    for aspect in ("9:16",):
        w, h = pr.SIZES[aspect]
        c, j = pr._Camera(shot, w, h), pr.skeleton(_pose(shot=shot))  # noqa: SLF001
        hd, ft = c.project(j["head"]), c.project(j["ft_l"])
        top, bottom = hd.y - 0.11 * hd.s, ft.y + 0.07 * ft.s
        ratio = (min(bottom, h) - max(top, 0)) / h
        low, high = pr.FIGURE_RATIO
        assert low <= ratio <= high, (shot, ratio)
        assert top > 20  # 위쪽 라벨 줄과 겹치지 않는다
        if shot == "full":
            assert bottom <= h  # 온몸 샷은 발까지 보인다
