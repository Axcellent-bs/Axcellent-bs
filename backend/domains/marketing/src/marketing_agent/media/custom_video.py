"""선언형 커스텀 영상 어댑터(공급자 이름 `custom:<id>`). 스펙은 `custom_spec.py`가 정한다.

제출 → 작업 ID → 상태 조회(폴링) → (필요하면 결과 조회) → 영상 URL. 공통
뼈대(`AsyncVideoProvider`)가
요청 검사·폴링 상한·내려받기(공용 주소만, 64MB)·무음·저장을 한다. 이 파일이 더하는 것:

- 모든 요청은 공통 뼈대의 `PublicOnlyTransport`(연결 직전 DNS 해석·사설/메타데이터 IP 차단)를
  지난다.
  리다이렉트는 따라가지 않는다(3xx = 실패). 스펙의 호스트는 운영자 허용 목록에 있어야 한다.
- 키는 환경변수에서 읽어 인증 헤더에만 쓴다. 로그·예외·결과에 값이 없다(예외는 사유 코드뿐).
  인증 헤더는 baseUrl 호스트로만 간다(상태 조회 주소가 응답에 있어도 같은 호스트가 아니면 거부).
- 응답은 1MB까지만 읽고, 한 작업당 상태 조회 횟수(`limits.maxPolls`), 총 대기(`poll.timeoutSec`),
  공급자별 시간당 제출 수(`MAX_SUBMITS_PER_HOUR`)를 지킨다. 비용은 스펙 `pricing`이 단가표
역할을 한다.
- 꺼져 있는(`enabled=false`) 공급자는 호출 전에 거부한다.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import time
from collections import deque
from typing import Any
from urllib.parse import quote, urlsplit

import httpx

from marketing_agent.media.base import MediaRequest, MediaResult
from marketing_agent.media.custom_spec import PLACEHOLDER_RE, CustomVideoSpec, allowed_hosts
from marketing_agent.media.safe_fetch import fetch_public
from marketing_agent.media.video_common import (
    AsyncVideoProvider,
    JobStatus,
    VideoProviderError,
)

MAX_RESPONSE_BYTES = 1024 * 1024
MAX_SUBMITS_PER_HOUR = 20
ASPECT_RATIO = "9:16"
JWT_TTL_S = 1800
_SUBMITS: dict[str, deque[float]] = {}
_STEP = re.compile(r"[A-Za-z0-9_-]+|\[(\d+)\]")


def dig(body: Any, path: str) -> Any:
    """점 표기 부분집합(`a.b[0].c`, 앞의 `$.`는 무시). 없으면 None."""
    cur = body
    for match in _STEP.finditer(path.removeprefix("$.")):
        token = match.group(0)
        if token.startswith("["):
            idx = int(match.group(1))
            cur = cur[idx] if isinstance(cur, list) and idx < len(cur) else None
        else:
            cur = cur.get(token) if isinstance(cur, dict) else None
        if cur is None:
            return None
    return cur


def render(value: Any, variables: dict[str, Any]) -> Any:
    """문자열 전체가 자리표시자면 원래 형(숫자 등)으로, 섞여 있으면 문자열로 바꾼다."""
    if isinstance(value, str):
        whole = PLACEHOLDER_RE.fullmatch(value)
        if whole:
            return variables[whole.group(1)]
        return PLACEHOLDER_RE.sub(lambda m: str(variables[m.group(1)]), value)
    if isinstance(value, dict):
        return {k: render(v, variables) for k, v in value.items()}
    if isinstance(value, list):
        return [render(v, variables) for v in value]
    return value


def _drop_empty(value: Any, names: set[str]) -> Any:
    if isinstance(value, dict):
        return {
            k: _drop_empty(v, names)
            for k, v in value.items()
            if not (k in names and v in ("", None))
        }
    if isinstance(value, list):
        return [_drop_empty(v, names) for v in value]
    return value


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def sign_jwt(access_key: str, secret_key: str, now: float) -> str:
    """Kling식 HS256 JWT. exp = 지금 + 30분, nbf = 지금 - 5초."""
    head = _b64(json.dumps({"alg": "HS256", "typ": "JWT"}, separators=(",", ":")).encode())
    claims = {"iss": access_key, "exp": int(now) + JWT_TTL_S, "nbf": int(now) - 5}
    body = _b64(json.dumps(claims, separators=(",", ":")).encode())
    sig = hmac.new(secret_key.encode(), f"{head}.{body}".encode(), hashlib.sha256).digest()
    return f"{head}.{body}.{_b64(sig)}"


def _safe_code(value: Any) -> str:
    text = re.sub(r"[^A-Za-z0-9_.-]", "", str(value))[:60]
    return text or "unknown"


class CustomVideoProvider(AsyncVideoProvider):
    def __init__(self, spec: CustomVideoSpec, *args: Any, **kwargs: Any) -> None:
        kwargs.setdefault("poll_interval_s", float(spec.poll.intervalSec))
        kwargs.setdefault("poll_max_wait_s", float(spec.poll.timeoutSec))
        super().__init__(*args, **kwargs)
        self.spec = spec
        self.name = spec.provider_name  # type: ignore[misc]
        self.key_env = spec.auth.secretRef  # type: ignore[misc]
        self.resolutions = tuple(spec.limits.resolutions)  # type: ignore[misc]
        self.min_duration_s = spec.limits.minDurationSec  # type: ignore[misc]
        self.max_duration_s = spec.limits.maxDurationSec  # type: ignore[misc]
        self._base = spec.baseUrl.rstrip("/")
        self._polls = 0
        self._poll_url: str | None = None
        self._now = time.time

    # --- 공통 도우미 ---
    def generate(self, request: MediaRequest) -> MediaResult:
        if not self.spec.enabled:
            raise VideoProviderError("custom_provider_disabled")
        if urlsplit(self._base).hostname not in allowed_hosts():
            raise VideoProviderError("host_not_allowed")
        return super().generate(request)

    def _headers(self, key: str) -> dict[str, str]:
        auth = self.spec.auth
        secret = key
        if auth.type == "jwt_hs256":
            access = os.getenv(auth.accessKeyRef or "", "").strip()
            if not access:
                raise VideoProviderError("api_key_not_configured")
            secret = sign_jwt(access, key, self._now())
        if any(ord(c) < 32 or ord(c) == 127 for c in secret):
            raise VideoProviderError("api_key_invalid")
        headers = {"Content-Type": "application/json", **self.spec.extraHeaders}
        headers[auth.headerName] = auth.valueTemplate.replace("{{secret}}", secret)
        return headers

    def _url(self, path: str, variables: dict[str, Any]) -> str:
        quoted = {k: quote(str(v), safe="") for k, v in variables.items()}
        return self._base + str(render(path, quoted))

    def _call(
        self, client: httpx.Client, method: str, url: str, headers: dict[str, str], body: Any = None
    ) -> Any:
        """JSON 응답을 1MB까지만 읽는다. 2xx가 아니면 사유 코드만 올린다."""
        with client.stream(method, url, headers=headers, json=body) as response:
            if not 200 <= response.status_code < 300:
                raise VideoProviderError(f"http_{response.status_code}")
            chunks: list[bytes] = []
            total = 0
            for chunk in response.iter_bytes():
                total += len(chunk)
                if total > MAX_RESPONSE_BYTES:
                    raise VideoProviderError("response_too_large")
                chunks.append(chunk)
        try:
            return json.loads(b"".join(chunks))
        except ValueError:
            raise VideoProviderError("invalid_json") from None

    def _variables(self, request: MediaRequest) -> dict[str, Any]:
        return {
            "prompt": request.prompt,
            "model": request.model,
            "durationSec": request.duration_s,
            "resolution": request.resolution,
            "aspectRatio": ASPECT_RATIO,
        }

    def _check_rate(self) -> None:
        recent = _SUBMITS.setdefault(self.name, deque())
        now = time.monotonic()
        while recent and now - recent[0] > 3600:
            recent.popleft()
        if len(recent) >= MAX_SUBMITS_PER_HOUR:
            raise VideoProviderError("rate_limited")
        recent.append(now)

    # --- AsyncVideoProvider ---
    def submit(self, client: httpx.Client, request: MediaRequest, key: str) -> str:
        self._check_rate()
        spec = self.spec.submit
        variables = self._variables(request)
        body = _drop_empty(render(spec.bodyTemplate, variables), set(spec.omitIfEmpty))
        url = self._url(spec.path, {"model": request.model})
        payload = self._call(client, spec.method, url, self._headers(key), body)
        task = dig(payload, spec.taskIdPath)
        if isinstance(task, int) and not isinstance(task, bool):
            task = str(task)
        if not isinstance(task, str) or not task or len(task) > 200:
            raise VideoProviderError("no_job_id")
        if spec.pollUrlPath:
            found = dig(payload, spec.pollUrlPath)
            if isinstance(found, str) and found:
                parts = urlsplit(found)
                if parts.scheme != "https" or parts.hostname != urlsplit(self._base).hostname:
                    raise VideoProviderError("poll_url_origin_mismatch")
                self._poll_url = found
        self._model = request.model
        return task

    def status(self, client: httpx.Client, job_id: str, key: str) -> JobStatus:
        self._polls += 1
        if self._polls > self.spec.limits.maxPolls:
            raise VideoProviderError("poll_limit")
        poll = self.spec.poll
        headers = self._headers(key)
        model = getattr(self, "_model", "")
        url = self._poll_url or self._url(poll.path, {"taskId": job_id, "model": model})
        payload = self._call(client, poll.method, url, headers)
        raw = dig(payload, poll.statusPath)
        if raw is None:
            raise VideoProviderError("status_missing")
        state = str(raw).strip().lower()
        if state in {v.strip().lower() for v in poll.failureValues}:
            detail = dig(payload, poll.errorPath) if poll.errorPath else None
            raise VideoProviderError(f"status_{_safe_code(state)}: {_safe_code(detail)}")
        if state not in {v.strip().lower() for v in poll.successValues}:
            return JobStatus("pending")
        result = self.spec.result
        source = payload
        if result.path:
            extra = {"taskId": job_id, "model": model}
            for name, expr in result.pathVars.items():
                value = dig(payload, expr)
                if not isinstance(value, str | int) or isinstance(value, bool):
                    raise VideoProviderError("result_var_missing")
                extra[name] = str(value)
            source = self._call(client, "GET", self._url(result.path, extra), headers)
        url_out = dig(source, result.videoUrlPath)
        if not isinstance(url_out, str) or not url_out:
            raise VideoProviderError("no_video_in_response")
        host = urlsplit(url_out).hostname or ""
        allowed = {urlsplit(self._base).hostname, *result.allowedHosts} & allowed_hosts()
        if urlsplit(url_out).scheme != "https" or host not in allowed:
            raise VideoProviderError("result_host_not_allowed")
        return JobStatus("done", video_url=url_out)

    def _download(self, client: httpx.Client, url: str) -> bytes:
        base_host = urlsplit(self._base).hostname
        auth_headers = {self.spec.auth.headerName: ""}

        def headers_for(hop: str) -> dict[str, str]:
            if self.spec.result.urlNeedsAuth and urlsplit(hop).hostname == base_host:
                return {k: v for k, v in self._download_headers.items() if k in auth_headers}
            return {}

        return fetch_public(
            client,
            url,
            resolver=self._resolver,
            headers_for=headers_for,
            max_bytes=64 * 1024 * 1024,
            monotonic=self._monotonic,
            error=VideoProviderError,
        )

    @property
    def _download_headers(self) -> dict[str, str]:
        key = os.getenv(self.key_env, "").strip()
        return self._headers(key)
