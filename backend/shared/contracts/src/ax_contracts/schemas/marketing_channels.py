"""마케팅 공식 SNS 채널 팔로워 수 (read, 11/18 기획 방향 선택지).

기획이 고객사 규모를 숫자로 재서 "유행에 올라타기"를 추천할지 정할 때 쓴다.
skill 구현은 마케팅 몫이다. 모르면 `status="unknown"`과 이유를 돌려주고 0으로 채우지 않는다.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from ax_contracts.schemas.common import AxModel, UtcDatetime

CHANNEL_FOLLOWERS_SKILL = "channel_followers"


class ChannelFollowers(AxModel):
    channel: str = Field(min_length=1, max_length=40)  # instagram·youtube 등
    followers: int = Field(ge=0)
    at: UtcDatetime | None = None  # 잰 시각


class ChannelFollowersOutput(AxModel):
    status: Literal["known", "unknown"]
    channels: list[ChannelFollowers] = Field(default_factory=list)
    reason: str | None = Field(default=None, max_length=200)  # unknown이면 이유
