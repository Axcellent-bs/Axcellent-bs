"""ax-agent-data 서버 진입점."""

from __future__ import annotations

import asyncio
import contextlib
import logging
import os
from collections.abc import AsyncIterator, Mapping
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

from fastapi import FastAPI

from ax_agent_template.client import call_agent
from ax_agent_template.config import agent_token
from ax_agent_template.llm import LLMSettings
from ax_agent_template.server import create_agent_app
from ax_contracts.schemas import AgentCard, RequestMeta
from ax_contracts.schemas.common import TENANT_ID_DEFAULT
from data_agent.clients.log_client import get_log_source
from data_agent.handlers import HANDLERS
from data_agent.monitor import run_snapshots_forever
from data_agent.report_store import Snapshot, get_report_store
from data_agent.skills.activity_report import build_day_snapshot
from data_agent.skills.anomaly_detection import get_metric_source

logger = logging.getLogger("ax.data")
SNAPSHOT_CONTEXT = "ctx_req_data_snapshot"
SNAPSHOT_DEADLINE = timedelta(minutes=2)


def _card_path() -> Path:
    """`agents/data/agent-card.json`을 찾는다. 컨테이너와 레포 루트 실행 모두에서 동작한다."""
    override = os.getenv("DATA_AGENT_CARD")
    candidates = [
        Path(override) if override else None,
        Path.cwd() / "domains" / "data" / "agent-card.json",
        Path(__file__).resolve().parents[2] / "agent-card.json",
    ]
    for path in candidates:
        if path and path.is_file():
            return path
    raise FileNotFoundError("agent-card.json을 찾을 수 없다 (DATA_AGENT_CARD로 경로를 지정한다)")


def load_card() -> AgentCard:
    return AgentCard.model_validate_json(_card_path().read_text(encoding="utf-8"))


def llm_configured(env: Mapping[str, str] | None = None) -> bool:
    """설명 문장용 LLM 설정이 있는지 (`DATA_LLM_*` 또는 공통 `LLM_*`의 모델과 주소).

    없으면 설명은 늘 템플릿이다(`fallback_reason=llm_unavailable`). 키 값은 보지 않는다.
    """
    settings = LLMSettings.from_env("data", env)
    return bool(settings.model and settings.base_url)


def start_report_snapshots(env: Mapping[str, str] | None = None) -> asyncio.Task | None:
    """`DATA_REPORT_DB`가 있으면 업무 리포트 일간 저장 주기를 켠다. 없거나 읽을 곳이 없으면 None.

    지표 감시(`monitor.run_forever`)는 켜지 않는다(지금처럼). 재무는 data 자신의 토큰으로 부른다.
    """
    env = os.environ if env is None else env
    store = get_report_store(env)
    if store is None:
        return None
    try:
        logs = get_log_source(env)
    except NotImplementedError as error:
        logger.warning("업무 리포트 일간 저장을 켜지 않음: %s", error)
        return None
    metrics = get_metric_source(env)

    async def build_day(day: date) -> Snapshot:
        meta = RequestMeta(
            context_id=SNAPSHOT_CONTEXT,
            token=agent_token("data"),
            tenant_id=TENANT_ID_DEFAULT,
            deadline=datetime.now(UTC) + SNAPSHOT_DEADLINE,
        )
        return await build_day_snapshot(day, meta, logs, metrics, call_agent)

    return asyncio.create_task(run_snapshots_forever(store, build_day, TENANT_ID_DEFAULT))


def build_app() -> FastAPI:
    """카드로 A2A 앱을 만든다. 카드와 핸들러의 skill이 다르면 시작할 때 실패한다.

    `/health`는 템플릿 응답에 `llm_configured`(설명 LLM 설정 여부)를 더한다.
    시작할 때 업무 리포트 일간 저장 주기를 켠다(`DATA_REPORT_DB`가 있을 때만).
    """
    app = create_agent_app(load_card(), HANDLERS)
    template_lifespan = app.router.lifespan_context

    @contextlib.asynccontextmanager
    async def lifespan(a: FastAPI) -> AsyncIterator[None]:
        async with template_lifespan(a):
            task = start_report_snapshots()
            try:
                yield
            finally:
                if task:
                    task.cancel()

    app.router.lifespan_context = lifespan
    app.router.routes = [r for r in app.router.routes if getattr(r, "path", None) != "/health"]

    @app.get("/health")
    def health() -> dict:
        return {"data": {"status": "ok", "llm_configured": llm_configured()}, "meta": {}}

    return app


# /.well-known/agent-card.json, /a2a 는 템플릿이 만든다. /health는 위에서 바꾼다.
app = build_app()
