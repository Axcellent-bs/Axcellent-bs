"""qa_findings skill 핸들러 (읽기 전용). 상태를 바꾸지 않아 `execute()`를 거치지 않는다."""

from __future__ import annotations

from datetime import UTC, datetime

from ax_contracts.schemas import AgentRequest, AgentResult
from support_agent import handlers as h
from support_agent import qa_rules
from support_agent.qa_schemas import QaFindingsInput


async def qa_findings(request: AgentRequest) -> AgentResult:
    """보낸 답변·초안을 코드 규칙으로 점검해 의심 목록과 대조군 샘플을 낸다 (설계 #441)."""
    parsed = h._parse(request, QaFindingsInput)
    if isinstance(parsed, AgentResult):
        return parsed
    if parsed.inquiry_id is not None and h.STORE.get(parsed.inquiry_id) is None:
        return h._not_found(request, parsed.inquiry_id)
    report = qa_rules.build_report(
        h.STORE,
        datetime.now(UTC),
        window_days=parsed.window_days,
        inquiry_id=parsed.inquiry_id,
        sample_size=parsed.sample_size,
    )
    return h._done(request, report.model_dump(mode="json"))


QA_HANDLERS = {"qa_findings": qa_findings}
