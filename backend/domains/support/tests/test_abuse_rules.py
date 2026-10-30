"""고객 메시지의 욕설·모욕·위협 표시: 가린 글, 단계, 원문 보존, 오탐 방지."""

import pytest

from support_agent import abuse_rules
from support_agent.schemas import Analysis
from support_agent.store import InquiryStore


@pytest.mark.parametrize(
    ("text", "level", "kinds"),
    [
        ("배송이 늦어요", "none", []),
        ("씨발 배송이 왜 이래요", "profanity", ["profanity"]),
        ("너네 쓰레기 같은 회사야, 꺼져", "insult", ["insult"]),
        ("씨발 환불 안 해주면 찾아가서 가만 안 둔다", "threat", ["threat", "profanity"]),
    ],
)
def test_level_and_kinds(text: str, level: str, kinds: list[str]) -> None:
    _, got_level, got_kinds = abuse_rules.assess(text)
    assert got_level == level
    assert got_kinds == kinds


def test_expressions_are_replaced_but_request_stays() -> None:
    shown, _, _ = abuse_rules.assess("씨발 환불해 주세요. 주문번호 ord_0002")
    assert "씨발" not in shown
    assert shown == "[비속어] 환불해 주세요. 주문번호 ord_0002"


@pytest.mark.parametrize(
    "text",
    [
        "시발점이 어디인가요",
        "쓰레기통 모양 상품이 깨져서 왔어요",
        "소비자원에 신고하고 고소하겠습니다",  # 정당한 권리 행사는 위협이 아니다
    ],
)
def test_ordinary_words_and_legitimate_threats_are_not_flagged(text: str) -> None:
    shown, level, kinds = abuse_rules.assess(text)
    assert (shown, level, kinds) == (text, "none", [])


def test_store_keeps_original_and_adds_display_text() -> None:
    store = InquiryStore()
    conversation = store.create(
        context_id="c",
        tenant_id="t",
        user_id=1,
        customer_name="고객",
        subject="환불",
        order_id=None,
        order_no=None,
        first_message="씨발 환불해 주세요",
        analysis=Analysis(),
    )
    message = conversation.messages[0]
    assert message.text == "씨발 환불해 주세요"
    assert message.display_text == "[비속어] 환불해 주세요"
    assert message.abuse_level == "profanity"
    assert conversation.preview == "[비속어] 환불해 주세요"

    store.add_customer_message(conversation.id, "정상 문의입니다", Analysis())
    later = conversation.messages[1]
    assert (later.display_text, later.abuse_level) == (None, "none")
