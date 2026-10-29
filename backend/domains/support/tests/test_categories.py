"""문의 분류는 코드 집합 안에서만 정해진다.

모델이 지어낸 이름이 같은 주제를 여러 묶음으로 가르지 않는다.
"""

import pytest

from support_agent import categories, drafting, kb
from support_agent.categories import CATEGORIES, PROMPT_HINT, normalize_category
from support_agent.schemas import LLMClassification


def classified(category: object) -> LLMClassification:
    return LLMClassification.model_validate(
        {
            "intent_ko": "문의",
            "category": category,
            "sentiment": "calm",
            "urgency": "low",
            "search_query": "q",
        }
    )


@pytest.mark.parametrize(
    ("written", "code"),
    [
        # 시험에서 모델이 실제로 지어낸 이름들
        ("배송 문의", "shipping"),
        ("결제 및 증빙", "payment"),
        ("결제/청구", "payment"),
        ("환불/반품", "refund"),
        ("환불 및 반품", "refund"),
        # 코드와 그 다른 이름
        ("refund", "refund"),
        ("Shipping", "shipping"),
        ("delivery", "shipping"),
        ("defect", "refund"),
        ("other", "general"),
        # 그 밖의 분야
        ("불량·파손·오배송", "refund"),
        ("주문 취소", "cancel"),
        ("적립금·계정", "account"),
        ("상품 문의", "product"),
        ("후기", "review"),
        ("개인정보 삭제 요청", "privacy_deletion"),
    ],
)
def test_names_the_model_wrote_become_codes(written: str, code: str) -> None:
    assert normalize_category(written) == code
    assert classified(written).category == code


@pytest.mark.parametrize("unknown", ["알 수 없는 이야기", "", "   ", None, 3, ["refund"]])
def test_unknown_values_fall_back_to_general_instead_of_failing(unknown: object) -> None:
    assert normalize_category(unknown) == categories.DEFAULT == "general"
    assert classified(unknown).category == "general"


def test_the_same_topic_written_two_ways_lands_in_one_group() -> None:
    """급증 감지·반복 문의 묶기는 분류 문자열이 같은지로 센다. 이름이 달라도 한 묶음이어야 한다."""
    assert classified("결제 및 증빙").category == classified("결제/청구").category == "payment"
    assert classified("배송 문의").category == classified("delivery").category == "shipping"


def test_the_response_schema_lists_only_the_codes() -> None:
    """엄격 스키마(strict)로 보낼 때 모델이 코드만 고르도록 enum으로 나간다."""
    prop = LLMClassification.model_json_schema()["properties"]["category"]
    assert set(prop["enum"]) == set(CATEGORIES)


def test_the_prompt_names_every_code() -> None:
    for code in CATEGORIES:
        assert code in PROMPT_HINT


def test_keyword_fallback_and_knowledge_base_use_known_codes() -> None:
    assert set(drafting._CATEGORY_KEYWORDS) <= set(CATEGORIES)
    assert {chunk["category"] for chunk in kb.KB_CHUNKS} <= set(CATEGORIES)
    for text in ("환불해 주세요", "배송이 언제 오나요", "그냥 인사드립니다", "개인정보 삭제 요청"):
        assert drafting.keyword_classification(text).category in CATEGORIES
