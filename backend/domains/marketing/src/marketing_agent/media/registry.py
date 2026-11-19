"""공급자 어댑터 등록부. 목, Gemini(이미지·영상·오디오), Seedance·Kling(영상) 어댑터가 있다.

실제 공급자 이름(`openai`, `gemini` 등)이 설정돼도 여기에 어댑터가 없으면 호출 전에 거부된다.
영상 공급자 추가 = `video_common.AsyncVideoProvider`를 상속한 어댑터 파일 1개 + 이 표 한 줄 +
`prices.toml` 항목(가격이 없으면 비용 상한 때문에 호출이 막힌다) + 키 변수 이름(`config.py`).
전환은 `MARKETING_VIDEO_PROVIDER`·`MARKETING_VIDEO_MODEL`·해당 공급자 키 변수만 바꾼다.
"""

from __future__ import annotations

from collections.abc import Callable

from marketing_agent.media.base import MediaProvider, MediaRequest, MediaResult
from marketing_agent.media.fal_video import NAME as FAL_NAME
from marketing_agent.media.fal_video import FalVideoProvider
from marketing_agent.media.gemini_audio import GeminiAudioProvider
from marketing_agent.media.gemini_image import NAME as GEMINI_NAME
from marketing_agent.media.gemini_image import GeminiImageProvider
from marketing_agent.media.gemini_video import GeminiVideoProvider
from marketing_agent.media.higgsfield_video import NAME as HIGGSFIELD_NAME
from marketing_agent.media.higgsfield_video import HiggsfieldVideoProvider
from marketing_agent.media.kling_video import NAME as KLING_NAME
from marketing_agent.media.kling_video import KlingVideoProvider
from marketing_agent.media.luma_video import NAME as LUMA_NAME
from marketing_agent.media.luma_video import LumaVideoProvider
from marketing_agent.media.mock import NAME as MOCK_NAME
from marketing_agent.media.mock import MockProvider
from marketing_agent.media.pika_video import NAME as PIKA_NAME
from marketing_agent.media.pika_video import PikaVideoProvider
from marketing_agent.media.pixverse_video import NAME as PIXVERSE_NAME
from marketing_agent.media.pixverse_video import PixVerseVideoProvider
from marketing_agent.media.replicate_video import NAME as REPLICATE_NAME
from marketing_agent.media.replicate_video import ReplicateVideoProvider
from marketing_agent.media.seedance_video import NAME as SEEDANCE_NAME
from marketing_agent.media.seedance_video import SeedanceVideoProvider
from marketing_agent.media.vidu_video import NAME as VIDU_NAME
from marketing_agent.media.vidu_video import ViduVideoProvider
from marketing_agent.media.wan_video import NAME as WAN_NAME
from marketing_agent.media.wan_video import WanVideoProvider


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
    FAL_NAME: FalVideoProvider,  # 게이트웨이. 키 MARKETING_FAL_API_KEY
    REPLICATE_NAME: ReplicateVideoProvider,  # 게이트웨이. 키 MARKETING_REPLICATE_API_KEY
    HIGGSFIELD_NAME: HiggsfieldVideoProvider,  # 키 MARKETING_HIGGSFIELD_API_KEY
    WAN_NAME: WanVideoProvider,  # 키 MARKETING_WAN_API_KEY + MARKETING_WAN_WORKSPACE_ID
    PIXVERSE_NAME: PixVerseVideoProvider,  # 키 MARKETING_PIXVERSE_API_KEY
    PIKA_NAME: PikaVideoProvider,  # 영상 전용. 키 MARKETING_PIKA_API_KEY
    LUMA_NAME: LumaVideoProvider,  # 영상 전용. 키 MARKETING_LUMA_API_KEY
    VIDU_NAME: ViduVideoProvider,  # 영상 전용. 키 MARKETING_VIDU_API_KEY
}


def get_adapter(name: str) -> MediaProvider | None:
    factory = ADAPTERS.get(name)
    return factory() if factory is not None else None
