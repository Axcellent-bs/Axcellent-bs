"""Vidu 영상 어댑터(공급자 이름 `vidu`, 모델 ID는 설정값 `MARKETING_VIDEO_MODEL`).

Vidu 공식 API 문서(2026-11-19 확인)를 쓴다. 키가 없어 실제 호출은 해 보지 못했다 [미검증(키 없음)].
- 문서: https://platform.vidu.com/docs/api-reference/video-models/vidu-q3/text-to-video ,
  https://platform.vidu.com/docs/api-reference/task-management/task-detail ,
  https://platform.vidu.com/docs/overview/pricing
- 호스트 `https://api.vidu.com`, 인증 헤더 `Authorization: Token <API 키>`(Bearer가 아니다).
- 생성: `POST /ent/v2/text2video` 본문 `{"model", "prompt", "duration", "aspect_ratio": "9:16",
  "resolution": "720p", "audio": false}` → 응답 `task_id`, `state`(created).
  모델 예: viduq3-pro·viduq3-turbo(길이 1~16초, 9:16·720p 지원).
- 조회: `GET /ent/v2/tasks/<id>/creations` → `state`(created·queueing·processing·success·failed),
  성공하면 `creations[].url`(24시간 유효). 실패하면 `err_code`.
- `audio: false`로 무음 요청한다. 그래도 소리가 섞여 오면 공통 뼈대가 ffmpeg로 제거한다.
- 가격은 크레딧이다(viduq3-turbo 720p 12크레딧/초, viduq3-pro 20크레딧/초, 1크레딧 = 0.03125 위안).
  USD 단가가 문서에 없고 환율 근거도 없어 `prices.toml`에 항목을 넣지 않았다 = 호출 거부
  (Seedance와 같은 처리) [확인 필요].
- 키는 `MARKETING_VIDU_API_KEY`에서만 읽는다.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import quote

import httpx

from marketing_agent.media.base import MediaRequest
from marketing_agent.media.config import VIDU_API_KEY_ENV
from marketing_agent.media.video_common import (
    AsyncVideoProvider,
    JobStatus,
    VideoProviderError,
    json_object,
)

NAME = "vidu"
API_HOST = "https://api.vidu.com"
ASPECT_RATIO = "9:16"
_PENDING = ("created", "queueing", "processing")
_CODE = re.compile(r"[A-Za-z0-9_.-]{1,64}")


def _headers(key: str) -> dict[str, str]:
    return {"Authorization": f"Token {key}", "Content-Type": "application/json"}


class ViduVideoProvider(AsyncVideoProvider):
    name = NAME
    key_env = VIDU_API_KEY_ENV
    resolutions = ("720p",)
    min_duration_s = 1  # 문서: 1~16초
    max_duration_s = 16  # 실제 상한은 단가표 max_duration_s·safety

    def submit(self, client: httpx.Client, request: MediaRequest, key: str) -> str:
        payload = {
            "model": request.model,
            "prompt": request.prompt,
            "duration": request.duration_s,
            "aspect_ratio": ASPECT_RATIO,
            "resolution": request.resolution,
            "audio": False,
        }
        response = client.post(f"{API_HOST}/ent/v2/text2video", json=payload, headers=_headers(key))
        if response.status_code != 200:
            raise VideoProviderError(f"http_{response.status_code}")
        job_id = json_object(response).get("task_id")
        if not isinstance(job_id, str) or not job_id:
            raise VideoProviderError("no_job_id")
        return job_id

    def status(self, client: httpx.Client, job_id: str, key: str) -> JobStatus:
        response = client.get(
            f"{API_HOST}/ent/v2/tasks/{quote(job_id, safe='')}/creations", headers=_headers(key)
        )
        if response.status_code != 200:
            raise VideoProviderError(f"http_{response.status_code}")
        body = json_object(response)
        state = body.get("state")
        if state in _PENDING:
            return JobStatus("pending")
        if state != "success":
            code = body.get("err_code")
            if isinstance(code, str) and _CODE.fullmatch(code):
                raise VideoProviderError(f"status_failed_{code}")
            raise VideoProviderError("status_failed")
        creations: Any = body.get("creations")
        first = creations[0] if isinstance(creations, list) and creations else None
        url = first.get("url") if isinstance(first, dict) else None
        if not isinstance(url, str) or not url:
            raise VideoProviderError("no_video_in_response")
        return JobStatus("done", video_url=url)
