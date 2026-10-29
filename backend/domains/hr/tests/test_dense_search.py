"""Dense 검색: 색인 재사용, 순위, 기준 미달 무응답, 질의 마스킹, 한도 초과 재시도."""

from __future__ import annotations

import pytest

from ax_agent_template.llm import MockLLMClient
from hr_agent.retrieval.dense import (
    DenseIndex,
    EmbedError,
    LLMEmbedder,
    cosine,
    mask,
    normalize,
    text_key,
)

CARDS = [
    ("c1", "결제 승인 오류가 늘어 배포를 롤백했다"),
    ("c2", "금요일 오후에는 배포하지 않는다"),
    ("c3", "퇴사자 계정은 당일 회수한다"),
]


class CountingEmbedder:
    """호출한 글 개수를 세는 목 임베더."""

    def __init__(self) -> None:
        self.calls: list[list[str]] = []
        self._mock = MockLLMClient()

    async def embed(self, texts: list[str]) -> list[list[float]]:
        self.calls.append(list(texts))
        return await self._mock.embed(texts)


@pytest.fixture
def index(tmp_path):
    embedder = CountingEmbedder()
    return DenseIndex(str(tmp_path / "v.db"), embedder, "mock"), embedder


@pytest.mark.asyncio
async def test_index_embeds_only_new_or_changed(index) -> None:
    idx, emb = index
    assert await idx.index(CARDS) == 3
    assert await idx.index(CARDS) == 0  # 그대로면 다시 부르지 않는다
    assert (
        await idx.index([("c1", "결제 승인 오류가 늘어 배포를 롤백했다"), ("c2", "바뀐 글")]) == 1
    )
    assert [len(c) for c in emb.calls] == [3, 1]


@pytest.mark.asyncio
async def test_index_is_reused_across_instances(tmp_path) -> None:
    path = str(tmp_path / "v.db")
    first = CountingEmbedder()
    await DenseIndex(path, first, "mock").index(CARDS)
    second = CountingEmbedder()
    assert await DenseIndex(path, second, "mock").index(CARDS) == 0
    assert second.calls == []


@pytest.mark.asyncio
async def test_model_change_reembeds(tmp_path) -> None:
    path = str(tmp_path / "v.db")
    emb = CountingEmbedder()
    await DenseIndex(path, emb, "m1").index(CARDS)
    assert await DenseIndex(path, emb, "m2").index(CARDS) == 3


@pytest.mark.asyncio
async def test_stored_vectors_are_unit_length(index) -> None:
    idx, _ = index
    await idx.index(CARDS)
    vec = idx.vector("c1")
    assert vec is not None
    assert cosine(vec, vec) == pytest.approx(1.0, abs=1e-5)


@pytest.mark.asyncio
async def test_search_ranks_closest_card_first(index) -> None:
    idx, _ = index
    await idx.index(CARDS)
    hits = await idx.search("결제 승인 오류 롤백", [c for c, _ in CARDS], k=3)
    assert hits[0].key == "c1"
    assert [h.score for h in hits] == sorted((h.score for h in hits), reverse=True)


@pytest.mark.asyncio
async def test_search_limits_to_candidates_and_k(index) -> None:
    idx, _ = index
    await idx.index(CARDS)
    hits = await idx.search("배포", ["c2", "c3"], k=1)
    assert len(hits) == 1
    assert hits[0].key in {"c2", "c3"}


@pytest.mark.asyncio
async def test_search_returns_nothing_below_min_cosine(index) -> None:
    idx, _ = index
    await idx.index(CARDS)
    assert await idx.search("전혀 관계없는 단어들", [c for c, _ in CARDS], min_cosine=0.99) == []


@pytest.mark.asyncio
async def test_same_query_is_embedded_once(index) -> None:
    idx, emb = index
    await idx.index(CARDS)
    await idx.search("배포 롤백", ["c1"])
    await idx.search("배포 롤백", ["c1"])
    assert sum(1 for c in emb.calls if c == ["배포 롤백"]) == 1


@pytest.mark.asyncio
async def test_query_is_masked_before_embedding(index) -> None:
    idx, emb = index
    await idx.index(CARDS)
    await idx.search("문의 메일 kim@example.com 전화 010-1234-5678", ["c1"])
    sent = emb.calls[-1][0]
    assert "kim@example.com" not in sent
    assert "010-1234-5678" not in sent


def test_mask_hides_email_phone_and_card_number() -> None:
    assert mask("a@b.co") == "[이메일]"
    assert mask("010-1234-5678") == "[전화]"
    assert mask("1234 5678 9012 3456") == "[번호]"
    assert mask("결제 오류") == "결제 오류"


def test_cosine_rejects_different_dimensions() -> None:
    with pytest.raises(ValueError):
        cosine([1.0, 0.0], [1.0])


def test_normalize_keeps_zero_vector() -> None:
    assert list(normalize([0.0, 0.0])) == [0.0, 0.0]


def test_text_key_is_stable_and_prefixed() -> None:
    assert text_key("a") == text_key("a")
    assert text_key("a").startswith("q:")


class FlakyClient:
    """처음 n번은 실패하는 임베딩 클라이언트."""

    def __init__(self, failures: int) -> None:
        self.failures = failures
        self.calls = 0

    async def embed(self, texts, context_id=None):
        self.calls += 1
        if self.calls <= self.failures:
            raise RuntimeError("429")
        return [[1.0, 0.0] for _ in texts]


@pytest.mark.asyncio
async def test_embedder_retries_then_succeeds() -> None:
    client = FlakyClient(failures=2)
    emb = LLMEmbedder(client, retries=3, retry_base_sec=0.0, pause_sec=0.0)
    assert await emb.embed(["a", "b"]) == [[1.0, 0.0], [1.0, 0.0]]
    assert client.calls == 3


@pytest.mark.asyncio
async def test_embedder_gives_up_without_leaking_message() -> None:
    emb = LLMEmbedder(FlakyClient(failures=99), retries=1, retry_base_sec=0.0, pause_sec=0.0)
    with pytest.raises(EmbedError) as err:
        await emb.embed(["a"])
    assert "429" not in str(err.value)


@pytest.mark.asyncio
async def test_embedder_splits_into_batches() -> None:
    client = FlakyClient(failures=0)
    emb = LLMEmbedder(client, batch_size=2, retry_base_sec=0.0, pause_sec=0.0)
    assert len(await emb.embed(["a", "b", "c", "d", "e"])) == 5
    assert client.calls == 3
