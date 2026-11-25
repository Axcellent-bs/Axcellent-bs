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
from ax_agent_template.server import create_agent_app
from ax_contracts.schemas import AgentCard, RequestMeta
from ax_contracts.schemas.common import TENANT_ID_DEFAULT
from data_agent import campaign_update, llm_settings, sales_memory, sales_reads
from data_agent import catalog as metric_catalog
from data_agent.clients.log_client import get_log_source
from data_agent.connectors.shop_api import close_shared_client
from data_agent.handlers import get_sales_store, scoped_handlers, use_sales_store
from data_agent.monitor import run_snapshots_forever
from data_agent.report_store import Snapshot, get_report_store
from data_agent.skills.activity_report import build_day_snapshot
from data_agent.skills.anomaly_detection import get_metric_source
from data_agent.stage_timing import install_sqlite_timer

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
    """설명 문장용 LLM 설정이 있는지 (요청 회사의 AI 모델·주소, 없으면 `DATA_LLM_*`·`LLM_*`).

    없으면 설명은 늘 템플릿이다(`fallback_reason=llm_unavailable`). 키 값은 보지 않는다.
    """
    settings = llm_settings.settings(env)
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
    install_sqlite_timer()  # 트렌드 감지 탭 단계별 시간 로그의 sqlite 몫
    app = create_agent_app(load_card(), scoped_handlers())  # 요청마다 그 회사 (1-5)
    template_lifespan = app.router.lifespan_context

    @contextlib.asynccontextmanager
    async def lifespan(a: FastAPI) -> AsyncIterator[None]:
        async with template_lifespan(a):
            task = start_report_snapshots()
            # 성과 결과를 기획에 보내기 (한 시간마다, DATA_SEND_CAMPAIGN_UPDATE가 꺼져 있으면
            # 기획을 부르지 않음)
            sends = asyncio.create_task(campaign_update.run_forever())
            # 구매자 요약 표 (11/18): 준비되지 않았으면(예전 파일) 시작하며 채운다 (뒤에서)
            summary = asyncio.create_task(
                sales_reads.ensure_summary(get_sales_store(), TENANT_ID_DEFAULT)
            )
            # 판매 분석 미리 계산 (11/18): 수집기가 새 행을 저장하면 기본 조회를 캐시에 넣는다
            warm = (
                asyncio.create_task(
                    sales_reads.run_forever(
                        get_sales_store,
                        metric_catalog.current,
                        TENANT_ID_DEFAULT,
                    )
                )  # fmt: skip
                if sales_reads.warm_enabled()
                else None
            )
            # 원본을 메모리에만 (3-3 A안, DATA_SALES_STORE=memory): 뒤에서 받아 바꿔 끼운다
            memory = (
                asyncio.create_task(sales_memory.run_forever(dict(os.environ), use_sales_store))
                if sales_memory.memory_mode(os.environ)
                else None
            )
            try:
                yield
            finally:
                if task:
                    task.cancel()
                if memory:
                    memory.cancel()
                sends.cancel()
                summary.cancel()
                if warm:
                    warm.cancel()
                await close_shared_client()  # 판매 서버 공용 HTTP 클라이언트 (#909)

    app.router.lifespan_context = lifespan
    app.router.routes = [r for r in app.router.routes if getattr(r, "path", None) != "/health"]

    @app.get("/health")
    def health() -> dict:
        return {"data": {"status": "ok", "llm_configured": llm_configured()}, "meta": {}}

    return app


# /.well-known/agent-card.json, /a2a 는 템플릿이 만든다. /health는 위에서 바꾼다.
app = build_app()
