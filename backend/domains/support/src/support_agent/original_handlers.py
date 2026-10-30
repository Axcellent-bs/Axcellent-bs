"""욕설로 가린 고객 메시지의 원문 열기 skill.

- `reveal_original`: 상담 콘솔은 욕설·모욕·위협이 있는 고객 메시지의 원문(`text`)을 응답에서 뺀다.
  상담원이 "원문 보기"를 누르면 이 skill이 원문을 돌려주고, 누가 열었는지를 문의의 AI 처리
  내력에 남긴다 (원문은 내력에 싣지 않는다). 가려진 메시지가 아니면 원문을 주지 않는다.
"""

from __future__ import annotations

from typing import Any

from ax_contracts.schemas import AgentRequest, AgentResult
from support_agent import handlers as h
from support_agent.schemas import RevealOriginalInput


@h._caller_meta
async def reveal_original(request: AgentRequest) -> AgentResult:
    parsed = h._parse(request, RevealOriginalInput)
    if isinstance(parsed, AgentResult):
        return parsed
    conversation = h.STORE.get(parsed.inquiry_id)
    if conversation is None:
        return h._not_found(request, parsed.inquiry_id)
    message = next((m for m in conversation.messages if m.id == parsed.message_id), None)
    if message is None or message.role != "customer" or message.abuse_level == "none":
        return h._done(
            request, {"message": "원문을 가린 고객 메시지가 아니다"}, outcome="not_found"
        )

    async def apply() -> tuple[dict[str, Any], bool]:
        h.log_activity(
            conversation.id,
            kind="original_viewed",
            actor="agent",
            title="상담원이 가려진 고객 메시지의 원문을 열어 봤어요",
            data={"message_id": message.id, "viewer": parsed.viewer.strip()},
        )
        return {
            "inquiry_id": conversation.id,
            "message_id": message.id,
            "text": message.text,
        }, False

    return await h._write(
        request,
        "reveal_original",
        f"inquiry:{conversation.id}:message:{message.id}",
        {"inquiry_id": conversation.id, "viewer": parsed.viewer.strip()},
        apply,
    )


ORIGINAL_HANDLERS = {
    "reveal_original": reveal_original,
}
