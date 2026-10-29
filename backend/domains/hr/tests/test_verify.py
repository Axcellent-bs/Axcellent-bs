"""LLM 검증: 판정 반영, 형식 오류와 실패 시 None, 데이터 블록 사용."""

import pytest

from hr_agent.retrieval.verify import MAX_CANDIDATES, RelevanceVerifier, _Judgement, _Verdicts

CANDS = [("card_a", "결제 재시도 2회 확정"), ("card_b", "금요일 배포 금지")]


class FakeLLM:
    def __init__(self, judgements=None, error=None) -> None:
        self.judgements, self.error = judgements, error
        self.seen: list[tuple[str, dict]] = []

    async def structured(self, prompt, schema, untrusted=None, **kwargs):
        self.seen.append((prompt, untrusted))
        if self.error:
            raise self.error
        return _Verdicts(judgements=self.judgements)


@pytest.mark.asyncio
async def test_verdicts_are_mapped_back_to_card_ids_by_code() -> None:
    llm = FakeLLM([_Judgement(label="c1", relevant=True), _Judgement(label="c2", relevant=False)])
    verdict = await RelevanceVerifier(llm).verify("결제 재시도", CANDS)
    assert verdict == {"card_a": True, "card_b": False}


@pytest.mark.asyncio
async def test_query_and_cards_go_in_the_data_block_with_temporary_labels() -> None:
    llm = FakeLLM([_Judgement(label="c1", relevant=True), _Judgement(label="c2", relevant=True)])
    await RelevanceVerifier(llm).verify("질의 글", CANDS)
    prompt, data = llm.seen[0]
    assert data == {"질의": "질의 글", "c1": CANDS[0][1], "c2": CANDS[1][1]}
    assert "card_a" not in prompt and "card_a" not in str(data)  # 실제 카드 id는 LLM에 주지 않는다


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "judgements",
    [
        [_Judgement(label="c1", relevant=True)],  # 빠짐
        [
            _Judgement(label="c1", relevant=True),
            _Judgement(label="c3", relevant=True),
        ],  # 모르는 label
        [_Judgement(label="c1", relevant=True), _Judgement(label="c1", relevant=False)],  # 중복
    ],
)
async def test_malformed_response_returns_none(judgements) -> None:
    assert await RelevanceVerifier(FakeLLM(judgements)).verify("q", CANDS) is None


@pytest.mark.asyncio
async def test_llm_failure_returns_none_and_counts_the_call() -> None:
    verifier = RelevanceVerifier(FakeLLM(error=RuntimeError("timeout")))
    assert await verifier.verify("q", CANDS) is None
    assert verifier.calls == 1


@pytest.mark.asyncio
async def test_no_candidates_means_no_call() -> None:
    verifier = RelevanceVerifier(FakeLLM([]))
    assert await verifier.verify("q", []) == {}
    assert verifier.calls == 0


@pytest.mark.asyncio
async def test_only_the_first_candidates_are_sent() -> None:
    many = [(f"id{i}", f"글 {i}") for i in range(MAX_CANDIDATES + 2)]
    llm = FakeLLM([_Judgement(label=f"c{i}", relevant=True) for i in range(1, MAX_CANDIDATES + 1)])
    verdict = await RelevanceVerifier(llm).verify("q", many)
    assert list(verdict) == [f"id{i}" for i in range(MAX_CANDIDATES)]
