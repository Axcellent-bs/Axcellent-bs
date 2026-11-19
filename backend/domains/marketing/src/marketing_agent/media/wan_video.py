"""Alibaba Wan 영상 어댑터(공급자 이름 `wan`, Model Studio 국제(싱가포르)). 모델 ID는
`MARKETING_VIDEO_MODEL`.

공식 문서(2026-11-19 확인):
https://www.alibabacloud.com/help/en/model-studio/text-to-video-api-reference
- wan2.7은 작업공간 전용 호스트를 쓴다: `https://<WorkspaceId>.ap-southeast-1.maas.aliyuncs.com`.
  작업공간 ID는 환경변수 `MARKETING_WAN_WORKSPACE_ID`(영문 소문자·숫자·-)에서만 읽는다.
- 생성: `POST /api/v1/services/aigc/video-generation/video-synthesis`, 헤더 `Authorization: Bearer
  <키>`,
  `X-DashScope-Async: enable`, 본문 `{"model": "wan2.7-t2v", "input": {"prompt"},
  "parameters": {"resolution": "720P", "ratio": "9:16", "duration": 2~15, "watermark": false}}`
  → 응답 `output.task_id`, `output.task_status`.
- 조회: `GET /api/v1/tasks/<task_id>` → PENDING·RUNNING(진행), SUCCEEDED(`output.video_url`,
  24시간 유효),
  FAILED·CANCELED·UNKNOWN(실패). 폴링 간격 권장 15초(어댑터는 공통 기본값을 쓴다).
- 오디오: `input.audio_url`을 안 주면 소리 처리가 문서에 명시돼 있지 않다. 소리가 섞여 오면 공통
  뼈대가 제거한다.
- 미검증(키 없음): 실제 호출은 해 보지 않았다. 구형 호스트(dashscope-intl)와 wan2.6 이하는
  지원하지 않는다.
  단가는 문서 가격 페이지에서 wan2.7-t2v 행을 확인하지 못해 `prices.toml`에 없다 = 호출 거부.
- 키는 `MARKETING_WAN_API_KEY`에서만 읽는다.
"""

from __future__ import annotations

import os
import re

import httpx

from marketing_agent.media.base import MediaRequest
from marketing_agent.media.config import WAN_API_KEY_ENV, WAN_WORKSPACE_ENV
from marketing_agent.media.video_common import (
    AsyncVideoProvider,
    JobStatus,
    VideoProviderError,
    json_object,
)

NAME = "wan"
REGION = "ap-southeast-1"
RATIO = "9:16"
_WORKSPACE = re.compile(r"[a-z0-9][a-z0-9-]{0,62}")
_MODEL = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}")
_ID = re.compile(r"[A-Za-z0-9-]{8,64}")
_PENDING = ("PENDING", "RUNNING")


def _host() -> str:
    workspace = os.getenv(WAN_WORKSPACE_ENV, "").strip()
    if not _WORKSPACE.fullmatch(workspace):
        raise VideoProviderError("workspace_not_configured")
    return f"https://{workspace}.{REGION}.maas.aliyuncs.com"


def _headers(key: str, *, async_job: bool = False) -> dict[str, str]:
    headers = {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    if async_job:
        headers["X-DashScope-Async"] = "enable"
    return headers


class WanVideoProvider(AsyncVideoProvider):
    name = NAME
    key_env = WAN_API_KEY_ENV
    resolutions = ("720p",)
    min_duration_s = 2
    max_duration_s = 15

    def submit(self, client: httpx.Client, request: MediaRequest, key: str) -> str:
        if not _MODEL.fullmatch(request.model):
            raise VideoProviderError("invalid_model_id")
        payload = {
            "model": request.model,
            "input": {"prompt": request.prompt},
            "parameters": {
                "resolution": request.resolution.upper(),
                "ratio": RATIO,
                "duration": request.duration_s,
                "watermark": False,
            },
        }
        response = client.post(
            f"{_host()}/api/v1/services/aigc/video-generation/video-synthesis",
            json=payload,
            headers=_headers(key, async_job=True),
        )
        if response.status_code != 200:
            raise VideoProviderError(f"http_{response.status_code}")
        output = json_object(response).get("output")
        job_id = output.get("task_id") if isinstance(output, dict) else None
        if not isinstance(job_id, str) or not _ID.fullmatch(job_id):
            raise VideoProviderError("no_job_id")
        return job_id

    def status(self, client: httpx.Client, job_id: str, key: str) -> JobStatus:
        if not _ID.fullmatch(job_id):
            raise VideoProviderError("invalid_job_id")
        response = client.get(f"{_host()}/api/v1/tasks/{job_id}", headers=_headers(key))
        if response.status_code != 200:
            raise VideoProviderError(f"http_{response.status_code}")
        output = json_object(response).get("output")
        if not isinstance(output, dict):
            raise VideoProviderError("task_not_found")
        state = output.get("task_status")
        if state in _PENDING:
            return JobStatus("pending")
        if state != "SUCCEEDED":
            raise VideoProviderError(f"status_{state}")
        url = output.get("video_url")
        if not isinstance(url, str) or not url:
            raise VideoProviderError("no_video_in_response")
        return JobStatus("done", video_url=url)
