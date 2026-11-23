"""회사 Higgsfield 계정(원격 MCP)으로 그림·영상 만들기 도우미 (2026-11-23).

`creative_render.py`가 회사 계정 연결이 있을 때 이 함수들을 부른다. 돈이 나가기 전 순서(예산·장부·
안전 관문)는 `creative_render.py`가 그대로 맡고, 여기는 Higgsfield와 주고받기만 한다.

도구(2026-11-23 `tools/list`의 입력 형식으로 맞췄다. 만들기 도구는 실제로 불러 보지 않았다):
- 미리 보기: `estimate_image_cost`·`estimate_video_cost`(`params`는 만들기와 같다, 작업을 만들지
  않는다) + `balance`(`credits`·`subscription_plan_type`). 크레딧이 모자라면 `credits_insufficient`
- 참조 그림: `media_upload`(파일 이름) → 받은 `upload_url`에 PUT(인증 헤더 없이) → `media_confirm`
  → `media_id`를 `params.medias[{value, role}]`에 넣는다. 역할 이름은 모델마다 다르다(확인한 값:
  nano_banana_pro 이미지 참조 `image_references`, kling3_0_turbo 등 영상 첫 장면 `start_image`)
- 만들기: 화면(위젯)을 여는 `generate_image`·`generate_video` 대신 `generate_image_batch`·
  `generate_video_batch`(요청 1개) → `job_id`
- 기다리기: `jobs_wait`(`timeout_seconds` 15 이하, 오래 기다려 주는 조회) → completed면 `result_url`
- `use_unlim=false`를 늘 보낸다. 안 보내면 무료 무제한이 있는 계정에서 작업을 만들지 않고 "무제한을
  쓸까요" 질문(`unlim_choice`)만 돌려준다. 사람이 승인한 것은 크레딧으로 만드는 일이다
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from typing import Any

import httpx

from marketing_agent import higgsfield_mcp_auth as auth
from marketing_agent.higgsfield_mcp import McpError, McpSession

ESTIMATE_TOOLS = {"image": "estimate_image_cost", "video": "estimate_video_cost"}
GENERATE_TOOLS = {"image": "generate_image_batch", "video": "generate_video_batch"}
IMAGE_REF_ROLE = "image_references"
VIDEO_START_ROLE = "start_image"
WAIT_TIMEOUT_S = 15  # jobs_wait 상한
MAX_WAITS = 40  # 15초 × 40 = 10분
FAILED = {"failed", "nsfw", "canceled", "ip_detected", "submission_failed"}
CONTEXT = "AXcellent marketing creative record render"
_UUID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
_CREDIT_WORDS = ("insufficient", "not enough credit", "credits")


class Higgsfield:
    """한 회사 계정의 MCP 연결. 401이면 access token을 한 번 새로 받아 다시 부른다."""

    def __init__(self, client: httpx.AsyncClient, company_id: str) -> None:
        self.client = client
        self.company_id = company_id
        self._session: McpSession | None = None

    async def call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        for attempt in (0, 1):
            if self._session is None:
                token = await auth.access_token(self.client, self.company_id, force=attempt == 1)
                self._session = McpSession(self.client, token)
            try:
                return await self._session.call(name, arguments)
            except McpError as error:
                if error.code != "unauthorized" or attempt == 1:
                    raise
                auth.forget(self.company_id)
                self._session = None
        raise McpError("unauthorized")  # pragma: no cover - 위에서 끝난다


def _number(value: object) -> float | None:
    if isinstance(value, bool) or not isinstance(value, int | float):
        return None
    return float(value)


async def balance(hf: Higgsfield) -> tuple[float | None, str | None]:
    """(남은 크레딧, 구독 요금제). 모르는 칸은 None."""
    out = await hf.call("balance", {})
    plan = out.get("subscription_plan_type")
    return _number(out.get("credits")), (plan[:40] if isinstance(plan, str) and plan else None)


async def preflight(hf: Higgsfield, kind: str, params: dict[str, Any]) -> float | None:
    """만들기 전 예상 크레딧과 남은 크레딧을 본다. 모자라면 `credits_insufficient`."""
    est = await hf.call(ESTIMATE_TOOLS[kind], {"params": params})
    cost_box = est.get("cost") if isinstance(est.get("cost"), dict) else {}
    cost = _number(cost_box.get("credits_exact"))
    if cost is None:
        cost = _number(cost_box.get("credits"))
    credits, _ = await balance(hf)
    if cost is not None and credits is not None and credits < cost:
        raise McpError("credits_insufficient")
    return cost


async def upload(hf: Higgsfield, data: bytes, filename: str, content_type: str) -> str:
    """참조 그림을 올리고 확인한 `media_id`."""
    out = await hf.call("media_upload", {"filename": filename, "content_type": content_type})
    uploads = out.get("uploads")
    first = uploads[0] if isinstance(uploads, list) and uploads else None
    if not isinstance(first, dict):
        raise McpError("upload_url_missing")
    url, media_id = first.get("upload_url"), first.get("media_id")
    if not (isinstance(url, str) and url.startswith("https://") and isinstance(media_id, str)):
        raise McpError("upload_url_missing")
    headers = {"Content-Type": str(first.get("content_type") or content_type)}
    put = await hf.client.put(url, content=data, headers=headers)  # 인증 헤더는 보내지 않는다
    if put.status_code not in (200, 201, 204):
        raise McpError(f"upload_http_{put.status_code}")
    await hf.call("media_confirm", {"type": "image", "media_id": media_id})
    return media_id


def _job_failure(job: dict[str, Any]) -> McpError:
    status = str(job.get("status") or "unknown")
    if any(w in str(job.get("error") or "").lower() for w in _CREDIT_WORDS):
        return McpError("credits_insufficient")
    return McpError(f"job_{status}")


async def submit(hf: Higgsfield, kind: str, params: dict[str, Any]) -> str:
    """작업 하나를 만들고 `job_id`."""
    out = await hf.call(
        GENERATE_TOOLS[kind], {"requests": [{"index": 0, "params": params}], "context": CONTEXT}
    )
    jobs = out.get("jobs")
    job = jobs[0] if isinstance(jobs, list) and jobs and isinstance(jobs[0], dict) else None
    if job is None:
        raise McpError("no_job")
    if job.get("status") in FAILED:
        raise _job_failure(job)
    job_id = job.get("job_id")
    if not isinstance(job_id, str) or not _UUID.fullmatch(job_id):
        raise McpError("no_job_id")
    return job_id


async def wait(hf: Higgsfield, job_id: str, sleep: Callable[[float], Awaitable[None]]) -> str:
    """끝날 때까지 기다려 결과 주소. 실패·시간 초과면 McpError."""
    for _ in range(MAX_WAITS):
        out = await hf.call(
            "jobs_wait",
            {"jobs": [{"index": 0, "job_id": job_id}], "timeout_seconds": WAIT_TIMEOUT_S},
        )
        jobs = out.get("jobs")
        job = jobs[0] if isinstance(jobs, list) and jobs and isinstance(jobs[0], dict) else {}
        status = job.get("status")
        if status == "completed":
            url = job.get("result_url")
            if not isinstance(url, str) or not url.startswith("https://"):
                raise McpError("no_result_url")
            return url
        if status in FAILED:
            raise _job_failure(job)
        if status == "lookup_failed" and job.get("retryable") is not True:
            raise McpError("job_lookup_failed")
        pause = _number(out.get("poll_after_seconds"))
        await sleep(min(pause, WAIT_TIMEOUT_S) if pause else 0.0)
    raise McpError("job_timeout")


def image_params(model: str, prompt: str, negative: str, aspect: str) -> dict[str, Any]:
    """그림 요청 params(참조 그림 없이. 미리 보기에도 쓴다). 피할 것은 프롬프트 끝에 붙인다."""
    text = prompt[:8000]
    if negative:
        text = f"{text}\n\nAvoid: {negative[:1000]}"
    return {"model": model, "prompt": text, "aspect_ratio": aspect, "use_unlim": False}


def video_params(model: str, prompt: str, aspect: str, seconds: int) -> dict[str, Any]:
    return {
        "model": model,
        "prompt": prompt[:2000],
        "aspect_ratio": aspect,
        "duration": seconds,
        "use_unlim": False,
    }


def with_media(params: dict[str, Any], media_id: str, role: str) -> dict[str, Any]:
    return {**params, "medias": [{"value": media_id, "role": role}]}
