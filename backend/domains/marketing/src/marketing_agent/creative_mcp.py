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
- 영상은 `use_unlim=false`를 늘 보낸다. 안 보내면 무료 무제한이 있는 계정에서 작업을 만들지 않고
  "무제한을 쓸까요" 질문(`unlim_choice`)만 돌려준다. 사람이 승인한 것은 크레딧으로 만드는 일이다
- 그림(이미지 시트·스토리보드·썸네일)의 무제한 여부는 `image_unlim()` 한 곳(`IMAGE_USE_UNLIM`).
  11/24 운영 계정이 무제한 불가라 지금은 False(크레딧: 잔액으로 미리 막고 `credits_insufficient`).
  무제한 설정일 때 무제한이 안 되는 응답(`unlim_choice`·"Unlimited generations aren't available"·
  크레딧이 필요하다는 오류·`credits_insufficient`)이면 크레딧으로 다시 시도하지 않고
  `image_unlim_unavailable`로 멈춘다. 무제한이면 미리 보기도 잔액으로 막지 않는다
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
# 11/24 영상 모델 능력 (models_explore로 확인한 것만).
# 모르는 모델은 지금 동작 = start_image·기존 상한
# - kling_4_0_flash: medias role start_image·image_references(최대 10)·video_references, 3~20초
# - kling3_0_turbo: start_image만
VIDEO_MODELS: dict[str, dict[str, Any]] = {
    "kling_4_0_flash": {"image_references": True, "max_image_references": 10, "max_s": 20},
    "kling3_0_turbo": {"image_references": False},
}
WAIT_TIMEOUT_S = 15  # jobs_wait 상한
MAX_WAITS = 40  # 15초 × 40 = 10분
FAILED = {"failed", "nsfw", "canceled", "ip_detected", "submission_failed"}
CONTEXT = "AXcellent marketing creative record render"
_UUID = re.compile(r"[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}")
_CREDIT_WORDS = ("insufficient", "not enough credit", "credits")

# 그림을 무제한으로만 만드나 (11/24). 나중에 회사 설정(무제한만 / 크레딧 써도 됨)으로 바꿀 자리는
# 여기 한 곳이다(`image_unlim()`만 바꾸면 그림 params·미리 보기·멈춤 판단이 같이 따라간다)
# 11/24 운영(wr_e783b8075f)에서 이 계정은 무제한이 안 된다고 확인했다
# ("Unlimited generations aren't available on this account", 그림 모델 모두 unlim.available=false).
# 재현 님 결정: 그림은 크레딧으로. 모델은 서버 설정 MARKETING_HIGGSFIELD_MCP_IMAGE_MODEL
# (예: gpt_image_2_6, 코드에 박지 않는다)
IMAGE_USE_UNLIM = False
UNLIM_UNAVAILABLE = "image_unlim_unavailable"
# 무제한이 안 된다는 뜻으로 보는 사유 코드 (크레딧으로 다시 시도하지 않는다)
_NO_UNLIM_CODES = ("unlim_choice", "credits_insufficient")
# 무제한을 거절하는 Higgsfield 문장 (11/24 실제 응답:
# "Unlimited generations aren't available on this account. Retry without use_unlim to generate
# with credits.")
_UNLIM_REFUSED = re.compile(
    r"unlimited[^.]{0,60}(?:aren'?t|are not|is not|isn'?t|not)\s+available"
    r"|retry without use_unlim|unlim[^.]{0,30}not available",
    re.IGNORECASE,
)


def image_unlim() -> bool:
    """그림을 무제한으로만 만드나. 지금은 False(크레딧). 회사 설정(hf-settings)은 나중에 여기로."""
    return IMAGE_USE_UNLIM


def uses_unlim(params: dict[str, Any]) -> bool:
    return params.get("use_unlim") is True


