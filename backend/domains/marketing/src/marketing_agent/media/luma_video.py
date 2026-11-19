"""Luma 영상 어댑터(공급자 이름 `luma`, 모델 ID는 설정값 `MARKETING_VIDEO_MODEL`).

Luma Agents API 문서(2026-11-19 확인)의 Ray 3.2 규격만 쓴다. 키가 없어 실제 호출은 해 보지 못했다
[미검증(키 없음)].
- 문서: https://docs.agents.lumalabs.ai , https://docs.agents.lumalabs.ai/guides/video-generation ,
  https://docs.agents.lumalabs.ai/guides/pricing
- 호스트 `https://agents.lumalabs.ai`, `Authorization: Bearer <API 키>`.
- 생성: `POST /v1/generations` 본문 `{"model", "type": "video", "prompt", "aspect_ratio": "9:16",
  "video": {"resolution": "720p", "duration": "5s"}}` → 응답 `id`, `state`(queued 등).
- 조회: `GET /v1/generations/<id>` → `state`가 `completed`면 `output[0].url`(서명된 주소),
  `failed`면 실패(`failure_code`).
  그 밖의 중간 상태 이름은 문서에 없어 completed·failed 외는 대기로 본다.
- 길이는 5초·10초만 있다. 규격 길이 상한(8초)과 맞추려 5초만 허용한다(요청 길이가 5가 아니면 거부).
- 해상도 720p, 9:16은 가이드 목록에 있다. 소리 옵션은 문서에 없다 → 공통 뼈대가 ffmpeg로 제거한다.
- 가격은 크레딧(720p 5초 100크레딧)이고 크레딧의 USD 환산은 계약별이라 문서에 없다.
  그래서 `prices.toml`에 항목이 없다 = 호출 거부(Seedance와 같은 처리) [확인 필요].
- 옛 Dream Machine API(`api.lumalabs.ai/dream-machine/v1`, ray-2·ray-flash-2)는 9:16이 목록에 없고
  지원 범위·가격이 확인되지 않아 구현하지 않는다 [미확인].
- 키는 `MARKETING_LUMA_API_KEY`에서만 읽는다.
"""

from __future__ import annotations

import re
from typing import Any
from urllib.parse import quote

import httpx

from marketing_agent.media.base import MediaRequest
from marketing_agent.media.config import LUMA_API_KEY_ENV
from marketing_agent.media.video_common import (
    AsyncVideoProvider,
    JobStatus,
    VideoProviderError,
    json_object,
)

NAME = "luma"
API_HOST = "https://agents.lumalabs.ai"
ASPECT_RATIO = "9:16"
DURATION_S = 5
_CODE = re.compile(r"[A-Za-z0-9_.-]{1,64}")


def _headers(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}


class LumaVideoProvider(AsyncVideoProvider):
    name = NAME
    key_env = LUMA_API_KEY_ENV
    resolutions = ("720p",)
    min_duration_s = DURATION_S
    max_duration_s = DURATION_S  # 문서 길이는 5s·10s. 규격(8초 이하)에서는 5초만 쓴다

    def submit(self, client: httpx.Client, request: MediaRequest, key: str) -> str:
        payload = {
            "model": request.model,
            "type": "video",
            "prompt": request.prompt,
            "aspect_ratio": ASPECT_RATIO,
            "video": {"resolution": request.resolution, "duration": f"{DURATION_S}s"},
        }
        response = client.post(f"{API_HOST}/v1/generations", json=payload, headers=_headers(key))
        if response.status_code not in (200, 201):
            raise VideoProviderError(f"http_{response.status_code}")
        job_id = json_object(response).get("id")
        if not isinstance(job_id, str) or not job_id:
            raise VideoProviderError("no_job_id")
        return job_id

    def status(self, client: httpx.Client, job_id: str, key: str) -> JobStatus:
        response = client.get(
            f"{API_HOST}/v1/generations/{quote(job_id, safe='')}", headers=_headers(key)
        )
        if response.status_code != 200:
            raise VideoProviderError(f"http_{response.status_code}")
        body = json_object(response)
        state = body.get("state")
        if state == "failed":
            code = body.get("failure_code")
            if isinstance(code, str) and _CODE.fullmatch(code):
                raise VideoProviderError(f"status_failed_{code}")
            raise VideoProviderError("status_failed")
        if state != "completed":
            return JobStatus("pending")
        output: Any = body.get("output")
        first = output[0] if isinstance(output, list) and output else None
        url = first.get("url") if isinstance(first, dict) else None
        if not isinstance(url, str) or not url:
            raise VideoProviderError("no_video_in_response")
        return JobStatus("done", video_url=url)
