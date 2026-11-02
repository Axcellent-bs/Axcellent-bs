"""기획에 보낼 변경 요청 제안 skill 핸들러 (읽기 전용).

급증 감지 결과로 제안만 만든다. 상태를 바꾸지 않고 기획으로 아무것도 보내지 않으므로
`execute()`를 거치지 않는다. 보내는 일은 사람이 고른 뒤 `request_change`가 한다.
"""

from __future__ import annotations

from datetime import UTC, datetime

from ax_contracts.schemas import AgentRequest, AgentResult
from support_agent import handlers as h
from support_agent import insights, proposal_rules
from support_agent.proposal_schemas import ProposeChangesInput


async def propose_change_requests(request: AgentRequest) -> AgentResult:
    """분류별 문의 급증에서 기획에 보낼 변경 요청안을 만든다 (보내지 않는다)."""
    parsed = h._parse(request, ProposeChangesInput)
    if isinstance(parsed, AgentResult):
        return parsed
    report = insights.detect_surge(
        h.STORE,
        datetime.now(UTC),
        window_minutes=parsed.window_minutes,
        baseline_hours=parsed.baseline_hours,
        min_count=parsed.min_count,
        ratio=parsed.ratio,
        baseline_method=parsed.baseline_method,
    )
    out = proposal_rules.build(report, has_request=lambda i: bool(h.CHANGES.for_inquiry(i)))
    return h._done(request, out.model_dump(mode="json"))


PROPOSAL_HANDLERS = {"propose_change_requests": propose_change_requests}
