"""S3 호환 미디어 호스트 (Instagram이 내려받을 이미지 주소를 만든다).

Meta는 게시 시점에 미디어를 **공개 HTTPS 주소**에서 직접 내려받는다. 서버가 사내망이라
파일을 직접 내줄 수 없으므로, 게시할 JPEG를 S3 호환 버킷(AWS S3, Cloudflare R2, MinIO 등)에
올리고 **유효기간이 있는 서명 URL**(SigV4, 읽기 전용)을 Meta에 넘긴다. 버킷은 비공개로 둔다.

- 외부 라이브러리 없이 SigV4를 직접 서명한다(`httpx`만 쓴다). 새 의존성이 없다.
- 오브젝트 이름은 파일 내용의 sha256 앞부분이라 같은 파일을 다시 올려도 같은 이름이다
  (올리기가 멱등). 원래 파일 이름은 버킷에 남기지 않는다.
- 이미지(JPEG)와 릴스용 영상(mp4)을 올린다. 영상 키는 고정 이름이 아니라 미디어 ID가 들어간
  고유 키이고(`video/<미디어 ID>-<해시>.mp4`), Content-Type은 `video/mp4`다. 크기 상한 300MB.
- 서명 URL에는 서명이 들어 있어 로그·예외·결과에 내지 않는다. 실패 원인도 닫힌 코드로만 올린다.
- 키 값은 환경변수에서만 읽는다. 이 파일은 환경변수 **이름**만 정의한다.

| 변수 | 뜻 | 기본 |
| --- | --- | --- |
| `MARKETING_MEDIA_S3_BUCKET` | 버킷 이름. **비우면 이 기능을 쓰지 않는다**(켜는 스위치) | 없음 |
| `MARKETING_MEDIA_S3_ACCESS_KEY_ID` | 접근 키 ID(비밀) | 없음 |
| `MARKETING_MEDIA_S3_SECRET_ACCESS_KEY` | 비밀 접근 키(비밀) | 없음 |
| `MARKETING_MEDIA_S3_ENDPOINT` | S3 호환 서비스 주소(https, 경로 없이) | AWS |
| `MARKETING_MEDIA_S3_REGION` | 리전. R2는 `auto` | `us-east-1` (R2면 `auto`) |
| `MARKETING_MEDIA_S3_PATH_STYLE` | `true`면 주소/버킷/키 방식 | 주소를 주면 `true` |

R2는 주소를 `https://<계정 ID>.r2.cloudflarestorage.com`으로 준다.
| `MARKETING_MEDIA_S3_PREFIX` | 오브젝트 이름 앞에 붙일 폴더 | `ig/` |
| `MARKETING_MEDIA_S3_URL_TTL_S` | 서명 URL 유효 시간(초, 1~86400) | `3600` |
"""

from __future__ import annotations

import hashlib
import hmac
import os
import re
from collections.abc import Callable
from datetime import UTC, datetime
from urllib.parse import quote, urlsplit

import httpx

BUCKET_ENV = "MARKETING_MEDIA_S3_BUCKET"
ENDPOINT_ENV = "MARKETING_MEDIA_S3_ENDPOINT"
REGION_ENV = "MARKETING_MEDIA_S3_REGION"
PATH_STYLE_ENV = "MARKETING_MEDIA_S3_PATH_STYLE"
PREFIX_ENV = "MARKETING_MEDIA_S3_PREFIX"
TTL_ENV = "MARKETING_MEDIA_S3_URL_TTL_S"
ACCESS_KEY_ENV = "MARKETING_MEDIA_S3_ACCESS_KEY_ID"
SECRET_KEY_ENV = "MARKETING_MEDIA_S3_SECRET_ACCESS_KEY"
SECRET_ENV_NAMES = (ACCESS_KEY_ENV, SECRET_KEY_ENV)  # 이름을 쓰는 곳은 이 파일뿐

DEFAULT_REGION = "us-east-1"
DEFAULT_PREFIX = "ig/"
DEFAULT_TTL_S = 3600
MAX_TTL_S = 86400  # Instagram 컨테이너 유효 24시간을 넘길 이유가 없다
KEY_HASH_CHARS = 24
VIDEO_MAX_BYTES = 300 * 1024 * 1024  # Instagram Reels 파일 상한
_VIDEO_ID_RE = re.compile(r"[^A-Za-z0-9_-]")
_ALGORITHM = "AWS4-HMAC-SHA256"


class MediaHostConfigError(RuntimeError):
    """설정이 비었거나 틀렸다. 메시지에는 변수 **이름**만 담는다."""


