"""총괄 자신의 `/a2a` skill (ORC-01, #272).

지금은 `find_active_incident` 하나다. 고객지원이 문의를 접수할 때 진행 중인 이벤트 번호
(`ctx_inc_`)를 이어받게 하려고 부른다(#274). 읽기 전용이라 이벤트를 열거나 바꾸지 않는다.
"""

from __future__ import annotations

import os
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from fastapi import FastAPI
from pydantic import ValidationError

from ax_agent_template.server import Handler, create_agent_app
from ax_contracts.schemas import (
    AgentCard,
    AgentRequest,
    AgentResult,
    Evidence,
    FindActiveIncidentInput,
    FindActiveIncidentOutput,
    TaskStatus,
)
from ax_contracts.schemas.finance import FinanceOpinion, ReceiveFinanceOpinionOutput
from ax_contracts.schemas.planning import AnswerPlanningQuestionInput
from ax_orchestrator.chat.a2a import CHAT_HANDLERS
from ax_orchestrator.chat.turn import get_chat_service
from ax_orchestrator.manager import IncidentManager
from ax_orchestrator.planning_questions import answer_question

GetManager = Callable[[], IncidentManager | None]


def _card_path() -> Path:
    """`orchestrator/agent-card.json`을 찾는다. 컨테이너와 레포 루트 실행 모두에서 동작한다."""
    override = os.getenv("ORCHESTRATOR_AGENT_CARD")
    candidates = [
        Path(override) if override else None,
        Path.cwd() / "orchestrator" / "agent-card.json",
        Path(__file__).resolve().parents[2] / "agent-card.json",
    ]
    for path in candidates:
        if path and path.is_file():
            return path
    raise FileNotFoundError(
        "agent-card.json을 찾을 수 없다 (ORCHESTRATOR_AGENT_CARD로 경로를 지정한다)"
    )


def load_card() -> AgentCard:
    return AgentCard.model_validate_json(_card_path().read_text(encoding="utf-8"))


def find_active_incident(get_manager: GetManager) -> Handler:
    async def handler(request: AgentRequest) -> AgentResult:
        try:
            parsed = FindActiveIncidentInput.model_validate(request.input)
        except ValidationError as error:
            errors = [f"{'.'.join(map(str, e['loc']))}: {e['type']}" for e in error.errors()]
            return AgentResult(
                meta=request.meta,
                status=TaskStatus.FAILED,
                outcome="invalid_input",
                output={"errors": errors},
            )
        manager = get_manager()
        inc = manager.find_active(parsed.component, parsed.occurred_at) if manager else None
        if inc is None:
            output = FindActiveIncidentOutput(found=False)
            summary = "맞는 이벤트 없음"
        else:
            output = FindActiveIncidentOutput(
                found=True,
                context_id=inc.context_id,
                component=inc.component,
                status=inc.status,  # type: ignore[arg-type]
                opened_at=inc.opened_at,
                closed_at=inc.closed_at,
                recovered_at=inc.recovered_at,
                notice_id=inc.shared.get("notice_id"),  # 점검 공지가 게시됐을 때만, 지어내지 않는다
            )
            summary = f"{inc.component} 이벤트 {inc.status}"
        return AgentResult(
            meta=request.meta,
            status=TaskStatus.COMPLETED,
            outcome="found" if output.found else "not_found",
            output=output.model_dump(mode="json"),
            evidence=Evidence(
                queries=["find_active_incident"],
                data_summary=summary,
                collected_at=datetime.now(UTC),
                ref_ids=[output.context_id] if output.context_id else [],
            ),
        )

    return handler


def answer_planning_question(get_manager: GetManager) -> Handler:
    """기획이 되묻는 근거 질문에 총괄이 보낸 원인 확인 요청의 기록으로 답한다(grade read)."""

    async def handler(request: AgentRequest) -> AgentResult:
        try:
            q = AnswerPlanningQuestionInput.model_validate(request.input)
        except ValidationError as error:
            errors = [f"{'.'.join(map(str, e['loc']))}: {e['type']}" for e in error.errors()]
            return AgentResult(
                meta=request.meta,
                status=TaskStatus.FAILED,
                outcome="invalid_input",
                output={"errors": errors},
            )
        out = answer_question(get_manager(), q, request.meta.context_id)
        return AgentResult(
            meta=request.meta,
            status=TaskStatus.COMPLETED,
            outcome=out.status,
            output=out.model_dump(mode="json"),
            evidence=Evidence(
                queries=[f"orchestrator.cause_check request_id={q.request_id}"],
                data_summary=f"{q.question_id} → {out.status}",
                collected_at=datetime.now(UTC),
                ref_ids=out.evidence_ids,
            ),
        )

    return handler


def receive_finance_opinion(get_manager: GetManager) -> Handler:
    """재무가 보낸 의견을 받아 보관한다. 같은 의견을 다시 받으면 `duplicate`로 답한다."""

    async def handler(request: AgentRequest) -> AgentResult:
        try:
            opinion = FinanceOpinion.model_validate(request.input)
        except ValidationError as error:
            errors = [f"{'.'.join(map(str, e['loc']))}: {e['type']}" for e in error.errors()]
            return AgentResult(
                meta=request.meta,
                status=TaskStatus.FAILED,
                outcome="invalid_input",
                output={"errors": errors},
            )
        manager = get_manager()
        if manager is None:
            return AgentResult(
                meta=request.meta,
                status=TaskStatus.FAILED,
                outcome="not_ready",
                output={"errors": ["총괄이 아직 준비되지 않았다. 나중에 다시 보낸다"]},
            )
        added = manager.finance_opinions.add(opinion, request.meta.context_id)
        return AgentResult(
            meta=request.meta,
            status=TaskStatus.COMPLETED,
            outcome="received" if added else "duplicate",
            output=ReceiveFinanceOpinionOutput(
                opinion_id=opinion.opinion_id, duplicate=not added
            ).model_dump(mode="json"),
            evidence=Evidence(
                queries=[f"finance.opinion opinion_id={opinion.opinion_id}"],
                data_summary=f"재무 의견 {opinion.kind} {'받음' if added else '이미 받음'}",
                collected_at=datetime.now(UTC),
                ref_ids=[opinion.opinion_id],
            ),
        )

    return handler


def build_a2a_app(get_manager: GetManager) -> FastAPI:
    """`/a2a`와 `/.well-known/agent-card.json`을 가진 앱. 총괄 서버 맨 끝에 붙인다."""
    get_chat_service(get_manager)  # 대화가 이벤트 기록을 읽을 수 있게 매니저를 넘겨 둔다
    return create_agent_app(
        load_card(),
        {
            "find_active_incident": find_active_incident(get_manager),
            "answer_planning_question": answer_planning_question(get_manager),
            "receive_finance_opinion": receive_finance_opinion(get_manager),
            **CHAT_HANDLERS,
        },
    )
