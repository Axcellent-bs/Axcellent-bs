"""PixVerse 영상 어댑터(공급자 이름 `pixverse`). 모델 ID(`v5`, `v6` 등)는 `MARKETING_VIDEO_MODEL`.

공식 문서(2026-11-19 확인): https://docs.platform.pixverse.ai/text-to-video-generation-13016634e0
- 생성: `POST https://app-api.pixverse.ai/openapi/v2/video/text/generate`, 헤더 `API-KEY`,
  `Ai-trace-id`(UUID),
  본문 `{"model", "prompt", "duration"(1~15), "quality"(360p~1080p), "aspect_ratio"("9:16" 지원),
  "generate_audio_switch": false}` → `{"ErrCode": 0, "Resp": {"video_id", "credits"}}`.
  HTTP 200이어도 `ErrCode`가 0이 아니면 실패다.
- 조회: `GET /openapi/v2/video/result/{video_id}`(경로만 문서에서 확인).
- 미검증(키 없음): 실제 호출은 해 보지 않았다. 조회 응답의 필드는 공식 문서 페이지에서 가져오지
  못했다
(상태 안내 페이지 404). 아래 `Resp.status`(1=완료, 5=생성 중)·`Resp.url`은 추정이다. 모르는 상태는
  실패로 처리한다.
  가격(크레딧→USD)도 확인하지 못해 `prices.toml`에 없다 = 호출 거부.
- 키는 `MARKETING_PIXVERSE_API_KEY`에서만 읽는다.
"""

from __future__ import annotations

import re
import uuid
from typing import Any

import httpx

from marketing_agent.media.base import MediaRequest
from marketing_agent.media.config import PIXVERSE_API_KEY_ENV
from marketing_agent.media.video_common import (
    AsyncVideoProvider,
    JobStatus,
    VideoProviderError,
    json_object,
)

NAME = "pixverse"
API_HOST = "https://app-api.pixverse.ai"
ASPECT_RATIO = "9:16"
_MODEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,31}")
STATUS_DONE = 1  # [미검증]
STATUS_PENDING = (5,)  # [미검증]


def _headers(key: str) -> dict[str, str]:
    return {"API-KEY": key, "Ai-trace-id": str(uuid.uuid4()), "Content-Type": "application/json"}


def _resp(response: httpx.Response) -> dict[str, Any]:
    if response.status_code != 200:
        raise VideoProviderError(f"http_{response.status_code}")
    body = json_object(response)
    if body.get("ErrCode") != 0:
        raise VideoProviderError(f"err_{body.get('ErrCode')}")
    resp = body.get("Resp")
    if not isinstance(resp, dict):
        raise VideoProviderError("invalid_json")
    return resp


class PixVerseVideoProvider(AsyncVideoProvider):
    name = NAME
    key_env = PIXVERSE_API_KEY_ENV
    resolutions = ("720p",)
    min_duration_s = 1
    max_duration_s = 15

    def submit(self, client: httpx.Client, request: MediaRequest, key: str) -> str:
        if not _MODEL.fullmatch(request.model):
            raise VideoProviderError("invalid_model_id")
        payload = {
            "model": request.model,
            "prompt": request.prompt,
            "duration": request.duration_s,
            "quality": request.resolution,
            "aspect_ratio": ASPECT_RATIO,
            "generate_audio_switch": False,
        }
        response = client.post(
            f"{API_HOST}/openapi/v2/video/text/generate", json=payload, headers=_headers(key)
        )
        video_id = _resp(response).get("video_id")
        if not isinstance(video_id, int) or isinstance(video_id, bool) or video_id <= 0:
            raise VideoProviderError("no_job_id")
        return str(video_id)

    def status(self, client: httpx.Client, job_id: str, key: str) -> JobStatus:
        if not job_id.isdigit():
            raise VideoProviderError("invalid_job_id")
        resp = _resp(
            client.get(f"{API_HOST}/openapi/v2/video/result/{job_id}", headers=_headers(key))
        )
        state = resp.get("status")
        if state in STATUS_PENDING:
            return JobStatus("pending")
        if state != STATUS_DONE:
            raise VideoProviderError(f"status_{state}")
        url = resp.get("url")
        if not isinstance(url, str) or not url:
            raise VideoProviderError("no_video_in_response")
        return JobStatus("done", video_url=url)
