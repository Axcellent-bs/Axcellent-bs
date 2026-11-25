"""ax-dashboard-api 서버 진입점."""

import asyncio
import logging
import os
import sys
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from ax_dashboard_api import (
    change_detector,
    company_ai,
    company_connection,
    handoffs,
    mcp_gateway,
    office_room,
    personal_mcp,
    public_info,
    settlements,
    shop_events,
    shop_relay,
    support_client,
    tool_connections,
)
from ax_dashboard_api.accounts import router as accounts_router
from ax_dashboard_api.connection_keys import router as connection_keys_router
from ax_dashboard_api.connection_keys import signup_router as connection_keys_signup_router
from ax_dashboard_api.cost_limit_routes import router as cost_limit_router
from ax_dashboard_api.envelope import SessionCompanyMiddleware, install_error_handlers, is_mock
from ax_dashboard_api.higgsfield_mcp_oauth import router as higgsfield_mcp_router
from ax_dashboard_api.instagram_oauth import refresh_interval as ig_refresh_interval
from ax_dashboard_api.instagram_oauth import refresh_loop as ig_refresh_loop
from ax_dashboard_api.instagram_oauth import router as instagram_oauth_router
from ax_dashboard_api.instagram_radar import router as instagram_radar_router
from ax_dashboard_api.promo_style import router as promo_style_router
from ax_dashboard_api.render_settings_routes import router as render_settings_router
from ax_dashboard_api.routes import router
from ax_dashboard_api.secret_routes import router as secret_router
from ax_dashboard_api.site_adapter.company_route import CompanyRouteMiddleware
from ax_dashboard_api.site_adapter.router import router as site_adapter_router
from ax_dashboard_api.site_adapter.setup import router as site_adapter_setup_router
from ax_dashboard_api.site_adapter.write_setup import router as site_adapter_write_router
from ax_dashboard_api.youtube_meme_keywords import router as youtube_meme_keywords_router
from ax_dashboard_api.youtube_oauth import router as youtube_oauth_router
from ax_secret_store import startup_check

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
    # 실제 서버에 목 스위치가 남아 있으면 뜨지 않는다. 테스트·AX_ALLOW_MOCK=1만 예외
    if is_mock() and os.getenv("AX_ALLOW_MOCK") != "1" and "pytest" not in sys.modules:
        raise RuntimeError(
            "목 스위치(DASHBOARD_MOCK·AGENT_TEMPLATE_MOCK)가 켜져 있어 "
            "서버를 시작하지 않는다. 목 모드는 테스트 전용이다."
        )
    startup_check()  # AX_SECRET_STORE=db인데 마스터 키·DB가 맞지 않으면 시작을 거부한다
    task = None
    # 목 모드(테스트)와 `DASHBOARD_RETENTION_SWEEP=off`에서는 돌리지 않는다
    if not is_mock() and (os.getenv("DASHBOARD_RETENTION_SWEEP") or "on").lower() != "off":
        task = asyncio.create_task(_retention_loop())
    # 변화 감지기: AX_CHANGE_DETECTOR_SECONDS가 0보다 클 때만 (기본 꺼짐, 목 모드에서도 돌지 않는다)
    detector = None
    if not is_mock() and change_detector.interval_seconds() > 0:
        detector = asyncio.create_task(change_detector.loop())
    # 인스타 로그인 연결 토큰(60일) 자동 연장. MARKETING_IG_REFRESH_INTERVAL_SECONDS<=0이면 끈다
    ig_refresh = None
    if not is_mock() and ig_refresh_interval() > 0:
        ig_refresh = asyncio.create_task(ig_refresh_loop())
    try:
        yield
    finally:
        for running in (task, detector, ig_refresh):
            if running is not None:
                running.cancel()


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
# 회사 연결·변환기 저장소를 요청 회사로 고른다(변환기 경로의 회사, 화면은 세션 회사).
# 세션 회사를 읽으므로 SessionCompanyMiddleware보다 먼저 더한다(나중에 더한 것이 바깥)
app.add_middleware(CompanyRouteMiddleware)
app.add_middleware(SessionCompanyMiddleware)  # 응답 meta.tenant_id가 세션 회사를 따른다
app.include_router(router)
# 영상 비용 상한·안전 정책 화면 (로그인 세션의 운영자·관리자, 변경은 limit_editor·policy_editor)
app.include_router(cost_limit_router)
# 시스템 > 키 관리: 암호화 DB의 고객사별 키 (세션의 운영자·관리자 중 secret_editor, 값 조회 불가)
app.include_router(secret_router)
# 회사 AI 설정(고객사 관리자)과 AI 연결 보기(회원 모두): 회사당 키·모델 하나, 키는 끝 글자만
app.include_router(company_ai.admin_router)
app.include_router(company_ai.view_router)
# 도구 연결 › 내 연결: 개인 MCP 연결(본인 세션만)과 MCP 서버의 토큰 검사(에이전트 토큰)
app.include_router(personal_mcp.router)
app.include_router(personal_mcp.check_router)
# 내 AI 도구가 개인 MCP 연결 토큰으로 고른 AI 직원에게 말 거는 창구 (설계 9장·열린 질문 5 (a))
app.include_router(mcp_gateway.router)
# 사람 할 일함: 에이전트가 넣는 창구(에이전트 토큰)와 사람이 보는 창구(읽기·쓰기 모두 사람 토큰)
app.include_router(handoffs.intake)
app.include_router(handoffs.inbox)
# 오피스 단톡방 대화 목록 (단톡방 대화는 모두에게 보인다, 읽기·쓰기 모두 사람 토큰)
app.include_router(office_room.router)
# 시스템 관리 > 회사 연결 (고객사 판매 서버 주소, 읽기·쓰기 모두 사람 토큰)
app.include_router(company_connection.router)
app.include_router(connection_keys_router)
app.include_router(connection_keys_signup_router)  # 로그인 전: 회사 전용 키로 첫 관리자 가입
# 계정·로그인: 사람마다 다른 세션 토큰 (로그인은 토큰 없이, 나머지는 세션 토큰)
app.include_router(accounts_router)
# 로그인 전 화면이 읽는 공개 정보 (설치된 회사 이름만, 토큰 없이. auth.PUBLIC_ROUTES)
app.include_router(public_info.router)
app.include_router(instagram_radar_router)
app.include_router(youtube_meme_keywords_router)  # 11/24 밈 소스: 쇼츠 검색 키워드
app.include_router(promo_style_router)  # 11/24 홍보 영상 방향 (재미 먼저, 브랜드는 끝에)
app.include_router(render_settings_router)  # 11/25 회사별 만들기 설정
app.include_router(youtube_oauth_router)
# 회사 Higgsfield 계정 연결(원격 MCP + OAuth, 구독 크레딧). 콜백만 토큰 없이(auth.PUBLIC_ROUTES)
app.include_router(higgsfield_mcp_router)
app.include_router(instagram_oauth_router)
app.include_router(tool_connections.router)
# 알림 받는 주소: 고객사 판매 서버가 회사 키로 이벤트를 보낸다 (사람 토큰 대신 회사 키)
app.include_router(shop_events.router)
# 판매자 정산: 고객지원(support)이 계산·지급 승인 요청을 한다 (요청은 사람 토큰)
app.include_router(settlements.router)
# 판매 서버 관리 읽기 중계: 화면이 판매 서버를 직접 부르지 않게
# (GET만, 서버 쪽 열람 토큰. 부르는 화면은 사람 토큰)
app.include_router(shop_relay.router)
# 사이트 변환기: 회사 연결이 kind=adapter면 AI들의 shop-api 주소가 여기다
app.include_router(site_adapter_router)
app.include_router(site_adapter_setup_router)
# 회사 연결 › 사이트 쓰기 (W4): 쓰기 짝·받은함 짝·동작별 직접 쓰기·최근 사이트 쓰기
app.include_router(site_adapter_write_router)
# 변화 감지기 상태 (사람 토큰): 고객사 판매 기록을 읽어 바뀐 것만 내부 이벤트로 만든다
app.include_router(change_detector.router)


@app.get("/health")
def health() -> dict:
    return {"data": {"status": "ok"}, "meta": {}}
