"""해결된 문의의 해결 메모 skill.

- `add_resolution_note`(쓰기): 해결된 문의에 상담원이 원인·조치·결과를 남긴다. 메모는 쌓기만 하고
  고치거나 지우지 않는다. 글은 `untrusted`로만 받고, 작성자는 호출한 화면(dashboard-api)이
  사람 토큰에서 채워 보낸 이름이다. 읽기는 `get_inquiry`가 문의와 함께 돌려주는
  `resolution_notes`(최신 먼저)를 쓴다.
"""

from __future__ import annotations

from typing import Any

from ax_agent_template.server import error_result
from ax_contracts.schemas import AgentRequest, AgentResult, TaskStatus
from support_agent import handlers as h
from support_agent import resolution_notes
from support_agent.schemas import AddResolutionNoteInput, ResolutionNote
from support_agent.store import now_iso


@h._caller_meta
async def add_resolution_note(request: AgentRequest) -> AgentResult:
    parsed = h._parse(request, AddResolutionNoteInput)
    if isinstance(parsed, AgentResult):
        return parsed
    conversation = h.STORE.get(parsed.inquiry_id)
    if conversation is None:
        return h._not_found(request, parsed.inquiry_id)
    if conversation.status != "resolved":
        return h._done(
            request,
            {"message": "해결된 문의에만 해결 메모를 남길 수 있다"},
            outcome="invalid_state",
        )
    values = {
        key: resolution_notes.clean(request.untrusted.get(key)) for key in resolution_notes.FIELDS
    }
    reason = resolution_notes.problem(values)
    if reason is not None:
        return error_result(request.meta, TaskStatus.FAILED, "invalid_input", reason)
    texts, masked_kinds = resolution_notes.mask(values)

    async def apply() -> tuple[dict[str, Any], bool]:
        note = ResolutionNote(
            id=len(conversation.resolution_notes) + 1,
            author=parsed.author.strip(),
            created_at=now_iso(),
            **texts,
        )
        h.STORE.add_resolution_note(conversation.id, note)
        h.log_activity(
            conversation.id,
            kind="resolution_note_added",
            actor="agent",
            title="상담원이 해결 메모를 남겼어요",
            data={"note_id": note.id, "author": note.author, "masked": masked_kinds},
        )
        return {"inquiry_id": conversation.id, "note_id": note.id}, False

    return await h._write(
        request,
        "add_resolution_note",
        f"inquiry:{conversation.id}",
        {"inquiry_id": conversation.id, "author": parsed.author.strip()},
        apply,
    )


RESOLUTION_NOTE_HANDLERS = {
    "add_resolution_note": add_resolution_note,
}
