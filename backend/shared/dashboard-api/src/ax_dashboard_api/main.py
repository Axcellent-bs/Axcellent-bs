"""ax-dashboard-api 서버 진입점."""

import asyncio
import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from ax_dashboard_api import (
    company_connection,
    handoffs,
    office_room,
    shop_events,
    support_client,
)
from ax_dashboard_api.envelope import install_error_handlers, is_mock
from ax_dashboard_api.routes import router

DEFAULT_CORS_ORIGINS = "http://localhost:3001,http://127.0.0.1:3001"

logger = logging.getLogger("ax.dashboard.retention")

# 개인정보를 3년을 넘겨 들고 있지 않게, 받은 지 3년이 지난 문의를 지우는 skill을 하루에 한 번 부른다
RETENTION_SWEEP_FIRST_DELAY_SECONDS = 60.0
RETENTION_SWEEP_INTERVAL_SECONDS = 6 * 3600.0


async def retention_sweep_once() -> bool:
    """3년 보유기간 삭제를 한 번 부른다. 오늘 한 번 처리한 키라 여러 번 불러도 한 번만 집행된다."""
    try:
        today = datetime.now(UTC).date().isoformat()
        await support_client.call("purge_expired_inquiries", {}, key=f"retention-expire:{today}")
        # 클레임 기록은 팀 기준(5년)이고 사람이 켜기 전에는 건수만 센다 (support가 정한다)
        await support_client.call("purge_expired_claims", {}, key=f"claim-retention:{today}")
        await idea_report_sweep_once()
        return True
    except Exception:  # noqa: BLE001 - 못 불러도 서버는 계속 돈다. 다음 주기에 다시 부른다
        logger.warning("3년 보유기간 삭제를 부르지 못했다", exc_info=True)
        return False


# 어제부터 거꾸로 이만큼의 날을 보낸다 (놓친 날을 메우고, 받은 날은 마케팅이 건너뛴다)
IDEA_REPORT_DAYS = 3


async def idea_report_sweep_once() -> None:
    """고객 아이디어 일일 보고를 마케팅에 넘기는 skill을 부른다. 못 불러도 다른 일은 계속된다."""
    today = datetime.now(UTC).date()
    for back in range(1, IDEA_REPORT_DAYS + 1):
        day = (today - timedelta(days=back)).isoformat()
        try:
            await support_client.call(
                "send_idea_report", {"report_date": day}, key=f"idea-report:{day}"
            )
        except Exception:  # noqa: BLE001 - 다음 주기에 같은 날을 다시 보낸다
            logger.warning("고객 아이디어 일일 보고를 부르지 못했다 (%s)", day, exc_info=True)


async def _retention_loop() -> None:
    await asyncio.sleep(RETENTION_SWEEP_FIRST_DELAY_SECONDS)
    while True:
        await retention_sweep_once()
        await asyncio.sleep(RETENTION_SWEEP_INTERVAL_SECONDS)


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    task = None
    # 목 모드(테스트)와 `DASHBOARD_RETENTION_SWEEP=off`에서는 돌리지 않는다
    if not is_mock() and (os.getenv("DASHBOARD_RETENTION_SWEEP") or "on").lower() != "off":
        task = asyncio.create_task(_retention_loop())
    try:
        yield
    finally:
        if task is not None:
            task.cancel()


app = FastAPI(title="ax-dashboard-api", lifespan=lifespan)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[
        o.strip()
        for o in (os.getenv("DASHBOARD_CORS_ORIGINS") or DEFAULT_CORS_ORIGINS).split(",")
        if o.strip()
    ],
    allow_methods=["*"],
    allow_headers=["*"],
)
install_error_handlers(app)
app.include_router(router)
# 사람 할 일함: 에이전트가 넣는 창구(에이전트 토큰)와 사람이 보는 창구(쓰기는 사람 토큰)
app.include_router(handoffs.intake)
app.include_router(handoffs.inbox)
# 오피스 단톡방 대화 목록 (단톡방 대화는 모두에게 보인다, 쓰기는 사람 토큰)
app.include_router(office_room.router)
# 시스템 관리 > 회사 연결 (고객사 판매 서버 주소, 쓰기는 사람 토큰)
app.include_router(company_connection.router)
# 알림 받는 주소: 고객사 판매 서버가 회사 키로 이벤트를 보낸다 (사람 토큰 대신 회사 키)
app.include_router(shop_events.router)


@app.get("/health")
def health() -> dict:
    return {"data": {"status": "ok"}, "meta": {}}
