"""3D 콘티 장소 묶음 보강 (11/26 J, 운영 wr_6d9d9e6df4 PROP-42).

장면 2 "거실 낮은 탁자"가 소파만(탁자 없음, 소파에 앉음), 장면 3 "현관"(신발장·우산·문)이 바깥 건물
거리로 그려졌다.
- 탁자 낱말이 있으면 탁자 상자를 인물 앞에, 앉으면 탁자 앞 의자(소파는 뒤로)
- 실내 묶음: 현관(문·신발장·우산)·복도(양옆 벽 문)·방(침대·창)·서점 안(책장 줄)
- 서점 앞·가게 앞: 유리문 가게 + 바깥 (눈 낱말이면 눈 바닥)
- place_ko 먼저, 없을 때만 장면 글. 현관 글에 바깥 낱말("문틈 너머 눈")이 있어도
  place_ko가 실내면 실내
"""

from __future__ import annotations

import re
from typing import Any

import pytest

from ax_agent_template import pose_render as pr
from ax_contracts.schemas.planning import KeyPose


def _pose(**kw: Any) -> KeyPose:
    return KeyPose.model_validate(kw)


def _faces(color: str) -> list[str]:
    return [pr.shade(color, k) for k in (pr.SIDE_K, pr.TOP_K, pr.FRONT_K)]


def _has_box(svg: str, color: str) -> bool:
    return all(f'fill="{face}"' in svg for face in _faces(color))


# PROP-42 장면 글 (운영 제작안 그대로의 모양)
TABLE_SCENE = pr.Scene(place="거실 낮은 탁자", text="탁자 앞에 앉아 책을 펼쳐 읽는다",
                       camera="medium shot, eye level")  # fmt: skip
ENTRANCE = pr.Scene(place="현관", text="현관 신발장 옆에서 우산을 들고 문틈 너머 눈을 본다",
                    camera="full shot, eye level")  # fmt: skip
STORE_SNOW = pr.Scene(place="눈 오는 서점 앞", text="서점 앞에서 손을 흔든다",
                      camera="full shot, low angle")  # fmt: skip


@pytest.mark.parametrize(
    ("place", "text", "want"),
    [
        ("현관", "문틈 너머 눈이 내린다", "entrance"), ("", "현관 신발장 앞에 선다", "entrance"),
        ("복도", "", "hallway"), ("침실", "", "bedroom"), ("자취방", "", "bedroom"),
        ("서점 안", "", "bookstore"), ("", "서점 책장 사이를 걷는다", "bookstore"),
        ("서점 앞", "", "storefront"), ("가게 앞", "", "storefront"),
        ("눈 오는 서점 앞", "", "storefront_snow"),
        ("서점 앞", "눈 내리는 저녁", "storefront_snow"),
        ("거실 낮은 탁자", "", "living"), ("거리", "현관 앞 골목", "street"),  # place_ko 먼저
    ],
)  # fmt: skip
def test_new_place_groups(place: str, text: str, want: str) -> None:
    assert pr.place_of(pr.Scene(place=place, text=text)) == want


def test_table_scene_puts_a_table_in_front_and_sits_on_a_chair_not_the_sofa() -> None:
    svg = pr.render(_pose(shot="medium", posture="sit", prop="book"), "9:16", TABLE_SCENE)
    assert _has_box(svg, pr.TABLE) and _has_box(svg, pr.SEAT)
    boxes = pr.furniture("living", 0.0, 1.0, "sit", table=True)
    table = next(b for b, c in boxes if c == pr.TABLE)
    assert table[5] < 1.0 and table[3] <= 0.45  # 인물 앞(카메라 쪽)의 낮은 탁자
    sofa = [b for b, c in boxes if c == pr.SOFA]
    assert sofa and all(b[4] > 2.5 for b in sofa)  # 소파는 뒤로 (엉덩이 밑이 아님)
    seat = next(b for b, c in boxes if c == pr.SEAT)
    assert seat[4] < 1.0 < seat[5]  # 탁자 앞 의자가 엉덩이 밑
    # 장소 없이 탁자만 있어도 탁자를 그린다 (예: "탁자 위 여권을 집어 든다")
    plain = pr.render(_pose(), "9:16", pr.Scene(text="탁자 위 여권을 집어 든다"))
    assert _has_box(plain, pr.TABLE)
    # 카페·사무실은 원래 탁자·책상이 있어 그대로
    cafe = pr.Scene(place="카페 창가", text="테이블에 컵")
    same = pr.render(_pose(), "9:16", pr.Scene(place="카페 창가"))
    assert pr.render(_pose(), "9:16", cafe) == same


def test_entrance_is_indoor_with_door_shoe_rack_and_umbrella() -> None:
    svg = pr.render(_pose(shot="full"), "9:16", ENTRANCE)
    _, back, _, floor = pr.ROOM_COLORS["entrance"]
    assert f'fill="{back}"' in svg and f'fill="{floor}"' in svg
    assert f'fill="{pr.DOOR}"' in svg and f'fill="{pr.GLASS}"' in svg
    assert _has_box(svg, pr.SHOE_RACK) and _has_box(svg, pr.UMBRELLA)
    assert 'url(#sky)' not in svg  # 바깥 하늘이 아니다


def test_hallway_bedroom_and_bookstore() -> None:
    hall = pr.render(_pose(), "9:16", pr.Scene(place="복도"))
    assert hall.count(f'fill="{pr.DOOR}"') >= 3  # 양옆 벽 문 + 끝 문
    bed = pr.render(_pose(), "9:16", pr.Scene(place="침실"))
    assert _has_box(bed, pr.BED) and f'fill="{pr.WINDOW}"' in bed
    shop = pr.render(_pose(), "9:16", pr.Scene(place="서점 안"))
    assert shop.count(f'fill="{pr.shade(pr.SHELF, pr.FRONT_K)}"') >= 4  # 책장 줄


def test_snowy_storefront_has_glass_door_and_snow_ground() -> None:
    svg = pr.render(_pose(shot="full", arm_r={"raise": 150, "bend": 20}), "9:16", STORE_SNOW)
    ground = pr.OUTDOOR_COLORS["storefront_snow"][2]
    assert ground == pr.OUTDOOR_COLORS["snow"][2] and f'fill="{ground}"' in svg
    assert 'url(#sky)' in svg and _has_box(svg, pr.FACADE) and _has_box(svg, pr.GLASS)
    dry = pr.render(_pose(shot="full"), "9:16", pr.Scene(place="서점 앞"))
    assert f'fill="{pr.OUTDOOR_COLORS["street"][2]}"' in dry and _has_box(dry, pr.GLASS)


def test_new_groups_fit_16kb_with_a_person() -> None:
    busy = {"posture": "sit", "prop": "book", "extra_props": ["mic", "laptop"],
            "label_ko": "14.5초 · 아주 긴 라벨 글자가 여기까지 들어간다"}  # fmt: skip
    scenes = [TABLE_SCENE, ENTRANCE, STORE_SNOW, pr.Scene(place="복도"), pr.Scene(place="침실"),
              pr.Scene(place="서점 안", text="탁자 위 책")]  # fmt: skip
    for scene in scenes:
        for shot in pr.SHOTS:
            for extra in ({}, busy):
                svg = pr.render(_pose(shot=shot, **extra), "9:16", scene)
                assert len(svg.encode("utf-8")) <= pr.MAX_BYTES, (scene.place, shot)
                assert 'fill="url(#sk)"' in svg
                assert re.search(r"<svg [^>]*>", svg)