class MediaHostError(Exception):
    """올리기 실패. `code`는 닫힌 집합이고 서버 응답·URL·키를 담지 않는다."""

    def __init__(self, code: str) -> None:
        super().__init__(f"media host error: {code}")
        self.code = code


def _hmac(key: bytes, message: str) -> bytes:
    return hmac.new(key, message.encode("utf-8"), hashlib.sha256).digest()


def _truthy(raw: str) -> bool:
    return raw.strip().lower() in ("1", "true", "yes", "on")


class S3MediaHost:
    def __init__(
        self,
        *,
        bucket: str,
        access_key_id: str,
        secret_access_key: str,
        region: str = DEFAULT_REGION,
        endpoint: str = "",
        path_style: bool | None = None,
        prefix: str = DEFAULT_PREFIX,
        url_ttl_s: int = DEFAULT_TTL_S,
        timeout_s: float = 30.0,
        client: httpx.AsyncClient | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        if not bucket:
            raise MediaHostConfigError(f"설정이 비어 있다: {BUCKET_ENV}")
        endpoint = endpoint.strip().rstrip("/")
        base = endpoint or f"https://s3.{region}.amazonaws.com"
        parts = urlsplit(base)
        if parts.scheme != "https" or not parts.netloc or parts.path not in ("", "/"):
            # Meta가 받을 서명 URL은 https여야 한다. 주소에 경로가 있으면 서명 대상이 달라진다
            raise MediaHostConfigError(f"https 주소여야 한다(경로 없이): {ENDPOINT_ENV}")
        self._bucket = bucket
        self._access_key = access_key_id
        self._secret_key = secret_access_key
        self._region = region
        self._netloc = parts.netloc
        self._path_style = bool(endpoint) if path_style is None else path_style
        stripped = prefix.strip().strip("/")
        self._prefix = f"{stripped}/" if stripped else ""
        self._ttl = max(1, min(int(url_ttl_s), MAX_TTL_S))
        self._timeout = timeout_s
        self._client = client
        self._clock = clock or (lambda: datetime.now(UTC))

    # ---- 설정 ----
    @classmethod
    def from_env_optional(cls, client: httpx.AsyncClient | None = None) -> S3MediaHost | None:
        """버킷 이름이 없으면 None(기능 꺼짐). 버킷이 있는데 키가 비면 이름만 담아 거부한다."""
        bucket = (os.getenv(BUCKET_ENV) or "").strip()
        if not bucket:
            return None
        access = (os.getenv(ACCESS_KEY_ENV) or "").strip()
        secret = (os.getenv(SECRET_KEY_ENV) or "").strip()
        missing = [n for n, v in ((ACCESS_KEY_ENV, access), (SECRET_KEY_ENV, secret)) if not v]
        if missing:
            raise MediaHostConfigError(f"설정이 비어 있다: {', '.join(missing)}")
        endpoint = (os.getenv(ENDPOINT_ENV) or "").strip()
        region = (os.getenv(REGION_ENV) or "").strip()
        if not region:
            region = "auto" if "r2.cloudflarestorage.com" in endpoint else DEFAULT_REGION
        style_raw = (os.getenv(PATH_STYLE_ENV) or "").strip()
        prefix = os.getenv(PREFIX_ENV)
        try:
            ttl = int((os.getenv(TTL_ENV) or "").strip() or DEFAULT_TTL_S)
        except ValueError:
            ttl = DEFAULT_TTL_S
        return cls(
            bucket=bucket,
            access_key_id=access,
            secret_access_key=secret,
            region=region,
            endpoint=endpoint,
            path_style=_truthy(style_raw) if style_raw else None,
            prefix=DEFAULT_PREFIX if prefix is None else prefix,
            url_ttl_s=ttl,
            client=client,
        )

    # ---- 주소 ----
    def object_key(self, content: bytes) -> str:
        """내용 해시로 이름을 만든다(같은 파일 = 같은 이름). 원래 파일 이름은 남기지 않는다."""
        return f"{self._prefix}{hashlib.sha256(content).hexdigest()[:KEY_HASH_CHARS]}.jpg"

    def video_object_key(self, content: bytes, media_id: str) -> str:
        """미디어 ID + 내용 해시로 고유 키를 만든다. 고정 이름을 쓰지 않는다."""
        safe_id = _VIDEO_ID_RE.sub("", media_id)[:64] or "media"
        digest = hashlib.sha256(content).hexdigest()[:KEY_HASH_CHARS]
        return f"{self._prefix}video/{safe_id}-{digest}.mp4"

    def _target(self, key: str) -> tuple[str, str]:
        """(host, 정규화된 경로). 경로는 서명에 그대로 들어간다."""
        encoded = quote(key, safe="/")
        if self._path_style:
            return self._netloc, f"/{quote(self._bucket, safe='')}/{encoded}"
        return f"{self._bucket}.{self._netloc}", f"/{encoded}"

    def _scope(self, date: str) -> str:
        return f"{date}/{self._region}/s3/aws4_request"

    def _sign(self, amz_date: str, canonical_request: str) -> str:
        digest = hashlib.sha256(canonical_request.encode("utf-8")).hexdigest()
        string_to_sign = "\n".join([_ALGORITHM, amz_date, self._scope(amz_date[:8]), digest])
        return self._signature(amz_date[:8], string_to_sign)

    def _signature(self, date: str, string_to_sign: str) -> str:
        k_date = _hmac(f"AWS4{self._secret_key}".encode(), date)
        k_region = _hmac(k_date, self._region)
        k_service = _hmac(k_region, "s3")
        k_signing = _hmac(k_service, "aws4_request")
        return hmac.new(k_signing, string_to_sign.encode("utf-8"), hashlib.sha256).hexdigest()

    # ---- 서명 URL (읽기 전용) ----
    def presign_get(self, key: str, ttl_s: int | None = None) -> str:
        now = self._clock().astimezone(UTC)
        amz_date = now.strftime("%Y%m%dT%H%M%SZ")
        date = amz_date[:8]
        expires = max(1, min(int(ttl_s if ttl_s is not None else self._ttl), MAX_TTL_S))
        host, path = self._target(key)
        query = {
            "X-Amz-Algorithm": _ALGORITHM,
            "X-Amz-Credential": f"{self._access_key}/{self._scope(date)}",
            "X-Amz-Date": amz_date,
            "X-Amz-Expires": str(expires),
            "X-Amz-SignedHeaders": "host",
        }
        canonical_query = "&".join(
            f"{quote(k, safe='')}={quote(v, safe='')}" for k, v in sorted(query.items())
        )
        canonical = f"GET\n{path}\n{canonical_query}\nhost:{host}\n\nhost\nUNSIGNED-PAYLOAD"
        signature = self._sign(amz_date, canonical)
        return f"https://{host}{path}?{canonical_query}&X-Amz-Signature={signature}"

    # ---- 올리기 ----
    async def put_object(self, key: str, body: bytes, content_type: str = "image/jpeg") -> None:
        now = self._clock().astimezone(UTC)
        amz_date = now.strftime("%Y%m%dT%H%M%SZ")
        date = amz_date[:8]
        host, path = self._target(key)
        payload_hash = hashlib.sha256(body).hexdigest()
        signed_headers = "host;x-amz-content-sha256;x-amz-date"
        canonical_headers = (
            f"host:{host}\nx-amz-content-sha256:{payload_hash}\nx-amz-date:{amz_date}\n"
        )
        canonical = f"PUT\n{path}\n\n{canonical_headers}\n{signed_headers}\n{payload_hash}"
        authorization = (
            f"{_ALGORITHM} Credential={self._access_key}/{self._scope(date)}, "
            f"SignedHeaders={signed_headers}, Signature={self._sign(amz_date, canonical)}"
        )
        headers = {
            "Authorization": authorization,
            "x-amz-content-sha256": payload_hash,
            "x-amz-date": amz_date,
            "Content-Type": content_type,
        }
        http = self._client or httpx.AsyncClient()
        try:
            response = await http.put(
                f"https://{host}{path}", content=body, headers=headers, timeout=self._timeout
            )
        except httpx.HTTPError:
            # 예외 문자열에 주소가 들어갈 수 있어 사슬을 끊는다
            raise MediaHostError("upload_failed") from None
        finally:
            if self._client is None:
                await http.aclose()
        if not response.is_success:
            raise MediaHostError("upload_rejected")  # 응답 본문은 옮기지 않는다

    async def upload_jpeg(self, content: bytes) -> str:
        """JPEG 바이트를 올리고 읽기 전용 서명 URL을 돌려준다. JPEG·크기 검사는 부르는 쪽이 한다."""
        key = self.object_key(content)
        await self.put_object(key, content)
        return self.presign_get(key)

    async def upload_video(self, content: bytes, media_id: str) -> str:
        """mp4 바이트를 올리고 읽기 전용 서명 URL을 돌려준다. 크기를 넘으면 올리지 않는다."""
        if not content or len(content) > VIDEO_MAX_BYTES:
            raise MediaHostError("video_size")
        key = self.video_object_key(content, media_id)
        await self.put_object(key, content, content_type="video/mp4")
        return self.presign_get(key)
