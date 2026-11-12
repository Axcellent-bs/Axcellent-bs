"""ax-agent-support 서버 진입점."""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI

from ax_agent_template import config
from ax_agent_template.server import create_agent_app
from ax_contracts.schemas import AgentCard
from support_agent import inquiry_pull, receipt_vision, settlement_books
from support_agent.audit_handlers import AUDIT_HANDLERS
from support_agent.change_handlers import CHANGE_HANDLERS
from support_agent.chat_handlers import CHAT_HANDLERS
from support_agent.claim_analysis import ANALYSIS_HANDLERS
from support_agent.claim_handlers import CLAIM_HANDLERS
from support_agent.feedback_handlers import FEEDBACK_HANDLERS
from support_agent.gap_handlers import GAP_HANDLERS
from support_agent.handlers import HANDLERS
from support_agent.handoff_handlers import HANDOFF_HANDLERS
from support_agent.idea_handlers import IDEA_HANDLERS
from support_agent.incident_handlers import INCIDENT_HANDLERS
from support_agent.incident_queue_handlers import INCIDENT_QUEUE_HANDLERS
from support_agent.inquiry_pull_handlers import INQUIRY_PULL_HANDLERS
from support_agent.insight_handlers import INSIGHT_HANDLERS
from support_agent.original_handlers import ORIGINAL_HANDLERS
from support_agent.payment_handlers import PAYMENT_HANDLERS
from support_agent.planning_question_handlers import PLANNING_QUESTION_HANDLERS
from support_agent.privacy_handlers import PRIVACY_HANDLERS
from support_agent.proposal_handlers import PROPOSAL_HANDLERS
from support_agent.purge_handlers import PURGE_HANDLERS
from support_agent.qa_handlers import QA_HANDLERS
from support_agent.resolution_note_handlers import RESOLUTION_NOTE_HANDLERS
from support_agent.settlement_handlers import SETTLEMENT_HANDLERS
from support_agent.signal_handlers import SIGNAL_HANDLERS
from support_agent.suggest_handlers import SUGGEST_HANDLERS
from support_agent.triage_handlers import TRIAGE_HANDLERS


def _card_path() -> Path:
    """`agents/support/agent-card.json`을 찾는다. 컨테이너와 레포 루트 실행 모두에서 동작한다."""
    override = os.getenv("SUPPORT_AGENT_CARD")
    if override:
        # 서비스 카드를 지정했는데 없으면 공용 카드(전부 맡음)로 떨어지지 않고 서버를 띄우지 않는다
        path = Path(override)
        if not path.is_file():
            raise FileNotFoundError(f"SUPPORT_AGENT_CARD가 가리키는 카드가 없다: {override}")
        return path
    candidates = [
        Path.cwd() / "domains" / "support" / "agent-card.json",
        Path(__file__).resolve().parents[2] / "agent-card.json",
    ]
    for path in candidates:
        if path and path.is_file():
            return path
    raise FileNotFoundError("agent-card.json을 찾을 수 없다 (SUPPORT_AGENT_CARD로 경로를 지정한다)")


def load_card() -> AgentCard:
    return AgentCard.model_validate_json(_card_path().read_text(encoding="utf-8"))


ALL_HANDLERS = {
    **HANDLERS,
    **CLAIM_HANDLERS,
    **FEEDBACK_HANDLERS,
    **RESOLUTION_NOTE_HANDLERS,
    **ORIGINAL_HANDLERS,
    **INSIGHT_HANDLERS,
    **PROPOSAL_HANDLERS,
    **PLANNING_QUESTION_HANDLERS,
    **INQUIRY_PULL_HANDLERS,
    **IDEA_HANDLERS,
    **CHANGE_HANDLERS,
    **ANALYSIS_HANDLERS,
    **SUGGEST_HANDLERS,
    **INCIDENT_HANDLERS,
    **INCIDENT_QUEUE_HANDLERS,
    **PAYMENT_HANDLERS,
    **HANDOFF_HANDLERS,
    **GAP_HANDLERS,
    **QA_HANDLERS,
    **TRIAGE_HANDLERS,
    **AUDIT_HANDLERS,
    **PRIVACY_HANDLERS,
    **SIGNAL_HANDLERS,
    **CHAT_HANDLERS,
    **PURGE_HANDLERS,
    **SETTLEMENT_HANDLERS,
}
# 영수증 사진 분석기: 이미지를 읽을 모델이 설정돼 있을 때만 꽂는다(목 모드·미설정이면 꽂지 않는다)
receipt_vision.connect()
_CARD = load_card()
app = create_agent_app(_CARD, ALL_HANDLERS)
_OWNED_SKILLS = frozenset(skill.id for skill in _CARD.skills)  # 이 서버가 받는 skill

# 판매 서버 문의함을 정해진 간격으로 읽는 루프를 앱 시작·종료에 맞춰 돌린다 (꺼져 있으면 안 돈다)
_app_lifespan = app.router.lifespan_context


@asynccontextmanager
async def _lifespan_with_inquiry_pull(application: FastAPI) -> AsyncIterator[None]:
    async with _app_lifespan(application):
        task = inquiry_pull.start(_OWNED_SKILLS)
        # 판매자 정산을 맡은 서비스(settlement)는 켤 때 뒤에서 장부를 모으고 계산해 둔다(목 모드는
        # 안
        # 함)
        if "seller_settlement" in _OWNED_SKILLS and not config.is_mock("support"):
            settlement_books.prime()
        try:
            yield
        finally:
            inquiry_pull.stop(task)


app.router.lifespan_context = _lifespan_with_inquiry_pull
