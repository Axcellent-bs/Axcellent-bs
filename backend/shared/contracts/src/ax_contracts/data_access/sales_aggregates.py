"""data 일별 집계 저장소 행 (3단계 3-1).

설계: `docs/superpowers/specs/2026-11-24-daily-aggregate-design.md` 7절.

고객 원본(주문·가명 구매자)을 AXcellent에 쌓지 않고 "회사·날짜·차원 → 숫자"만 남긴다.

- 이 행들은 data 저장소(`aggregates.db`) 안의 모양이다. 화면·skill 출력 계약은 바꾸지 않는다.
- 어떤 행에도 구매자 id·주문 id·주문 시각(분 단위)을 두지 않는다.
- 더할 수 있는 숫자(주문 수·매출·수량·환불액)는 일별 행 `SalesDailyRow`로, k 숨김 없이 둔다.
- 사람을 세는 숫자(서로 다른 구매자 수 등)는 정해진 기간 스냅샷으로 두고, **저장할 때** 카탈로그
  `[sales] min_group_size`(k) 숨김을 적용한다. 숨긴 칸은 `HiddenCount(value=None, hidden=True)`.
- 모든 행은 `tenant_id`로 회사가 나뉜다.
"""

from __future__ import annotations

import datetime as dt
from typing import Literal

from pydantic import Field

from ax_contracts.schemas.common import TENANT_ID_DEFAULT, AxModel, UtcDatetime

AggPeriod = Literal["1d", "7d", "30d", "90d", "365d", "this_month", "last_month"]
AggGrain = Literal["order", "line"]
AggInput = Literal["sales_db", "company_server"]
AggDimKind = Literal["all", "category", "genre", "product", "attr", "attr_combo"]


class AggRowBase(AxModel):
    """모든 집계 행의 공통 칸."""

    tenant_id: str = TENANT_ID_DEFAULT
    run_id: str
    computed_at: UtcDatetime
    source_cursor: str | None = None  # 원본 기준 (병행: sync 커서, 3-3 뒤: 회사 서버 as_of)


class SalesDailyRow(AggRowBase):
    """일별 매출 (`agg_sales_daily`). 주문 숫자는 접수 날짜, 환불은 환불 날짜(한국).

    `order` 행은 상품 칸이 비고 주문 단위, `line` 행은 상품(분류·장르) 단위다. 주문 수를 상품끼리
    더하지 않게 두 단으로 나눈다. 매출 = Σ revenue_net − Σ refund_amount.
    """

    day: dt.date
    grain: AggGrain
    product_id: str = ""
    category_code: str = ""
    genre_code: str = ""
    src_source: str = ""
    src_medium: str = ""
    src_campaign: str = ""
    coupon_used: bool = False
    orders: int = 0  # order: 결제 완료 주문 수 · line: 그 상품이 든 결제 완료 주문 수
    revenue_net: int = 0  # 줄 순매출 합 (환불 전)
    qty: int = 0
    coupon_discount: int = 0  # order만
    points_used: int = 0  # order만
    refund_amount: int = 0  # 그 날 환불액 (line은 줄 순매출 비율로 나눈 몫)
    created_orders: int = 0  # order만. 상태 무관 접수 주문 수 (품질 보고용)


class HiddenCount(AxModel):
    """사람 수 칸: hidden이면 value None ("k 미만")."""

    value: int | None = None
    hidden: bool = False


class BuyerPeriodRow(AggRowBase):
    """기간 스냅샷 구매자 (`agg_buyers_period`). 기간 끝 = 기준일 24시(한국)."""

    as_of: dt.date
    period: AggPeriod
    dim_kind: AggDimKind
    dim_code: str = ""
    attr_cell: dict[str, str] = Field(default_factory=dict)  # 키 순서 = 카탈로그 속성 순서
    buyers: HiddenCount
    new_buyers: HiddenCount
    repeat_buyers: HiddenCount
    new_revenue: int | None = None  # dim_kind=all·attr_cell 비움 줄만 (금액이라 숨기지 않음)
    repeat_revenue: int | None = None
    title: str | None = None  # 상품일 때 기간 안 처음 나온 이름


