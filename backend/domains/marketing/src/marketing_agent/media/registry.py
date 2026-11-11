"""공급자 어댑터 등록부. 목과 Gemini(이미지·영상) 어댑터가 있다.

실제 공급자 이름(`openai`, `gemini` 등)이 설정돼도 여기에 어댑터가 없으면 호출 전에 거부된다.
실제 어댑터를 붙일 때 이 표에 한 줄을 더한다(별도 작업, 사람이 키를 넣는 단계).
"""

from __future__ import annotations

from collections.abc import Callable

from marketing_agent.media.base import MediaProvider, MediaRequest, MediaResult
from marketing_agent.media.gemini_image import NAME as GEMINI_NAME
from marketing_agent.media.gemini_image import GeminiImageProvider
from marketing_agent.media.gemini_video import GeminiVideoProvider
from marketing_agent.media.mock import NAME as MOCK_NAME
from marketing_agent.media.mock import MockProvider


class GeminiProvider(GeminiImageProvider):
    """`gemini` 한 이름으로 kind에 따라 이미지·영상 어댑터를 고른다(skill은 이름만 넘긴다)."""

    def __init__(self) -> None:
        super().__init__()
        self._video = GeminiVideoProvider()

    def generate(self, request: MediaRequest) -> MediaResult:
        if request.media_kind == "video":
            return self._video.generate(request)
        return super().generate(request)


ADAPTERS: dict[str, Callable[[], MediaProvider]] = {
    MOCK_NAME: MockProvider,
    GEMINI_NAME: GeminiProvider,
}


def get_adapter(name: str) -> MediaProvider | None:
    factory = ADAPTERS.get(name)
    return factory() if factory is not None else None
