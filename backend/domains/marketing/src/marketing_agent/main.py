"""ax-agent-marketing 서버 진입점."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from ax_agent_template.server import create_agent_app
from ax_contracts.schemas import AgentCard
from marketing_agent import brief_auto_handoff, campaign_auto, handlers, state_db
from marketing_agent.handlers import HANDLERS
from marketing_agent.sns_publish import publisher


def _setup_app_logging() -> None:
    """`marketing.*` 로거의 INFO 이상을 서비스 로그(stderr → .logs/<이름>.log)로 내보낸다.

    uvicorn은 자기 로거(`uvicorn.*`)만 설정하고 루트 로거에는 핸들러가 없어서, 마케팅 코드의
    `logger.info`는 어디에도 안 나가고 `warning`만 기본 임시 핸들러로 나간다. 이 서비스 프로세스의
    `marketing` 로거에만 핸들러를 달아(루트·다른 로거는 그대로, 전파는 유지) 중복 없이 한 번만 켠다.
    형식은 uvicorn 기본(`INFO:     ...`)과 맞춘다.
    """
    log = logging.getLogger("marketing")
    if any(getattr(h, "_ax_marketing", False) for h in log.handlers):
        return
    handler = logging.StreamHandler()
    handler.setFormatter(logging.Formatter("%(levelname)s:     %(name)s: %(message)s"))
    handler._ax_marketing = True  # type: ignore[attr-defined]
    log.addHandler(handler)
    log.setLevel(logging.INFO)


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


_setup_app_logging()
app = create_agent_app(load_card(), HANDLERS)

# publish_sns: 승인 이벤트 구독(REDIS_URL이 없으면 꺼짐을 기록) + 폴링 대비책 + 시작 시 멈춘 건 복구
publisher.setup_subscriber()
_base_lifespan = app.router.lifespan_context


OUTBOX_FLUSH_ENV = "MARKETING_OUTBOX_FLUSH_S"


def _outbox_interval() -> float:
    try:
        return float(os.getenv(OUTBOX_FLUSH_ENV, "30"))
    except ValueError:
        return 30.0


async def _outbox_loop() -> None:
    """outbox를 주기마다 다시 보낸다. 실패는 다음 주기에 다시(로그만)."""
    while True:
        await asyncio.sleep(_outbox_interval())
        try:
            sent = await state_db.flush(handlers.STATE.state_db)
        except Exception:  # noqa: BLE001
            logging.getLogger(__name__).exception("marketing_outbox_flush_failed")
            continue
        if sent:
            logging.getLogger(__name__).info("marketing_outbox_sent count=%s", sent)


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
        # 캠페인 종료 뒤 결과 자동 전달: 켜져 있을 때만 일한다
        # (MARKETING_CAMPAIGN_AUTO_RECORD, 기본 꺼짐)
        auto: asyncio.Task[None] | None = None
        if campaign_auto.AutoConfig.from_env().poll_s > 0:
            auto = asyncio.get_running_loop().create_task(
                campaign_auto.auto_record_loop(
                    handlers.STATE.campaigns,
                    handlers._brief_content,  # noqa: SLF001
                    # 기간 광고 숫자 합계가 있으면 그 값을 광고비로(없으면 승인 예산)
                    lambda item: campaign_auto.ad_metrics_spend(handlers.STATE.ad_metrics, item),
                ),
                name="campaign-auto-record",
            )
        # 받은 기획안 → SNS·미디어 요청 초안 자동 넘김(MARKETING_BRIEF_AUTO_HANDOFF, 기본 꺼짐).
        # 미디어 생성·게시는 부르지 않는다
        handoff_task: asyncio.Task[None] | None = None
        if brief_auto_handoff.AutoHandoffConfig.from_env().poll_s > 0:
            handoff_task = asyncio.get_running_loop().create_task(
                brief_auto_handoff.auto_handoff_loop(
                    lambda: handlers.STATE.briefs, handlers.handoff_brief_to_sns
                ),
                name="brief-auto-handoff",
            )
        # 이벤트 버스에 못 보낸 이벤트(outbox)를 주기적으로 다시 보낸다
        # (MARKETING_OUTBOX_FLUSH_S, 기본 30초)
        outbox_task: asyncio.Task[None] | None = None
        if _outbox_interval() > 0:
            outbox_task = asyncio.get_running_loop().create_task(
                _outbox_loop(), name="marketing-outbox"
            )
        try:
            yield
        finally:
            if outbox_task is not None:
                outbox_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await outbox_task
            if handoff_task is not None:
                handoff_task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await handoff_task
            if auto is not None:
                auto.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await auto
            if poller is not None:
                poller.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await poller
                publisher.RT.poller = "off"


app.router.lifespan_context = _lifespan  # type: ignore[assignment]
