"""Runway 영상 어댑터(공급자 이름 `runway`, 모델 ID는 설정값 `MARKETING_VIDEO_MODEL`).

Runway 공식 API 문서·OpenAPI 명세(2026-11-19 확인: docs.dev.runwayml.com 의 openapi.json,
ai-context.md, guides/pricing.md)가 말하는 형태만 쓴다.
키가 없어 실제 호출은 해 보지 못했다 [미검증(키 없음)].
- 호스트 `https://api.dev.runwayml.com`, `Authorization: Bearer <API 비밀키>`,
  모든 요청에 `X-Runway-Version: 2024-11-06`(없으면 실패).
- 생성: `POST /v1/text_to_video` 본문
  `{"model", "promptText", "ratio": "720:1280", "duration", ...}` → 응답 `{"id": <uuid>}`.
  모델 ID는 본문 `model`이고, 모델마다 허용 필드가 달라 아래 표(_MODELS)로 가른다.
  - `gen4.5`: ratio `1280:720`·`720:1280`, duration 정수 2~10. 오디오 필드 없음.
  - `veo3.1`·`veo3.1_fast`(Runway가 중계하는 Google Veo): ratio에 `720:1280`·`1080:1920` 등,
    duration은 4·6·8만, `audio` 불리언(켜면 단가가 오른다). 이 어댑터는 `audio: false`만 보낸다.
  - `gen4_turbo`는 이미지→영상 전용이라 여기서 받지 않는다(텍스트→영상 모델 목록에 없음).
- 조회: `GET /v1/tasks/{id}` → `status`(PENDING·THROTTLED·RUNNING·SUCCEEDED·FAILED·CANCELLED),
  SUCCEEDED면 `output[]`(URL, 24~48시간 뒤 만료), FAILED면 `failureCode`.
  THROTTLED는 오류가 아니라 대기(레이트 리밋 뒤)다.
- 해상도는 720p(9:16 = `720:1280`) 한 가지다. 1080p(`1080:1920`)는 단가 확인 전이라 열지 않는다.
- 소리: 항상 무음으로만 쓴다. veo는 `audio: false`, gen4.5는 오디오 필드가 없다.
  그래도 mp4에 오디오가 섞여 오면 공통 뼈대가 ffmpeg로 제거한다.
  소리를 두는 모드(`remove_audio=False`)여도 소리를 켜지 않는다(소리 단가가 따로라 가격표에 없다):
  이 공급자 영상은 소리 없는 영상으로 받는다.
- 가격은 크레딧(1크레딧 = 0.01 USD)×초 → `prices.toml`의 `per_second_usd`.
- 키는 `MARKETING_RUNWAY_API_KEY`에서만 읽는다(Google 등 다른 공급자 키를 보내지 않는다).
  결과 URL 내려받기에는 키를 보내지 않는다(공통 뼈대, 공용 주소만).
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import quote

import httpx

from marketing_agent.media.base import MediaRequest
from marketing_agent.media.config import RUNWAY_API_KEY_ENV
from marketing_agent.media.video_common import (
    AsyncVideoProvider,
    JobStatus,
    VideoProviderError,
    json_object,
)

NAME = "runway"
API_HOST = "https://api.dev.runwayml.com"
API_VERSION = "2024-11-06"
RATIO = "720:1280"  # 720p 9:16
_PENDING = ("PENDING", "THROTTLED", "RUNNING")


@dataclass(frozen=True)
class _ModelSpec:
    durations: tuple[int, ...]  # 허용 길이(초)
    audio_flag: bool  # 본문에 `audio` 불리언이 있는 모델


_MODELS: dict[str, _ModelSpec] = {
    "gen4.5": _ModelSpec(tuple(range(2, 11)), audio_flag=False),
    "veo3.1": _ModelSpec((4, 6, 8), audio_flag=True),
    "veo3.1_fast": _ModelSpec((4, 6, 8), audio_flag=True),
}


def _headers(key: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {key}",
        "X-Runway-Version": API_VERSION,
        "Content-Type": "application/json",
    }


class RunwayVideoProvider(AsyncVideoProvider):
    name = NAME
    key_env = RUNWAY_API_KEY_ENV
    resolutions = ("720p",)
    min_duration_s = 2  # gen4.5 하한. 모델별 허용 길이는 submit에서 다시 본다
    max_duration_s = 10  # gen4.5 상한. 실제 상한은 단가표 max_duration_s·safety

    def submit(self, client: httpx.Client, request: MediaRequest, key: str) -> str:
        spec = _MODELS.get(request.model)
        if spec is None:
            raise VideoProviderError("invalid_model_id")
        if request.duration_s not in spec.durations:
            raise VideoProviderError(f"unsupported_duration: {request.duration_s}")
        payload: dict[str, object] = {
            "model": request.model,
            "promptText": request.prompt[:1000],  # gen4.5 문서 상한 1000자
            "ratio": RATIO,
            "duration": request.duration_s,
        }
        if spec.audio_flag:
            payload["audio"] = False
        response = client.post(f"{API_HOST}/v1/text_to_video", json=payload, headers=_headers(key))
        if response.status_code != 200:
            raise VideoProviderError(f"http_{response.status_code}")
        job_id = json_object(response).get("id")
        if not isinstance(job_id, str) or not job_id:
            raise VideoProviderError("no_job_id")
        return job_id

    def status(self, client: httpx.Client, job_id: str, key: str) -> JobStatus:
        response = client.get(
            f"{API_HOST}/v1/tasks/{quote(job_id, safe='')}", headers=_headers(key)
        )
        if response.status_code != 200:
            raise VideoProviderError(f"http_{response.status_code}")
        body = json_object(response)
        state = body.get("status")
        if state in _PENDING:
            return JobStatus("pending")
        if state != "SUCCEEDED":
            code = body.get("failureCode")
            suffix = f": {code[:60]}" if isinstance(code, str) and code else ""
            raise VideoProviderError(f"status_{state}{suffix}")
        output = body.get("output")
        url = output[0] if isinstance(output, list) and output else None
        return JobStatus("done", video_url=url if isinstance(url, str) else None)
