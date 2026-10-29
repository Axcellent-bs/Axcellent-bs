"""해결된 문의에 대한 고객 평가(후기) skill: 남기기, 목록, 만족도 요약, 공개·숨김.

- `submit_feedback`(쓰기): 해결된 문의에 별점과 "해결됐는가"와 후기 글을 문의당 한 번 남긴다.
  고객 화면(shop-api)은 본인 문의만, 상담 화면(dashboard-api)은 고객을 대신 시험할 때 쓴다.
- `list_feedback`·`feedback_overview`(읽기): 후기 관리 화면과 만족도 숫자. 숫자는 코드가 센 값이다.
- `set_feedback_status`(쓰기): 확인 대기·공개·숨김을 상담원이 정한다.
후기 글은 고객이 쓴 글이라 `untrusted.comment`로만 받고, 개인정보를 가려 저장한다 (`feedback.py`).
"""

from __future__ import annotations

from typing import Any

from ax_contracts.schemas import AgentRequest, AgentResult
from support_agent import feedback
from support_agent import handlers as h
from support_agent.schemas import (
    Conversation,
    Feedback,
    ListFeedbackInput,
    SetFeedbackStatusInput,
    SubmitFeedbackInput,
)
from support_agent.store import now_iso

STATUS_KO = {"visible": "공개로", "hidden": "숨김으로", "flagged": "확인 대기로"}  # 조사까지 포함


def _item(conversation: Conversation) -> dict[str, Any]:
    """후기 관리 화면의 한 줄: 어느 문의의 후기인지와 후기 자체."""
    return {
        "inquiry_id": conversation.id,
        "customer_name": conversation.customer_name,
        "subject": conversation.subject,
        "category": conversation.category,
        "order_id": conversation.order_id,
        "order_no": conversation.order_no,
        "feedback": conversation.feedback.model_dump(mode="json")
        if conversation.feedback
        else None,
    }


def _invalid_state(request: AgentRequest, message: str) -> AgentResult:
    return h._done(request, {"message": message}, outcome="invalid_state")


@h._caller_meta
async def submit_feedback(request: AgentRequest) -> AgentResult:
    parsed = h._parse(request, SubmitFeedbackInput)
    if isinstance(parsed, AgentResult):
        return parsed
    conversation = h.STORE.get(parsed.inquiry_id)
    if conversation is None or h._not_owned(conversation, parsed.user_id):
        return h._not_found(request, parsed.inquiry_id)
    if conversation.status != "resolved":
        return _invalid_state(request, "해결된 문의에만 평가를 남길 수 있다")
    if conversation.feedback is not None:
        return _invalid_state(request, "이미 평가를 남긴 문의다")
    raw = request.untrusted.get("comment")
    comment = raw if isinstance(raw, str) else ""
    # 고객 화면이 보낸 요청은 늘 고객이 남긴 평가다. 시험용 표시는 상담 화면만 쓸 수 있다
    source = "customer" if request.meta.requester in h.CUSTOMER_GATEWAYS else parsed.source

    async def apply() -> tuple[dict[str, Any], bool]:
        if conversation.feedback is not None:  # 실행을 기다리는 사이 다른 요청이 먼저 남겼다
            return {"inquiry_id": conversation.id, "rated": False}, False
        masked, flags = feedback.assess(comment, parsed.rating, parsed.resolved)
        status = feedback.initial_status(flags)
        saved = Feedback(
            rating=parsed.rating,
            resolved=parsed.resolved,
            comment=masked,
            status=status,  # type: ignore[arg-type]
            flags=flags,
            source=source,  # type: ignore[arg-type]
            created_at=now_iso(),
        )
        h.STORE.set_feedback(conversation.id, saved)
        h.log_activity(
            conversation.id,
            kind="feedback_received",
            actor="customer",
            title="고객이 해결 결과를 평가했어요",
            data={
                "rating": saved.rating,
                "resolved": saved.resolved,
                "status": saved.status,
                "source": saved.source,
                "flags": [f.code for f in flags],
            },
        )
        # 고객에게는 접수 사실만 돌려준다 (표시·공개 여부 같은 내부 판단은 보이지 않는다)
        return {"inquiry_id": conversation.id, "rated": True, "rating": saved.rating}, False

    return await h._write(
        request,
        "submit_feedback",
        f"inquiry:{conversation.id}",
        {"inquiry_id": conversation.id, "rating": parsed.rating},
        apply,
    )


async def list_feedback(request: AgentRequest) -> AgentResult:
    parsed = h._parse(request, ListFeedbackInput)
    if isinstance(parsed, AgentResult):
        return parsed
    rows = [c for c in h.STORE.all() if c.feedback is not None]
    if parsed.status is not None:
        rows = [c for c in rows if c.feedback and c.feedback.status == parsed.status]
    if parsed.max_rating is not None:
        rows = [c for c in rows if c.feedback and c.feedback.rating <= parsed.max_rating]
    rows.sort(key=lambda c: (c.feedback.created_at if c.feedback else "", c.id), reverse=True)
    return h._done(request, {"items": [_item(c) for c in rows[: parsed.limit]], "total": len(rows)})


async def feedback_overview(request: AgentRequest) -> AgentResult:
    return h._done(request, feedback.overview(h.STORE.all()))


@h._caller_meta
async def set_feedback_status(request: AgentRequest) -> AgentResult:
    parsed = h._parse(request, SetFeedbackStatusInput)
    if isinstance(parsed, AgentResult):
        return parsed
    conversation = h.STORE.get(parsed.inquiry_id)
    if conversation is None:
        return h._not_found(request, parsed.inquiry_id)
    if conversation.feedback is None:
        return _invalid_state(request, "평가가 없는 문의다")

    async def apply() -> tuple[dict[str, Any], bool]:
        assert conversation.feedback is not None
        updated = conversation.feedback.model_copy(
            update={
                "status": parsed.status,
                "decided_by": request.meta.requester or "agent_console",
                "decided_at": now_iso(),
            }
        )
        h.STORE.set_feedback(conversation.id, updated)
        h.log_activity(
            conversation.id,
            kind="feedback_status_changed",
            actor="agent",
            title=f"상담원이 후기를 {STATUS_KO[parsed.status]} 정했어요",
            data={"status": parsed.status},
        )
        return _item(h.STORE.get(conversation.id) or conversation), False

    return await h._write(
        request,
        "set_feedback_status",
        f"inquiry:{conversation.id}:{parsed.status}",
        {"inquiry_id": conversation.id, "status": parsed.status},
        apply,
    )


FEEDBACK_HANDLERS = {
    "submit_feedback": h.for_customer_gateway(submit_feedback, needs_user=True),
    "list_feedback": list_feedback,
    "feedback_overview": feedback_overview,
    "set_feedback_status": set_feedback_status,
}