class CohortRow(AggRowBase):
    """코호트 (`agg_cohorts`). 첫 결제 달별 30·60·90일 안 재구매."""

    as_of: dt.date
    cohort_month: str = Field(pattern=r"^\d{4}-\d{2}$")  # "2026-08" (한국)
    attr_cell: dict[str, str] = Field(default_factory=dict)
    first_buyers: HiddenCount
    repeat: dict[Literal["30", "60", "90"], HiddenCount | None]  # None = 아직 다 안 지남


class AtRiskRow(AggRowBase):
    """떠날 위험 단골 (`agg_at_risk`). 계산 때의 카탈로그 값(churn_days 등)을 같이 남긴다."""

    as_of: dt.date
    attr_cell: dict[str, str]
    regulars: HiddenCount
    at_risk: HiddenCount
    top_genre: str | None = None
    churn_days: int
    repeat_min_orders: int


class PairRow(AggRowBase):
    """함께 산 상품 (`agg_pairs`). 사람 단위, 상위 20쌍. a = 산 사람이 더 많은 쪽."""

    as_of: dt.date
    period: AggPeriod
    level: Literal["genre", "product"]
    attr_cell: dict[str, str] = Field(default_factory=dict)
    code_a: str
    code_b: str
    together_buyers: int
    a_buyers: int
    b_buyers: int
    overall_buyers: int
    together_orders: int | None = None  # 같은 주문에 함께 든 수 (설계 결정 필요 5)
    hidden_slots: int = 0  # together < k라 숫자 없이 채운 자리 수


class SegmentProfileRow(AggRowBase):
    """속성 묶음 프로필 (`agg_segment_profile`). 즐겨 사는 것·프로필·이벤트 후보가 쓴다."""

    as_of: dt.date
    period: AggPeriod
    attr_cell: dict[str, str]
    item_level: Literal["category", "genre", "product"]
    item_code: str
    buyers: HiddenCount
    item_buyers: int
    orders: int | None = None
    revenue: int | None = None
    title: str | None = None


class ChannelValueRow(AggRowBase):
    """채널 가치 (`agg_channel_value`). 출처는 카탈로그 이름으로 묶은 뒤(묶은 뒤 k)."""

    as_of: dt.date
    period: AggPeriod
    source_label: str
    new_buyers: HiddenCount
    horizon_net: int | None = None
    horizon_orders: int | None = None
    repeaters: int | None = None
    excluded_incomplete: int = 0
    horizon_days: int


class QualityDailyRow(AggRowBase):
    """품질 (`agg_quality_daily`). 주문 번호는 남기지 않고 이유별 수만."""

    day: dt.date
    created_orders: int = 0
    paid_orders: int = 0
    dropped_by_reason: dict[str, int] = Field(default_factory=dict)
    unknown_codes: dict[str, int] = Field(default_factory=dict)
    unmapped_attributes: dict[str, int] = Field(default_factory=dict)
    customers: int | None = None


class AggRun(AxModel):
    """계산 기록 한 줄 (`agg_runs`)."""

    run_id: str
    tenant_id: str = TENANT_ID_DEFAULT
    as_of: dt.date
    input: AggInput
    started_at: UtcDatetime
    finished_at: UtcDatetime | None = None
    status: Literal["running", "done", "failed"]
    reason: str | None = None
    source_cursor: str | None = None
    rows: dict[str, int] = Field(default_factory=dict)  # 표 이름 → 행 수
    # 아래는 예전 기록에 없다 (3-2 ②). 계산에 쓴 원본 판매 출처 이름(sync_state.source, 예: "fake").
    # 고객 분석 선택지(segment_options)가 원본을 열지 않고 "가짜 데이터" 표시를 정한다
    source_name: str | None = None


class AggCompareRow(AxModel):
    """병행 비교 한 줄 (`agg_compare`, 3-1b). 구매자 id·주문 id는 남기지 않는다."""

    tenant_id: str = TENANT_ID_DEFAULT
    as_of: dt.date
    target: str  # "sales_analysis:30d" 등
    same: bool
    diffs: list[str] = Field(default_factory=list)  # "revenue: 1234000 != 1233000"
    compared_at: UtcDatetime
