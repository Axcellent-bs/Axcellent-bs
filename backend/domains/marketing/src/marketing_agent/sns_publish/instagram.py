"""Instagram 게시 어댑터 인터페이스와 에러 분류 (설계 6절, 분석 A-1).

- 어댑터는 Graph API Content Publishing 흐름을 그대로 따른다:
  컨테이너 생성 → 상태 조회 → `media_publish`(1회) → 미디어 조회.
- `MARKETING_SNS_PROVIDER`는 `mock`(기본)과 `instagram`(`instagram_graph.py`, Instagram Login)만
  쓴다. 그 밖의 값이나 `instagram`인데 설정이 비었으면 `get_adapter()`가 `AdapterUnavailable`을
  올려 **호출 전에** 거부한다.
- 토큰·앱 시크릿은 환경변수 **이름만** 여기서 정한다. 이 패키지는 값을 읽지도, 출력하지도 않는다.
- Facebook 로그인 경로의 삭제(`DELETE /{media-id}`)는 별도 액션(가칭 `unpublish_sns`, C 제안)으로만
  쓰고 `execute()`를 거쳐야 한다. 여기에는 인터페이스 자리만 둔다(`delete_media`).
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Literal, Protocol

# 값은 사람이 .env에 넣는다. 코드·로그·출력에 값을 내지 않는다.
SECRET_ENV_NAMES = (
    "MARKETING_IG_ACCESS_TOKEN",
    "MARKETING_IG_USER_ID",
    "MARKETING_IG_APP_SECRET",
)
TOKEN_ENV, USER_ID_ENV = SECRET_ENV_NAMES[0], SECRET_ENV_NAMES[1]  # 이름을 쓰는 곳은 이 파일뿐
PROVIDER_ENV = "MARKETING_SNS_PROVIDER"
PROVIDER_MOCK = "mock"

MediaKind = Literal["image", "reel"]
ContainerState = Literal["IN_PROGRESS", "FINISHED", "ERROR", "EXPIRED", "PUBLISHED"]


class AdapterUnavailable(RuntimeError):
    """설정이 실제 모드인데 실제 어댑터가 아직 없다."""


class IgApiError(Exception):
    """Graph API가 에러 응답을 돌려줬다(요청은 서버에 닿았다). 메시지에 토큰·URL을 담지 않는다."""

    def __init__(self, code: int, subcode: int | None = None) -> None:
        super().__init__(f"instagram api error code={code} subcode={subcode}")
        self.code = code
        self.subcode = subcode


class IgResponseLost(Exception):
    """요청은 보냈지만 응답을 받지 못했다(타임아웃·연결 끊김). 서버에서 처리됐는지 모른다."""


@dataclass(frozen=True)
class ContainerRequest:
    media_ref: str  # asset_ref. 어댑터가 공개 URL 등으로 바꾼다. 로그에 원문을 남기지 않는다
    media_kind: MediaKind
    caption: str
    is_ai_generated: bool
    share_to_feed: bool  # 기본값이 문서에 없으므로 항상 명시해서 보낸다


@dataclass(frozen=True)
class ContainerStatus:
    status_code: ContainerState
    error_code: int | None = None
    error_subcode: int | None = None
    media_id: str | None = None  # PUBLISHED일 때 알 수 있으면


@dataclass(frozen=True)
class MediaInfo:
    media_id: str
    permalink: str | None
    timestamp: str | None
    media_product_type: str | None
    is_ai_generated: bool | None  # 응답에 없으면 None


class InstagramAdapter(Protocol):
    name: str

    async def create_container(self, request: ContainerRequest) -> str: ...

    async def get_container_status(self, container_id: str) -> ContainerStatus: ...

    async def media_publish(self, container_id: str) -> str: ...

    async def get_media(self, media_id: str) -> MediaInfo: ...

    async def delete_media(self, media_id: str) -> None:
        """Facebook 로그인 경로의 삭제. 별도 액션이 `executed`를 받은 뒤에만 부른다."""
        ...


# ---- 에러 분류 (분석 A-1 표). 모르는 코드는 재시도하지 않고 실패(fail-closed) ----
Action = Literal["retry", "new_container", "fail"]


@dataclass(frozen=True)
class ErrorRule:
    code: int
    subcode: int | None
    failure: str
    action: Action


ERROR_RULES: tuple[ErrorRule, ...] = (
    ErrorRule(9, 2207042, "ig_daily_limit", "fail"),
    ErrorRule(4, 2207051, "ig_spam_suspected", "fail"),
    ErrorRule(25, 2207050, "ig_account_restricted", "fail"),
    ErrorRule(24, 2207006, "ig_media_not_found", "new_container"),
    ErrorRule(24, 2207008, "ig_container_missing", "retry"),
    ErrorRule(9004, 2207052, "ig_media_url_unreachable", "fail"),
    ErrorRule(352, 2207026, "ig_video_format", "fail"),
    ErrorRule(-2, 2207003, "ig_download_timeout", "retry"),
    ErrorRule(-2, 2207020, "ig_media_expired", "new_container"),
    ErrorRule(-1, 2207001, "ig_server_error", "retry"),
    ErrorRule(-1, 2207032, "ig_create_failed", "retry"),
    ErrorRule(-1, 2207053, "ig_upload_failed", "retry"),
    ErrorRule(9007, 2207027, "ig_not_ready", "retry"),
)
TOKEN_ERROR_CODE = 190  # 공식 표에 없음 [확인 필요]. 사람이 토큰을 다시 발급한다


def classify(code: int | None, subcode: int | None = None) -> tuple[str, Action]:
    """(실패 코드, 처리). 재시도는 `retry`만 백오프로, `new_container`는 컨테이너 단계에서 1회."""
    if code == TOKEN_ERROR_CODE:
        return "ig_token_invalid", "fail"
    for rule in ERROR_RULES:
        if rule.code == code and (rule.subcode is None or rule.subcode == subcode):
            return rule.failure, rule.action
    return "ig_unknown_error", "fail"


PROVIDER_INSTAGRAM = "instagram"


def get_adapter() -> InstagramAdapter:
    """설정된 어댑터. `mock`(기본)과 `instagram`(Instagram Login 실제 어댑터)만 쓴다.

    `instagram`은 명시적으로 켜야 한다. 토큰·계정 ID가 비어 있으면 호출 전에 거부한다
    (메시지에는 변수 이름만 담는다).
    """
    provider = (os.getenv(PROVIDER_ENV) or PROVIDER_MOCK).strip().lower()
    if provider == PROVIDER_INSTAGRAM:
        from marketing_agent.sns_publish.instagram_graph import (
            GraphConfigError,
            GraphInstagramAdapter,
        )

        try:
            return GraphInstagramAdapter.from_env()
        except GraphConfigError as error:
            raise AdapterUnavailable(str(error)) from None
    if provider != PROVIDER_MOCK:
        raise AdapterUnavailable("알 수 없는 MARKETING_SNS_PROVIDER (mock 또는 instagram만 가능)")
    from marketing_agent.sns_publish.instagram_mock import MockInstagramAdapter

    return _mock_singleton(MockInstagramAdapter)


_mock: InstagramAdapter | None = None


def _mock_singleton(factory: type) -> InstagramAdapter:
    global _mock
    if _mock is None:
        _mock = factory()
    return _mock


def reset_adapter() -> None:
    """테스트 초기화용."""
    global _mock
    _mock = None


def set_adapter(adapter: InstagramAdapter | None) -> None:
    """테스트가 가짜 어댑터를 끼운다."""
    global _mock
    _mock = adapter
