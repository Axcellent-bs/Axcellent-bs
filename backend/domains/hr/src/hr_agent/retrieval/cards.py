"""카드 검색 부품: Dense 색인을 카드 목록에 적용하고, 설정으로 색인을 만든다."""

from __future__ import annotations

import os
from collections.abc import Mapping, Sequence
from pathlib import Path

from ax_agent_template.llm import LLMClient, LLMSettings
from ax_contracts.schemas import Card
from hr_agent.retrieval.dense import DenseIndex, LLMEmbedder


def dense_text(card: Card) -> str:
    """임베딩할 카드 글. 골든 셋 평가와 같은 제목과 본문이다."""
    return f"{card.title}\n{card.body}"


class DenseCardSearch:
    """카드 목록에서 질의와 의미가 가까운 카드를 찾는다. 새 카드·바뀐 카드만 임베딩한다."""

    def __init__(self, index: DenseIndex) -> None:
        self._index = index

    async def search(
        self, query: str, cards: Sequence[Card], k: int = 5, min_cosine: float = 0.0
    ) -> list[tuple[Card, float]]:
        if not cards:
            return []
        await self._index.index([(c.card_id, dense_text(c)) for c in cards])
        hits = await self._index.search(query, [c.card_id for c in cards], k, min_cosine)
        by_id = {c.card_id: c for c in cards}
        return [(by_id[h.key], h.score) for h in hits]


def build_dense_search(
    db_path: str, env: Mapping[str, str] | None = None
) -> DenseCardSearch | None:
    """임베딩 설정(`MEMORY_*` 또는 `LLM_*`·`EMBED_MODEL`)이 있고 hr DB가 파일일 때만 만든다.

    벡터는 hr DB 옆의 `hr_vectors.db`에 둔다. 설정이 없으면 None이고, 호출하는 쪽은 Dice로 대체한다.
    """
    settings = LLMSettings.from_env("memory", env if env is not None else os.environ)
    if not (settings.embed_model and settings.base_url) or db_path == ":memory:":
        return None
    path = str(Path(db_path).with_name("hr_vectors.db"))
    return DenseCardSearch(
        DenseIndex(path, LLMEmbedder(LLMClient("memory", settings)), settings.embed_model)
    )
