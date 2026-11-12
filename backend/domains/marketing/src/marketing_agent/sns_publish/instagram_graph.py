"""Instagram Login 경로의 실제 어댑터 (`graph.instagram.com`, Content Publishing).

- 흐름: 컨테이너 생성 → 상태 조회 → `media_publish`(1회) → 미디어 조회. 인터페이스는 목과 같다.
- 토큰·계정 ID는 환경변수에서만 읽는다(이름은 `instagram.SECRET_ENV_NAMES`).
  값은 로그·예외·결과에 내지 않는다. HTTP 예외는 URL(쿼리에 토큰이 붙는다)을 담고 있어서
  원인 사슬을 끊고 종류만 올린다.
- 공개 미디어 주소: Meta가 게시 시점에 미디어를 직접 내려받는다. `media_ref`가 `https://`로
  시작하면 그대로 쓰고, 로컬 경로면 `MARKETING_IG_MEDIA_BASE_URL`(공개 HTTPS 주소) 뒤에
  파일 이름을 붙인다. 파일을 그 주소에 올려 두는 일은 사람이 한다.
  `MARKETING_MEDIA_S3_BUCKET`이 있으면 대신 미디어 폴더의 JPEG를 S3 호환 버킷에 올리고
  유효기간 있는 서명 URL을 쓴다(`media_host.py`). 이 경로는 이미지(JPEG)만 지원한다.
- 이미지는 JPEG만 된다(공식 규격). 로컬 파일이면 호출 전에 JPEG 여부와 8MB 이하를 확인한다.
- 삭제 API(`delete_media`)는 이 로그인 경로에 없다. 잘못 올린 게시물은 사람이 앱에서 지운다.
- 상태 `ERROR`에서 사유 코드를 주는 필드는 `status`(문자열)뿐일 수 있어 코드는 None일 수 있다.
  모르는 코드는 재시도하지 않고 실패한다(`classify`).
- 캐러셀(이미지 2~10장): 자식 컨테이너(`is_carousel_item`) → 부모 컨테이너(`media_type=CAROUSEL`,
  `children`) → `media_publish`. AI 표시는 부모에만 보낸다(자식에 넣으면 오류, 설계 문서 6절).
  [확인 필요] 자식·부모 필드 이름(`is_carousel_item`·`media_type=CAROUSEL`·`children`)은 이 레포의
  Meta 공식 문서 대조 기록(sns-design.md)에 없다. 그래서 `MARKETING_IG_CAROUSEL_ENABLED=true`로
  사람이 켜기 전에는 `supports_carousel=False`라 `publish_sns`가 승인 요청 전에 거부한다.
- 응답 유실: 타임아웃·연결 끊김은 `IgResponseLost`로 올린다. 게시 호출은 같은 컨테이너로
  다시 보내지 않는 책임이 퍼블리셔에 있다.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import httpx

from marketing_agent import sns_media
from marketing_agent.sns_publish.instagram import (
    TOKEN_ENV,
    USER_ID_ENV,
    CarouselRequest,
    ContainerRequest,
    ContainerState,
    ContainerStatus,
    IgApiError,
    IgResponseLost,
    MediaInfo,
)
from marketing_agent.sns_publish.media_host import (
    MediaHostConfigError,
    MediaHostError,
    S3MediaHost,
)

MEDIA_BASE_URL_ENV = "MARKETING_IG_MEDIA_BASE_URL"
API_BASE_ENV = "MARKETING_IG_API_BASE_URL"
API_VERSION_ENV = "MARKETING_IG_API_VERSION"
TIMEOUT_ENV = "MARKETING_IG_TIMEOUT_S"
CAROUSEL_ENV = "MARKETING_IG_CAROUSEL_ENABLED"  # 필드 이름 확인 뒤 사람이 켠다

DEFAULT_API_BASE = "https://graph.instagram.com"
JPEG_MAX_BYTES = 8 * 1024 * 1024
_STATES = ("IN_PROGRESS", "FINISHED", "ERROR", "EXPIRED", "PUBLISHED")

# 코드 매핑(`classify`는 이 값으로 처리를 정한다). 이미지 검사 실패도 같은 표를 쓴다.
_MEDIA_URL_UNREACHABLE = (9004, 2207052)  # ig_media_url_unreachable
_MEDIA_FORMAT = (352, 2207026)  # ig_video_format (이미지 규격 위반에도 쓴다)
_SERVER_ERROR = (-1, 2207001)  # ig_server_error → 재시도


class GraphConfigError(RuntimeError):
    """필수 설정이 비었다. 메시지에는 변수 **이름**만 담는다."""


class GraphInstagramAdapter:
    name = "instagram"

    def __init__(
        self,
        *,
        access_token: str,
        user_id: str,
        media_base_url: str = "",
        api_base_url: str = DEFAULT_API_BASE,
        api_version: str = "",
        timeout_s: float = 30.0,
        client: httpx.AsyncClient | None = None,
        media_host: S3MediaHost | None = None,
        carousel_enabled: bool = False,
    ) -> None:
        self.supports_carousel = carousel_enabled
        self._media_host = media_host
        self._token = access_token
        self._user_id = user_id
        self._media_base = media_base_url.rstrip("/")
        base = api_base_url.rstrip("/")
        self._root = f"{base}/{api_version.strip('/')}" if api_version.strip("/") else base
        self._timeout = timeout_s
        self._client = client

    # ---- 설정 ----
    @classmethod
    def from_env(cls, client: httpx.AsyncClient | None = None) -> GraphInstagramAdapter:
        token = (os.getenv(TOKEN_ENV) or "").strip()
        user_id = (os.getenv(USER_ID_ENV) or "").strip()
        missing = [n for n, v in ((TOKEN_ENV, token), (USER_ID_ENV, user_id)) if not v]
        if missing:
            raise GraphConfigError(f"설정이 비어 있다: {', '.join(missing)}")
        try:
            timeout = float((os.getenv(TIMEOUT_ENV) or "").strip() or 30)
        except ValueError:
            timeout = 30.0
        try:
            media_host = S3MediaHost.from_env_optional()
        except MediaHostConfigError as error:
            raise GraphConfigError(str(error)) from None
        return cls(
            media_host=media_host,
            access_token=token,
            user_id=user_id,
            media_base_url=(os.getenv(MEDIA_BASE_URL_ENV) or "").strip(),
            api_base_url=(os.getenv(API_BASE_ENV) or "").strip() or DEFAULT_API_BASE,
            api_version=(os.getenv(API_VERSION_ENV) or "").strip(),
            timeout_s=timeout if timeout > 0 else 30.0,
            client=client,
            carousel_enabled=(os.getenv(CAROUSEL_ENV) or "").strip().lower()
            in ("1", "true", "yes"),
        )

    # ---- 미디어 주소·규격 ----
    def public_media_url(self, media_ref: str) -> str:
        if media_ref.startswith("https://"):
            return media_ref
        if media_ref.startswith("http://") or not self._media_base:
            # 공개 HTTPS 주소가 없으면 Meta가 내려받을 수 없다. 호출 전에 실패한다
            raise IgApiError(*_MEDIA_URL_UNREACHABLE)
        if not self._media_base.startswith("https://"):
            raise IgApiError(*_MEDIA_URL_UNREACHABLE)
        return f"{self._media_base}/{Path(media_ref).name}"

    @staticmethod
    def check_local_image(media_ref: str) -> None:
        """로컬 파일이면 JPEG와 크기를 호출 전에 본다(없거나 원격이면 건너뛴다)."""
        if media_ref.startswith(("https://", "http://")):
            return
        path = Path(media_ref)
        try:
            if not path.is_file():
                return
            size = path.stat().st_size
            with path.open("rb") as handle:
                head = handle.read(3)
        except OSError:
            return
        if head != b"\xff\xd8\xff" or size > JPEG_MAX_BYTES:
            raise IgApiError(*_MEDIA_FORMAT)

    async def _hosted_image_url(self, media_ref: str) -> str:
        """미디어 폴더 안의 JPEG를 S3 호환 버킷에 올리고 읽기 전용 서명 URL을 돌려준다.

        올리기 전에 폴더 안쪽 경로·JPEG·크기를 본다. 어느 단계가 실패해도 Graph API는 부르지 않는다.
        """
        assert self._media_host is not None
        try:
            path = sns_media.resolve_asset(media_ref)
        except sns_media.MediaPathError:
            raise IgApiError(*_MEDIA_URL_UNREACHABLE) from None
        try:
            content = path.read_bytes()
        except OSError:
            raise IgApiError(*_MEDIA_URL_UNREACHABLE) from None
        if content[:3] != b"\xff\xd8\xff" or len(content) > JPEG_MAX_BYTES:
            raise IgApiError(*_MEDIA_FORMAT)
        try:
            return await self._media_host.upload_jpeg(content)
        except MediaHostError:
            raise IgApiError(*_MEDIA_URL_UNREACHABLE) from None

    def _uses_media_host(self, media_ref: str) -> bool:
        return self._media_host is not None and not media_ref.startswith(("https://", "http://"))

    # ---- HTTP ----
    async def _request(
        self, method: str, path: str, *, params: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        url = f"{self._root}/{path.lstrip('/')}"
        payload = {**(params or {}), "access_token": self._token}
        http = self._client or httpx.AsyncClient()
        try:
            if method == "GET":
                response = await http.get(url, params=payload, timeout=self._timeout)
            else:
                response = await http.post(url, data=payload, timeout=self._timeout)
        except httpx.HTTPError:
            # 예외 문자열에 토큰이 든 URL이 들어갈 수 있어 사슬을 끊는다
            raise IgResponseLost(path.split("/")[-1]) from None
        finally:
            if self._client is None:
                await http.aclose()
        return self._parse(response)

    @staticmethod
    def _parse(response: httpx.Response) -> dict[str, Any]:
        try:
            body = response.json()
        except ValueError:
            body = None
        if response.is_success and isinstance(body, dict) and "error" not in body:
            return body
        error = body.get("error") if isinstance(body, dict) else None
        if isinstance(error, dict) and isinstance(error.get("code"), int):
            subcode = error.get("error_subcode")
            raise IgApiError(error["code"], subcode if isinstance(subcode, int) else None)
        if response.status_code >= 500:
            raise IgApiError(*_SERVER_ERROR)
        raise IgApiError(-1, None)  # 모르는 응답: classify가 실패로 닫는다

    # ---- 인터페이스 ----
    async def create_container(self, request: ContainerRequest) -> str:
        params: dict[str, Any] = {
            "caption": request.caption,
            "is_ai_generated": "true" if request.is_ai_generated else "false",
        }
        if request.media_kind == "image":
            if self._uses_media_host(request.media_ref):
                params["image_url"] = await self._hosted_image_url(request.media_ref)
            else:
                self.check_local_image(request.media_ref)
                params["image_url"] = self.public_media_url(request.media_ref)
        else:
            params["media_type"] = "REELS"
            params["video_url"] = self.public_media_url(request.media_ref)
            params["share_to_feed"] = "true" if request.share_to_feed else "false"
        body = await self._request("POST", f"{self._user_id}/media", params=params)
        container_id = body.get("id")
        if not isinstance(container_id, str) or not container_id:
            raise IgApiError(-1, None)
        return container_id

    async def _image_url(self, media_ref: str) -> str:
        if self._uses_media_host(media_ref):
            return await self._hosted_image_url(media_ref)
        self.check_local_image(media_ref)
        return self.public_media_url(media_ref)

    async def create_carousel_item(self, media_ref: str) -> str:
        if not self.supports_carousel:
            raise IgApiError(*_MEDIA_FORMAT)  # 켜지 않았다. 호출하지 않는다
        params: dict[str, Any] = {  # is_ai_generated는 자식에 넣지 않는다
            "image_url": await self._image_url(media_ref),
            "is_carousel_item": "true",
        }
        body = await self._request("POST", f"{self._user_id}/media", params=params)
        container_id = body.get("id")
        if not isinstance(container_id, str) or not container_id:
            raise IgApiError(-1, None)
        return container_id

    async def create_carousel_container(self, request: CarouselRequest) -> str:
        if not self.supports_carousel:
            raise IgApiError(*_MEDIA_FORMAT)
        params: dict[str, Any] = {
            "media_type": "CAROUSEL",
            "children": ",".join(request.child_ids),
            "caption": request.caption,
            "is_ai_generated": "true" if request.is_ai_generated else "false",
        }
        body = await self._request("POST", f"{self._user_id}/media", params=params)
        container_id = body.get("id")
        if not isinstance(container_id, str) or not container_id:
            raise IgApiError(-1, None)
        return container_id

    async def get_container_status(self, container_id: str) -> ContainerStatus:
        body = await self._request("GET", container_id, params={"fields": "status_code"})
        state = body.get("status_code")
        if state not in _STATES:
            raise IgApiError(-1, None)
        return ContainerStatus(status_code=_state(state))

    async def media_publish(self, container_id: str) -> str:
        body = await self._request(
            "POST", f"{self._user_id}/media_publish", params={"creation_id": container_id}
        )
        media_id = body.get("id")
        if not isinstance(media_id, str) or not media_id:
            raise IgApiError(-1, None)
        return media_id

    async def get_media(self, media_id: str) -> MediaInfo:
        # `is_ai_generated` 필드는 요청하지 않는다(모르는 필드면 호출 전체가 실패할 수 있다).
        # 앱 화면의 라벨은 사람이 따로 확인해 기록한다(설계 9-2).
        fields = "permalink,timestamp,media_product_type"
        body = await self._request("GET", media_id, params={"fields": fields})
        return MediaInfo(
            media_id=media_id,
            permalink=_text(body.get("permalink")),
            timestamp=_text(body.get("timestamp")),
            media_product_type=_text(body.get("media_product_type")),
            is_ai_generated=None,
        )

    async def delete_media(self, media_id: str) -> None:
        raise NotImplementedError("Instagram Login 경로에는 삭제 API가 없다. 앱에서 사람이 지운다")


def _state(value: str) -> ContainerState:
    return value  # type: ignore[return-value]  # _STATES로 이미 걸렀다


def _text(value: Any) -> str | None:
    return value if isinstance(value, str) else None
