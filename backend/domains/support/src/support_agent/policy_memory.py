"""memory의 정책 카드를 답변 근거로 바꾼다 (이슈 #193, MEM-03·MEM-04).

memory `card_search`(의미 검색, `kind=policy`)로 찾고, 카드는 읽기만 한다.
정책 카드의 내용은 memory와 정책 담당이 소유한다.
`low_confidence` 카드는 판정 근거로 쓰지 않으므로 제외한다.
memory에 닿지 못했거나 쓸 만한 카드가 없으면 `kb.search`(시드 조각)로 대체한다.
"""

from __future__ import annotations

import logging
from collections.abc import Awaitable, Callable
from typing import Any

from ax_contracts.schemas import AgentRequest, AgentResult, TaskStatus
from support_agent import kb
from support_agent.schemas import Source

logger = logging.getLogger("ax.support.policy_memory")

SendFn = Callable[[str, AgentRequest], Awaitable[AgentResult]]
Retriever = Callable[[str], Awaitable[list[Source]]]  # 질의 -> 근거 조각

TOP_K = 3
_POLICY_TAG = "policy"


def to_source(hit: dict[str, Any]) -> Source | None:
    """`CardHit`(dict)를 근거 조각으로 바꾼다. 쓸 수 없는 카드는 None."""
    card = hit.get("card")
    if not isinstance(card, dict) or hit.get("low_confidence"):
        return None
    if card.get("status", "active") != "active" or not card.get("card_id"):
        return None
    doc_id = next((t for t in card.get("tags") or [] if t != _POLICY_TAG), "policy")
    head, _, section = str(card.get("title") or "").partition(":")
    return Source(
        id=str(card["card_id"]),
        doc_id=doc_id,
        category=doc_id,
        title=head.strip(),
        section=section.strip(),
        text=str(card.get("body") or ""),
        score=round(float(hit.get("score") or 0.0), 3),
    )


async def fetch_sources(
    request: AgentRequest, query: str, send: SendFn, limit: int = TOP_K
) -> list[Source] | None:
    """정책 카드를 찾아 근거로 돌려준다. memory에 닿지 못하면 None(대체 필요), 없으면 빈 목록."""
    if not query.strip():
        return []
    search = AgentRequest(
        meta=request.meta.model_copy(update={"idempotency_key": None}),
        skill="card_search",
        input={"query": query, "kind": ["policy"], "top_k": limit},
    )
    try:
        reply = await send("memory", search)
    except Exception:
        logger.warning("card_search 호출 실패", exc_info=True)
        return None
    if reply.status != TaskStatus.COMPLETED:
        return None
    hits = reply.output.get("hits")
    if not isinstance(hits, list):
        return None
    sources = [s for h in hits if isinstance(h, dict) and (s := to_source(h)) is not None]
    return sources[:limit]


async def search(
    request: AgentRequest, query: str, send: SendFn, limit: int = TOP_K
) -> list[Source]:
    """memory 정책 카드를 먼저 쓰고, 닿지 못했거나 쓸 카드가 없으면 시드 조각으로 대체한다."""
    found = await fetch_sources(request, query, send, limit)
    if found:
        return found
    return kb.search(query, limit=limit)


def retriever(request: AgentRequest, send: SendFn) -> Retriever:
    """`drafting.analyze`에 넘길 검색 함수를 만든다."""

    async def run(query: str) -> list[Source]:
        return await search(request, query, send)

    return run
