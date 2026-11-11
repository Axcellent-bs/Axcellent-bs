"""Gemini 영상 어댑터(공급자 이름 `gemini`, 모델 ID는 설정값 `MARKETING_VIDEO_MODEL`).

공식 문서(https://aistudio.google.com/docs/omni, https://aistudio.google.com/docs/video,
2026-11-11 확인)가 말하는 호출 형태만 쓴다. 이미지 어댑터와 같은 Interactions API다.

- 요청: `POST /v1beta/interactions`, 헤더 `x-goog-api-key`(URL에 키를 넣지 않는다), `Api-Revision`,
  본문 `{"model", "input": <텍스트>, "background": false, "store": false, "stream": false,
  "response_format": {"type": "video", "aspect_ratio", "resolution": "720p", "duration": "<N>s",
  "delivery": "uri"}}`. 문서는 동기 단발 생성(`background=false`)을 기본으로 설명한다.
- 응답: `steps[type=model_output].content[type=video]`에 `data`(base64) 또는 `uri`
  (`.../v1beta/files/<id>:download?alt=media`). `uri`면 `GET /v1beta/files/<id>`의 `state`가
  `ACTIVE`가 될 때까지 폴링(`FAILED`면 실패)한 뒤 `:download?alt=media`로 받는다.
  폴링 간격·총 대기 상한은 생성자 인자이며, 상한을 넘으면 실패한다.
- 규격은 '릴스·쇼츠 통합 9:16' 한 가지다: 종횡비 9:16, 해상도 `720p`, 길이 3~8초
  (문서 하한 3초·상한 15초, 단가표 `max_duration_s` 8). 허용 조합은 단가표에 있는 것만이다.
  1080p는 문서가 9:16 해상도를 밝히지 않아 단가표에 없고, 업스케일(ffmpeg)은 여기서 하지 않는다.
- 모델 ID는 코드에 두지 않는다. `MARKETING_VIDEO_MODEL`로 gemini-omni-1.1-flash·1.2-flash 중
  단가표에 있는 것을 고른다(2026-12-03부터 1.1 요청은 1.2로 라우팅·청구된다).
- 무음: 문서에 오디오 끄기 옵션이 없다(기본으로 오디오 생성). `remove_audio=True`인데 받은 mp4에
  오디오 트랙이 있으면 파일을 쓰지 않고 실패시킨다(오디오를 쓰지 않는다).
- 키는 `MARKETING_VIDEO_API_KEY`에서만 읽고 로그·예외·결과에 내지 않는다. 키는 Google API
  호스트에만 보내고, 리다이렉트는 직접 따라가며 다른 호스트로는 키를 보내지 않는다.
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import os
import re
import time
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any
from urllib.parse import urljoin, urlsplit

import httpx

from marketing_agent.media.base import MediaRequest, MediaResult, Usage
from marketing_agent.media.config import API_KEY_ENVS

NAME = "gemini"
API_HOST = "generativelanguage.googleapis.com"
ENDPOINT = f"https://{API_HOST}/v1beta/interactions"
FILES_ENDPOINT = f"https://{API_HOST}/v1beta/files"
API_REVISION = "2026-05-20"
ALLOWED_RESOLUTIONS = ("720p",)  # 단가표에 있는 9:16 해상도만
RESOLUTION = ALLOWED_RESOLUTIONS[0]
ASPECT_RATIO = "9:16"  # 릴스·쇼츠 통합 규격 한 가지
MIN_DURATION_S = 3  # 문서: duration 3s~15s
MAX_DURATION_S = 8  # 단가표 max_duration_s·safety 기준
REQUEST_TIMEOUT_S = 300.0  # 동기 생성 1회
POLL_INTERVAL_S = 5.0
POLL_MAX_WAIT_S = 120.0
MAX_VIDEO_BYTES = 64 * 1024 * 1024
MAX_REDIRECTS = 3
_FILE_ID = re.compile(r"^/v1beta/files/([A-Za-z0-9_-]+)(?::download)?$")


class GeminiVideoError(Exception):
    """어댑터 실패. 메시지에는 키·응답 본문을 넣지 않는다."""


def _count(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


def has_audio_track(mp4: bytes) -> bool:
    """mp4의 `hdlr` 박스 핸들러 타입이 `soun`인 트랙이 있는지 본다(전체 파싱은 하지 않는다)."""
    start = 0
    while (at := mp4.find(b"hdlr", start)) != -1:
        # hdlr: type(4) + version/flags(4) + pre_defined(4) + handler_type(4)
        if mp4[at + 12 : at + 16] == b"soun":
            return True
        start = at + 4
    return False


def _find_video(body: dict[str, Any]) -> dict[str, Any]:
    for step in body.get("steps") or []:
        if not isinstance(step, dict) or step.get("type") != "model_output":
            continue
        for block in step.get("content") or []:
            if (
                isinstance(block, dict)
                and block.get("type") == "video"
                and (block.get("data") or block.get("uri"))
            ):
                if str(block.get("mime_type") or "video/mp4") != "video/mp4":
                    raise GeminiVideoError("unsupported_video_mime")
                return block
    raise GeminiVideoError("no_video_in_response")


def _json(response: httpx.Response) -> dict[str, Any]:
    try:
        body = response.json()
    except ValueError:
        raise GeminiVideoError("invalid_json") from None
    if not isinstance(body, dict):
        raise GeminiVideoError("invalid_json")
    return body


class GeminiVideoProvider:
    name = NAME

    def __init__(
        self,
        transport: httpx.BaseTransport | None = None,
        *,
        poll_interval_s: float = POLL_INTERVAL_S,
        poll_max_wait_s: float = POLL_MAX_WAIT_S,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._transport = transport  # 테스트용 가짜 전송
        self._poll_interval_s = poll_interval_s
        self._poll_max_wait_s = poll_max_wait_s
        self._sleep = sleep
        self._monotonic = monotonic

    def generate(self, request: MediaRequest) -> MediaResult:
        if request.media_kind != "video":
            raise GeminiVideoError("unsupported_media_kind")
        if request.resolution not in ALLOWED_RESOLUTIONS:
            raise GeminiVideoError(f"unsupported_resolution: {request.resolution}")
        duration = request.duration_s
        if duration is None or not MIN_DURATION_S <= duration <= MAX_DURATION_S:
            raise GeminiVideoError(f"unsupported_duration: {duration}")
        if not request.model:
            raise GeminiVideoError("model_not_configured")
        key = os.getenv(API_KEY_ENVS["video"], "").strip()
        if not key:
            raise GeminiVideoError("api_key_not_configured")

        payload = {
            "model": request.model,
            "input": request.prompt,
            "background": False,
            "store": False,
            "stream": False,
            "response_format": {
                "type": "video",
                "aspect_ratio": ASPECT_RATIO,
                "resolution": RESOLUTION,
                "duration": f"{duration}s",
                "delivery": "uri",
            },
        }
        headers = {
            "x-goog-api-key": key,
            "Api-Revision": API_REVISION,
            "Content-Type": "application/json",
        }
        try:
            with httpx.Client(timeout=REQUEST_TIMEOUT_S, transport=self._transport) as client:
                response = client.post(ENDPOINT, json=payload, headers=headers)
                if response.status_code != 200:
                    raise GeminiVideoError(f"http_{response.status_code}")
                body = _json(response)
                if body.get("status") not in (None, "completed"):
                    raise GeminiVideoError(f"status_{body.get('status')}")
                data = self._video_bytes(client, _find_video(body), key)
        except httpx.HTTPError as error:
            raise GeminiVideoError(f"request_failed: {type(error).__name__}") from None

        if not data or len(data) > MAX_VIDEO_BYTES:
            raise GeminiVideoError("invalid_video_size")
        if request.remove_audio and has_audio_track(data):
            raise GeminiVideoError("audio_track_present")
        path = request.output_dir / "asset.mp4"
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

    def _video_bytes(self, client: httpx.Client, block: dict[str, Any], key: str) -> bytes:
        if block.get("data"):
            try:
                return base64.b64decode(str(block["data"]), validate=True)
            except (binascii.Error, ValueError):
                raise GeminiVideoError("invalid_video_data") from None
        parts = urlsplit(str(block["uri"]))
        match = _FILE_ID.match(parts.path)
        if parts.scheme != "https" or parts.hostname != API_HOST or match is None:
            raise GeminiVideoError("unexpected_video_uri")
        file_id = match.group(1)
        self._wait_active(client, file_id, key)
        return self._download(client, f"{FILES_ENDPOINT}/{file_id}:download?alt=media", key)

    def _wait_active(self, client: httpx.Client, file_id: str, key: str) -> None:
        started = self._monotonic()
        while True:
            response = client.get(f"{FILES_ENDPOINT}/{file_id}", headers={"x-goog-api-key": key})
            if response.status_code != 200:
                raise GeminiVideoError(f"http_{response.status_code}")
            state = _json(response).get("state")
            if state == "ACTIVE":
                return
            if state not in (None, "PROCESSING", "STATE_UNSPECIFIED"):
                raise GeminiVideoError(f"file_state_{state}")  # FAILED 등
            if self._monotonic() - started + self._poll_interval_s > self._poll_max_wait_s:
                raise GeminiVideoError("poll_timeout")
            self._sleep(self._poll_interval_s)

    @staticmethod
    def _download(client: httpx.Client, url: str, key: str) -> bytes:
        for _ in range(MAX_REDIRECTS + 1):
            # 키는 Google API 호스트에만 보낸다. 리다이렉트는 직접 따라간다.
            send_key = urlsplit(url).hostname == API_HOST
            response = client.get(url, headers={"x-goog-api-key": key} if send_key else {})
            if response.status_code in (301, 302, 303, 307, 308):
                location = response.headers.get("location")
                if not location:
                    raise GeminiVideoError("invalid_redirect")
                url = urljoin(url, location)
                if urlsplit(url).scheme != "https":
                    raise GeminiVideoError("invalid_redirect")
                continue
            if response.status_code != 200:
                raise GeminiVideoError(f"http_{response.status_code}")
            return response.content
        raise GeminiVideoError("too_many_redirects")
