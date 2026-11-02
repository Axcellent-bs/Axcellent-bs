"""서버를 켤 때 정책 시드 카드를 저장하고 검색 인덱스에 올린다 (#192 후속).

시드는 멱등이라 다시 켜도 카드는 늘지 않는다. 임베딩이 안 되면(모델 미설정·네트워크) 서버는 그대로
켜고 로그만 남긴다. 그 경우 카드는 저장돼 있지만 검색에는 나오지 않는다.
끄려면 `MEMORY_SEED_POLICIES=off`.
"""

from __future__ import annotations

import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI

from ax_memory.embed import card_text
from ax_memory.seed.loader import SeedReport, seed_policies

logger = logging.getLogger("ax.memory.seed")

ENV_SWITCH = "MEMORY_SEED_POLICIES"


def enabled() -> bool:
    return os.getenv(ENV_SWITCH, "on").strip().lower() != "off"


async def seed_and_index(state: Any) -> SeedReport | None:
    """정책 카드를 저장하고 한 번에 임베딩해 인덱스에 올린다. 실패해도 예외를 내지 않는다."""
    try:
        report = seed_policies(state.store)
        cards = state.store.list(kind="policy", status="active")
    except Exception:
        logger.warning("정책 시드 저장 실패", exc_info=True)
        return None
    try:
        vectors = await state.embedder.embed([card_text(c) for c in cards])
        for card, vector in zip(cards, vectors, strict=True):
            state.index.upsert(card.card_id, card.tenant_id, vector)
    except Exception:
        logger.warning(
            "정책 카드 %d장을 저장했지만 임베딩에 실패해 검색에는 나오지 않는다", len(cards)
        )
        logger.debug("임베딩 실패 상세", exc_info=True)
        return report
    logger.info(
        "정책 카드 %d장 검색 가능 (새로 %d, 기존 %d)", len(cards), report.created, report.existing
    )
    return report


def seed_on_start(app: FastAPI, get_state: Any) -> None:
    """앱이 시작될 때 `seed_and_index`를 돌리도록 lifespan을 감싼다."""
    inner = app.router.lifespan_context

    @asynccontextmanager
    async def lifespan(application: FastAPI) -> AsyncIterator[None]:
        if enabled():
            await seed_and_index(get_state())
        async with inner(application):
            yield

    app.router.lifespan_context = lifespan
