"""explain 숫자 검증: facts 값에서 나온 다른 표기는 통과, 지어낸 숫자는 탈락."""

from __future__ import annotations

import pytest

from data_agent.explain import _bad_numbers, _numbers_valid

FACTS: dict[str, int | float | str] = {
    "metric": "payment_fail_rate",
    "baseline": 0.42,
    "observed": 2.747,
    "ratio": 0.0123,
    "damage_amount": 1234000,
    "failed_order_count": 12,
    "affected_customer_count": 7,
}


@pytest.mark.parametrize(
    "summary",
    [
        "기준선 42%",  # 비율 → 퍼센트
        "기준선 42.0%",
        "비율 1.23%, 1.2%",  # 퍼센트 + 반올림
        "관측 2.75",  # 반올림 (자릿수 줄임)
        "관측 2.7",
        "관측 3",
        "관측 2.747",  # 그대로
        "기준선 0.4",  # 0.42 반올림
        "피해 금액 1,234,000원",  # 천 단위 쉼표 + 단위
        "피해 금액 1234000원",
        "실패 주문 12건, 영향 고객 7명",  # 단위
    ],
)
def test_notation_from_facts_passes(summary: str) -> None:
    assert _numbers_valid(summary, FACTS), _bad_numbers(summary, FACTS)


def test_new_number_fails() -> None:
    assert _bad_numbers("추정 손실 9,876,543원", FACTS) == [9876543.0]
    assert _bad_numbers("오류율 47%", FACTS) == [47.0]


@pytest.mark.parametrize(
    ("summary", "bad"),
    [
        ("합계 19건", 19.0),  # 12 + 7
        ("차이 5명", 5.0),  # 12 − 7
        ("고객 비율 58.3%", 58.3),  # 7 / 12
        ("관측/기준 6.54배", 6.54),  # 2.747 / 0.42
    ],
)
def test_value_computed_from_facts_fails(summary: str, bad: float) -> None:
    assert _bad_numbers(summary, FACTS) == [bad]


@pytest.mark.parametrize(
    ("summary", "bad"),
    [
        ("관측 2.8", 2.8),  # 2.747의 소수 첫째 자리 반올림은 2.7
        ("관측 2.74", 2.74),  # 버림은 반올림이 아니다
        ("비율 0", 0.0),  # 0이 아닌 값을 반올림해 0이 된 것은 같은 값으로 보지 않는다
        ("기준선 43%", 43.0),
    ],
)
def test_rounding_must_match_original(summary: str, bad: float) -> None:
    assert _bad_numbers(summary, FACTS) == [bad]


def test_unit_and_name_numbers_need_window_or_code_in_facts() -> None:
    # facts에 구간 길이·코드 목록이 없으면 "5분", "5xx"는 다른 facts 값과 같아도 탈락
    facts = {**FACTS, "failed_order_count": 5}
    assert _bad_numbers("5분 동안 5xx 응답", facts) == [5.0]
    # 있으면 통과
    with_window = {**facts, "window_minutes": 5, "codes": "5xx, 503"}
    assert _numbers_valid("5분 동안 5xx 응답", with_window)
    # 구간 길이는 반올림·퍼센트를 허용하지 않는다
    assert _bad_numbers("5.0분", {**FACTS, "window_minutes": 4.96}) == [5.0]


def test_known_limit_rounded_integer_used_with_another_meaning_passes() -> None:
    # 알려진 한계: 숫자 옆 낱말의 뜻은 보지 않는다. facts 2.747의 반올림 3이 다른 뜻("3개 부서")으로
    # 쓰여도 통과한다. 규칙은 그대로 두고 이 동작을 고정해 둔다(바꾸면 이 테스트가 알려 준다).
    assert _numbers_valid("관측값이 3개 부서에서 올랐습니다", {"observed": 2.747})
