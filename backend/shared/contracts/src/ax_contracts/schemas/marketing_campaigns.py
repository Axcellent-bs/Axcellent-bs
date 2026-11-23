"""마케팅이 시작한 캠페인 목록 읽기 (read, 2026-11-23).

마케팅 캠페인 대장(`campaign_auto.CampaignTracker`)에는 안전 관문이 `executed`로 끝난 캠페인이
남는다. 화면은 이 목록으로 "유튜브 영상이 어느 캠페인 영상인지"를 고르고, 결과 저장 칸에 시작 때
적은 콘텐츠 카테고리·형식·제작비를 미리 채운다. 숫자를 만들지 않고 시작 때 값 그대로 돌려준다.
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from ax_contracts.schemas.common import AxModel

LIST_MARKETING_CAMPAIGNS_SKILL = "list_marketing_campaigns"


class ListMarketingCampaignsInput(AxModel):
    limit: int = Field(default=50, ge=1, le=200)


class MarketingCampaignItem(AxModel):
    """대장 한 줄. `status`: active(진행 중) · recorded_auto(자동으로 결과를 보냄) ·
    recorded_human(사람이 결과를 저장) · canceled · gave_up(자동 전송 포기)."""

    campaign_id: str
    campaign_name: str
    channel: str
    budget_krw: int
    started_on: str  # KST 날짜
    ends_on: str | None = None
    status: str
    content_category: str | None = None
    format: Literal["shorts", "reels"] | None = None
    production_cost_krw: int | None = None


class ListMarketingCampaignsOutput(AxModel):
    items: list[MarketingCampaignItem] = Field(default_factory=list)
    total: int = 0
