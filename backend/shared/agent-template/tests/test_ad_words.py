"""광고 금지어: 흔한 단어에 걸리는 오탐 예외."""

import pytest

from ax_agent_template.ad_words import ALLOWED_WORDS, blocked_word, mark


@pytest.mark.parametrize(
    "text",
    [
        "시발점부터 출발",
        "시 발 점",
        "서울 시발역 근처",
        "신제품 출시발표",
        "동시발생 이벤트",
        "Middlesex 대학",
        "XXXL 사이즈",
        "2019금요일 특가",
        "Takeshita",
        "Take.Shi.ta",
    ],
)
def test_common_words_pass(text):
    assert blocked_word([text]) is None
    assert mark([text]) is None


@pytest.mark.parametrize(
    "text", ["시발", "시 발", "씨발", "fuckjerry", "F.U.C.K", "병신", "19금", "개시발", "병신년"]
)
def test_blocked_words_still_blocked(text):
    assert blocked_word([text]) is not None


@pytest.mark.parametrize(
    "text", ["시발점 시발", "시 발 점 그리고 시 발", "시발역 병신", "XXXL xxx"]
)
def test_allowed_word_with_blocked_word_is_blocked(text):
    assert blocked_word([text]) is not None


def test_extra_words_still_work():
    assert blocked_word(["시발점 foo"], extra=["foo"]) == "foo"


def test_allowed_words_are_not_blocked_words_themselves():
    for w in ALLOWED_WORDS:
        assert blocked_word([w]) is None
