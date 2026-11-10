"""분류·초안 모델 호출이 바쁠 때(429·5xx) 한 번만 다시 부르고, 그 밖의 오류는 다시 부르지 않는다."""

import asyncio

import pytest

from ax_agent_template.llm import LLMResponseError, MockLLMClient
from support_agent import drafting
from support_agent.schemas import LLMClassification, LLMDrafts, StyleReply

CLASSIFIED = LLMClassification(
    intent_ko="결제 문의",
    category="payment",
    sentiment="frustrated",
    urgency="medium",
    search_query="결제 오류",
    keywords=["결제", "버튼"],
)
DRAFTS = LLMDrafts(
    replies=[
        StyleReply(style="empathetic", reply="불편을 드려 죄송합니다. 확인 후 안내드리겠습니다."),
        StyleReply(style="concise", reply="접수되었습니다. 확인 후 안내드립니다."),
        StyleReply(style="step_by_step", reply="접수, 확인, 안내 순서로 진행합니다."),
    ]
)


class Flaky(MockLLMClient):
    """앞의 몇 번은 지정한 오류를 내고, 그다음부터 정해 둔 응답을 돌려준다."""

    def __init__(self, errors: list[Exception]) -> None:
        super().__init__(responses={LLMClassification: CLASSIFIED, LLMDrafts: DRAFTS})
        self.errors = errors
        self.seen: list[str] = []

    async def structured(self, prompt, schema, untrusted=None, images=None, context_id=None):  # noqa: ANN001, ANN201
        self.seen.append(schema.__name__)
        if self.errors:
            raise self.errors.pop(0)
        return await super().structured(prompt, schema, untrusted, images, context_id)


@pytest.fixture(autouse=True)
def _fast(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_TEMPLATE_MOCK", "1")
    monkeypatch.setenv(
        drafting.ENV_SPECULATIVE, "off"
    )  # 호출 순서를 그대로 보려고 먼저 쓰기는 끈다
    monkeypatch.setattr(drafting, "RETRY_DELAY", 0)


def run(llm: Flaky, monkeypatch: pytest.MonkeyPatch, text: str = "결제 버튼이 안 눌려요"):  # noqa: ANN202
    monkeypatch.setattr(drafting, "get_llm", lambda: llm)
    return asyncio.run(drafting.analyze(text, "결제"))


def busy(code: int = 503) -> LLMResponseError:
    return LLMResponseError(f"LLM API가 {code}를 돌려줬다", status_code=code)


@pytest.mark.parametrize("code", [429, 500, 502, 503, 504])
def test_바쁜_모델은_한_번_다시_불러서_모델_분류를_쓴다(
    monkeypatch: pytest.MonkeyPatch, code: int
) -> None:
    llm = Flaky([busy(code)])
    analysis, fallback = run(llm, monkeypatch)
    assert fallback is False
    assert analysis.classification.category == "payment"  # 키워드 대체(일반 문의)로 떨어지지 않는다
    assert llm.seen == ["LLMClassification", "LLMClassification", "LLMDrafts"]


def test_초안도_바쁘면_한_번_다시_부른다(monkeypatch: pytest.MonkeyPatch) -> None:
    llm = Flaky([])
    original = llm.structured
    state = {"n": 0}

    async def second_call_busy(prompt, schema, *a, **k):  # noqa: ANN001, ANN002, ANN003, ANN202
        state["n"] += 1
        if schema is LLMDrafts and state["n"] == 2:
            llm.seen.append("LLMDrafts")
            raise busy()
        return await original(prompt, schema, *a, **k)

    monkeypatch.setattr(llm, "structured", second_call_busy)
    analysis, fallback = run(llm, monkeypatch)
    assert fallback is False and len(analysis.drafts) == 3


def test_두_번_바쁘면_대체로_떨어진다(monkeypatch: pytest.MonkeyPatch) -> None:
    llm = Flaky([busy(), busy()])
    analysis, fallback = run(llm, monkeypatch)
    assert fallback is True
    assert llm.seen[:2] == ["LLMClassification", "LLMClassification"]  # 세 번 이상 부르지 않는다


@pytest.mark.parametrize(
    "error",
    [
        LLMResponseError("LLM API가 400를 돌려줬다", status_code=400),
        LLMResponseError("LLM API 호출 실패: ReadTimeout"),  # 시간 초과: 접수가 더 늘어진다
        LLMResponseError("응답이 스키마와 맞지 않는다(ValidationError) — 2번 요청했다"),
    ],
)
def test_바쁨이_아닌_오류는_다시_부르지_않는다(
    monkeypatch: pytest.MonkeyPatch, error: LLMResponseError
) -> None:
    llm = Flaky([error])
    analysis, fallback = run(llm, monkeypatch)
    assert fallback is True
    assert llm.seen.count("LLMClassification") == 1


def test_단계별_시간이_따로_남는다(monkeypatch: pytest.MonkeyPatch) -> None:
    analysis, _ = run(Flaky([]), monkeypatch)
    t = analysis.timings_ms
    assert t is not None
    assert {"classify", "classify_llm", "retrieve", "draft"} <= set(t)
    assert t["classify_llm"] <= t["classify"] and t["retrieve"] <= t["classify"]