def unlim_unavailable(error: McpError) -> McpError | None:
    """무제한으로 만들던 그림이 무제한이 안 된다는 답을 받았으면 `image_unlim_unavailable`.

    unlim_choice·credits_insufficient, 또는 크레딧이 필요하다는 문장의 tool_error·작업 실패.
    원인 칸(단계·도구·문장)은 그대로 옮기고 원래 사유는 error_class 자리에 남긴다."""
    detail = (getattr(error, "detail", None) or "").lower()
    credit_said = any(w in detail for w in (*_CREDIT_WORDS, "unlim", "upgrade"))
    refused = bool(_UNLIM_REFUSED.search(detail))  # 무제한 거절 문장 (tool_error·rpc_error 모두)
    said = error.code in ("tool_error", "rpc_error") and (credit_said or refused)
    if error.code in _NO_UNLIM_CODES or said:
        return McpError(UNLIM_UNAVAILABLE, stage=getattr(error, "stage", None),
                        tool=getattr(error, "tool", None), detail=getattr(error, "detail", None),
                        error_class=error.code)  # fmt: skip
    return None


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
        raise McpError("unauthorized", tool=name)  # pragma: no cover - 위에서 끝난다


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
    """만들기 전 예상 크레딧과 남은 크레딧을 본다. 모자라면 `credits_insufficient`.

    무제한 params(`use_unlim=true`, 11/24 그림)는 크레딧을 쓰지 않으므로 잔액으로 막지 않는다
    (예상 0도 그대로 통과). 무제한이 안 된다는 답이면 `image_unlim_unavailable`."""
    unlim = uses_unlim(params)
    try:
        est = await hf.call(ESTIMATE_TOOLS[kind], {"params": params})
    except McpError as error:
        if unlim and (no := unlim_unavailable(error)) is not None:
            raise no from error
        raise
    cost_box = est.get("cost") if isinstance(est.get("cost"), dict) else {}
    cost = _number(cost_box.get("credits_exact"))
    if cost is None:
        cost = _number(cost_box.get("credits"))
    credits, _ = await balance(hf)
    if not unlim and cost is not None and credits is not None and credits < cost:
        raise McpError("credits_insufficient")
    return cost


# 11/24 presigned 업로드 헤더. media_upload 결과에 헤더 칸(headers·required_headers)이 있으면
# 이 허용 목록 안의 것만 붙인다. 없어도 주소의 X-Amz-SignedHeaders에 if-none-match가 있으면
# `If-None-Match: *`를 붙인다(11/24 Claude 쪽 연결로 본 주소가 content-type;host;if-none-match였다)
UPLOAD_HEADER_FIELDS = ("headers", "required_headers")
_ALLOWED_UPLOAD = ("content-type", "if-none-match", "content-md5", "cache-control")


def _allowed_upload(name: str) -> bool:
    low = name.strip().lower()
    return low in _ALLOWED_UPLOAD or low.startswith("x-amz-")


def signed_headers(url: str) -> set[str]:
    """presigned 주소의 `X-Amz-SignedHeaders` (소문자 이름들). 없으면 빈 집합."""
    from urllib.parse import parse_qs, urlsplit

    query = parse_qs(urlsplit(url).query)
    raw = next((v[0] for k, v in query.items() if k.lower() == "x-amz-signedheaders" and v), "")
    return {h.strip().lower() for h in raw.split(";") if h.strip()}


def upload_headers(first: dict[str, Any], url: str, content_type: str) -> dict[str, str]:
    """업로드 PUT에 붙일 헤더.

    결과 헤더 칸이 먼저, 없으면 Content-Type + 서명에 든 If-None-Match."""
    headers = {"Content-Type": content_type}
    given: dict[str, Any] = {}
    for field in UPLOAD_HEADER_FIELDS:
        box = first.get(field)
        if isinstance(box, dict):
            given = box
            break
    for name, value in given.items():
        if isinstance(name, str) and isinstance(value, str | int) and _allowed_upload(name):
            if name.strip().lower() == "content-type":
                headers["Content-Type"] = str(value)
            else:
                headers[name.strip()] = str(value)
    has_inm = any(n.lower() == "if-none-match" for n in headers)
    if not has_inm and "if-none-match" in signed_headers(url):
        headers["If-None-Match"] = "*"
    return headers


