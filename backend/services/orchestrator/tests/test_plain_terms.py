"""화면·카드에 들어가는 글의 영어 코드를 한국어로 바꾸는 규칙."""

from __future__ import annotations

from ax_orchestrator.engine import _record_note
from ax_orchestrator.naming import plain_terms


def test_metric_and_component_codes_become_korean() -> None:
    text = "payment_fail_rate 이상 직후 payment 문의가 급증했다 (shop-api)"
    out = plain_terms(text)
    assert out == "결제 실패율 이상 직후 결제 문의가 급증했다 (쇼핑몰 서버)"


def test_words_inside_other_names_are_left_alone() -> None:
    assert plain_terms("paymentgateway와 my_api_key") == "paymentgateway와 my_api_key"


def test_record_note_drops_the_repeated_prefix_and_translates_the_result() -> None:
    note = "문의 #901 처리 결과 기록: answered, 경비 사용 없음"
    assert _record_note(note) == "답변 완료, 경비 사용 없음"
    assert _record_note(None) is None
