"""분류 필요 문의 skill: `set_triage`(쓰기, A).

- `send`: 상담원이 문의를 "분류 필요"로 올린다(`reason=manual`).
- `decide`: 분류 이름(`label`, 예: "장바구니 화면 오류")을 정하고 마친다. 먼저 올라가 있어야 한다.
- `clear`: 표시를 지운다(잘못 올렸을 때).
자동으로 올리는 일은 접수 때 `triage.mark_automatically`가 한다. 읽기는 `list_inquiries`가 주는
`triage`·`tenant_id`로 화면이 모은다(회사별·상태별). 상태 변경이라 `execute()` 관문을 거친다.
고객 원문은 읽지 않고, 분류 이름에 개인정보가 있으면 거절한다.
"""

from __future__ import annotations

import hashlib

from ax_agent_template.server import error_result
from ax_contracts.schemas import AgentRequest, AgentResult, TaskStatus
from support_agent import guard, triage
from support_agent import handlers as h
from support_agent.schemas import SetTriageInput, Triage
from support_agent.store import now_iso


def _label_digest(label: str | None) -> str:
    return hashlib.sha1((label or "").encode()).hexdigest()[:8]


async def set_triage(request: AgentRequest) -> AgentResult:
    parsed = h._parse(request, SetTriageInput)
    if isinstance(parsed, AgentResult):
        return parsed
    conversation = h.STORE.get(parsed.inquiry_id)
    if conversation is None:
        return h._not_found(request, parsed.inquiry_id)
    label = (parsed.label or "").strip() or None
    if parsed.action == "decide":
        if label is None:
            return error_result(
                request.meta, TaskStatus.FAILED, "invalid_input", "분류 이름(label)이 필요하다"
            )
        if guard.find_pii(label):  # 처리 내력에 개인정보가 남지 않게 막는다
            return error_result(
                request.meta, TaskStatus.FAILED, "invalid_input", "분류 이름에 개인정보가 있다"
            )
        if conversation.triage is None:
            return h._done(
                request,
                {"message": "분류 필요로 올린 문의만 분류를 정할 수 있다"},
                outcome="invalid_state",
            )
    request = h.incident_context.follow_inquiry(request, conversation.context_id)

    async def apply() -> tuple[dict, bool]:
        current = h.STORE.get(parsed.inquiry_id)
        assert current is not None
        if parsed.action == "send":
            h.STORE.set_triage(parsed.inquiry_id, triage.needed("manual", "agent"))
            h.log_activity(
                parsed.inquiry_id,
                kind="triage_needed",
                actor="agent",
                title=triage.REASON_TITLE["manual"],
                data={"reason": "manual"},
            )
        elif parsed.action == "decide":
            assert current.triage is not None
            done = Triage(
                state="done",
                reason=current.triage.reason,
                marked_by=current.triage.marked_by,
                marked_at=current.triage.marked_at,
                label=label,
                decided_at=now_iso(),
            )
            h.STORE.set_triage(parsed.inquiry_id, done)
            h.log_activity(
                parsed.inquiry_id,
                kind="triage_decided",
                actor="agent",
                title="상담원이 분류를 정하고 마쳤어요",
                detail=label or "",
                data={"label": label},
            )
        else:
            h.STORE.set_triage(parsed.inquiry_id, None)
            h.log_activity(
                parsed.inquiry_id,
                kind="triage_cleared",
                actor="agent",
                title="상담원이 분류 필요 표시를 지웠어요",
            )
        updated = h.STORE.get(parsed.inquiry_id)
        assert updated is not None
        return h._view(updated), False

    return await h._write(
        request,
        "set_triage",
        f"inquiry:{parsed.inquiry_id}:{parsed.action}:{_label_digest(label)}",
        {"inquiry_id": parsed.inquiry_id, "action": parsed.action},
        apply,
    )


TRIAGE_HANDLERS = {"set_triage": set_triage}