async def upload(hf: Higgsfield, data: bytes, filename: str, content_type: str) -> str:
    """참조 그림을 올리고 확인한 `media_id`."""
    from marketing_agent.higgsfield_mcp import (
        bad_header,
        clean_detail,
        clean_value,
        from_http_error,
    )

    tool, stage = "media_upload", "upload_put"
    out = await hf.call(tool, {"filename": filename, "content_type": content_type})
    uploads = out.get("uploads")
    first = uploads[0] if isinstance(uploads, list) and uploads else None
    if not isinstance(first, dict):
        raise McpError("upload_url_missing", stage=stage, tool=tool)
    url, media_id = first.get("upload_url"), first.get("media_id")
    if not (isinstance(url, str) and isinstance(media_id, str)):
        raise McpError("upload_url_missing", stage=stage, tool=tool)
    # MCP가 준 칸은 앞뒤 공백을 지우고, 공백·제어문자가 남으면 client_error (칸 이름만)
    url = clean_value(url, "upload_url", stage=stage, tool=tool)
    if not url.startswith("https://"):
        raise McpError("upload_url_missing", stage=stage, tool=tool)
    kind = clean_value(first.get("content_type") or content_type, "content_type",
                       stage=stage, tool=tool)  # fmt: skip
    headers = upload_headers(first, url, kind)
    if (name := bad_header(headers)) is not None:  # 보내기 전에: 헤더 이름만
        raise McpError("client_error", stage=stage, tool=tool, detail=f"header {name}")
    try:
        put = await hf.client.put(url, content=data, headers=headers)  # 인증 헤더는 보내지 않는다
    except (httpx.HTTPError, httpx.InvalidURL) as error:
        raise from_http_error(error, stage=stage, tool=tool) from error
    if put.status_code not in (200, 201, 204):
        raise McpError(f"upload_http_{put.status_code}", stage=stage, tool=tool,
                       status=put.status_code, detail=clean_detail(put.text[:400]))  # fmt: skip
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


def image_params(model: str, prompt: str, negative: str, aspect: str,
                 unlim: bool | None = None) -> dict[str, Any]:  # fmt: skip
    """그림 요청 params(참조 그림 없이. 미리 보기에도 쓴다). 피할 것은 프롬프트 끝에 붙인다.

    `use_unlim`은 `image_unlim()`(11/24: 무제한만). 부르는 쪽이 `unlim`을 주면 그 값."""
    text = prompt[:8000]
    if negative:
        text = f"{text}\n\nAvoid: {negative[:1000]}"
    use = image_unlim() if unlim is None else unlim
    return {"model": model, "prompt": text, "aspect_ratio": aspect, "use_unlim": use}


def video_params(model: str, prompt: str, aspect: str, seconds: int) -> dict[str, Any]:
    return {
        "model": model,
        "prompt": prompt[:2000],
        "aspect_ratio": aspect,
        "duration": seconds,
        "use_unlim": False,
    }


def takes_image_references(model: str) -> bool:
    """영상 모델이 참조 이미지(`image_references`)를 받는지. 모르는 모델이면 False(지금 동작)."""
    return bool(VIDEO_MODELS.get(model.strip(), {}).get("image_references"))


def video_max_seconds(model: str) -> int | None:
    """영상 모델 길이 상한(아는 모델만)."""
    value = VIDEO_MODELS.get(model.strip(), {}).get("max_s")
    return int(value) if value else None


def with_media(params: dict[str, Any], media_id: str, role: str) -> dict[str, Any]:
    return {**params, "medias": [{"value": media_id, "role": role}]}


def with_medias(params: dict[str, Any], media_ids: list[str], role: str) -> dict[str, Any]:
    """같은 역할로 여러 장(순서 그대로)."""
    return {**params, "medias": [{"value": m, "role": role} for m in media_ids]}


def max_image_references(model: str) -> int:
    """영상 모델이 받는 `image_references` 최대 장수. 아는 값이 없으면 1(대표 한 장)."""
    value = VIDEO_MODELS.get(model.strip(), {}).get("max_image_references")
    return int(value) if value else 1
