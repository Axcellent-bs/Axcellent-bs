"""비동기 영상 공급자 공통 뼈대. 새 공급자는 이 클래스를 상속해 `submit`·`status` 두 개만 만든다.

공통으로 처리하는 것(공급자별 if/else를 두지 않는다):
- 요청 검사(영상만, 허용 해상도·길이, 모델·키 설정), 작업 생성 → 상태 폴링 →
  내려받기 → 무음 처리 → 저장.
- 폴링 간격·총 대기 상한은 생성자 인자이며 상한을 넘으면 실패한다.
- 내려받기는 https만, 키를 보내지 않는다(결과 URL은 서명된 주소다).
  리다이렉트는 https로만 직접 따라간다.
- 오디오 끄기 옵션이 없거나 믿을 수 없는 공급자를 위해 `remove_audio`면 받은 mp4에 오디오가 있을 때
  ffmpeg로 제거하고 다시 검사한다(실패하면 파일을 쓰지 않고 실패).
- 키는 `key_env`에서만 읽고 로그·예외·결과에 내지 않는다. 예외는 사유 코드만 담는다.
- 모델 ID는 코드에 두지 않는다. `request.model`(설정 `MARKETING_VIDEO_MODEL`)만 쓴다.
"""

from __future__ import annotations

import hashlib
import os
import socket
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, ClassVar, Literal

import httpx

from marketing_agent.media.base import MediaProviderError, MediaRequest, MediaResult, Usage
from marketing_agent.media.gemini_video import has_audio_track, strip_audio
from marketing_agent.media.safe_fetch import (
    PublicOnlyTransport,
    Resolver,
    fetch_public,
)
from marketing_agent.media.video_limits import VideoCaps

REQUEST_TIMEOUT_S = 60.0
POLL_INTERVAL_S = 5.0
POLL_MAX_WAIT_S = 600.0  # 작업 큐 방식은 동기 생성보다 오래 걸린다
MAX_VIDEO_BYTES = 64 * 1024 * 1024
MAX_REDIRECTS = 3  # fetch_public과 같은 값


class VideoProviderError(MediaProviderError):
    """영상 어댑터 실패. 메시지는 사유 코드뿐이다."""


@dataclass(frozen=True)
class JobStatus:
    state: Literal["pending", "done"]  # 실패는 `status()`가 VideoProviderError로 올린다
    video_url: str | None = None
    usage: Usage | None = None
    model_id: str | None = None


def json_object(response: httpx.Response) -> dict[str, Any]:
    try:
        body = response.json()
    except ValueError:
        raise VideoProviderError("invalid_json") from None
    if not isinstance(body, dict):
        raise VideoProviderError("invalid_json")
    return body


def count(value: Any) -> int | None:
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None


class AsyncVideoProvider:
    name: ClassVar[str]
    key_env: ClassVar[str]
    resolutions: ClassVar[tuple[str, ...]]
    min_duration_s: ClassVar[int]
    max_duration_s: ClassVar[int]

    @property
    def video_caps(self) -> VideoCaps:
        """단일 클립 규격. 연장·시작 이미지는 아직 보내지 않으므로 긴 영상은 이어 붙이지 못한다."""
        return VideoCaps(min_s=self.min_duration_s, max_s=self.max_duration_s)

    def __init__(
        self,
        transport: httpx.BaseTransport | None = None,
        *,
        poll_interval_s: float = POLL_INTERVAL_S,
        poll_max_wait_s: float = POLL_MAX_WAIT_S,
        sleep: Callable[[float], None] = time.sleep,
        monotonic: Callable[[], float] = time.monotonic,
        resolver: Resolver = socket.getaddrinfo,
    ) -> None:
        self._transport = transport  # 테스트용 가짜 전송
        self._resolver = resolver
        self._poll_interval_s = poll_interval_s
        self._poll_max_wait_s = poll_max_wait_s
        self._sleep = sleep
        self._monotonic = monotonic

    # --- 공급자가 채우는 부분 ---
    def submit(self, client: httpx.Client, request: MediaRequest, key: str) -> str:
        """작업을 만들고 작업 ID를 돌려준다."""
        raise NotImplementedError

    def status(self, client: httpx.Client, job_id: str, key: str) -> JobStatus:
        """작업 상태. 끝났으면 `done`+영상 URL, 실패면 VideoProviderError."""
        raise NotImplementedError

    # --- 공통 ---
    def generate(self, request: MediaRequest) -> MediaResult:
        if request.media_kind != "video":
            raise VideoProviderError("unsupported_media_kind")
        if request.resolution not in self.resolutions:
            raise VideoProviderError(f"unsupported_resolution: {request.resolution}")
        duration = request.duration_s
        if duration is None or not self.min_duration_s <= duration <= self.max_duration_s:
            raise VideoProviderError(f"unsupported_duration: {duration}")
        if not request.model:
            raise VideoProviderError("model_not_configured")
        key = os.getenv(self.key_env, "").strip()
        if not key:
            raise VideoProviderError("api_key_not_configured")
        try:
            # 실서비스(transport 없음)는 공용 주소로만 연결한다. 테스트 가짜 전송은 그대로 쓴다
            transport = self._transport or PublicOnlyTransport(self._resolver)
            with httpx.Client(timeout=REQUEST_TIMEOUT_S, transport=transport) as client:
                job_id = self.submit(client, request, key)
                done = self._wait(client, job_id, key)
                data = self._download(client, str(done.video_url))
        except httpx.HTTPError as error:
            raise VideoProviderError(f"request_failed: {type(error).__name__}") from None
        if not data or len(data) > MAX_VIDEO_BYTES:
            raise VideoProviderError("invalid_video_size")
        audio_stripped = False
        if request.remove_audio and has_audio_track(data):
            data = strip_audio(data, request.output_dir)
            audio_stripped = True
        path = request.output_dir / "asset.mp4"
        path.write_bytes(data)
        return MediaResult(
            asset_path=path,
            sha256=hashlib.sha256(data).hexdigest(),
            provider=self.name,
            model_id=done.model_id or request.model,
            usage=done.usage,
            generated_at=datetime.now(UTC),
            provider_job_id=job_id,
            audio_stripped=audio_stripped,
        )

    def _wait(self, client: httpx.Client, job_id: str, key: str) -> JobStatus:
        started = self._monotonic()
        while True:
            current = self.status(client, job_id, key)
            if current.state == "done":
                if not current.video_url:
                    raise VideoProviderError("no_video_in_response")
                return current
            if self._monotonic() - started + self._poll_interval_s > self._poll_max_wait_s:
                raise VideoProviderError("poll_timeout")
            self._sleep(self._poll_interval_s)

    def _download(self, client: httpx.Client, url: str) -> bytes:
        """https·공용 주소만, 홉마다 재검사. 키를 보내지 않는다. 크기·시간 상한을 지킨다."""
        return fetch_public(
            client,
            url,
            resolver=self._resolver,
            max_bytes=MAX_VIDEO_BYTES,
            monotonic=self._monotonic,
            error=VideoProviderError,
        )
