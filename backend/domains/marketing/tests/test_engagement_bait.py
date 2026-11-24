"""참여 조건부 게시 판정(has_engagement_bait).

11/24 PROP-22 콘셉트 "공유할 밈"이 영상 초안 준비에서 forbidden_phrase로 막힌 문제:
참여 단어만 있으면 막던 규칙을 "단어 뒤 12자 안에 요구·조건 표현"으로 좁힌다.
"""

import pytest

from marketing_agent import sns

PROP_22_CONCEPT = "이십대가 웃으며 공유할 책 읽는 일상 밈"


@pytest.mark.parametrize(
    "text",
    ["공유할 책 일상 밈", "좋아요가 많은 릴스 형식", "댓글 반응이 좋았던 주제", PROP_22_CONCEPT],
)
def test_description_sentences_pass(text: str) -> None:
    assert not sns.has_engagement_bait(text)
    assert sns.engagement_bait_count(text) == 0


@pytest.mark.parametrize(
    "text",
    [
        "좋아요 누르면 추첨",
        "공유하고 댓글 남기면 증정",
        "팔로우 부탁해요",
        "댓글로 알려 주세요",  # 혜택 없는 요청도 지금 규칙처럼 막는다
        "리그램 해 주시면 선물",
        "친구 태그하고 응모",
        "팔 로 우 하 면 쿠폰",  # 띄어쓰기로 피하지 못한다
    ],
)
def test_participation_conditions_are_blocked(text: str) -> None:
    assert sns.has_engagement_bait(text)


def test_demand_beyond_window_is_not_counted() -> None:
    # 참여 단어 뒤 12자를 넘어서 나온 요구 표현은 그 단어의 조건으로 보지 않는다
    assert not sns.has_engagement_bait("공유할 책 읽는 일상을 담은 짧은 영상, 선물")
    assert sns.engagement_bait_count("좋아요 누르고 공유하면 추첨") == 2


def test_hashtag_check_keeps_word_rule() -> None:
    assert sns.tag_check("#공유책") == "forbidden_phrase"
    assert sns.FORBIDDEN_PHRASES == ("좋아요", "팔로우", "공유", "댓글")
