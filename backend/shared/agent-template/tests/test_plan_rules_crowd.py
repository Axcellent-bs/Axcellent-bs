"""다른 인물이 가까이 나오는지: 군중 낱말을 좁힌다 (11/26 E, #1911 후속).

"배경"·"background"는 거의 모든 장면 글에 있어 "친구와 마주 보고 대화, 배경은 카페"도 군중으로
통과됐다. 기획 plan_checks·마케팅 plan_review가 이 공용 함수를 같이 쓴다."""

from __future__ import annotations

import pytest

from ax_agent_template import plan_rules as pr


@pytest.mark.parametrize(
    ("body", "camera"),
    [
        ("회사 캐릭터가 친구와 마주 보고 대화, 배경은 카페", "medium shot"),
        ("회사 캐릭터가 친구와 회색 배경 앞에 선다", ""),
        ("the character talks with a friend, background lights", "close-up"),
    ],
)
def test_plain_background_words_no_longer_hide_a_close_person(body: str, camera: str) -> None:
    assert pr.close_other_person(body, camera)


@pytest.mark.parametrize(
    ("body", "camera"),
    [
        ("무대 위, 배경의 관객들", ""),
        ("친구와 노래, 배경의 사람들이 손뼉 친다", ""),
        ("친구와 서 있고 뒤쪽 관객이 환호한다", ""),
        ("a friend nearby, people in the background", ""),
        ("a friend and a background crowd", ""),
        ("friends on stage, crowd in the background", ""),
        ("walking with friends", "wide shot with friends"),
    ],
)
def test_crowd_or_wide_still_passes(body: str, camera: str) -> None:
    assert not pr.close_other_person(body, camera)


def test_background_is_not_a_crowd_word() -> None:
    assert "배경" not in pr.CROWD_WORDS and "background" not in pr.CROWD_WORDS
