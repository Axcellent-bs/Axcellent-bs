"""3D 그레이박스 콘티 (11/26 운영 CR-cb113dca245a · 누비투어).

콘티 SVG 배경이 바닥 격자뿐이었고, 앉은 자세를 못 그렸고, 엔드카드 칸에도 사람이 있었다.
- 장소 묶음 → 방(바닥·뒷벽·옆벽 원근)·바깥, 가구는 직육면체 면 3개(면마다 밝기 다르게)
- camera_en 앵글 → 카메라 높이, posture sit·kneel·lie → 엉덩이 높이·무릎
- 라벨 시각 = 넘긴 칸 시각, 엔드카드 칸은 사람 없이, 칸당 16KB 이하, 장소를 모르면 예전 격자"""

from __future__ import annotations

import re
from typing import Any

import pytest

from ax_agent_template import pose_ladder
from ax_agent_template import pose_render as pr
from ax_contracts.schemas.planning import KeyPose


def _pose(**kw: Any) -> KeyPose:
    return KeyPose.model_validate(kw)


def _polygon_fills(svg: str) -> list[str]:
    return re.findall(r'<polygon points="[^"]+" fill="(#[0-9a-f]{6})"', svg)


LIVING = pr.Scene(place="거실 소파", text="저녁 거실, 스탠드 불빛", camera="medium shot, eye level")


def test_living_room_has_floor_walls_in_perspective_and_a_three_face_sofa() -> None:
    svg = pr.render(_pose(shot="medium", posture="sit", prop="phone"), "9:16", LIVING)
    ceiling, back, side, floor = pr.ROOM_COLORS["living"]
    fills = _polygon_fills(svg)
    assert floor in fills and back in fills and fills.count(side) == 2  # 바닥·뒷벽·옆벽 둘
    sofa = [pr.shade(pr.SOFA, k) for k in (pr.SIDE_K, pr.TOP_K, pr.FRONT_K)]
    assert len(set(sofa)) == 3  # 면마다 밝기가 다르다
    for face in sofa:
        assert face in fills
    # 상자 하나 = 옆·위·앞 세 면이 한 묶음
    box = re.search(r"<g [^>]*>((?:<polygon [^>]*/>){3})</g>", svg)
    assert box is not None and box.group(1).count("<polygon") == 3
    # 옆벽은 원근: 뒷벽 쪽 모서리가 앞쪽보다 칸 가운데에 가깝다
    left = re.findall(rf'<polygon points="([^"]+)" fill="{side}"', svg)[0]
    xs = [float(p.split(",")[0]) for p in left.split()]
    assert max(xs[:2]) > min(xs[:2])
    assert 'stroke="#e6dccd"' not in svg  # 장소를 알면 격자 대신 방


def test_no_scene_keeps_the_old_floor_grid() -> None:
    svg = pr.render(_pose(shot="medium"))
    assert '<g stroke="#e6dccd"' in svg
    assert pr.render(_pose(shot="medium"), "9:16", pr.Scene()) == svg  # 장소 낱말 없음 = 격자


@pytest.mark.parametrize(
    ("place", "text", "want"),
    [
        ("거실 소파", "", "living"), ("", "집에서 쉬는 주인공", "living"),
        ("카페 창가", "", "cafe"), ("공항 대합실", "", "airport"), ("기차역", "", "airport"),
        ("사무실", "", "office"), ("눈 덮인 거리", "", "snow"), ("거리", "", "street"),
        # 11/26 J 바뀐 기대값: 책방은 서점 안 묶음 (예전 "" = 격자)
        ("", "캐리어를 끌고 눈 덮인 골목을 걷는다", "snow"), ("책방", "", "bookstore"),
        ("", "", ""),
        ("", "탁자 위 여권을 집어 든다", ""),  # "집어"는 집이 아니다
    ],
)  # fmt: skip
def test_place_groups(place: str, text: str, want: str) -> None:
    assert pr.place_of(pr.Scene(place=place, text=text)) == want


@pytest.mark.parametrize(
    ("camera", "want"),
    [("medium shot, eye level, slow push-in", "eye"), ("full shot, low angle, static", "low"),
     ("close-up, slightly high angle", "high"), ("overhead top-down shot", "overhead"),
     ("", "eye")],
)  # fmt: skip
def test_angle_words(camera: str, want: str) -> None:
    assert pr.angle_of(camera) == want


def test_low_angle_camera_is_lower_than_eye_level_and_high_is_higher() -> None:
    for shot in pr.SHOTS:
        eye, low = pr._Camera(shot, 180, 320, "eye"), pr._Camera(shot, 180, 320, "low")  # noqa: SLF001
        high = pr._Camera(shot, 180, 320, "high")  # noqa: SLF001
        assert low.pos[1] < eye.pos[1] < high.pos[1]
    low_svg = pr.render(_pose(), "9:16", pr.Scene(place="거실", camera="medium shot, low angle"))
    eye_svg = pr.render(_pose(), "9:16", pr.Scene(place="거실", camera="medium shot, eye level"))
    assert low_svg != eye_svg


def test_camera_looks_at_its_target() -> None:
    """바라보는 점이 칸 가운데 높이에 온다 (11/26 기울기가 두 배였던 것을 고침)."""
    for angle in pr.ANGLES:
        for shot in pr.SHOTS:
            c = pr._Camera(shot, 180, 320, angle)  # noqa: SLF001
            look = pr.CAMERAS[shot][1]
            target = (look[0], look[1] + pr.ANGLES[angle][1], look[2])
            assert abs(c.project(target).y - 160) < 0.5, (angle, shot)


