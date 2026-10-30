"""욕설 순화 2단계: 규칙 보강, LLM 순화문 검사, 접수 흐름, 끄는 스위치, 원문 보존."""

import asyncio
import itertools
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

from support_agent import abuse_rules, abuse_soften, claim_handlers, handlers
from support_agent.abuse_soften import SoftenResult
from support_agent.claims import ClaimStore
from support_agent.main import app
from support_agent.store import InquiryStore

_KEYS = itertools.count()


@pytest.fixture(autouse=True)
def _fresh(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_TEMPLATE_MOCK", "1")
    monkeypatch.setattr(handlers, "STORE", InquiryStore())
    monkeypatch.setattr(claim_handlers, "CLAIMS", ClaimStore())


class FakeLLM:
    def __init__(self, result: SoftenResult | Exception) -> None:
        self.result = result
        self.seen: list[dict[str, Any]] = []

    async def structured(self, prompt: str, model: Any, untrusted: dict[str, Any]) -> Any:
        self.seen.append(untrusted)
        if isinstance(self.result, Exception):
            raise self.result
        return self.result


def use_llm(monkeypatch: pytest.MonkeyPatch, result: SoftenResult | Exception) -> FakeLLM:
    fake = FakeLLM(result)
    monkeypatch.setenv("SUPPORT_ABUSE_SOFTEN", "on")
    monkeypatch.setenv("LLM_MODEL", "m")
    monkeypatch.setenv("LLM_BASE_URL", "http://llm.test")
    monkeypatch.setattr(abuse_soften, "_llm", lambda: fake)
    return fake


def call(client: TestClient, skill: str, input: dict[str, Any], untrusted: dict[str, Any]) -> Any:
    body = {
        "meta": {
            "context_id": "ctx_inq_0001",
            "token": "mock:dashboard-api",
            "path": [],
            "deadline": (datetime.now(UTC) + timedelta(seconds=30)).isoformat(),
            "idempotency_key": str(next(_KEYS)),
        },
        "skill": skill,
        "input": input,
        "untrusted": untrusted,
    }
    return client.post("/a2a", json=body).json()["output"]


def create(client: TestClient, message: str, subject: str = "환불") -> Any:
    return call(
        client,
        "create_inquiry",
        {"user_id": 1, "customer_name": "김하늘", "order_id": "101", "order_no": "ORD-1"},
        {"subject": subject, "message": message},
    )


# ---- 규칙 보강 -------------------------------------------------------------------------
@pytest.mark.parametrize("text", ["시1발 뭐야", "씨.발 환불", "ㅅ-ㅂ 진짜"])
def test_symbol_separated_profanity_is_caught(text: str) -> None:
    shown, level, kinds = abuse_rules.assess(text)
    assert (level, kinds) == ("profanity", ["profanity"])
    assert "[비속어]" in shown


@pytest.mark.parametrize("text", ["김씨 발송 부탁해요", "이번 출시 발표 언제죠", "3시 30분 발송"])
def test_ordinary_text_is_not_caught_by_the_evasion_rule(text: str) -> None:
    assert abuse_rules.assess(text)[1] == "none"


# ---- 순화문 검사 -----------------------------------------------------------------------
ORIGINAL = "씨발 주문 ORD-1 12,000원 환불해 주세요. 전화는 [전화번호]"


def test_digits_inside_a_disguised_curse_are_not_counted_as_facts() -> None:
    assert abuse_soften.valid(
        "배송이 늦어요 시1발 언제 오나요", "배송이 늦습니다. 언제 오는지 알려 주세요."
    )


def test_a_good_softened_text_passes() -> None:
    assert abuse_soften.valid(ORIGINAL, "주문 ORD-1 12,000원 환불을 요청합니다. 전화는 [전화번호]")


@pytest.mark.parametrize(
    "softened",
    [
        "",
        "주문 ORD-1 환불을 요청합니다. 전화는 [전화번호]",  # 금액이 사라짐
        "주문 ORD-1 12,000원 환불을 요청합니다.",  # 자리표시가 사라짐
        "씨발 주문 ORD-1 12,000원 환불. 전화는 [전화번호]",  # 욕설이 남음
        "주문 ORD-1 12,000원 환불. 전화는 [전화번호] " + "가" * 200,  # 너무 길어짐
    ],
)
def test_a_bad_softened_text_is_rejected(softened: str) -> None:
    assert not abuse_soften.valid(ORIGINAL, softened)


# ---- 접수 흐름 -------------------------------------------------------------------------
def test_a_flagged_message_gets_a_softened_text(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = use_llm(
        monkeypatch,
        SoftenResult(abuse_level="insult", softened_text="주문 ORD-1 12,000원 환불을 요청합니다."),
    )
    created = create(TestClient(app), "씨발 쓰레기 같은 회사야 ORD-1 12,000원 환불해")
    message = created["messages"][0]
    assert message["text"].startswith("씨발")  # 원문 그대로
    # 사과는 코드가 맨 앞에 한 문장만 붙이고, 뒤에는 모델이 옮긴 요청만 온다
    expected = "불편을 드려 죄송합니다. 주문 ORD-1 12,000원 환불을 요청합니다."
    assert message["softened_text"] == expected
    assert message["abuse_level"] == "insult"  # 규칙(비속어)과 모델(모욕) 중 높은 쪽
    assert fake.seen == [
        {"message": "씨발 쓰레기 같은 회사야 ORD-1 12,000원 환불해"}
    ]  # 개인정보만 가린 글
    entry = next(a for a in created["activity"] if a["kind"] == "abusive_message")
    assert entry["data"]["softened"] is True
    assert created["preview"] == expected


def test_a_bad_llm_answer_falls_back_to_the_rules(monkeypatch: pytest.MonkeyPatch) -> None:
    use_llm(monkeypatch, SoftenResult(abuse_level="profanity", softened_text="환불"))  # 금액 없음
    created = create(TestClient(app), "씨발 ORD-1 12,000원 환불해")
    message = created["messages"][0]
    assert message["softened_text"] is None
    assert message["display_text"].startswith("[비속어]")


def test_an_llm_failure_does_not_break_the_inquiry(monkeypatch: pytest.MonkeyPatch) -> None:
    use_llm(monkeypatch, RuntimeError("boom"))
    created = create(TestClient(app), "씨발 환불해 주세요")
    assert created["messages"][0]["abuse_level"] == "profanity"
    assert created["messages"][0]["softened_text"] is None


def test_a_polite_calm_message_never_reaches_the_llm(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = use_llm(monkeypatch, SoftenResult(abuse_level="none", softened_text=""))
    create(TestClient(app), "표지가 찢어져 왔어요. 환불해 주세요.")
    assert fake.seen == []


def test_the_switch_off_keeps_the_message_local(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = use_llm(monkeypatch, SoftenResult(abuse_level="insult", softened_text="환불 요청"))
    monkeypatch.setenv("SUPPORT_ABUSE_SOFTEN", "off")
    created = create(TestClient(app), "씨발 환불해 주세요")
    assert fake.seen == []
    assert created["messages"][0]["softened_text"] is None


def test_mock_mode_without_the_switch_is_off(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SUPPORT_ABUSE_SOFTEN", raising=False)
    assert abuse_soften.enabled() is False


# ---- 제목 ------------------------------------------------------------------------------
def test_an_abusive_subject_is_masked_for_the_list() -> None:
    created = create(TestClient(app), "환불해 주세요", subject="씨발 이게 뭐냐")
    assert created["subject"] == "씨발 이게 뭐냐"
    assert created["subject_display"] == "[비속어] 이게 뭐냐"
    clean = create(TestClient(app), "환불해 주세요", subject="환불")
    assert clean["subject_display"] is None


def test_softening_starts_before_the_draft_analysis_finishes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """규칙이 찾은 글은 초안 분석을 기다리지 않고 순화를 먼저 시작한다 (접수 시간 단축)."""
    order: list[str] = []
    fake = use_llm(
        monkeypatch,
        SoftenResult(abuse_level="insult", softened_text="주문 ORD-1 12,000원 환불을 요청합니다."),
    )
    original = fake.structured

    async def structured(prompt: str, model: Any, untrusted: dict[str, Any]) -> Any:
        order.append("soften")
        return await original(prompt, model, untrusted)

    fake.structured = structured  # type: ignore[method-assign]
    real_analyze = handlers.drafting.analyze

    async def analyze(*args: Any, **kwargs: Any) -> Any:
        order.append("analyze-start")
        await asyncio.sleep(0)  # 순화 작업이 돌 틈을 준다
        await asyncio.sleep(0)
        order.append("analyze-end")
        return await real_analyze(*args, **kwargs)

    monkeypatch.setattr(handlers.drafting, "analyze", analyze)
    created = create(TestClient(app), "씨발 쓰레기 같은 회사야 ORD-1 12,000원 환불해")
    assert order.index("soften") < order.index("analyze-end")
    assert created["messages"][0]["softened_text"].endswith("환불을 요청합니다.")
