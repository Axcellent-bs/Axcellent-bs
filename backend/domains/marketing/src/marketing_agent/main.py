"""ax-agent-marketing 서버 진입점."""

from __future__ import annotations

import asyncio
import contextlib
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from ax_agent_template.server import create_agent_app
from ax_contracts.schemas import AgentCard
from marketing_agent.handlers import HANDLERS
from marketing_agent.sns_publish import publisher


def _card_path() -> Path:
    """`agents/marketing/agent-card.json`을 찾는다. 컨테이너와 레포 루트 실행 모두에서 동작한다."""
    override = os.getenv("MARKETING_AGENT_CARD")
    candidates = [
        Path(override) if override else None,
        Path.cwd() / "domains" / "marketing" / "agent-card.json",
        Path(__file__).resolve().parents[2] / "agent-card.json",
    ]
    for path in candidates:
        if path and path.is_file():
            return path
    raise FileNotFoundError(
        "agent-card.json을 찾을 수 없다 (MARKETING_AGENT_CARD로 경로를 지정한다)"
    )


def load_card() -> AgentCard:
    return AgentCard.model_validate_json(_card_path().read_text(encoding="utf-8"))


app = create_agent_app(load_card(), HANDLERS)

# publish_sns: 승인 이벤트 구독(REDIS_URL이 없으면 꺼짐을 기록) + 폴링 대비책 + 시작 시 멈춘 건 복구
publisher.setup_subscriber()
_base_lifespan = app.router.lifespan_context


@asynccontextmanager
async def _lifespan(application: object) -> AsyncIterator[None]:
    async with _base_lifespan(application):  # type: ignore[arg-type]
        await publisher.recover_interrupted()
        poller: asyncio.Task[None] | None = None
        if publisher.config().review_poll_s > 0:
            poller = asyncio.get_running_loop().create_task(
                publisher.review_poll_loop(), name="sns-review-poll"
            )
            publisher.RT.poller = "on"
        try:
            yield
        finally:
            if poller is not None:
                poller.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await poller
                publisher.RT.poller = "off"


app.router.lifespan_context = _lifespan  # type: ignore[assignment]
