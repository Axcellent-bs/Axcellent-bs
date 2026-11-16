"""아카이브 에이전트 skill 호출 (A2A). 결정 기록 화면이 쓴다.

`hr_client`와 같은 방식이고 받는 쪽만 다르다(`archive`). 결과를 API 오류로 바꾸는 규칙(`unwrap`)과
멱등 키 만드는 방식도 같다. 서버는 서비스 `decisions`(8114)이고 대응표(`services.py`)가
skill로 찾는다.
"""

from __future__ import annotations

from typing import Any

from ax_dashboard_api import hr_client

TARGET = "archive"


async def call(
    skill: str,
    input: dict[str, Any] | None = None,
    *,
    context_id: str | None = None,
    key: str | None = None,
    target: str | None = None,
) -> dict[str, Any]:
    """아카이브 skill을 부른다. 상태를 바꾸는 skill에는 `key`나 `target`으로 멱등 키를 준다."""
    return await hr_client.call(
        skill, input, context_id=context_id, key=key, target=target, to=TARGET
    )