def test_sit_lowers_the_pelvis_and_folds_the_knees_forward() -> None:
    stand, sit = pr.skeleton(_pose()), pr.skeleton(_pose(posture="sit"))
    assert sit["pelvis"][1] < stand["pelvis"][1] - 0.3
    assert sit["kn_l"][2] < sit["hip_l"][2] - 0.3  # 무릎이 몸 앞(-z)으로
    assert sit["head"][1] < stand["head"][1]
    kneel, lie = pr.skeleton(_pose(posture="kneel")), pr.skeleton(_pose(posture="lie"))
    assert kneel["kn_l"][1] < 0.1 and kneel["pelvis"][1] < stand["pelvis"][1]
    assert max(v[1] for v in lie.values()) < 0.7  # 누우면 몸 전체가 낮다


def test_sitting_in_a_living_room_sits_on_the_sofa() -> None:
    svg = pr.render(_pose(posture="sit"), "9:16", LIVING)
    standing = pr.render(_pose(), "9:16", LIVING)
    assert svg != standing
    seat = pr.furniture("living", 0.0, 0.0, "sit")[0][0]
    assert seat[3] < pr.skeleton(_pose(posture="sit"))["pelvis"][1]  # 소파 윗면 < 엉덩이
    assert pr.furniture("", 0.0, 0.0, "sit")  # 장소를 몰라도 앉을 상자


def test_every_place_and_shot_fits_16kb() -> None:
    busy = {"posture": "sit", "prop": "book", "extra_props": ["mic", "laptop"],
            "label_ko": "14.5초 · 아주 긴 라벨 글자가 여기까지 들어간다"}  # fmt: skip
    for kind, words in pr.PLACE_WORDS:
        for shot in pr.SHOTS:
            for angle in ("eye level", "low angle", "overhead"):
                for extra in ({}, busy):
                    scene = pr.Scene(place=words[0], camera=f"{shot}, {angle}")
                    svg = pr.render(_pose(shot=shot, **extra), "9:16", scene)
                    assert len(svg.encode("utf-8")) <= pr.MAX_BYTES, (kind, shot, angle)
                    assert 'fill="url(#sk)"' in svg  # 인물은 늘 있다


def test_label_time_is_the_panel_time() -> None:
    pose = _pose(label_ko="0초 · 소파에서 폰")
    svg = pr.render(pose, "9:16", LIVING, at_s=5.5)
    assert ">5.5초 · 소파에서 폰</text>" in svg and ">0초" not in svg
    assert ">0초 · 소파에서 폰</text>" in pr.render(pose)  # 시각을 안 넘기면 받은 글 그대로


def test_end_card_has_no_person() -> None:
    svg = pr.end_card()
    assert svg.startswith(f'<svg xmlns="{pr.SVG_NS}" viewBox="0 0 180 320">')
    assert pr.END_CARD_TEXT in svg and pr.END_BG in svg
    for absent in ("<circle", "<line", "<ellipse", "<polygon", "url(#sk)", "<radialGradient"):
        assert absent not in svg


def test_head_shows_where_it_looks() -> None:
    side = pr.render(_pose(yaw=90))
    back = pr.render(_pose(yaw=180))
    nose = f'fill="{pr.NOSE}"/>'
    assert nose in side and nose not in back


# ---------------------------------------------------------------- 자세 낱말 (LLM·규칙 같은 표)


def test_pose_prompt_lists_posture() -> None:
    assert '"sit"' in pr.POSE_FIELDS and "소파" in pr.POSE_FIELDS and '"lie"' in pr.POSE_FIELDS


@pytest.mark.parametrize(
    ("text", "want"),
    [("거실 소파에 앉아 휴대폰을 본다", "sit"), ("A woman sits on a chair", "sit"),
     ("무릎 꿇고 기도한다", "kneel"), ("침대에 누워 천장을 본다", "lie"),
     ("거리에 서서 손을 흔든다", "stand"), ("", "stand")],
)  # fmt: skip
def test_posture_words(text: str, want: str) -> None:
    assert pr.posture_from_text(text) == want


def test_llm_posture_is_kept_and_missing_posture_comes_from_the_scene_words() -> None:
    sofa = "거실 소파에 앉아 휴대폰을 보는 여행자"
    said = pr.key_pose(pr.PoseLLM.model_validate({"posture": "sit"}), "x", 0, "")
    assert said is not None and said.posture == "sit"
    silent = pr.key_pose(pr.PoseLLM.model_validate({"shot": "medium"}), "x", 0, sofa)
    assert silent is not None and silent.posture == "sit"
    stood = pr.key_pose(pr.PoseLLM.model_validate({"posture": "stand"}), "x", 0, sofa)
    assert stood is not None and stood.posture == "stand"  # LLM이 정했으면 그대로
    odd = pr.key_pose(pr.PoseLLM.model_validate({"posture": "fly"}), "x", 0, "")
    assert odd is not None and odd.posture == "stand"


def test_rule_pose_sits_on_the_sofa() -> None:
    assert pose_ladder.rule_pose("거실 소파에 앉아 휴대폰을 본다").posture == "sit"
    assert pose_ladder.rule_pose("무릎 꿇고 상자를 연다").posture == "kneel"
    assert pose_ladder.rule_pose("거리를 걷는다").posture == "stand"
