"""로그·오류 코드로 원인을 알 수 없는 문의를 "분류 필요"로 모은다 (이슈: 분류 필요 문의 관리).

예: "장바구니 창이 안 열려서 결제가 안 돼요"는 결제 문의지만 연결된 주문에 실패한 결제 기록(오류
코드)이 없다. 이런 문의는 자동 진단(`diagnose_payment`)이 원인을 못 찾으므로 상담원이 따로 보고
원인 이름(분류)을 정해야 한다.

규칙(코드만, LLM 없음, 고객 원문을 읽지 않는다)
- `unclassified`: 문의 분류가 어느 코드에도 맞지 않아 기본값(`general`)이다.
- `no_error_code`: 문의 분류가 결제(`payment`)인데 연결된 주문이 없거나, 주문에 실패한 결제 시도
  (오류 코드)가 하나도 없다. 진행 중인 사건(`ctx_inc_`)에 이어진 문의는 원인이 이미 사건으로
  잡혀 있으니 올리지 않는다.
- 이미 `triage`가 있으면(상담원이 올렸거나 분류를 마침) 자동으로 덮어쓰지 않는다.
- 주문을 읽지 못하면(shop-api 오류·시드 주문) 원인이 없다고 단정하지 않고 올리지 않는다.
  `payment`인데 주문이 아예 없을 때만 `no_error_code`로 올린다.
- 접수를 막지 않는다. 판정 중 오류는 로그만 남기고 표시 없이 지나간다.
"""

from __future__ import annotations

import logging
from typing import Any

from support_agent import shop_client
from support_agent.incident_context import INCIDENT_PREFIX
from support_agent.schemas import Conversation, Triage, TriageReason
from support_agent.store import now_iso

logger = logging.getLogger("ax.support.triage")

REASON_TITLE: dict[str, str] = {
    "no_error_code": "오류 코드가 없는 문의라 분류 필요로 올렸어요",
    "unclassified": "어느 분류에도 맞지 않아 분류 필요로 올렸어요",
    "manual": "상담원이 분류 필요로 올렸어요",
}


def needed(reason: TriageReason, by: str = "auto") -> Triage:
    return Triage(
        state="needed",
        reason=reason,
        marked_by="auto" if by == "auto" else "agent",
        marked_at=now_iso(),
    )


def _failed_attempts(order_facts: dict[str, Any]) -> list[dict[str, Any]]:
    return [p for p in order_facts.get("payments", []) if p.get("status") == "failed"]


async def assess(conversation: Conversation) -> TriageReason | None:
    """자동으로 올릴 사유. 올리지 않으면 `None`."""
    if conversation.triage is not None:
        return None
    category = conversation.category
    if category == "general":
        return "unclassified"
    if category != "payment":
        return None
    if conversation.context_id.startswith(INCIDENT_PREFIX):
        return None  # 이미 진행 중인 사건에 이어진 문의다
    if not conversation.order_id:
        return "no_error_code"
    if shop_client.order_source() == "seed":
        return None
    try:
        facts = await shop_client.fetch_order_facts(conversation.order_id)
    except shop_client.ShopError:
        return None
    if facts is None:
        return "no_error_code"
    return None if _failed_attempts(facts) else "no_error_code"


async def mark_automatically(conversation: Conversation) -> Triage | None:
    """접수 직후 자동 판정. 올릴 문의면 표시와 내력을 남기고 표시를 돌려준다. 오류는 삼킨다."""
    from support_agent import handlers as h  # 순환 import를 피해 쓸 때 가져온다

    try:
        reason = await assess(conversation)
        if reason is None:
            return None
        triage = needed(reason)
        h.STORE.set_triage(conversation.id, triage)
        h.log_activity(
            conversation.id,
            kind="triage_needed",
            actor="system",
            title=REASON_TITLE[reason],
            data={"reason": reason},
        )
        return triage
    except Exception:  # noqa: BLE001 - 접수를 막지 않는다
        logger.warning("분류 필요 판정 실패 (inquiry %s)", conversation.id, exc_info=False)
        return None
