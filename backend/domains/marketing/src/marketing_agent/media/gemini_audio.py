"""Gemini 오디오(BGM) 어댑터(공급자 이름 `gemini`, 모델 ID는 설정값 `MARKETING_AUDIO_MODEL`).

공식 문서(https://aistudio.google.com/docs/music-generation, https://aistudio.google.com/docs/pricing,
2026-11-11 확인)가 말하는 호출 형태만 쓴다. 이미지·영상 어댑터와 같은 Interactions API다.

- 요청: `POST /v1beta/interactions`, 헤더 `x-goog-api-key`(URL에 키를 넣지 않는다)·`Api-Revision`,
  본문 `{"model", "input": <텍스트>, "response_format": {"type": "audio"}}`.
- 응답: `steps[type=model_output].content[type=audio]`의 `data`(base64 MP3, 44.1kHz 스테레오).
- 모델: `lyria-3-clip-preview`(30초 고정 클립), `lyria-3.5`(풀 곡, 약 2분). 어느 쪽이든 모델 ID는
  `MARKETING_AUDIO_MODEL`에서만 온다. 단가표에 있는 것만 비용 관문을 통과한다.
- 연주곡 전용: 문서는 전용 파라미터 없이 프롬프트에 "Instrumental only, no vocals."를 쓰라고 한다.
  그래서 연주곡 제한 문구(`INSTRUMENTAL_CLAUSE`)를 프롬프트에 항상 덧붙인다.
- 모든 출력에 SynthID 워터마크가 들어간다(문서). 가사·구조 텍스트 블록은 쓰지 않는다.
- [확인 필요] WAV 지정 방법(문서에 없음, MP3만), `store` 필드 지원, 오디오 응답의 `usage`.
  확인되지 않은 필드는 보내지 않는다.
- 키는 `MARKETING_AUDIO_API_KEY`에서만 읽고 로그·예외·결과에 내지 않는다.
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
from marketing_agent.media.gemini_video import API_REVISION, ENDPOINT

NAME = "gemini"
REQUEST_TIMEOUT_S = 300.0
MAX_AUDIO_BYTES = 32 * 1024 * 1024
INSTRUMENTAL_CLAUSE = "Instrumental only, no vocals, no lyrics, no spoken words, no sound effects."
_MP3_MIMES = ("audio/mpeg", "audio/mp3")


class GeminiAudioError(Exception):
    """어댑터 실패. 메시지에는 키·응답 본문을 넣지 않는다."""


def with_instrumental_clause(prompt: str) -> str:
    """연주곡 제한 문구가 없으면 덧붙인다. 요청은 항상 이 함수를 거쳐 나간다."""
    text = prompt.strip()
    return text if INSTRUMENTAL_CLAUSE in text else f"{text} {INSTRUMENTAL_CLAUSE}".strip()


def is_mp3(data: bytes) -> bool:
    """ID3 태그 또는 MPEG 오디오 프레임 동기 비트로 시작하는지만 본다."""
    return data[:3] == b"ID3" or (len(data) > 2 and data[0] == 0xFF and data[1] & 0xE0 == 0xE0)


def _count(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def _find_audio(body: dict[str, Any]) -> dict[str, Any]:
    for step in body.get("steps") or []:
        if not isinstance(step, dict) or step.get("type") != "model_output":
            continue
        for block in step.get("content") or []:
            if isinstance(block, dict) and block.get("type") == "audio" and block.get("data"):
                if str(block.get("mime_type") or "audio/mpeg") not in _MP3_MIMES:
                    raise GeminiAudioError("unsupported_audio_mime")
                return block
    raise GeminiAudioError("no_audio_in_response")


class GeminiAudioProvider:
    name = NAME

    def __init__(self, transport: httpx.BaseTransport | None = None) -> None:
        self._transport = transport  # 테스트용 가짜 전송

    def generate(self, request: MediaRequest) -> MediaResult:
        if request.media_kind != "audio":
            raise GeminiAudioError("unsupported_media_kind")
        if not request.model:
            raise GeminiAudioError("model_not_configured")
        key = os.getenv(API_KEY_ENVS["audio"], "").strip()
        if not key:
            raise GeminiAudioError("api_key_not_configured")

        payload = {
            "model": request.model,
            "input": with_instrumental_clause(request.prompt),
            "response_format": {"type": "audio"},
        }
        headers = {
            "x-goog-api-key": key,
            "Api-Revision": API_REVISION,
            "Content-Type": "application/json",
        }
        try:
            with httpx.Client(timeout=REQUEST_TIMEOUT_S, transport=self._transport) as client:
                response = client.post(ENDPOINT, json=payload, headers=headers)
        except httpx.HTTPError as error:
            raise GeminiAudioError(f"request_failed: {type(error).__name__}") from None
        if response.status_code != 200:
            raise GeminiAudioError(f"http_{response.status_code}")
        try:
            body = response.json()
        except ValueError:
            raise GeminiAudioError("invalid_json") from None
        if not isinstance(body, dict):
            raise GeminiAudioError("invalid_json")
        if body.get("status") not in (None, "completed"):
            raise GeminiAudioError(f"status_{body.get('status')}")
        block = _find_audio(body)
        try:
            data = base64.b64decode(str(block["data"]), validate=True)
        except (binascii.Error, ValueError):
            raise GeminiAudioError("invalid_audio_data") from None
        if not data or len(data) > MAX_AUDIO_BYTES or not is_mp3(data):
            raise GeminiAudioError("invalid_audio")

        path = request.output_dir / "asset.mp3"
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
