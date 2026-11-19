"""안전 정책(비용 임계값) 현황 읽기. 안전 서버 `GET /api/safety/cost-policy`를 읽기만 한다.

마케팅은 정책 값을 바꾸지 못한다(쓰기 경로 없음).
못 읽으면 None이고, 상향 저장은 거절한다(fail-closed).
"""

from __future__ import annotations

from typing import Any

import httpx

from ax_agent_template.config import agent_token, agent_url, get_agent_name

TIMEOUT_S = 8.0

NOTES = {
    "video_item": "영상 한 건 비용. 이 값 이상이면 안전 정책이 사람 확인으로 보냅니다",
    "image_item": "이미지·캐러셀 한 장 비용",
    "audio_item": "BGM 한 곡 비용",
    "daily_all_media": "하루 합계는 이미지·영상·BGM을 모두 합산합니다(영상만의 한도가 아님)",
    "video_duration": "이 길이를 넘는 영상 요청은 사람이 봅니다",
}


async def fetch() -> dict[str, Any] | None:
    try:
        async with httpx.AsyncClient() as client:
            response = await client.get(
                f"{agent_url('safety')}/api/safety/cost-policy",
                headers={"Authorization": f"Bearer {agent_token(get_agent_name())}"},
                timeout=TIMEOUT_S,
            )
        response.raise_for_status()
        data = response.json()["data"]
    except Exception:  # noqa: BLE001 - 못 읽으면 정책을 모른다(상향은 거절)
        return None
    return data if isinstance(data, dict) and isinstance(data.get("items"), list) else None


def ceiling(policy: dict[str, Any] | None, key: str) -> tuple[int, int] | None:
    """(정책 값, 설정할 수 있는 최댓값). `>=` 규칙은 값 이상이 보류라 최댓값은 값-1이다."""
    for item in (policy or {}).get("items", []):
        if item.get("key") == key and isinstance(item.get("value"), int):
            value = item["value"]
            return value, (value - 1 if item.get("op") == ">=" else value)
    return None


def with_limits(policy: dict[str, Any] | None) -> dict[str, Any]:
    """화면용: 항목마다 `max_allowed`(설정 가능한 최댓값)와 비고를 붙인다."""
    if policy is None:
        return {"available": False, "editable": False, "items": [], "requests": [], "history": []}
    items = []
    for item in policy.get("items", []):
        cap = ceiling(policy, str(item.get("key")))
        note = NOTES.get(str(item.get("scope")), "")
        items.append({**item, "max_allowed": cap[1] if cap else None, "note": note})
    return {
        "available": True,
        "editable": bool(policy.get("editable")),
        "items": items,
        "requests": policy.get("requests", []),
        "history": policy.get("history", []),
    }
