"""혼자인 장면을 인물 3명으로 세지 않는다 (11/26 H).

운영 wr_eabc06b19e(누비투어, 캐릭터 없음): 장면 4 "주인공이 스마트폰을 든 채 카메라를 보며 손을
흔들고 마무리"가 2차까지 "인물 3명 (허용 2명)"이었다. 낱말 "무리"(여럿)가 "마무리" 안에서 걸렸다."""

from __future__ import annotations

import pytest

from planning_agent import plan_checks as pc

pytestmark = pytest.mark.role("planning")
SCENE4 = "주인공이 스마트폰을 든 채 카메라를 보며 손을 흔들고 마무리"


def _scene(visual: str, people: int = 1) -> dict[str, object]:
    return {"visual": visual, "prompt": "A traveler", "action_en": "she waves", "people": people}


@pytest.mark.parametrize(
    ("text", "n"),
    [
        (SCENE4, 0),  # 글에 인원 낱말 없음 → LLM 칸(1명)
        ("주인공이 친구에게 전화한다", 0),  # 말 상대는 세지 않는다
        ("끝에 누비투어 채널 이름이 나온다", 0),
        ("회사 캐릭터가 친구 두 명과 셋이서 무대에 선다", 3),
        ("무리 지어 걷는 여행자들", 3),  # 낱말 처음의 "무리"는 그대로 센다
        ("관객들 앞에서", 3),
    ],
)
def test_people_words_count_only_at_word_start(text: str, n: int) -> None:
    assert pc.people_in_text(text) == n


def test_scene4_is_one_person_and_passes() -> None:
    assert pc.feasibility([_scene(SCENE4)]) == []
    assert pc.feasibility([_scene("주인공이 친구에게 전화한다")]) == []
    got = pc.feasibility([_scene("회사 캐릭터가 친구 두 명과 셋이서 무대에 선다")])
    assert [(x["kind"], x["detail_ko"]) for x in got] == [("people", "인물 3명 (허용 2명)")]


def test_text_saying_alone_wins_over_the_llm_field() -> None:
    assert pc.feasibility([_scene("주인공 혼자 공항 벤치에서 졸다 깬다", people=3)]) == []
    assert pc.scene_people(3, "주인공이 홀로 걷는다") == 1
    assert pc.scene_people(3, "무대 위 장면") == 3  # 글이 말하지 않으면 LLM 칸 그대로
    assert pc.scene_people(1, "혼자 남은 주인공 옆으로 친구들이 몰려온다") == 3  # 여럿이면 아님


def test_company_character_rule_is_unchanged() -> None:
    """#1911 회사 캐릭터 기준(close_person)은 글이 "혼자"여도 LLM 칸을 그대로 본다."""
    got = pc.feasibility([_scene("주인공 혼자 서 있다", people=2)], has_character=True)
    assert [x.get("code") for x in got] == ["close_person"]
