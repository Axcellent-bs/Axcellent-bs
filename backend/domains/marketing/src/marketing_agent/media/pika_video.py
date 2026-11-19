"""Pika 영상 어댑터(공급자 이름 `pika`, 모델 ID는 설정값 `MARKETING_VIDEO_MODEL`).

Pika 공식 개발자 플랫폼 문서(2026-11-19 확인)만 쓴다.
키가 없어 실제 호출은 해 보지 못했다 [미검증(키 없음)].
- 문서: https://dev.pika.art/llms/pika/pika-2.5/text-to-video ,
  https://dev.pika.art/models/pika/pika-h3-flex/text-to-video
- 호스트 `https://api.dev.pika.art`, 인증 헤더 `X-API-Key: <API 키>`.
- 생성: `POST /v1/media/pika/<모델>/text-to-video` → 응답 `id`, `status`(queued 등).
  접수 거절이면 `status: failed`와 `error.code`가 같은 응답으로 온다.
- 조회: `GET /v1/media/jobs/<id>` → `status`(queued·running·completed·failed·cancelled),
  완료되면 `output.video.url`.
- 지원 모델(코드 표에 있는 것만. 경로에 들어가므로 임의 문자열은 거부):
  - `pika-2.5`: 길이 5·10초만, 필드 `duration_s`, 해상도 480p·720p·1080p, 9:16 지원.
    규격 길이 상한(8초)과 맞추려 5초만 허용한다(요청 길이가 5가 아니면 거부).
  - `pika-h3-flex`: 길이 3~15초 정수 `duration`, 해상도 480p·768p·1080p·2k·4k(480p·768p만 원본,
    나머지는 업스케일), 9:16 지원,
    소리가 함께 생성된다(끄는 옵션 문서에 없음 → 공통 뼈대가 ffmpeg로 제거).
    규격 해상도 720p 요청은 원본 해상도 `768p`로 보낸다(720p 값이 없어서).
- 가격은 초당 USD(`prices.toml` per_second_usd). 성공한 생성만 과금된다고 문서에 있다.
  pika-2.5의 1080p 5초 항목이 초당 0.06이 아닌 0.09로 보여
  문서 간 차이가 있다(720p는 0.04) [미검증].
- 키는 `MARKETING_PIKA_API_KEY`에서만 읽는다.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import quote

import httpx

from marketing_agent.media.base import MediaRequest
from marketing_agent.media.config import PIKA_API_KEY_ENV
from marketing_agent.media.video_common import (
    AsyncVideoProvider,
    JobStatus,
    VideoProviderError,
    json_object,
)

NAME = "pika"
API_HOST = "https://api.dev.pika.art"
ASPECT_RATIO = "9:16"
MODELS = ("pika-2.5", "pika-h3-flex")  # 경로에 들어가는 모델은 이 표에 있는 것만
_ERROR_CODE = re.compile(r"[A-Za-z0-9_.-]{1,64}")


def _headers(key: str) -> dict[str, str]:
    return {"X-API-Key": key, "Content-Type": "application/json"}


def _error_code(job: dict[str, Any]) -> str:
    error = job.get("error")
    code = error.get("code") if isinstance(error, dict) else None
    return code if isinstance(code, str) and _ERROR_CODE.fullmatch(code) else "unknown"


class PikaVideoProvider(AsyncVideoProvider):
    name = NAME
    key_env = PIKA_API_KEY_ENV
    resolutions = ("720p",)
    min_duration_s = 3  # pika-h3-flex 하한. pika-2.5는 submit에서 5초만 받는다
    max_duration_s = 15  # pika-h3-flex 문서 상한. 실제 상한은 단가표 max_duration_s·safety

    def submit(self, client: httpx.Client, request: MediaRequest, key: str) -> str:
        if request.model not in MODELS:
            raise VideoProviderError("invalid_model_id")
        payload: dict[str, Any] = {"prompt": request.prompt, "aspect_ratio": ASPECT_RATIO}
        if request.model == "pika-2.5":
            if request.duration_s != 5:
                raise VideoProviderError(f"unsupported_duration: {request.duration_s}")
            payload |= {"duration_s": 5, "resolution": request.resolution}
        else:
            payload |= {"duration": request.duration_s, "resolution": "768p"}
        response = client.post(
            f"{API_HOST}/v1/media/pika/{request.model}/text-to-video",
            json=payload,
            headers=_headers(key),
        )
        if response.status_code != 200:
            raise VideoProviderError(f"http_{response.status_code}")
        body = json_object(response)
        if body.get("status") in ("failed", "cancelled"):
            raise VideoProviderError(f"rejected_{_error_code(body)}")
        job_id = body.get("id")
        if not isinstance(job_id, str) or not job_id:
            raise VideoProviderError("no_job_id")
        return job_id

    def status(self, client: httpx.Client, job_id: str, key: str) -> JobStatus:
        response = client.get(
            f"{API_HOST}/v1/media/jobs/{quote(job_id, safe='')}", headers=_headers(key)
        )
        if response.status_code != 200:
            raise VideoProviderError(f"http_{response.status_code}")
        body = json_object(response)
        state = body.get("status")
        if state in ("queued", "running"):
            return JobStatus("pending")
        if state != "completed":
            raise VideoProviderError(f"status_{_error_code(body)}")
        output = body.get("output")
        video = output.get("video") if isinstance(output, dict) else None
        url = video.get("url") if isinstance(video, dict) else None
        if not isinstance(url, str) or not url:
            raise VideoProviderError("no_video_in_response")
        return JobStatus("done", video_url=url)
