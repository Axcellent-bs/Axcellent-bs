"""부서 AI 직접 대화 지우기 (요청한 사람 본인 대화만).

- `POST /api/dashboard/<부서>-chat/{thread_id}/remove`(routes.py)와
  단톡방 삭제(office_room.py)가 같이 쓴다.
- 부서마다 A2A `<부서>_chat_remove`를 부른다. 본인 확인은 부서 쪽 skill이 한다
  (남의 대화 → forbidden 403, 없는 대화 → not_found 404).
- 인사 대화는 A2A가 아니라 인사 서비스(decisions)의 HTTP `POST /api/hr/chat/{thread_id}/remove`라
  사람 토큰을 그대로 넘겨 부른다.
- 쓰기라 사람 토큰이 필요하다. DELETE 메서드는 쓰지 않는다(251 nginx는 경로마다 POST만 연다).
"""

from __future__ import annotations

import os
from typing import Any

import httpx
from fastapi import Request

from ax_agent_template import config
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
from ax_dashboard_api.envelope import ApiError, is_mock

HR_CALL_SECONDS = 15.0

_CALLS = {
    "data": data_chat.call,
    "dev": dev_chat.call,
    "finance": finance_chat.call,
    "infra": infra_chat.call,
    "orchestrator": orchestrator_chat.call,
    "planning": planning_chat.call,
    "marketing": marketing_client.chat_call,
}


def _hr_base() -> str:
    """인사 대화 HTTP 주소 (decisions 서버). `AX_HR_CHAT_URL`로 바꿀 수 있다."""
    return (os.getenv("AX_HR_CHAT_URL") or config.agent_url("decisions")).rstrip("/")


def _error_of(body: Any) -> tuple[str | None, str | None]:
    err = body.get("error") if isinstance(body, dict) else None
    if not isinstance(err, dict):
        return None, None
    return err.get("code"), err.get("message")


async def _remove_hr(thread_id: str, authorization: str | None) -> dict[str, Any]:
    url = f"{_hr_base()}/api/hr/chat/{thread_id}/remove"
    try:
        async with httpx.AsyncClient(timeout=HR_CALL_SECONDS) as http:
            res = await http.post(url, headers={"Authorization": authorization or ""})
    except httpx.HTTPError as exc:
        reason = f"인사 AI에 연결할 수 없다 ({type(exc).__name__})"
        raise ApiError(502, "UPSTREAM_FAILED", reason) from None
    try:
        body: Any = res.json()
    except ValueError:
        body = None
    if res.status_code >= 400:
        code, message = _error_of(body)
        reason = str(message or "인사 AI가 지우지 못했다")
        raise ApiError(res.status_code, str(code or "UPSTREAM_FAILED"), reason)
    data = body.get("data") if isinstance(body, dict) else None
    return data if isinstance(data, dict) else {"thread_id": thread_id, "removed": True}


async def remove(agent: str, thread_id: str, request: Request) -> dict[str, Any]:
    """그 부서에서 요청한 사람의 대화 하나를 지운다. 결과 `{thread_id, removed}`."""
    authorization = request.headers.get("authorization")
    who = human_from_authorization(authorization)  # 사람 토큰이 아니면 401·403
    if agent == "hr":
        return await _remove_hr(thread_id, authorization)
    if agent == "support":
        if is_mock():
            raise ApiError(501, "NOT_SUPPORTED_IN_MOCK", "직접 대화는 실제 모드에서만 된다")
        # 끝내기(support_chat_close)와 같은 값을 넘긴다
        _, role = support_chat.viewer_of(authorization)
        return await support_client.call(
            "support_chat_remove",
            {"thread_id": thread_id, "viewer": who, "role": role},
            target=f"chat-remove:{thread_id}",
        )
    call = _CALLS.get(agent)
    if call is None:
        raise ApiError(404, "NOT_FOUND", f"모르는 부서다 ({agent})")
    return await call(f"{agent}_chat_remove", {"who": who, "thread_id": thread_id})
