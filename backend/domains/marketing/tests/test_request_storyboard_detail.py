"""의뢰 스토리보드가 기획 장면 칸을 모두 쓴다 (11/26 B, `plan_storyboard`, LLM 없음).

운영 CR-cb113dca245a(누비투어): 칸 시간·장소·샷이 비어 콘티가 빈약했다(샷은 칸마다 돌려 쓰기,
앵글은 늘 눈높이, 엔드카드 칸에도 사람). 가짜 값만 쓴다."""

from __future__ import annotations

from typing import Any

from marketing_agent import request_creative as rc
from marketing_agent.video_draft import VideoPlanIn

SHEET = {"summary_ko": "누비", "consistency_en": "The exact same tour guide girl."}


def _nubi() -> VideoPlanIn:
    scenes: list[dict[str, Any]] = [
        {"n": 1, "start_s": 0, "end_s": 4, "visual": "누비가 공항 출국장에서 손을 흔든다",
         "caption": "여행 준비 끝!", "prompt": "a tour guide girl waves at the airport hall",
         "start_frame": "누비가 캐리어를 끌고 웃으며 화면을 본다", "place_ko": "공항 출국장",
         "action_en": "she raises her right hand and waves with a big smile",
         "camera_en": "medium shot, slow push in",
         "start_pose": {"shot": "medium", "prop": "phone", "extra_props": ["suitcase"]}},
        {"n": 2, "start_s": 4, "end_s": 8, "visual": "누비가 지도를 펼친다",
         "caption": "어디로 갈까?", "prompt": "she unfolds a paper map on a cafe table",
         "start_frame": "카페 창가에서 지도를 든 누비", "place_ko": "카페",
         "action_en": "she spreads the map and points at a city, curious expression",
         "camera_en": "high angle close-up from above the table"},
        {"n": 3, "start_s": 8, "end_s": 13, "visual": "누비가 해변을 달린다",
         "caption": "누비투어와 함께", "prompt": "she runs along a sunny beach",
         "start_frame": "모래사장 위 누비 전신", "place_ko": "해변",
         "action_en": "she runs toward the camera, laughing",
         "camera_en": "low angle wide tracking shot"},
        {"n": 4, "start_s": 13, "end_s": 15, "visual": "누비투어 채널 이름과 로고",
         "caption": "", "prompt": "dark background end card",
         "start_svg": "<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 90 160'></svg>"},
    ]
    return VideoPlanIn.model_validate({
        "concept": "누비투어 여행 홍보", "script": "누비가 떠나는 여행", "duration_s": 15,
        "style": "flat illustration", "scenes": scenes,
    })  # fmt: skip


def test_panels_take_times_shots_angles_and_captions_from_the_plan() -> None:
    board = rc.plan_storyboard(_nubi(), SHEET)
    assert board is not None
    shots = board["shots"]
    assert [(s["start_s"], s["end_s"]) for s in shots] == [(0, 4), (4, 8), (8, 13), (13, 15)]
    # 1: 포즈 샷 medium / 2: camera_en close-up·high angle / 3: wide·low angle
    assert [s["shot_size"] for s in shots[:3]] == ["medium", "close_up", "wide"]
    assert [s["angle"] for s in shots[:3]] == ["eye_level", "high", "low"]
    captions = ["여행 준비 끝!", "어디로 갈까?", "누비투어와 함께"]
    assert [s["caption_ko"] for s in shots[:3]] == captions
    assert shots[0]["scene_ko"] == "누비가 공항 출국장에서 손을 흔든다"
    assert shots[0]["composition_ko"] == "공항 출국장 · 누비가 캐리어를 끌고 웃으며 화면을 본다"


def test_end_card_panel_has_no_character() -> None:
    shots = rc.plan_storyboard(_nubi(), SHEET)["shots"]  # type: ignore[index]
    end = shots[-1]
    assert end["has_character"] is False and end["scene_ko"] == rc.END_CARD_TEXT
    assert "same tour guide girl" not in end["prompt_en"]  # 캐릭터 문장도 넣지 않는다


def test_sheet_prompt_carries_place_shot_angle_action_expression_props() -> None:
    sheet = rc.plan_storyboard(_nubi(), SHEET)["sheet_prompt_en"]  # type: ignore[index]
    panel1 = sheet.split("Panel 1:")[1].split("Panel 2:")[0]
    for want in ("medium shot", "eye-level", "공항 출국장", "waves with a big smile",
                 "웃으며", "phone", "suitcase"):  # fmt: skip
        assert want in panel1, want
    panel2 = sheet.split("Panel 2:")[1].split("Panel 3:")[0]
    for want in ("close-up", "high-angle", "카페", "curious expression"):
        assert want in panel2, want
    assert "low-angle" in sheet.split("Panel 3:")[1]


def test_end_card_word_rule_matches_planning() -> None:
    assert rc.end_card_scene("채널 이름과 로고")
    assert rc.end_card_scene("엔드카드")
    assert not rc.end_card_scene("누비가 손을 흔들며 채널 이름을 보여 준다")  # 사람 낱말
    assert not rc.end_card_scene("해변을 달리는 장면")
    assert not rc.end_card_scene("")


def test_plain_old_plan_still_cycles_shots() -> None:
    plan = VideoPlanIn.model_validate({
        "concept": "c", "script": "s", "duration_s": 10,
        "scenes": [{"prompt": f"scene {i} prompt text", "visual": f"화면 {i}"} for i in range(3)],
    })  # fmt: skip
    shots = rc.plan_storyboard(plan, SHEET)["shots"]  # type: ignore[index]
    assert [s["shot_size"] for s in shots] == list(rc.SHOT_CYCLE[:3])
    assert all(s["angle"] == "eye_level" and s["start_s"] is None for s in shots)
