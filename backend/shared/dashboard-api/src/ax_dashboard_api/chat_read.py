"""부서 AI 대화 상세 읽기 (dashboard-api가 중계하는 8곳: marketing·planning·finance·orchestrator·
data·infra·dev·support).

단톡방(전체 대화방·이슈 방)에 올라온 대화는 로그인한 사람 누구나 원문을 읽는다(11/20).
- 단톡방 줄(office_room.db, 숨기지 않음, 건 사람 이름 있음)이 있으면 부서에 건 사람 이름
  (`read_who`)으로 묻는다. 부서의 열람 규칙(본인만 원문)은 그대로라 1:1 대화는 지금처럼 본인만 본다
- 응답에 `shared`(단톡방 대화인지)·`owner`(건 사람)를 더한다. 화면이 "재현 → @총괄"처럼 그린다
- 이어 쓰기·끝내기·지우기는 이 함수를 쓰지 않는다(요청한 사람 그대로, 부서의 본인 검사가 막는다)
"""

from __future__ import annotations

from typing import Any

from ax_dashboard_api import (
    data_chat,
    dev_chat,
    finance_chat,
    infra_chat,
    marketing_client,
    orchestrator_chat,
    planning_chat,
    support_chat,
    support_client,
)
from ax_dashboard_api.auth import human_from_authorization
from ax_dashboard_api.envelope import ApiError
from ax_dashboard_api.office_room import shared_owner

_CALLS = {
    "data": data_chat.call,
    "dev": dev_chat.call,
    "finance": finance_chat.call,
    "infra": infra_chat.call,
    "orchestrator": orchestrator_chat.call,
    "planning": planning_chat.call,
    "marketing": marketing_client.chat_call,
}


async def get_thread(agent: str, thread_id: str, who: str,
                     authorization: str | None) -> dict[str, Any]:  # fmt: skip
    """부서 대화 하나 (부서 열람 규칙 그대로: `who` 본인이면 원문, 아니면 restricted)."""
    if agent == "support":
        _, role = support_chat.viewer_of(authorization)
        out = await support_client.call(
            "support_chat_get", {"thread_id": thread_id, "viewer": who, "role": role}
        )
        return out if isinstance(out, dict) else {}
    call = _CALLS.get(agent)
    if call is None:
        raise ApiError(404, "NOT_FOUND", f"모르는 부서다 ({agent})")
    out = await call(f"{agent}_chat_get", {"who": who, "thread_id": thread_id})
    thread = out.get("thread") if isinstance(out, dict) else None
    return thread if isinstance(thread, dict) else {}


async def read(agent: str, thread_id: str, authorization: str | None) -> dict[str, Any]:
    """상세 라우트가 돌려줄 대화.

    단톡방 대화면 건 사람 이름으로 원문을 읽고 shared·owner를 붙인다."""
    who = human_from_authorization(authorization)
    owner = shared_owner(agent, thread_id)
    thread = await get_thread(agent, thread_id, owner or who, authorization)
    return {**thread, "shared": owner is not None, "owner": owner}
