"""진단 근거로 쓸 아카이브 이슈 카드를 읽는다 (아카이브 읽기 skill `list_cards`).

이슈 카드는 아카이브 AI가 쌓는다. 총괄은 평가 근거로 보내진 이슈마다 그 이슈의 카드를 읽어
이벤트 유형과 해결 기록(처리 방식·조치)을 본다. 아카이브에 닿지 않으면 진단은 총괄 기록만으로
이어 가고, 카드를 못 읽었다는 사실을 진단 메모에 남긴다.
"""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from typing import Any

from ax_agent_template.client import call_agent
from ax_contracts.schemas import AgentRequest, RequestMeta, TaskStatus

CALL_TIMEOUT_SECONDS = 15
TARGET = "archive"
SKILL = "list_cards"
# 이슈 카드 종류. 같은 이슈에 결정·사례 카드도 있을 수 있어 이슈 카드만 쓴다
KIND = "incident"


class CardsUnavailable(RuntimeError):
    """아카이브에서 카드를 읽지 못했다."""


async def _one(context_id: str, request_context: str) -> dict[str, Any] | None:
    sent = AgentRequest(
        meta=RequestMeta(
            context_id=request_context,
            token="replaced-by-call_agent",
            path=[],  # 진단은 인사가 보낸 요청과 따로 시작하는 읽기 조회다
            deadline=datetime.now(UTC) + timedelta(seconds=CALL_TIMEOUT_SECONDS),
        ),
        skill=SKILL,
        input={"context_id": context_id, "limit": 20},
    )
    try:
        result = await call_agent(TARGET, sent)
    except Exception as exc:  # noqa: BLE001
        raise CardsUnavailable(str(exc)[:120]) from exc
    if result.status != TaskStatus.COMPLETED:
        raise CardsUnavailable(f"{result.outcome}")
    cards = [c for c in (result.output or {}).get("cards", []) if c.get("kind") == KIND]
    if not cards:
        return None
    return max(cards, key=lambda c: c.get("created_at") or "")  # type: ignore[no-any-return]


async def fetch_cards_via_archive(
    context_ids: list[str], request_context: str
) -> dict[str, dict[str, Any]]:
    """이슈 번호 → 가장 최근 이슈 카드. 카드가 없는 이슈는 빠진다.

    하나라도 못 읽으면 `CardsUnavailable`을 올린다.
    """
    found = await asyncio.gather(*(_one(cid, request_context) for cid in context_ids))
    return {cid: card for cid, card in zip(context_ids, found, strict=True) if card is not None}
