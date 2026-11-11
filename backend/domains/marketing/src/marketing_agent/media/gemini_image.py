"""Gemini 이미지 어댑터(공급자 이름 `gemini`, 모델 ID는 설정값 `MARKETING_IMAGE_MODEL`).

공식 문서(https://aistudio.google.com/docs/image-generation, 2026-11-11 확인)는 이 모델을
Interactions API(`POST /v1beta/interactions`)로 부르게 한다. generateContent 예시는 문서에 없다.

- 요청: 헤더 `x-goog-api-key`(키는 URL에 넣지 않는다), `Api-Revision`, 본문
  `{"model", "input": [{"type": "text", "text": ...}], "response_format": {"type": "image",
  "mime_type", "aspect_ratio", "image_size": "1K"}}`.
- 응답: `steps[type=model_output].content[type=image].data`(base64), `usage.total_input_tokens` /
  `total_output_tokens`, `id`.
- 해상도 표(1K, 출력 1120토큰): 1:1 1024x1024, 4:5 928x1152, 9:16 768x1376.
  그 밖은 호출 전에 거부한다.
- 키는 `MARKETING_IMAGE_API_KEY`에서만 읽고 로그·예외·결과에 내지 않는다.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import os
from datetime import UTC, datetime
from typing import Any

import httpx

from marketing_agent.media.base import MediaRequest, MediaResult, Usage
from marketing_agent.media.config import API_KEY_ENVS

NAME = "gemini"
ENDPOINT = "https://generativelanguage.googleapis.com/v1beta/interactions"
API_REVISION = "2026-05-20"
IMAGE_SIZE = "1K"
REQUEST_MIME = "image/png"
TIMEOUT_S = 120.0
# 문서 표(Nano Banana 2.1, 1K): 해상도 -> 종횡비
ASPECT_RATIOS = {"1024x1024": "1:1", "928x1152": "4:5", "768x1376": "9:16"}
_SUFFIX = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp"}


class GeminiImageError(Exception):
    """어댑터 실패. 메시지에는 키·응답 본문을 넣지 않는다."""


def _count(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _find_image(body: dict[str, Any]) -> tuple[bytes, str]:
    for step in body.get("steps") or []:
        if not isinstance(step, dict) or step.get("type") != "model_output":
            continue
        for block in step.get("content") or []:
            if isinstance(block, dict) and block.get("type") == "image" and block.get("data"):
                mime = str(block.get("mime_type") or REQUEST_MIME)
                if mime not in _SUFFIX:
                    raise GeminiImageError("unsupported_image_mime")
                try:
                    return base64.b64decode(str(block["data"]), validate=True), mime
                except (binascii.Error, ValueError):
                    raise GeminiImageError("invalid_image_data") from None
    raise GeminiImageError("no_image_in_response")


class GeminiImageProvider:
    name = NAME

    def __init__(self, transport: httpx.BaseTransport | None = None) -> None:
        self._transport = transport  # 테스트용 가짜 전송

    def generate(self, request: MediaRequest) -> MediaResult:
        if request.media_kind != "image":
            raise GeminiImageError("unsupported_media_kind")
        aspect = ASPECT_RATIOS.get(request.resolution)
        if aspect is None:
            raise GeminiImageError(f"unsupported_resolution: {request.resolution}")
        if not request.model:
            raise GeminiImageError("model_not_configured")
        key = os.getenv(API_KEY_ENVS["image"], "").strip()
        if not key:
            raise GeminiImageError("api_key_not_configured")

        payload = {
            "model": request.model,
            "input": [{"type": "text", "text": request.prompt}],
            "response_format": {
                "type": "image",
                "mime_type": REQUEST_MIME,
                "aspect_ratio": aspect,
                "image_size": IMAGE_SIZE,
            },
        }
        headers = {
            "x-goog-api-key": key,
            "Api-Revision": API_REVISION,
            "Content-Type": "application/json",
        }
        try:
            with httpx.Client(timeout=TIMEOUT_S, transport=self._transport) as client:
                response = client.post(ENDPOINT, json=payload, headers=headers)
        except httpx.HTTPError as error:
            raise GeminiImageError(f"request_failed: {type(error).__name__}") from None
        if response.status_code != 200:
            raise GeminiImageError(f"http_{response.status_code}")
        try:
            body = response.json()
        except ValueError:
            raise GeminiImageError("invalid_json") from None
        if not isinstance(body, dict):
            raise GeminiImageError("invalid_json")
        if body.get("status") not in (None, "completed"):
            raise GeminiImageError(f"status_{body.get('status')}")

        data, mime = _find_image(body)
        path = request.output_dir / f"asset.{_SUFFIX[mime]}"
        path.write_bytes(data)
        usage_raw = body.get("usage")
        usage = None
        if isinstance(usage_raw, dict):
            usage = Usage(
                input_tokens=_count(usage_raw.get("total_input_tokens")),
                output_tokens=_count(usage_raw.get("total_output_tokens")),
            )
            if usage.input_tokens is None and usage.output_tokens is None:
                usage = None
        return MediaResult(
            asset_path=path,
            sha256=hashlib.sha256(data).hexdigest(),
            provider=NAME,
            model_id=str(body.get("model") or request.model),
            usage=usage,
            generated_at=datetime.now(UTC),
            provider_job_id=str(body.get("id") or f"gemini_{request.prompt_sha256[:12]}"),
        )
