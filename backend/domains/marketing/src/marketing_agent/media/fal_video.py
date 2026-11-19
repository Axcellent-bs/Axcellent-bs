"""fal.ai 게이트웨이 영상 어댑터(공급자 이름 `fal`). 모델 ID(`MARKETING_VIDEO_MODEL`)만 바꿔
LTX·Hunyuan·Wan 등을 부른다.

공식 큐 문서(2026-11-19 확인): https://fal.ai/docs/model-endpoints/queue
- 제출: `POST https://queue.fal.run/<owner>/<app>[/<경로>]` 본문은 모델 입력 JSON, `Authorization:
  Key <키>`
  → 응답 `request_id`.
- 상태: `GET https://queue.fal.run/<owner>/<app>/requests/<id>/status` →
  IN_QUEUE·IN_PROGRESS·COMPLETED.
  주의: 상태·결과 주소에는 모델 경로 전체가 아니라 owner/app 두 칸만 쓴다(문서가 405를 경고).
- 결과: `GET https://queue.fal.run/<owner>/<app>/requests/<id>` → `video.url`(fal.media 호스트).
- 응답이 주는 status_url·response_url은 따라가지 않는다(키가 엉뚱한 호스트로 가는 것을 막으려고
  주소를 직접 만든다).
- 미검증(키 없음): 실제 호출은 해 보지 않았다. 입력 필드 `aspect_ratio`·`duration`·`resolution`은
  모델마다 달라서(예: fal-ai/ltx-video 입력에는 이 필드가 없다) 모델별 확인이 필요하다.
모르는 필드를 무시하는지도 미확인. COMPLETED 외 종결 상태 문자열은 문서에 없어 모르는 상태는
  실패로 본다.
- 모델별 단가는 `prices.toml`에 없으면 호출이 막힌다(가격 미확인 = 거부).
- 키는 `MARKETING_FAL_API_KEY`에서만 읽는다.
"""

from __future__ import annotations

import re

import httpx

from marketing_agent.media.base import MediaRequest
from marketing_agent.media.config import FAL_API_KEY_ENV
from marketing_agent.media.video_common import (
    AsyncVideoProvider,
    JobStatus,
    VideoProviderError,
    json_object,
)

NAME = "fal"
QUEUE_HOST = "https://queue.fal.run"
ASPECT_RATIO = "9:16"
_SEG = r"[A-Za-z0-9][A-Za-z0-9._-]{0,63}"
_MODEL = re.compile(rf"{_SEG}/{_SEG}(?:/{_SEG}){{0,3}}")
_ID = re.compile(r"[A-Za-z0-9-]{8,64}")


def _headers(key: str) -> dict[str, str]:
    return {"Authorization": f"Key {key}", "Content-Type": "application/json"}


class FalVideoProvider(AsyncVideoProvider):
    name = NAME
    key_env = FAL_API_KEY_ENV
    resolutions = ("720p",)
    min_duration_s = 2
    max_duration_s = 15  # 모델마다 다르다. 실제 상한은 단가표 max_duration_s·safety

    def __init__(self, *args, **kwargs) -> None:  # noqa: ANN002, ANN003
        super().__init__(*args, **kwargs)
        self._app: dict[str, str] = {}  # 작업 ID -> "owner/app"

    def submit(self, client: httpx.Client, request: MediaRequest, key: str) -> str:
        if not _MODEL.fullmatch(request.model):
            raise VideoProviderError("invalid_model_id")
        payload = {
            "prompt": request.prompt,
            "aspect_ratio": ASPECT_RATIO,
            "duration": request.duration_s,
            "resolution": request.resolution,
        }
        response = client.post(f"{QUEUE_HOST}/{request.model}", json=payload, headers=_headers(key))
        if response.status_code not in (200, 201):
            raise VideoProviderError(f"http_{response.status_code}")
        job_id = json_object(response).get("request_id")
        if not isinstance(job_id, str) or not _ID.fullmatch(job_id):
            raise VideoProviderError("no_job_id")
        self._app[job_id] = "/".join(request.model.split("/")[:2])
        return job_id

    def status(self, client: httpx.Client, job_id: str, key: str) -> JobStatus:
        app = self._app.get(job_id)
        if app is None:
            raise VideoProviderError("task_not_found")
        base = f"{QUEUE_HOST}/{app}/requests/{job_id}"
        response = client.get(f"{base}/status", headers=_headers(key))
        if response.status_code not in (200, 202):
            raise VideoProviderError(f"http_{response.status_code}")
        state = json_object(response).get("status")
        if state in ("IN_QUEUE", "IN_PROGRESS"):
            return JobStatus("pending")
        if state != "COMPLETED":
            raise VideoProviderError(f"status_{state}")
        result = client.get(base, headers=_headers(key))
        if result.status_code != 200:
            raise VideoProviderError(f"http_{result.status_code}")
        video = json_object(result).get("video")
        url = video.get("url") if isinstance(video, dict) else None
        if not isinstance(url, str) or not url:
            raise VideoProviderError("no_video_in_response")
        return JobStatus("done", video_url=url)
