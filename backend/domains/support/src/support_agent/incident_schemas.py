"""사건(장애)으로 피해를 본 고객 보상 skill의 입출력."""

from __future__ import annotations

from typing import Literal

from pydantic import Field

from ax_contracts.schemas.common import AxModel

MAX_BUYERS = 50
BUYER_PATTERN = r"^buy_[0-9]{4}$"  # \d는 유니코드 숫자(예: buy_١٢٣٤)도 받아서 쓰지 않는다


class RequestIncidentCompensationInput(AxModel):
    """영향 고객 목록은 데이터분석 영향 분석 결과를 총괄이 넘긴다 (문의를 안 낸 고객도 있다).

    단가와 건수 상한은 정책표(`compensation.py`)가 정한다. 사유는 결제 장애만 받는다.
    """

    buyer_ids: list[str] = Field(min_length=1, max_length=MAX_BUYERS)
    reason_code: Literal["payment_failure"] = "payment_failure"
    count: int = Field(default=1, ge=1, le=3)
    impact_ref: str | None = Field(default=None, max_length=100)  # 영향 분석 결과 참조 (근거용)


class IncidentCouponResult(AxModel):
    buyer_id: str
    status: Literal["requested", "held", "pending", "duplicate", "failed", "skipped"]
    sequence: int | None = None
    marketing_status: str | None = None
    marketing_outcome: str | None = None
    coupon_id: str | None = None
    total_value: int = 0


class IncidentCompensationOutput(AxModel):
    reason_code: str
    unit_value: int
    count: int
    requested: int
    results: list[IncidentCouponResult]
    stopped_early: bool = False  # 마케팅에 닿지 않거나 거부·검증 중이라 남은 고객은 보내지 않았다
    limit_reached: bool = False  # 사건당 인원·합계 상한에 닿아 남은 고객은 보내지 않았다
    notes: list[str] = Field(default_factory=list)


# ----------------------------------------------------------------- list_incident_compensation
MAX_COMPARE_BUYERS = 200
INCIDENT_CONTEXT_PATTERN = r"^ctx_inc_[A-Za-z0-9]+$"


class ListIncidentCompensationInput(AxModel):
    """사건(`ctx_inc_`)마다 보상 쿠폰을 받은 고객을 돌려준다 (읽기).

    `incident_context_id`를 주면 그 사건만, 안 주면 보상 기록이 있는 모든 사건을 돌려준다.
    `buyer_ids`(예: 데이터분석 영향 분석의 영향 고객)를 주면 보상 기록이 없는 고객을
    `missing_buyer_ids`로 알려 준다. 이 skill은 누락을 찾기만 하고 보상을 요청하지 않는다.
    """

    incident_context_id: str | None = Field(default=None, pattern=INCIDENT_CONTEXT_PATTERN)
    buyer_ids: list[str] | None = Field(default=None, max_length=MAX_COMPARE_BUYERS)


class CompensatedBuyer(AxModel):
    buyer_id: str
    status: Literal["issued", "linked_duplicate"]
    reason_code: str
    coupon_id: str | None = None
    count: int = 0
    unit_value: int = 0
    total_value: int = 0
    marketing_outcome: str | None = None


class IncidentCompensation(AxModel):
    context_id: str
    buyers: list[CompensatedBuyer]
    issued_buyers: int  # 쿠폰이 실제로 지급된 고객 수 (`linked_duplicate`는 뺀다)
    issued_total: int  # 그 합계 원
    missing_buyer_ids: list[str] | None = None  # `buyer_ids`를 줬을 때만: 보상 기록이 없는 고객


class ListIncidentCompensationOutput(AxModel):
    incidents: list[IncidentCompensation]
    notes: list[str] = Field(default_factory=list)
