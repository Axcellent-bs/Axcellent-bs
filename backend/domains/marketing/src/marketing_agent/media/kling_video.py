"""Kling 영상 어댑터(공급자 이름 `kling`, 모델 ID는 설정값 `MARKETING_VIDEO_MODEL`).

Kling 공식 API 문서(2026-11-18 확인)의 '모델 버전이 경로에 있는 새 규격'만 쓴다.
- 문서: https://kling.ai/document-api/api/get-started/authentication.md ,
  https://kling.ai/document-api/api/video/4-0/text-to-video.md ,
  https://kling.ai/document-api/api/video/4-0-flash/text-to-video.md ,
  https://kling.ai/document-api/pricing/base/video.md
- 호스트 `https://api-singapore.klingai.com`(중국 밖 서버용), `Authorization: Bearer <API 키>`.
- 생성: `POST /text-to-video/<모델>`(모델은 본문이 아니라 경로) 본문
  `{"prompt", "settings": {"aspect_ratio": "9:16", "duration": N, "resolution": "720p"}}`
  → 응답 `data.id`, `data.status`(submitted·processing·succeeded·failed).
- 조회: `GET /tasks?task_ids=<id>` → `data[]`의 `status`,
  `outputs[]`(`type=video`의 `url`, 30일 보관).
- 9:16 지원, 해상도 720p(4.0 일반은 1080p도 있으나 규격은 720p 한 가지),
  길이 3~20초(공통 상한).
- 소리: 문서에 소리 옵션이 없다. 오디오가 섞여 오면 공통 뼈대가 ffmpeg로 제거한다.
- 가격은 토큰이 아니라 초당 USD(`prices.toml`의 `per_second_usd`). 응답 사용량은 안 쓰고
  추정액으로 정산한다.
- 옛 규격(모델을 `model_name`에 쓰고 Access/Secret Key로 JWT를 만드는 방식)은 지원하지 않는다.
  [확인 필요] 새 규격에 없는 모델(2.x 등)을 쓰려면 별도 확인이 필요하다.
- 키는 `MARKETING_KLING_API_KEY`에서만 읽는다.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import quote

import httpx

from marketing_agent.media.base import MediaRequest
from marketing_agent.media.config import KLING_API_KEY_ENV
from marketing_agent.media.video_common import (
    AsyncVideoProvider,
    JobStatus,
    VideoProviderError,
    json_object,
)

NAME = "kling"
API_HOST = "https://api-singapore.klingai.com"
ASPECT_RATIO = "9:16"
_MODEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")  # 경로에 들어가므로 모양을 제한한다
_PENDING = ("submitted", "processing")


def _headers(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}


class KlingVideoProvider(AsyncVideoProvider):
    name = NAME
    key_env = KLING_API_KEY_ENV
    resolutions = ("720p",)
    min_duration_s = 3  # 문서: 3~20(4.0 flash)·3~30(4.0)
    max_duration_s = 20  # 두 모델 모두 되는 상한. 실제 상한은 단가표 max_duration_s·safety

    def submit(self, client: httpx.Client, request: MediaRequest, key: str) -> str:
        if not _MODEL.fullmatch(request.model):
            raise VideoProviderError("invalid_model_id")
        payload = {
            "prompt": request.prompt,
            "settings": {
                "aspect_ratio": ASPECT_RATIO,
                "duration": request.duration_s,
                "resolution": request.resolution,
            },
        }
        response = client.post(
            f"{API_HOST}/text-to-video/{request.model}", json=payload, headers=_headers(key)
        )
        if response.status_code != 200:
            raise VideoProviderError(f"http_{response.status_code}")
        data = json_object(response).get("data")
        job_id = data.get("id") if isinstance(data, dict) else None
        if not isinstance(job_id, str) or not job_id:
            raise VideoProviderError("no_job_id")
        return job_id

    def status(self, client: httpx.Client, job_id: str, key: str) -> JobStatus:
        response = client.get(
            f"{API_HOST}/tasks?task_ids={quote(job_id, safe='')}", headers=_headers(key)
        )
        if response.status_code != 200:
            raise VideoProviderError(f"http_{response.status_code}")
        rows = json_object(response).get("data")
        task: Any = rows[0] if isinstance(rows, list) and rows else None
        if not isinstance(task, dict):
            raise VideoProviderError("task_not_found")
        state = task.get("status")
        if state in _PENDING:
            return JobStatus("pending")
        if state != "succeeded":
            raise VideoProviderError(f"status_{state}")
        for output in task.get("outputs") or []:
            if isinstance(output, dict) and output.get("type") == "video":
                url = output.get("url")
                return JobStatus("done", video_url=url if isinstance(url, str) else None)
        raise VideoProviderError("no_video_in_response")
