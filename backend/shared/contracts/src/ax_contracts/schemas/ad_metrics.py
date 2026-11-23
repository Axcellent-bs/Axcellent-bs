"""캠페인별 광고 숫자(수동 입력) 계약 (docs/marketing-ad-metrics-design.md B, 2026-11-23).

- 캠페인(`campaign_id`) × 날짜(KST) × `source` × `medium`당 한 줄. 사람이 적은 값을 그대로 둔다
- **`None` = 모름, 0 = 0**. 둘을 섞지 않는다. 합계에 `None`이 섞이면 그 칸 합계는 `None`과
  `missing_dates`
- 마케팅은 저장·조회만 한다. 클릭률·광고비 대비 판매액 같은 비율은 데이터분석이 계산한다
  (이 계약에 없다)
- `source`·`medium` 허용 조합은 마케팅 `links.py`의 조합표(응답 `combos`로도 준다)
"""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from ax_contracts.schemas.common import AxModel

RECORD_AD_METRICS_SKILL = "record_campaign_ad_metrics"
GET_AD_METRICS_SKILL = "get_campaign_ad_metrics"
AD_CAMPAIGN_ID_PATTERN = r"^cmp_[A-Za-z0-9][A-Za-z0-9_-]{0,63}$"
AD_DATE_PATTERN = r"^\d{4}-\d{2}-\d{2}$"

AdMetricsRejectReason = Literal[
    "unknown_campaign",  # 받은 기획안에 없는 캠페인 번호
    "invalid_date",
    "future_date",  # 한국 날짜로 오늘보다 뒤
    "invalid_source_medium",  # 조합표에 없는 source·medium
    "clicks_over_impressions",
    "storage_error",
]


class RecordAdMetricsInput(AxModel):
    """광고 숫자 한 줄 적기(고쳐 쓰기). `entered_by`는 dashboard-api가 사람 토큰에서 채운다."""

    campaign_id: str = Field(pattern=AD_CAMPAIGN_ID_PATTERN)
    date: str = Field(pattern=AD_DATE_PATTERN)
    source: str = Field(min_length=1, max_length=20)
    medium: str = Field(min_length=1, max_length=20)
    impressions: int | None = Field(default=None, ge=0, le=10**12)
    clicks: int | None = Field(default=None, ge=0, le=10**12)
    spend_krw: int | None = Field(default=None, ge=0, le=10**13)
    report_ref: str | None = Field(default=None, max_length=120)
    entered_by: str = Field(min_length=1, max_length=80)


class AdMetricsRow(AxModel):
    campaign_id: str
    date: str
    source: str
    medium: str
    impressions: int | None = None
    clicks: int | None = None
    spend_krw: int | None = None
    report_ref: str | None = None
    entered_by: str
    entered_at: str
    updated_at: str
    revisions: int = 0  # 고쳐 쓴 횟수(이전 값은 마케팅 저장소의 revisions 표에)


class RecordAdMetricsOutput(AxModel):
    outcome: Literal["recorded", "updated", "unchanged", "rejected"]
    reason_code: AdMetricsRejectReason | None = None
    row: AdMetricsRow | None = None


class GetAdMetricsInput(AxModel):
    """`campaign_id` 또는 기간(`date_from`·`date_to`, KST, 양 끝 포함). 둘 다 주면 둘 다 맞는 줄."""

    campaign_id: str | None = Field(default=None, pattern=AD_CAMPAIGN_ID_PATTERN)
    date_from: str | None = Field(default=None, pattern=AD_DATE_PATTERN)
    date_to: str | None = Field(default=None, pattern=AD_DATE_PATTERN)


class AdMetricsTotals(AxModel):
    """칸별 합계. 한 줄이라도 `None`이면 그 칸 합계는 `None`이고 빈 날짜를 `missing_dates`에."""

    impressions: int | None = None
    clicks: int | None = None
    spend_krw: int | None = None
    missing_dates: dict[str, list[str]] = Field(default_factory=dict)


class GetAdMetricsOutput(AxModel):
    status: Literal["ok", "unknown"]
    reason: str | None = None  # unknown이면 이유("아직 입력 없음")
    rows: list[AdMetricsRow] = Field(default_factory=list)
    totals: AdMetricsTotals | None = None
    combos: dict[str, list[str]] = Field(default_factory=dict)  # source → 허용 medium
    default_base_url: str | None = None  # 추적 링크 기본 주소(마케팅 설정값, 화면에서 바꿀 수 있다)
