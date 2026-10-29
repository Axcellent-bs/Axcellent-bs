"""ax-agent-data 서버 진입점."""

from __future__ import annotations

import os
from collections.abc import Mapping
from pathlib import Path

from fastapi import FastAPI

from ax_agent_template.llm import LLMSettings
from ax_agent_template.server import create_agent_app
from ax_contracts.schemas import AgentCard
from data_agent.handlers import HANDLERS


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


def build_app() -> FastAPI:
    """카드로 A2A 앱을 만든다. 카드와 핸들러의 skill이 다르면 시작할 때 실패한다.

    `/health`는 템플릿 응답에 `llm_configured`(설명 LLM 설정 여부)를 더한다.
    """
    app = create_agent_app(load_card(), HANDLERS)
    app.router.routes = [r for r in app.router.routes if getattr(r, "path", None) != "/health"]

    @app.get("/health")
    def health() -> dict:
        return {"data": {"status": "ok", "llm_configured": llm_configured()}, "meta": {}}

    return app


# /.well-known/agent-card.json, /a2a 는 템플릿이 만든다. /health는 위에서 바꾼다.
app = build_app()
