"""언어별 문의 관리: 문의마다 고객 언어를 저장하고, 목록을 언어별로 나눠 본다."""

from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

from support_agent import claim_handlers, handlers, insights
from support_agent.claims import ClaimStore
from support_agent.main import app
from support_agent.schemas import Analysis
from support_agent.store import InquiryStore

MESSAGES = {
    "ko": "책 표지가 찢어져서 왔어요. 환불해 주세요",
    "en": "My book arrived damaged. Can I get a refund?",
    "ja": "本が破れて届きました。返金できますか",
    "zh": "书到的时候是破的，可以退款吗",
}


@pytest.fixture(autouse=True)
def _fresh(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_TEMPLATE_MOCK", "1")
    monkeypatch.setattr(handlers, "STORE", InquiryStore())
    monkeypatch.setattr(claim_handlers, "CLAIMS", ClaimStore())


def _make(store: InquiryStore, message: str, subject: str = "문의") -> int:
    return store.create(
        context_id="c",
        tenant_id="t",
        user_id=1,
        customer_name="고객",
        subject=subject,
        order_id=None,
        order_no=None,
        first_message=message,
        analysis=Analysis(),
    ).id


def test_each_inquiry_keeps_the_language_of_the_customer_message() -> None:
    store = InquiryStore()
    ids = {lang: _make(store, text) for lang, text in MESSAGES.items()}
    assert {lang: store.get(i).language for lang, i in ids.items()} == {  # type: ignore[union-attr]
        lang: lang for lang in MESSAGES
    }


def test_list_can_be_filtered_by_language() -> None:
    store = InquiryStore()
    for text in MESSAGES.values():
        _make(store, text)
    _make(store, "Where is my order?")
    assert [i.language for i in store.list(language="en")] == ["en", "en"]
    assert [i.language for i in store.list(language="zh")] == ["zh"]
    assert len(store.list()) == 5
    assert store.list(language="other") == []


def test_the_language_follows_the_last_customer_message() -> None:
    store = InquiryStore()
    inquiry_id = _make(store, MESSAGES["en"])
    store.add_agent_message(inquiry_id, "We are checking your order.")  # 상담원 답은 언어 그대로
    assert store.get(inquiry_id).language == "en"  # type: ignore[union-attr]
    store.add_customer_message(inquiry_id, MESSAGES["ja"], Analysis())
    assert store.get(inquiry_id).language == "ja"  # type: ignore[union-attr]


def test_waiting_list_and_skill_carry_the_language() -> None:
    store = InquiryStore()
    _make(store, MESSAGES["ja"])
    waiting = insights.overview(store, datetime.now(UTC) + timedelta(minutes=5)).waiting
    assert [w.language for w in waiting] == ["ja"]


def test_list_inquiries_skill_filters_by_language(monkeypatch: pytest.MonkeyPatch) -> None:
    for text in MESSAGES.values():
        _make(handlers.STORE, text)
    client = TestClient(app)

    def call(input: dict[str, Any]) -> list[dict[str, Any]]:
        body = {
            "meta": {
                "context_id": "ctx_inq_0001",
                "token": "mock:dashboard-api",
                "path": [],
                "deadline": (datetime.now(UTC) + timedelta(seconds=30)).isoformat(),
                "idempotency_key": f"k-{len(str(input))}",
            },
            "skill": "list_inquiries",
            "input": input,
            "untrusted": {},
        }
        return client.post("/a2a", json=body).json()["output"]["items"]

    assert [i["language"] for i in call({"language": "zh"})] == ["zh"]
    assert len(call({})) == 4
