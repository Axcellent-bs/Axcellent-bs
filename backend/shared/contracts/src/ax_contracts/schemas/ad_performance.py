"""광고 성과 읽기 계약 (data `ad_performance`, 11/26). 읽기 전용: 광고 만들기·예산·켜기/끄기는 없다.

- 메타(Graph `act_<id>/insights`)·구글 애즈(REST `customers/{id}/googleAds:search`)의 캠페인 값을
  받은 그대로 둔다. 단위 변환은 구글 `cost_micros ÷ 1e6`만 한다. 통화는 받은 통화 코드 그대로
- **`None` = 모름**(플랫폼이 주지 않음), 0 = 0. 섞지 않는다
- 비율(연령·성별 비중)은 코드가 계산한다. 클릭률·조회율은 플랫폼 값 그대로(`ctr_unit`로 단위 표시)
- 토큰 값은 어디에도 넣지 않는다(필요한 것은 env 이름만)
"""

from __future__ import annotations

import datetime as dt
from typing import Literal

from pydantic import Field

from ax_contracts.schemas.common import AxModel, UtcDatetime
from ax_contracts.schemas.data import DataFact

AD_PERFORMANCE_SKILL = "ad_performance"
AdPlatform = Literal["google", "meta"]


class AdPerformanceRequest(AxModel):
    """company: 회사 설정 id(없으면 지금 회사). days: 최근 며칠(기본 7). 저장분만 읽는다."""

    company: str | None = Field(default=None, min_length=1, max_length=64)
    days: int = Field(default=7, ge=1, le=90)
    platform: Literal["google", "meta", "all"] = "all"


class AdCampaign(AxModel):
    """캠페인 한 줄 (기간 합계, 플랫폼 값 그대로)."""

    platform: AdPlatform
    campaign_id: str = Field(min_length=1, max_length=64)
    name: str = Field(default="", max_length=200)
    cost: float | None = Field(default=None, ge=0)  # 구글은 cost_micros ÷ 1e6, 메타는 spend
    currency: str | None = Field(default=None, max_length=8)  # 받은 통화 코드 (KRW 등)
    impressions: int | None = Field(default=None, ge=0)
    reach: int | None = Field(default=None, ge=0)  # 메타만
    clicks: int | None = Field(default=None, ge=0)
    ctr: float | None = Field(default=None, ge=0)  # 플랫폼 값 그대로
    ctr_unit: Literal["ratio", "percent"] | None = None  # 구글 비율(0~1) · 메타 %
    views: int | None = Field(default=None, ge=0)  # 구글 video_trueview_views · 메타 video_play
    thruplays: int | None = Field(default=None, ge=0)  # 메타 video_thruplay_watched_actions
    view_rate: float | None = Field(default=None, ge=0)  # 구글 video_trueview_view_rate (비율)
    p25: int | None = Field(default=None, ge=0)  # 메타 25%·50%·75%·100% 시청
    p50: int | None = Field(default=None, ge=0)
    p75: int | None = Field(default=None, ge=0)
    p100: int | None = Field(default=None, ge=0)
    conversions: float | None = Field(default=None, ge=0)  # 플랫폼이 준 값 (구글 conversions)
    # 등록 영상(video_performance)과 이어지면 그 영상 key (캠페인 이름에 영상 id·캠페인 번호가
    # 있을 때만, 못 이으면 None — 지어내지 않는다)
    video_key: str | None = Field(default=None, max_length=80)


class AdAudience(AxModel):
    """연령·성별 한 칸. share는 코드 계산(그 칸 ÷ 그 플랫폼 합계)."""

    platform: AdPlatform
    age: str = Field(default="", max_length=30)  # 받은 값 그대로 (18-24 · AGE_RANGE_18_24)
    gender: str = Field(default="", max_length=30)
    impressions: int | None = Field(default=None, ge=0)
    views: int | None = Field(default=None, ge=0)
    impressions_share: float | None = Field(default=None, ge=0, le=1)
    views_share: float | None = Field(default=None, ge=0, le=1)


class AdPlatformStatus(AxModel):
    """플랫폼마다 읽었는지. 못 읽으면 이유와 필요한 것(env 이름·회사 설정 칸)."""

    platform: AdPlatform
    status: Literal["known", "unknown"] = "unknown"
    reason: str | None = Field(default=None, max_length=200)
    needs: list[str] = Field(default_factory=list, max_length=8)
    as_of: dt.date | None = None  # 기간 끝 날짜
    collected_at: UtcDatetime | None = None


class AdPerformanceOutput(AxModel):
    schema_version: str = "ad_performance.v1"
    company: str | None = Field(default=None, max_length=64)
    days: int = 7
    period: str | None = Field(default=None, max_length=40)  # "<from>~<to>"
    status: Literal["known", "unknown"] = "unknown"
    reason: str | None = Field(default=None, max_length=200)
    platforms: list[AdPlatformStatus] = Field(default_factory=list, max_length=2)
    campaigns: list[AdCampaign] = Field(default_factory=list, max_length=200)
    audience: list[AdAudience] = Field(default_factory=list, max_length=120)
    as_of: dt.date | None = None
    facts: list[DataFact] = Field(default_factory=list)
    summary: str = Field(default="", max_length=300)
