"""공급자 어댑터 등록부. 목과 Gemini 이미지 어댑터가 있다.

실제 공급자 이름(`openai`, `gemini` 등)이 설정돼도 여기에 어댑터가 없으면 호출 전에 거부된다.
실제 어댑터를 붙일 때 이 표에 한 줄을 더한다(별도 작업, 사람이 키를 넣는 단계).
"""

from __future__ import annotations

from collections.abc import Callable

from marketing_agent.media.base import MediaProvider
from marketing_agent.media.gemini_image import NAME as GEMINI_NAME
from marketing_agent.media.gemini_image import GeminiImageProvider
from marketing_agent.media.mock import NAME as MOCK_NAME
from marketing_agent.media.mock import MockProvider

ADAPTERS: dict[str, Callable[[], MediaProvider]] = {
    MOCK_NAME: MockProvider,
    GEMINI_NAME: GeminiImageProvider,
}


def get_adapter(name: str) -> MediaProvider | None:
    factory = ADAPTERS.get(name)
    return factory() if factory is not None else None
