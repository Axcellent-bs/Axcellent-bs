"""공식 SNS 채널 팔로워 수 (skill `channel_followers`, read, 11/18 기획 요청).

기획이 고객사 규모를 숫자로 재서 방향을 고를 때 쓴다. 계약은
`ax_contracts.schemas.marketing_channels.ChannelFollowersOutput`이다.

- 출처: Instagram Graph API `GET /{ig-user-id}?fields=followers_count` (Meta 공식 문서
  IG User 레퍼런스). 게시 어댑터와 같은 연결·토큰(`MARKETING_IG_*`)을 쓰고 읽기만 한다.
- 채널 목록은 `MARKETING_FOLLOWER_CHANNELS`(쉼표 구분, 예: `instagram`)에서 읽는다.
  지금 읽을 수 있는 채널은 instagram뿐이다.
- 모르면 0으로 채우지 않고 `unknown`과 이유를 돌려준다. 설정한 채널 중 하나라도 못 재면
  일부만 known으로 내지 않고 전체를 `unknown`으로 한다(규모를 낮게 읽는 일을 막는다).
- 이유 문구에는 토큰·URL·응답 본문을 담지 않는다(HTTP 상태·Meta 오류 코드 숫자만).
"""

from __future__ import annotations

import os
from datetime import UTC, datetime

import httpx

from ax_contracts.schemas.marketing_channels import ChannelFollowers, ChannelFollowersOutput
from marketing_agent.sns_publish.instagram import (
    PROVIDER_ENV,
    PROVIDER_INSTAGRAM,
    PROVIDER_MOCK,
    TOKEN_ENV,
    USER_ID_ENV,
)
from marketing_agent.sns_publish.instagram_graph import (
    API_BASE_ENV,
    API_VERSION_ENV,
    DEFAULT_API_BASE,
    TIMEOUT_ENV,
)

CHANNELS_ENV = "MARKETING_FOLLOWER_CHANNELS"
INSTAGRAM = "instagram"
SUPPORTED = (INSTAGRAM,)


class FollowersUnavailable(Exception):
    """잴 수 없다. 메시지는 사람이 읽는 이유(비밀값 없음)다."""


def configured_channels() -> list[str]:
    raw = os.getenv(CHANNELS_ENV) or ""
    seen: list[str] = []
    for part in raw.split(","):
        name = part.strip().lower()
        if name and name not in seen:
            seen.append(name)
    return seen


def _unknown(reason: str) -> ChannelFollowersOutput:
    return ChannelFollowersOutput(status="unknown", reason=reason[:200])


def _timeout() -> float:
    try:
        value = float((os.getenv(TIMEOUT_ENV) or "").strip() or 30)
    except ValueError:
        return 30.0
    return value if value > 0 else 30.0


async def _instagram_followers(client: httpx.AsyncClient | None) -> int:
    provider = (os.getenv(PROVIDER_ENV) or PROVIDER_MOCK).strip().lower()
    if provider != PROVIDER_INSTAGRAM:
        raise FollowersUnavailable("instagram 연결이 꺼져 있다(MARKETING_SNS_PROVIDER)")
    token = (os.getenv(TOKEN_ENV) or "").strip()
    user_id = (os.getenv(USER_ID_ENV) or "").strip()
    missing = [n for n, v in ((TOKEN_ENV, token), (USER_ID_ENV, user_id)) if not v]
    if missing:
        raise FollowersUnavailable(f"instagram 설정이 비어 있다: {', '.join(missing)}")
    base = (os.getenv(API_BASE_ENV) or "").strip().rstrip("/") or DEFAULT_API_BASE
    version = (os.getenv(API_VERSION_ENV) or "").strip().strip("/")
    root = f"{base}/{version}" if version else base
    http = client or httpx.AsyncClient()
    try:
        response = await http.get(
            f"{root}/{user_id}",
            params={"fields": "followers_count", "access_token": token},
            timeout=_timeout(),
        )
    except httpx.HTTPError:  # 예외 문자열에 토큰이 든 URL이 있을 수 있어 사슬을 끊는다
        raise FollowersUnavailable("instagram 호출이 닿지 않았다(시간 초과·연결 오류)") from None
    finally:
        if client is None:
            await http.aclose()
    try:
        body = response.json()
    except ValueError:
        body = None
    if not response.is_success or not isinstance(body, dict) or "error" in body:
        error = body.get("error") if isinstance(body, dict) else None
        code = error.get("code") if isinstance(error, dict) else None
        suffix = f", Meta 오류 코드 {code}" if isinstance(code, int) else ""
        raise FollowersUnavailable(f"instagram 호출 실패(HTTP {response.status_code}{suffix})")
    count = body.get("followers_count")
    if not isinstance(count, int) or isinstance(count, bool) or count < 0:
        raise FollowersUnavailable("instagram 응답에 followers_count가 없다")
    return count


async def channel_followers(client: httpx.AsyncClient | None = None) -> ChannelFollowersOutput:
    channels = configured_channels()
    if not channels:
        return _unknown(f"팔로워를 읽을 채널이 설정되지 않았다({CHANNELS_ENV})")
    unsupported = [c for c in channels if c not in SUPPORTED]
    if unsupported:
        return _unknown(f"팔로워를 읽을 수 없는 채널: {', '.join(unsupported)}")
    measured: list[ChannelFollowers] = []
    for name in channels:  # 지금은 instagram뿐
        try:
            count = await _instagram_followers(client)
        except FollowersUnavailable as error:
            return _unknown(str(error))
        measured.append(ChannelFollowers(channel=name, followers=count, at=datetime.now(UTC)))
    return ChannelFollowersOutput(status="known", channels=measured)
