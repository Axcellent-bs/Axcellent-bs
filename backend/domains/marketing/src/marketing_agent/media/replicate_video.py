"""Replicate 게이트웨이 영상 어댑터(공급자 이름 `replicate`). 모델 ID(`owner/name`)만 바꿔 여러
영상 모델을 부른다.

공식 HTTP 문서(2026-11-19 확인): https://replicate.com/docs/reference/http
- 생성: `POST https://api.replicate.com/v1/models/<owner>/<name>/predictions`(공식 모델용),
  `Authorization: Bearer <토큰>`, 본문 `{"input": {...}}` → 응답 `id`, `status`, `urls.get`.
- 조회: `GET https://api.replicate.com/v1/predictions/<id>` → status
  starting·processing·succeeded·failed·canceled,
  성공 시 `output`(URL 문자열 또는 URL 목록), 실패 시 `error`.
- 미검증(키 없음): 실제 호출은 해 보지 않았다. 입력 필드 `aspect_ratio`·`duration`·`resolution`은
  모델마다
다르다. 문서는 결과 파일 요청에 Authorization 헤더가 필요하다고 적었으나, 공통 내려받기는 키를
  보내지 않는다
(키 유출 방지). 막히면 이 점을 다시 확인해야 한다. 버전 해시 지정 모델(`owner/name:version`)은
  지원하지 않는다.
- 모델별 단가는 `prices.toml`에 없으면 호출이 막힌다.
- 키는 `MARKETING_REPLICATE_API_KEY`에서만 읽는다.
"""

from __future__ import annotations

import re
from urllib.parse import quote

import httpx

from marketing_agent.media.base import MediaRequest
from marketing_agent.media.config import REPLICATE_API_KEY_ENV
from marketing_agent.media.video_common import (
    AsyncVideoProvider,
    JobStatus,
    VideoProviderError,
    json_object,
)

NAME = "replicate"
API_HOST = "https://api.replicate.com"
ASPECT_RATIO = "9:16"
_SEG = r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}"
_MODEL = re.compile(rf"{_SEG}/{_SEG}")
_ID = re.compile(r"[A-Za-z0-9]{8,64}")
_PENDING = ("starting", "processing")


def _headers(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}


class ReplicateVideoProvider(AsyncVideoProvider):
    name = NAME
    key_env = REPLICATE_API_KEY_ENV
    resolutions = ("720p",)
    min_duration_s = 2
    max_duration_s = 15

    def submit(self, client: httpx.Client, request: MediaRequest, key: str) -> str:
        if not _MODEL.fullmatch(request.model):
            raise VideoProviderError("invalid_model_id")
        payload = {
            "input": {
                "prompt": request.prompt,
                "aspect_ratio": ASPECT_RATIO,
                "duration": request.duration_s,
                "resolution": request.resolution,
            }
        }
        response = client.post(
            f"{API_HOST}/v1/models/{request.model}/predictions", json=payload, headers=_headers(key)
        )
        if response.status_code not in (200, 201):
            raise VideoProviderError(f"http_{response.status_code}")
        job_id = json_object(response).get("id")
        if not isinstance(job_id, str) or not _ID.fullmatch(job_id):
            raise VideoProviderError("no_job_id")
        return job_id

    def status(self, client: httpx.Client, job_id: str, key: str) -> JobStatus:
        response = client.get(
            f"{API_HOST}/v1/predictions/{quote(job_id, safe='')}", headers=_headers(key)
        )
        if response.status_code != 200:
            raise VideoProviderError(f"http_{response.status_code}")
        body = json_object(response)
        state = body.get("status")
        if state in _PENDING:
            return JobStatus("pending")
        if state != "succeeded":
            raise VideoProviderError(f"status_{state}")
        output = body.get("output")
        if isinstance(output, list) and output:
            output = output[0]
        if not isinstance(output, str) or not output:
            raise VideoProviderError("no_video_in_response")
        return JobStatus("done", video_url=output)
