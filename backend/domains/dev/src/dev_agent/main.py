"""ax-agent-dev 서버 진입점 (개발·배포, 8111).

- 템플릿 `create_agent_app(card, DEV_HANDLERS)`가 `/.well-known/agent-card.json`, `/health`,
  `POST /a2a`를 준다.
- 화면용 REST·SSE(`/api/planning/*`, 공용 + 개발)를 같은 앱에 붙인다. 주소는 기획 서비스와 같은
  접두사를 써서 화면은 그대로 둔다.
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from ax_agent_template import config as template_config
from ax_agent_template.server import create_agent_app
from ax_contracts.schemas import AgentCard
from ax_workflow.auth import AuthError
from dev_agent import hold_resume, llm_key, vcs
from dev_agent.api import create_dev_router
from dev_agent.handlers import DEV_HANDLERS
from dev_agent.runtime import get_service
from dev_agent.vcs import inventory

# 화면: 업무 시스템 dashboard-web 「개발 AI」 탭(3001), 옛 planning-web(3002·5173)
DEFAULT_CORS_ORIGINS = (
    "http://localhost:3001,http://127.0.0.1:3001,"
    "http://localhost:3002,http://localhost:5173,http://127.0.0.1:5173"
)


def _card_path() -> Path:
    """`domains/dev/agent-card.json`을 찾는다. 서비스 카드는 `DEV_AGENT_CARD`(옛 이름
    `PLANNING_AGENT_CARD`)로 지정한다."""
    override = os.getenv("DEV_AGENT_CARD") or os.getenv("PLANNING_AGENT_CARD")
    candidates = [
        Path(override) if override else None,
        Path.cwd() / "domains" / "dev" / "agent-card.json",
        Path(__file__).resolve().parents[2] / "agent-card.json",
    ]
    for path in candidates:
        if path and path.is_file():
            return path
    raise FileNotFoundError("agent-card.json을 찾을 수 없다 (DEV_AGENT_CARD로 경로를 지정한다)")


def load_card() -> AgentCard:
    return AgentCard.model_validate_json(_card_path().read_text(encoding="utf-8"))


def _cors_origins() -> list[str]:
    raw = os.getenv("PLANNING_CORS_ORIGINS", DEFAULT_CORS_ORIGINS)
    return [o.strip() for o in raw.split(",") if o.strip()]


# 화면에서 넣어 둔 형상관리 연결을 다시 읽는다 (PLANNING_VCS_STORE가 있을 때)
vcs.load_saved()
# 화면에서 넣어 둔 고객사 API 키(BYOK)를 다시 읽는다 (PLANNING_LLM_KEY_STORE가 있을 때)
llm_key.load_saved()
app = create_agent_app(load_card(), DEV_HANDLERS)
app.include_router(create_dev_router(get_service))
# 관문이 보류한 액션: 사람 결정 이벤트를 받아 이어서 한다 (REDIS_URL이 없으면 폴링만)
hold_resume.setup_subscriber(get_service)


async def _resync_peer_on_start() -> None:
    """켤 때 기획 서비스와 맞춘다. 기획이 뜰 때까지(최대 5분) 기다린 뒤, 받았다고 하지 않았거나
    기획이 갖고 있지 않은 넘기기만 보낸다. 끝내 안 뜨면 못 보낸 것으로 남겨 다시 보낸다."""
    try:
        await get_service().resync_peer(wait_peer=True)
    except Exception:  # 켤 때 뒷일이라 서버를 멈추지 않는다
        logging.getLogger("ax.dev").exception("기획 서비스와 맞추기 실패")


def _runs_loops() -> bool:
    """주기 루프(PR 맞추기·연결 대장)를 켤지. 목 모드(시험 전용)에서는 켜지 않는다.

    시험이 `with TestClient(app)`로 앱을 켤 때마다 대화 되돌리기·형상관리 읽기가 돌지 않게 한다."""
    return not template_config.is_mock("dev")


def _start_loops() -> Callable[[], None]:
    """켤 때 도는 주기 루프를 띄우고 끄는 함수를 돌려준다.

    - PR 맞추기 루프(`start_pr_sync`): 형상관리에서 직접 머지·닫은 PR 맞추기, CI 다시 보기, 멈춘
      작업 다시 하기, 구독형 작업 되돌리기, 사람이 승인한 보류 건 잇기, 대화 손보기.
    - 연결 대장(`inventory.start_daily`): 켤 때 한 번, 그 뒤 하루마다 서버 버전·토큰 권한 읽기.
    둘 다 이미 돌고 있으면 새로 띄우지 않는다 (프로세스에 하나)."""
    if not _runs_loops():
        return lambda: None
    svc = get_service()
    svc.start_pr_sync()
    inventory.start_daily()

    def stop() -> None:
        svc.stop_pr_sync()
        inventory.stop_daily()

    return stop


# 템플릿 앱은 `lifespan`을 써서 `on_startup`이 실행되지 않는다 (기획 main과 같다, 11/23 확인).
# 켤 때 도는 일(기획과 맞추기·PR 맞추기 루프·연결 대장)은 템플릿 lifespan을 감싸 띄우고
# 끌 때 멈춘다
_template_lifespan = app.router.lifespan_context


@asynccontextmanager
async def _lifespan(a: Any) -> AsyncIterator[None]:
    async with _template_lifespan(a):
        task = asyncio.get_running_loop().create_task(_resync_peer_on_start())
        stop_loops = _start_loops()
        try:
            yield
        finally:
            task.cancel()
            stop_loops()


app.router.lifespan_context = _lifespan


@app.exception_handler(AuthError)
async def _auth_error(_: Request, exc: AuthError) -> JSONResponse:
    # 쓰기 토큰 거부도 화면이 읽는 `{error: {code, message}}` 모양으로 준다
    return JSONResponse(
        status_code=exc.status_code,
        content={"error": {"code": exc.code, "message": exc.detail}},
    )


app.add_middleware(
    CORSMiddleware,
    allow_origins=_cors_origins(),
    allow_methods=["GET", "POST"],
    allow_headers=["*"],
)
