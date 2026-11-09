"""qa_findings skill 핸들러 (읽기 전용). 상태를 바꾸지 않아 `execute()`를 거치지 않는다."""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from ax_contracts.schemas import AgentRequest, AgentResult, TaskStatus
from support_agent import handlers as h
from support_agent import qa_rules
from support_agent.qa_schemas import QaFindingsInput

logger = logging.getLogger(__name__)


async def _linked_claims(request: AgentRequest) -> dict[int, list[dict]] | None:
    """클레임 서비스에서 문의별 클레임을 한 번에 읽는다. 못 읽으면 None이다."""
    try:
        reply = await h.SEND(
            "support", AgentRequest(meta=h._peer_meta(request), skill="list_claims", input={})
        )
        if reply.status != TaskStatus.COMPLETED:
            return None
        by_inquiry: dict[int, list[dict]] = {}
        for claim in reply.output.get("items", []):
            if claim.get("conversation_id") is not None:
                by_inquiry.setdefault(claim["conversation_id"], []).append(claim)
        return by_inquiry
    except Exception:  # noqa: BLE001 - 클레임을 못 읽어도 나머지 규칙은 돈다
        logger.warning("클레임을 읽지 못해 unsupported_number를 건너뛴다", exc_info=True)
        return None


async def qa_findings(request: AgentRequest) -> AgentResult:
    """보낸 답변·초안을 코드 규칙으로 점검해 의심 목록과 대조군 샘플을 낸다 (설계 #441)."""
    parsed = h._parse(request, QaFindingsInput)
    if isinstance(parsed, AgentResult):
        return parsed
    if parsed.inquiry_id is not None and h.STORE.get(parsed.inquiry_id) is None:
        return h._not_found(request, parsed.inquiry_id)
    claims = await _linked_claims(request)
    report = qa_rules.build_report(
        h.STORE,
        datetime.now(UTC),
        window_days=parsed.window_days,
        inquiry_id=parsed.inquiry_id,
        sample_size=parsed.sample_size,
        claims_by_inquiry=claims,
        claims_available=claims is not None,
    )
    return h._done(request, report.model_dump(mode="json"))


QA_HANDLERS = {"qa_findings": qa_findings}
