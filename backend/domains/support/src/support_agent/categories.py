"""문의 분류 코드.

분류 이름이 문의마다 달라지면(모델이 "배송 문의", "배송 지연"처럼 그때그때 지어내면)
같은 주제의 문의가 서로 다른 묶음으로 갈라진다. 문의 급증 감지, 같은 고객의 반복 문의 묶기,
총괄의 분류별 현황이 모두 분류 문자열이 같은지로 세기 때문이다.
그래서 분류는 이 코드 집합 안에서만 고르게 하고, 모델이 쓴 이름은 코드로 바꾼다.

코드는 키워드 분류·지식 조각(`kb.py`)·테스트가 이미 쓰던 값(`refund`, `shipping`,
`privacy_deletion`, `general`)에 화면 라벨(`frontend/src/lib/labels.ts`)이 쓰는 분류를 더한 것이다.
"""

from __future__ import annotations

from typing import Any, Literal

Category = Literal[
    "refund",
    "shipping",
    "payment",
    "cancel",
    "product",
    "account",
    "review",
    "privacy_deletion",
    "general",
]
CATEGORIES: tuple[str, ...] = (
    "refund",
    "shipping",
    "payment",
    "cancel",
    "product",
    "account",
    "review",
    "privacy_deletion",
    "general",
)
DEFAULT = "general"

# 분류 프롬프트에 알려 주는 코드별 뜻
PROMPT_HINT = (
    "refund(환불·반품·교환·불량·파손), shipping(배송·택배), payment(결제·청구·영수증), "
    "cancel(주문 취소), product(상품 정보·재고), account(계정·적립금), review(후기), "
    "privacy_deletion(개인정보 삭제 요청), general(그 밖의 문의)"
)

# 코드로 쓰이던 다른 이름 (정확히 같을 때)
_CODE_ALIASES = {
    "delivery": "shipping",
    "defect": "refund",
    "return": "refund",
    "exchange": "refund",
    "billing": "payment",
    "other": "general",
    "etc": "general",
}

# 모델이 자유롭게 쓴 이름에서 단어로 찾는다. 앞에 있는 코드가 먼저 맞는다.
_WORDS: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("privacy_deletion", ("개인정보", "privacy", "탈퇴")),
    (
        "refund",
        ("환불", "반품", "교환", "불량", "파손", "오배송", "refund", "return", "exchange"),
    ),
    ("cancel", ("취소", "cancel")),
    ("shipping", ("배송", "택배", "도착", "delivery", "shipping")),
    (
        "payment",
        ("결제", "청구", "증빙", "영수증", "카드", "payment", "billing", "invoice", "receipt"),
    ),
    ("account", ("계정", "적립", "포인트", "회원", "로그인", "account", "point")),
    ("product", ("상품", "재고", "제품", "product", "stock")),
    ("review", ("후기", "리뷰", "review")),
)


def normalize_category(value: Any) -> str:
    """모델이 쓴 분류 이름을 분류 코드로 바꾼다. 알 수 없으면 `general`이다."""
    if not isinstance(value, str):
        return DEFAULT
    text = value.strip().casefold()
    if text in CATEGORIES:
        return text
    if text in _CODE_ALIASES:
        return _CODE_ALIASES[text]
    for code, words in _WORDS:
        if any(word in text for word in words):
            return code
    return DEFAULT
