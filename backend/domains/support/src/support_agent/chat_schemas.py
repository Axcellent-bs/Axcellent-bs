"""고객지원 AI 직접 대화 skill의 입출력 (chat.py, chat_handlers.py).

사람이 쓴 말(원문)은 입력 모델이 아니라 `untrusted.message`로만 받는다.
"""

from __future__ import annotations

from typing import Annotated, Any

from pydantic import BeforeValidator, Field

from ax_contracts.schemas.common import AxModel
from support_agent.chat import Role

MAX_CONTEXT_CHARS = 8000  # 화면 API가 만든 집계(숫자)의 JSON 길이 상한


def _legacy_staff_is_member(value: Any) -> Any:
    """예전 화면 API가 보내던 `staff`는 없어졌다. 재시작 중 어긋나도 member로 읽어 막지 않는다."""
    return "member" if value == "staff" else value


ViewerRole = Annotated[Role, BeforeValidator(_legacy_staff_is_member)]


class ChatSendInput(AxModel):
    """말 하나를 보낸다. `thread_id`가 없으면 새 대화를 연다.

    `who`는 화면 API가 사람 토큰에서 채운 이름이다 (본문에서 받은 값이 아니다).
    `context`는 화면 API가 코드로 센 문의·클레임·후기 집계 숫자다 (고객 원문·이름은 없다).
    """

    thread_id: str | None = Field(default=None, max_length=40)
    who: str = Field(min_length=1, max_length=60)
    context: dict[str, Any] = Field(default_factory=dict)


class ChatListInput(AxModel):
    viewer: str | None = Field(default=None, max_length=60)  # 사람 토큰 이름 (없으면 모르는 사람)
    role: ViewerRole = "member"


class ChatGetInput(AxModel):
    thread_id: str = Field(min_length=1, max_length=40)
    viewer: str | None = Field(default=None, max_length=60)
    role: ViewerRole = "member"


class ChatCloseInput(AxModel):
    thread_id: str = Field(min_length=1, max_length=40)
    viewer: str = Field(min_length=1, max_length=60)
    role: ViewerRole = "member"
