"""Seedance 영상 어댑터(공급자 이름 `seedance`, 모델 ID는 설정값 `MARKETING_VIDEO_MODEL`).

BytePlus ModelArk 공식 문서(2026-11-18 확인)가 말하는 형태만 쓴다.
- 문서: https://docs.byteplus.com/docs/en/modelark/create-video-generation-task-api.md ,
  https://docs.byteplus.com/docs/en/modelark/get-video-generation-task-api.md ,
  https://docs.byteplus.com/docs/en/modelark/seedance-2-5.md
- 생성: `POST /api/v3/contents/generations/tasks`, `Authorization: Bearer <키>`,
  본문 `{"model", "content": [{"type": "text", "text"}], "ratio": "9:16", "resolution", "duration",
  "generate_audio": false, "watermark": false}` → 응답 `id`.
- 조회: `GET /api/v3/contents/generations/tasks/{id}` → `status`(queued·running·succeeded·failed·
  cancelled·expired), `content.video_url`(24시간만 유효), `usage.completion_tokens`, `error`.
- 9:16 지원, 해상도 480p·720p(2.5 일반은 1080p·4k도 있으나 단가 미확인이라 열지 않는다),
  길이 4~30초.
  `generate_audio=false`면 무음. 그래도 오디오가 섞여 오면 공통 뼈대가 ffmpeg로 제거한다.
- [확인 필요] 가격(토큰 단가·영상 토큰 계산식·최소 토큰)은 공식 문서에서 확인하지 못해
  `prices.toml`에 항목이 없다 = 비용 상한 때문에 호출이 막힌다(안전한 기본). 사람이 확인해 넣는다.
- [확인 필요] 키 헤더는 문서 예시(`Authorization: Bearer $ARK_API_KEY`)만 근거다.
- 키는 `MARKETING_SEEDANCE_API_KEY`에서만 읽는다.
"""

from __future__ import annotations

from urllib.parse import quote

import httpx

from marketing_agent.media.base import MediaRequest, Usage
from marketing_agent.media.config import SEEDANCE_API_KEY_ENV
from marketing_agent.media.video_common import (
    AsyncVideoProvider,
    JobStatus,
    VideoProviderError,
    count,
    json_object,
)

NAME = "seedance"
TASKS_ENDPOINT = "https://ark.ap-southeast.bytepluses.com/api/v3/contents/generations/tasks"
ASPECT_RATIO = "9:16"
_PENDING = ("queued", "running")


def _headers(key: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {key}", "Content-Type": "application/json"}


def _error_code(body: dict) -> str:
    error = body.get("error")
    code = error.get("code") if isinstance(error, dict) else None
    return code[:60] if isinstance(code, str) and code else "unknown"


class SeedanceVideoProvider(AsyncVideoProvider):
    name = NAME
    key_env = SEEDANCE_API_KEY_ENV
    resolutions = ("480p", "720p")
    min_duration_s = 4  # 문서: duration [4, 30]
    max_duration_s = 30  # 실제 상한은 단가표 max_duration_s·safety가 정한다

    def submit(self, client: httpx.Client, request: MediaRequest, key: str) -> str:
        payload = {
            "model": request.model,
            "content": [{"type": "text", "text": request.prompt}],
            "ratio": ASPECT_RATIO,
            "resolution": request.resolution,
            "duration": request.duration_s,
            "generate_audio": not request.remove_audio,
            "watermark": False,
        }
        response = client.post(TASKS_ENDPOINT, json=payload, headers=_headers(key))
        if response.status_code != 200:
            raise VideoProviderError(f"http_{response.status_code}")
        job_id = json_object(response).get("id")
        if not isinstance(job_id, str) or not job_id:
            raise VideoProviderError("no_job_id")
        return job_id

    def status(self, client: httpx.Client, job_id: str, key: str) -> JobStatus:
        response = client.get(f"{TASKS_ENDPOINT}/{quote(job_id, safe='')}", headers=_headers(key))
        if response.status_code != 200:
            raise VideoProviderError(f"http_{response.status_code}")
        body = json_object(response)
        state = body.get("status")
        if state in _PENDING:
            return JobStatus("pending")
        if state != "succeeded":
            raise VideoProviderError(f"status_{state}: {_error_code(body)}")
        content = body.get("content")
        url = content.get("video_url") if isinstance(content, dict) else None
        usage_raw = body.get("usage")
        tokens = count(usage_raw.get("completion_tokens")) if isinstance(usage_raw, dict) else None
        model = body.get("model")
        return JobStatus(
            "done",
            video_url=url if isinstance(url, str) else None,
            usage=Usage(output_tokens=tokens) if tokens is not None else None,
            model_id=model if isinstance(model, str) else None,
        )
