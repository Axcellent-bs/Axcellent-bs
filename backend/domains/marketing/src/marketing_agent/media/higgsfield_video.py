"""Higgsfield 영상 어댑터(공급자 이름 `higgsfield`). 모델 ID(`MARKETING_VIDEO_MODEL`)는 엔드포인트
경로다.

(사용자가 말한 '킥스필드'는 재헌 님이 Higgsfield로 확인했다.) 공식 문서(2026-11-19 확인):
https://docs.higgsfield.ai/docs/guides/video.md , .../concepts/requests.md ,
.../models/wan-2-7/reference-to-video.md
- 호스트 `https://api.higgsfield.ai`, 인증 `Authorization: Key <ID>:<SECRET>`.
- 생성: `POST https://api.higgsfield.ai/<모델 경로>`(예: `wan/v2.7/reference-to-video`) →
  `request_id`, `status=queued`.
- 상태: `GET https://api.higgsfield.ai/requests/<id>/status` → queued·in_progress(진행),
  completed(완료, `video.url`), failed·nsfw·canceled(실패). 결과 파일은 최소 7일 보관.
- 공식 모델 목록은 Seedance·Kling·Wan·LTX·MiniMax·PixVerse 등 여러 계열을 싣는다(자체가 게이트웨이).
- 미검증(키 없음): 실제 호출은 해 보지 않았다. 필드는 모델 페이지마다 다르다. 확인한 것은 Wan 2.7
reference-to-video(`prompt`·`aspect_ratio`·`resolution`·`duration`, 참조 `image_urls`/`video_urls`
  필수)뿐이라,
텍스트만으로 되는 모델의 경로·필드는 모델 페이지에서 따로 확인해야 한다. 오디오 끄기 필드
  없음(공통 뼈대가 ffmpeg로 제거).
- 모델별 단가는 `prices.toml`에 없으면 호출이 막힌다.
- 키는 `MARKETING_HIGGSFIELD_API_KEY`(`<ID>:<SECRET>` 한 줄)에서만 읽는다.
"""

from __future__ import annotations

import re
from urllib.parse import quote

import httpx

from marketing_agent.media.base import MediaRequest
from marketing_agent.media.config import HIGGSFIELD_API_KEY_ENV
from marketing_agent.media.video_common import (
    AsyncVideoProvider,
    JobStatus,
    VideoProviderError,
    json_object,
)

NAME = "higgsfield"
API_HOST = "https://api.higgsfield.ai"
ASPECT_RATIO = "9:16"
_SEG = r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}"
_MODEL = re.compile(rf"{_SEG}(?:/{_SEG}){{0,4}}")
_ID = re.compile(r"[A-Za-z0-9-]{8,64}")
_PENDING = ("queued", "in_progress")


def _headers(key: str) -> dict[str, str]:
    return {"Authorization": f"Key {key}", "Content-Type": "application/json"}


class HiggsfieldVideoProvider(AsyncVideoProvider):
    name = NAME
    key_env = HIGGSFIELD_API_KEY_ENV
    resolutions = ("720p",)
    min_duration_s = 2
    max_duration_s = 10  # 확인한 Wan 2.7 문서의 상한

    def submit(self, client: httpx.Client, request: MediaRequest, key: str) -> str:
        if not _MODEL.fullmatch(request.model):
            raise VideoProviderError("invalid_model_id")
        payload = {
            "prompt": request.prompt,
            "aspect_ratio": ASPECT_RATIO,
            "resolution": request.resolution,
            "duration": request.duration_s,
        }
        response = client.post(f"{API_HOST}/{request.model}", json=payload, headers=_headers(key))
        if response.status_code not in (200, 201, 202):
            raise VideoProviderError(f"http_{response.status_code}")
        job_id = json_object(response).get("request_id")
        if not isinstance(job_id, str) or not _ID.fullmatch(job_id):
            raise VideoProviderError("no_job_id")
        return job_id

    def status(self, client: httpx.Client, job_id: str, key: str) -> JobStatus:
        response = client.get(
            f"{API_HOST}/requests/{quote(job_id, safe='')}/status", headers=_headers(key)
        )
        if response.status_code != 200:
            raise VideoProviderError(f"http_{response.status_code}")
        body = json_object(response)
        state = body.get("status")
        if state in _PENDING:
            return JobStatus("pending")
        if state != "completed":
            raise VideoProviderError(f"status_{state}")
        video = body.get("video")
        url = video.get("url") if isinstance(video, dict) else None
        if not isinstance(url, str) or not url:
            raise VideoProviderError("no_video_in_response")
        return JobStatus("done", video_url=url)
