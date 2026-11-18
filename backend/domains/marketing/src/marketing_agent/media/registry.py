"""공급자 어댑터 등록부. 목, Gemini(이미지·영상·오디오), Seedance·Kling(영상) 어댑터가 있다.

실제 공급자 이름(`openai`, `gemini` 등)이 설정돼도 여기에 어댑터가 없으면 호출 전에 거부된다.
영상 공급자 추가 = `video_common.AsyncVideoProvider`를 상속한 어댑터 파일 1개 + 이 표 한 줄 +
`prices.toml` 항목(가격이 없으면 비용 상한 때문에 호출이 막힌다) + 키 변수 이름(`config.py`).
전환은 `MARKETING_VIDEO_PROVIDER`·`MARKETING_VIDEO_MODEL`·해당 공급자 키 변수만 바꾼다.
"""

from __future__ import annotations

from collections.abc import Callable

from marketing_agent.media.base import MediaProvider, MediaRequest, MediaResult
from marketing_agent.media.gemini_audio import GeminiAudioProvider
from marketing_agent.media.gemini_image import NAME as GEMINI_NAME
from marketing_agent.media.gemini_image import GeminiImageProvider
from marketing_agent.media.gemini_video import GeminiVideoProvider
from marketing_agent.media.kling_video import NAME as KLING_NAME
from marketing_agent.media.kling_video import KlingVideoProvider
from marketing_agent.media.mock import NAME as MOCK_NAME
from marketing_agent.media.mock import MockProvider
from marketing_agent.media.seedance_video import NAME as SEEDANCE_NAME
from marketing_agent.media.seedance_video import SeedanceVideoProvider


class GeminiProvider(GeminiImageProvider):
    """`gemini` 한 이름으로 kind에 따라 이미지·영상 어댑터를 고른다(skill은 이름만 넘긴다)."""

    def __init__(self) -> None:
        super().__init__()
        self._video = GeminiVideoProvider()
        self._audio = GeminiAudioProvider()

    def generate(self, request: MediaRequest) -> MediaResult:
        if request.media_kind == "video":
            return self._video.generate(request)
        if request.media_kind == "audio":
            return self._audio.generate(request)
        return super().generate(request)


ADAPTERS: dict[str, Callable[[], MediaProvider]] = {
    MOCK_NAME: MockProvider,
    GEMINI_NAME: GeminiProvider,
    SEEDANCE_NAME: SeedanceVideoProvider,  # 영상 전용. 키 MARKETING_SEEDANCE_API_KEY
    KLING_NAME: KlingVideoProvider,  # 영상 전용. 키 MARKETING_KLING_API_KEY
}


def get_adapter(name: str) -> MediaProvider | None:
    factory = ADAPTERS.get(name)
    return factory() if factory is not None else None
