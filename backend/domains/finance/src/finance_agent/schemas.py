"""finance skill 입출력 (재무 구성안 4-2, 공통 구성안 12장, 재무 재설계 2026-10-23 7장).

`verify_amount`·`review_hold`·`loss_aggregation` skill은 재무 재설계로 없앴다.
금액은 모두 정수 원, 시각은 UTC ISO8601 문자열이다.
"""

from __future__ import annotations

import datetime as dt
from typing import Any, Literal

from pydantic import Field

from ax_contracts.schemas import AxModel


# ---------------------------------------------- closing_report (orchestrator·data → finance, 읽기)
class ClosingReportInput(AxModel):
    """기간 손익 요약. `date`가 없으면 오늘(UTC), `week`면 그 주(월~일), `month`면 그 달."""

    period: Literal["day", "week", "month"] = "day"
    date: dt.date | None = None


class ClosingLine(AxModel):
    code: str
    label: str
    amount: int | None  # None = 받지 못한 자료(`unavailable`) 때문에 확인할 수 없음. 0원과 다르다


class JournalLine(AxModel):
    account: str
    debit: int = Field(ge=0)
    credit: int = Field(ge=0)
    memo: str


class ClosingReportOutput(AxModel):
    """숫자는 코드가 계산한다. `commentary`만 LLM이 쓸 수 있다(`commentary_source`)."""

    period: Literal["day", "week", "month"]
    date: str
    window_from: str
    window_to: str
    lines: list[ClosingLine]
    journal: list[JournalLine]
    commentary: str
    commentary_source: Literal["llm", "template"]
    status: Literal["draft", "confirmed"]
    closable: bool = True  # 기간이 끝나 확정할 수 있는가
    snapshot_at: str | None = None  # 확정 때 숫자를 저장한 시각. 없으면 지금 숫자다
    drift: list[dict[str, Any]] = Field(default_factory=list)  # 확정 뒤 달라진 줄
    close_warnings: list[dict[str, Any]] = Field(default_factory=list)
    adjustment_proposal: dict[str, Any] | None = None  # 조정 분개안. 사람이 반영한다
    adjustments: list[dict[str, Any]] = Field(default_factory=list)  # 이 기간에 반영한 차이
    prior_adjustments: list[dict[str, Any]] = Field(default_factory=list)  # 이 달에 잡은 전기 조정
    net_after_adjustments: int | None = None
    confirmed_by: str | None = None
    confirmed_at: str | None = None
    unavailable: list[str] = Field(default_factory=list)
    applied_rules: list[dict[str, Any]] = Field(default_factory=list)  # 적용한 규칙과 근거


# ------------------------------- record_incident_finance (orchestrator → finance, 읽기·기록)
class IncidentFinanceInput(AxModel):
    """장애 이벤트의 구간과 데이터 영향 분석 결과 (총괄이 옮겨 준 값 그대로).

    context_id는 요청 meta의 장애 이벤트 번호(`ctx_inc_`)다. 영향 숫자를 모르면 비운다(None).
    """

    component: str | None = Field(default=None, max_length=80)
    window_from: dt.datetime
    window_to: dt.datetime
    failed_order_count: int | None = Field(default=None, ge=0)
    affected_customer_count: int | None = Field(default=None, ge=0)
    damage_amount: int | None = Field(default=None, ge=0)
    affected_customer_ids: list[str] = Field(default_factory=list, max_length=20000)


class IncidentFinanceOutput(AxModel):
    """장애 이벤트 재무 기록. 모르는 금액은 0이 아니라 null이고 `unavailable`에 이름이 있다."""

    damage_amount: int | None  # 데이터 영향 분석의 피해 금액 그대로
    compensation_total: int | None  # 이 이벤트로 나간 보상 쿠폰 합계 (재무 장부)
    refund_total: int | None  # 장애 구간에 영향 고객에게 나간 환불 합계
    money_out_total: int | None  # 보상 + 환불 (둘 중 하나라도 모르면 null)
    # 장애 구간에 시간 초과으로 남은 결제(돈이 빠져나갔을 수 있어 확인·환불 필요). 보상과 별도
    refund_needed_total: int | None = None
    compensation_coupon_count: int | None
    failed_orders: int | None
    affected_customers: int | None
    context_id: str
    component: str | None
    window_from: str
    window_to: str
    ledger_source: str | None
    unavailable: list[str]
    recorded: bool
    recorded_at: str


# ------------------------------- record_support_cost (orchestrator → finance, 읽기·기록)
class SupportCostInput(AxModel):
    """고객지원이 닫은 문의의 처리 결과 (`support.inquiry_resolved` 이벤트의 사실 필드)."""

    inquiry_id: int
    order_id: str | None = None
    resolution: Literal["answered", "claim_decided", "compensated", "escalated"]
    claim_id: int | None = None
    claim_decision: Literal["approve", "reject"] | None = None
    coupon_id: str | None = None
    refund_amount: int | None = Field(default=None, ge=0)


class SupportCostOutput(AxModel):
    """문의 한 건의 비용 기록.

    `expense_used`가 false여도(경비를 쓰지 않아도) 상담 처리 결과는 기록한다.
    주문을 장부에서 찾지 못하면 `shipping_fee`는 비우고 `unavailable`에 적는다(추정하지 않는다).
    """

    record_id: str
    inquiry_id: int
    order_id: str | None = None
    status: Literal["recorded", "already_recorded", "updated"]
    resolution: str
    claim_decision: str | None = None
    refund_amount: int
    coupon_issued: bool
    expense_used: bool
    shipping_fee: int | None = None
    note: str
    unavailable: list[str] = Field(default_factory=list)
