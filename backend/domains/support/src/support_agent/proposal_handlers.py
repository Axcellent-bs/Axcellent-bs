"""기획에 보낼 변경 요청 제안 skill 핸들러.

- `propose_change_requests`(읽기): 급증 감지 결과로 제안만 만든다. 상태를 바꾸지 않고 기획으로
  아무것도 보내지 않으므로 `execute()`를 거치지 않는다.
- `decline_change_proposal`(A): 상담원이 "보내지 않기"를 고르면 이유와 결정한 사람만 남긴다.
  기획으로는 아무것도 나가지 않는다. 쓰기라서 `execute()`(안전 관문)를 거친다.

보내기는 사람이 고른 뒤 `request_change`가 한다.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from ax_agent_template.server import error_result
from ax_contracts.schemas import AgentRequest, AgentResult, TaskStatus
from support_agent import guard, insights, proposal_rules
from support_agent import handlers as h
from support_agent.proposal_decisions import NOTE_LIMIT
from support_agent.proposal_schemas import (
    DeclineProposalInput,
    DeclineProposalOutput,
    ProposeChangesInput,
)


async def propose_change_requests(request: AgentRequest) -> AgentResult:
    """분류별 문의 급증에서 기획에 보낼 변경 요청안을 만든다 (보내지 않는다)."""
    parsed = h._parse(request, ProposeChangesInput)
    if isinstance(parsed, AgentResult):
        return parsed
    now = datetime.now(UTC)  # 급증 감지와 원인 이름 세기가 같은 시각을 쓴다
    report = insights.detect_surge(
        h.STORE,
        now,
        window_minutes=parsed.window_minutes,
        baseline_hours=parsed.baseline_hours,
        min_count=parsed.min_count,
        ratio=parsed.ratio,
        baseline_method=parsed.baseline_method,
    )
    out = proposal_rules.build(
        report,
        has_request=lambda i: bool(h.CHANGES.for_inquiry(i)),
        decision_for=h.DECISIONS.get,
        labels_for=lambda topic: insights.triage_label_counts(
            h.STORE, topic, now, window_minutes=parsed.window_minutes
        ),
    )
    return h._done(request, out.model_dump(mode="json"))


async def decline_change_proposal(request: AgentRequest) -> AgentResult:
    """제안을 보내지 않기로 한 이유를 남긴다. 같은 제안에 두 번 해도 한 번만 기록된다."""
    parsed = h._parse(request, DeclineProposalInput)
    if isinstance(parsed, AgentResult):
        return parsed
    if h.STORE.get(parsed.inquiry_id) is None:
        return h._not_found(request, parsed.inquiry_id)
    raw = request.untrusted.get("note")
    if raw is not None and not isinstance(raw, str):
        return error_result(
            request.meta, TaskStatus.FAILED, "invalid_input", "note는 글이어야 한다"
        )
    note = raw.strip() if isinstance(raw, str) else ""
    if len(note) > NOTE_LIMIT:
        return error_result(
            request.meta, TaskStatus.FAILED, "invalid_input", f"메모는 {NOTE_LIMIT}자 이하여야 한다"
        )
    if guard.find_pii(note):  # 기록에 개인정보가 남지 않게 막는다
        return error_result(
            request.meta, TaskStatus.FAILED, "invalid_input", "메모에 개인정보가 있다"
        )

    async def apply() -> tuple[dict[str, Any], bool]:
        decision, created = h.DECISIONS.decline(
            parsed.topic_code,
            parsed.inquiry_id,
            parsed.reason,
            context_id=request.meta.context_id,
            decided_by=parsed.author.strip(),
            note=note or None,
            tenant_id=request.meta.tenant_id,
        )
        output = DeclineProposalOutput(
            topic_code=decision.topic_code,
            inquiry_id=decision.inquiry_id,
            reason=decision.reason,
            decided_by=decision.decided_by,
            created=created,
        )
        return output.model_dump(mode="json"), False

    return await h._write(
        request,
        "decline_change_proposal",
        f"inquiry:{parsed.inquiry_id}:decline:{parsed.topic_code}",
        {
            "inquiry_id": parsed.inquiry_id,
            "topic_code": parsed.topic_code,
            "reason": parsed.reason,
        },
        apply,
    )


PROPOSAL_HANDLERS = {
    "propose_change_requests": propose_change_requests,
    "decline_change_proposal": decline_change_proposal,
}
