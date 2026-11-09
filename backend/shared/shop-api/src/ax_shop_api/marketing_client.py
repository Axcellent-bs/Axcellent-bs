"""마케팅(marketing) 받은 기획안 읽기 중계 (A2A). 관리자 화면(shop-web)이 shop-api만 부르게 한다.

- 읽기 skill `list_campaign_briefs`·`get_campaign_brief`만 부른다(등급 read, 상태를 바꾸지 않는다).
  요청자는 `shop-api`이고 두 skill의 요청 가능 목록에 들어 있다.
- 요청마다 새 사건 ID `ctx_shop_<hex>`를 붙인다. 읽기라 멱등 키는 쓰지 않는다.
- 마케팅이 꺼져 있거나 느려도 shop-api는 죽지 않고 `ShopError`로 답한다.
- 기획안 내용은 마케팅이 보관한 그대로 전달한다(값을 만들거나 바꾸지 않는다).
"""

from __future__ import annotations

import re
import uuid
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime, timedelta
from typing import Any

from ax_agent_template import config
from ax_agent_template.client import call_agent
from ax_contracts.schemas import AgentRequest, AgentResult, RequestMeta, TaskStatus
from ax_shop_api.service import ShopError

AGENT_NAME = "shop-api"
CONTEXT_PREFIX = "ctx_shop_"
TARGET = "marketing"
DEADLINE_SECONDS = 10
LIST_LIMIT_MAX = 100
PROPOSAL_ID = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_-]{0,63}$")

SendFn = Callable[[str, AgentRequest], Awaitable[AgentResult]]
SEND: SendFn = call_agent  # 테스트에서 바꿀 수 있다


async def _call(skill: str, input: dict[str, Any]) -> dict[str, Any]:
    with config.agent_name_scope(AGENT_NAME):
        request = AgentRequest(
            meta=RequestMeta(
                context_id=f"{CONTEXT_PREFIX}{uuid.uuid4().hex[:12]}",
                token=config.agent_token(AGENT_NAME),
                deadline=datetime.now(UTC) + timedelta(seconds=DEADLINE_SECONDS),
            ),
            skill=skill,
            input=input,
        )
        result = await SEND(TARGET, request)
    if result.status == TaskStatus.COMPLETED:
        return result.output
    if result.status == TaskStatus.CANCELED:
        raise ShopError("UPSTREAM_TIMEOUT", "마케팅이 제때 응답하지 않았다", 504)
    if result.status == TaskStatus.REJECTED:
        raise ShopError("UPSTREAM_REJECTED", f"마케팅이 요청을 거부했다 ({result.outcome})", 403)
    raise ShopError(
        "UPSTREAM_ERROR", f"마케팅 호출 실패 ({result.outcome or result.status.value})", 502
    )


async def list_briefs(limit: int = 50) -> dict[str, Any]:
    """받은 기획안 목록(받은 시각 늦은 순). `{items, total, unknown_reason}`."""
    if not 1 <= limit <= LIST_LIMIT_MAX:
        raise ShopError("INVALID_LIMIT", f"limit은 1~{LIST_LIMIT_MAX}이어야 한다", 400)
    return await _call("list_campaign_briefs", {"limit": limit})


async def get_brief(proposal_id: str) -> dict[str, Any]:
    """기획안 하나. 없으면 404."""
    if not PROPOSAL_ID.match(proposal_id):
        raise ShopError("INVALID_PROPOSAL_ID", "기획안 번호 형식이 올바르지 않다", 400)
    view = await _call("get_campaign_brief", {"proposal_id": proposal_id})
    if not view.get("found") or view.get("item") is None:
        raise ShopError("NOT_FOUND", "받은 기획안을 찾을 수 없다", 404)
    return view["item"]
