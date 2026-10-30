"""memory의 정책 카드를 답변 근거로 바꾼다 (이슈 #193, MEM-03·MEM-04).

memory `card_search`(의미 검색, `kind=policy`)로 찾고, 카드는 읽기만 한다.
정책 카드의 내용은 memory와 정책 담당이 소유한다.
`low_confidence` 카드는 판정 근거로 쓰지 않으므로 제외한다.
memory에 닿지 못했거나 카드를 하나도 못 찾으면 `kb.search`(시드 조각)로 대체한다.
카드를 찾았지만 미확정이거나 점수가 낮으면 근거 없음이다(엉뚱한 분류의 카드로 채우지 않는다).
"""

from __future__ import annotations

import logging
import os
from collections.abc import Awaitable, Callable
from typing import Any

from ax_contracts.schemas import AgentRequest, AgentResult, TaskStatus
from support_agent import kb
from support_agent.schemas import Source

logger = logging.getLogger("ax.support.policy_memory")

SendFn = Callable[[str, AgentRequest], Awaitable[AgentResult]]
Retriever = Callable[[str], Awaitable[list[Source]]]  # 질의 -> 근거 조각

TOP_K = 3
# memory에서는 넉넉히 받는다. 미확정 카드가 상위 자리를 차지해도 쓸 카드가 TOP_K장 남도록
FETCH_K = 6
# 이 점수보다 낮은 카드는 근거로 쓰지 않는다. 확정된 카드가 없는 분류(예: 개인정보 삭제)에서
# 엉뚱한 분류의 카드(취소·환불 0.59~0.62)가 근거로 들어오는 것을 막는다. 값은 시드(제안)다.
MIN_SCORE = 0.65
MIN_SCORE_ENV = "SUPPORT_POLICY_MIN_SCORE"
_POLICY_TAG = "policy"


def min_score() -> float:
    """근거로 쓸 최소 점수. `SUPPORT_POLICY_MIN_SCORE`(0~1)로 바꾼다. 잘못된 값은 기본값을 쓴다."""
    try:
        value = float(os.getenv(MIN_SCORE_ENV, ""))
    except ValueError:
        return MIN_SCORE
    return value if 0.0 <= value <= 1.0 else MIN_SCORE


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
    """정책 카드를 찾아 근거로 돌려준다.

    None이면 시드로 대체해야 한다(memory에 닿지 못함, 응답 모양이 다름, 카드를 하나도 못 찾음).
    빈 목록이면 memory가 카드를 찾았지만 쓸 만한 것이 없다는 뜻이라 근거 없음으로 둔다
    (미확정 카드이거나 점수가 `min_score` 미만). 이때 시드로 채우지 않는다.
    """
    if not query.strip():
        return []
    search = AgentRequest(
        meta=request.meta.model_copy(update={"idempotency_key": None}),
        skill="card_search",
        input={"query": query, "kind": ["policy"], "top_k": max(FETCH_K, limit)},
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
    if not hits:
        return None
    floor = min_score()
    sources = [
        s
        for h in hits
        if isinstance(h, dict) and (s := to_source(h)) is not None and s.score >= floor
    ]
    return sources[:limit]


async def search(
    request: AgentRequest, query: str, send: SendFn, limit: int = TOP_K
) -> list[Source]:
    """memory 정책 카드를 먼저 쓴다. 닿지 못했거나 카드를 하나도 못 찾으면 시드 조각으로 대체한다.

    카드를 찾았지만 미확정이거나 점수가 낮아 쓸 것이 없으면 근거 없음(빈 목록)이다.
    """
    found = await fetch_sources(request, query, send, limit)
    if found is None:
        return kb.search(query, limit=limit)
    return found


def retriever(request: AgentRequest, send: SendFn) -> Retriever:
    """`drafting.analyze`에 넘길 검색 함수를 만든다."""

    async def run(query: str) -> list[Source]:
        return await search(request, query, send)

    return run
