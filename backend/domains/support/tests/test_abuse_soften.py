"""욕설 순화 2단계: 규칙 보강, LLM 순화문 검사, 접수 흐름, 끄는 스위치, 원문 보존."""

import asyncio
import itertools
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from fastapi.testclient import TestClient

from ax_agent_template.llm import LLMResponseError
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
    assert created["preview"] == "주문 ORD-1 12,000원 환불을 요청합니다."  # 사과는 뺀다


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


def test_feedback_list_shows_the_masked_subject(monkeypatch: pytest.MonkeyPatch) -> None:
    """후기 관리 한 줄에도 욕설이 든 제목 원문이 나가지 않는다."""
    from support_agent import feedback_handlers

    created = create(TestClient(app), "환불해 주세요", subject="씨발 환불")
    conversation = handlers.STORE.get(created["id"])
    assert conversation is not None
    assert feedback_handlers._item(conversation)["subject"] == "[비속어] 환불"
    polite = create(TestClient(app), "환불해 주세요", subject="환불 문의")
    polite_conversation = handlers.STORE.get(polite["id"])
    assert polite_conversation is not None
    assert feedback_handlers._item(polite_conversation)["subject"] == "환불 문의"


# ---- 띄어 쓴 욕설 의심 ----------------------------------------------------------------
@pytest.mark.parametrize("text", ["씨 발 환불", "병   신 같은", "지\n랄 하네", "ㅅ ㅂ 진짜"])
def test_a_spaced_curse_is_only_suspected_not_masked(text: str) -> None:
    assert abuse_rules.maybe_evasive(text)
    assert abuse_rules.assess(text)[1] == "none"  # 규칙은 가리지 않는다 (오탐 방지)


@pytest.mark.parametrize("text", ["김씨 발송해 주세요", "환불 부탁드립니다", "씨앗 발아가 안 돼요"])
def test_ordinary_text_is_not_suspected(text: str) -> None:
    # "김씨 발송"은 의심하지만 모델이 걸러낸다: 아래 접수 흐름 테스트 참고
    if "김씨" in text:
        assert abuse_rules.maybe_evasive(text)
    else:
        assert not abuse_rules.maybe_evasive(text)


def test_a_suspected_spaced_curse_is_softened_by_the_model(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = use_llm(
        monkeypatch,
        SoftenResult(
            abuse_level="profanity", softened_text="주문 ORD-1 12,000원 환불을 요청합니다."
        ),
    )
    created = create(TestClient(app), "씨 발 ORD-1 12,000원 환불해 주세요")  # 화난 말투가 아니어도
    message = created["messages"][0]
    assert fake.seen and message["abuse_level"] == "profanity"
    assert "llm" in message["abuse_kinds"]
    assert message["softened_text"].endswith("환불을 요청합니다.")


def test_a_name_that_looks_like_a_spaced_curse_is_left_alone(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """모델이 욕설이 아니라고 하면("김씨 발송") 아무것도 가리지 않는다."""
    fake = use_llm(monkeypatch, SoftenResult(abuse_level="none", softened_text=""))
    created = create(TestClient(app), "김씨 발송 일정을 알려 주세요")
    message = created["messages"][0]
    assert fake.seen  # 의심돼서 모델은 봤다
    assert (message["abuse_level"], message["softened_text"]) == ("none", None)
    assert "abusive_message" not in [a["kind"] for a in created["activity"]]


# ---- 서버가 바쁠 때 한 번 다시 부르기 -----------------------------------------------------
class FlakyLLM:
    """앞의 몇 번은 지정한 오류로 실패하고 그다음부터 결과를 돌려준다."""

    def __init__(self, failures: list[Exception], result: SoftenResult) -> None:
        self.failures = failures
        self.result = result
        self.calls = 0

    async def structured(self, prompt: str, model: Any, untrusted: dict[str, Any]) -> Any:
        self.calls += 1
        if self.failures:
            raise self.failures.pop(0)
        return self.result


def _flaky(monkeypatch: pytest.MonkeyPatch, failures: list[Exception]) -> FlakyLLM:
    fake = FlakyLLM(
        failures,
        SoftenResult(
            abuse_level="profanity", softened_text="주문 ORD-1 12,000원 환불을 요청합니다."
        ),
    )
    monkeypatch.setattr(abuse_soften, "_llm", lambda: fake)
    monkeypatch.setattr(abuse_soften, "RETRY_DELAY", 0)
    return fake


ORIGINAL_TEXT = "씨발 ORD-1 12,000원 환불해"


def test_a_busy_model_is_called_once_more(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _flaky(monkeypatch, [LLMResponseError("LLM API가 503를 돌려줬다")])
    result = asyncio.run(abuse_soften.soften(ORIGINAL_TEXT, "profanity"))
    assert fake.calls == 2 and result is not None
    assert result[0].endswith("환불을 요청합니다.")


def test_a_rate_limit_is_also_retried(monkeypatch: pytest.MonkeyPatch) -> None:
    fake = _flaky(monkeypatch, [LLMResponseError("LLM API가 429를 돌려줬다")])
    assert asyncio.run(abuse_soften.soften(ORIGINAL_TEXT, "profanity")) is not None
    assert fake.calls == 2


def test_it_gives_up_after_one_retry(monkeypatch: pytest.MonkeyPatch) -> None:
    busy = LLMResponseError("LLM API가 503를 돌려줬다")
    fake = _flaky(monkeypatch, [busy, busy, busy])
    assert asyncio.run(abuse_soften.soften(ORIGINAL_TEXT, "profanity")) is None
    assert fake.calls == 2  # 두 번까지만 부른다


@pytest.mark.parametrize(
    "error",
    [
        LLMResponseError("LLM API 호출 실패: ReadTimeout"),
        LLMResponseError("LLM API가 400를 돌려줬다"),
    ],
)
def test_timeouts_and_client_errors_are_not_retried(
    monkeypatch: pytest.MonkeyPatch, error: Exception
) -> None:
    fake = _flaky(monkeypatch, [error])
    assert asyncio.run(abuse_soften.soften(ORIGINAL_TEXT, "profanity")) is None
    assert fake.calls == 1  # 시간 초과는 접수를 더 늘리고, 4xx는 다시 불러도 같다
