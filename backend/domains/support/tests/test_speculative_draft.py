"""분류를 기다리는 동안 한국어 문의의 초안을 먼저 쓴다. 근거가 같을 때만 쓰고, 다르면 다시 쓴다."""

import asyncio

import pytest

from ax_agent_template.llm import LLMResponseError, MockLLMClient
from support_agent import drafting
from support_agent.schemas import LLMClassification, LLMDrafts, Source, StyleReply

CLASSIFIED = LLMClassification(
    intent_ko="결제 문의",
    category="payment",
    sentiment="frustrated",
    urgency="medium",
    search_query="결제 오류 안내",
    keywords=["결제"],
)


def reply(text: str) -> LLMDrafts:
    return LLMDrafts(
        replies=[
            StyleReply(style="empathetic", reply=f"불편을 드려 죄송합니다. {text}"),
            StyleReply(style="concise", reply=f"접수되었습니다. {text}"),
            StyleReply(style="step_by_step", reply=f"접수, 확인, 안내 순서입니다. {text}"),
        ]
    )


def source(sid: str) -> Source:
    return Source(
        id=sid,
        doc_id=sid,
        category="payment",
        title=f"문서 {sid}",
        section="1",
        text="본문",
        score=0.9,
    )


class Recorder(MockLLMClient):
    """분류는 `classify_delay` 뒤에, 초안은 근거 목록마다 다른 문장으로 돌려준다."""

    def __init__(self, classify_delay: float = 0.05, draft_error: Exception | None = None) -> None:
        super().__init__(responses={LLMClassification: CLASSIFIED, LLMDrafts: reply("기본")})
        self.classify_delay = classify_delay
        self.draft_error = draft_error
        self.draft_prompts: list[str] = []

    async def structured(self, prompt, schema, untrusted=None, images=None, context_id=None):  # noqa: ANN001, ANN201
        if schema is LLMClassification:
            await asyncio.sleep(self.classify_delay)
            return CLASSIFIED
        self.draft_prompts.append(prompt)
        if self.draft_error is not None:
            raise self.draft_error
        await asyncio.sleep(0.01)
        ids = [
            line.split("]")[0].lstrip("[") for line in prompt.splitlines() if line.startswith("[")
        ]
        return reply("근거 " + ",".join(ids))


class Retriever:
    """검색어마다 근거를 돌려주고 불린 검색어를 남긴다."""

    def __init__(self, by_query: dict[str, list[str]], default: list[str]) -> None:
        self.by_query = by_query
        self.default = default
        self.queries: list[str] = []

    async def __call__(self, query: str) -> list[Source]:
        self.queries.append(query)
        return [source(i) for i in self.by_query.get(query, self.default)]


@pytest.fixture(autouse=True)
def _env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("AGENT_TEMPLATE_MOCK", "1")
    monkeypatch.delenv(drafting.ENV_SPECULATIVE, raising=False)
    monkeypatch.setattr(drafting, "RETRY_DELAY", 0)


def run(llm: Recorder, retrieve: Retriever, text: str, monkeypatch: pytest.MonkeyPatch):  # noqa: ANN202
    monkeypatch.setattr(drafting, "get_llm", lambda: llm)
    return asyncio.run(drafting.analyze(text, "결제", retrieve=retrieve))


def test_근거가_같으면_먼저_쓴_초안을_쓰고_초안_모델은_한_번만_부른다(monkeypatch) -> None:
    llm, retr = Recorder(), Retriever({}, ["pol_1", "pol_2"])
    analysis, fallback = run(llm, retr, "결제 버튼이 안 눌려요", monkeypatch)
    assert fallback is False
    assert len(llm.draft_prompts) == 1  # 분류 뒤에 다시 쓰지 않았다
    assert analysis.timings_ms["draft_reused"] == 1
    assert [s.id for s in analysis.sources] == ["pol_1", "pol_2"]
    assert len(analysis.drafts) == 3 and "pol_1,pol_2" in analysis.drafts[0].reply
    assert "결제 버튼이 안 눌려요" in retr.queries[0]  # 먼저 찾을 때는 고객 글로 찾는다
    assert CLASSIFIED.search_query in retr.queries  # 분류 뒤에는 진짜 검색어로 찾는다


def test_근거가_다르면_먼저_쓴_초안을_버리고_진짜_근거로_다시_쓴다(monkeypatch) -> None:
    llm = Recorder()
    retr = Retriever({CLASSIFIED.search_query: ["pol_9"]}, ["pol_1", "pol_2"])
    analysis, _ = run(llm, retr, "결제 버튼이 안 눌려요", monkeypatch)
    assert analysis.timings_ms["draft_reused"] == 0
    assert len(llm.draft_prompts) == 2  # 먼저 쓴 것 + 다시 쓴 것 (질은 예전과 같다)
    assert [s.id for s in analysis.sources] == ["pol_9"]
    assert "근거 pol_9" in analysis.drafts[0].reply and "pol_1" not in analysis.drafts[0].reply


def test_한국어가_아니면_먼저_쓰지_않는다(monkeypatch) -> None:
    llm, retr = Recorder(), Retriever({}, ["pol_1"])
    analysis, _ = run(llm, retr, "My payment button does not work at all", monkeypatch)
    assert analysis.timings_ms["draft_reused"] == 0
    assert len(llm.draft_prompts) == 1
    assert all("payment button" not in q for q in retr.queries)  # 고객 글로 먼저 찾지 않았다


def test_먼저_쓰기가_실패하면_지금처럼_다시_쓴다(monkeypatch) -> None:
    llm = Recorder(draft_error=LLMResponseError("LLM API 호출 실패: ReadTimeout"))
    analysis, fallback = run(llm, Retriever({}, ["pol_1"]), "결제 버튼이 안 눌려요", monkeypatch)
    assert fallback is True  # 두 번 다 실패했으니 고정 템플릿이다
    assert analysis.timings_ms["draft_reused"] == 0
    assert len(llm.draft_prompts) == 2


def test_스위치를_끄면_예전처럼_분류_뒤에_쓴다(monkeypatch) -> None:
    monkeypatch.setenv(drafting.ENV_SPECULATIVE, "off")
    llm, retr = Recorder(), Retriever({}, ["pol_1"])
    analysis, _ = run(llm, retr, "결제 버튼이 안 눌려요", monkeypatch)
    assert analysis.timings_ms["draft_reused"] == 0 and len(llm.draft_prompts) == 1
    assert retr.queries == [CLASSIFIED.search_query]


def test_먼저_쓴_초안은_분류와_겹쳐_돌아서_더_빨리_끝난다(monkeypatch) -> None:
    import time

    llm = Recorder(classify_delay=0.3)
    llm_slow_draft = llm.structured

    async def slow(prompt, schema, *a, **k):  # noqa: ANN001, ANN002, ANN003, ANN202
        if schema is LLMDrafts:
            await asyncio.sleep(0.3)
        return await llm_slow_draft(prompt, schema, *a, **k)

    monkeypatch.setattr(llm, "structured", slow)
    started = time.perf_counter()
    run(llm, Retriever({}, ["pol_1"]), "결제 버튼이 안 눌려요", monkeypatch)
    took = time.perf_counter() - started
    assert took < 0.55  # 순서대로면 0.6초가 넘는다 (0.3 + 0.3)
