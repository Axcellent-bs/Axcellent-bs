"""총괄 AI 직접 대화 입출력 (dashboard-api ↔ 총괄). who는 dashboard-api가 사람 토큰에서 채운다."""

from __future__ import annotations

from typing import Any

from pydantic import Field

from ax_contracts.schemas.common import AxModel

ORCHESTRATOR_CHAT_MESSAGE_MAX = 2000


class OrchestratorChatSendInput(AxModel):
    who: str = Field(min_length=1, max_length=80)
    thread_id: str | None = Field(default=None, min_length=1, max_length=64)
    message: str = Field(min_length=1, max_length=ORCHESTRATOR_CHAT_MESSAGE_MAX)


class OrchestratorChatListInput(AxModel):
    who: str = Field(min_length=1, max_length=80)
    limit: int = Field(default=50, ge=1, le=100)


class OrchestratorChatRef(AxModel):
    who: str = Field(min_length=1, max_length=80)
    thread_id: str = Field(min_length=1, max_length=64)


class OrchestratorChatThreadOutput(AxModel):
    thread: dict[str, Any]


class OrchestratorChatListOutput(AxModel):
    threads: list[dict[str, Any]]
