"""캠페인 시작·결과 전달 (skill `start_campaign`, `record_campaign_result`).

- 시작: 재무 `review_campaign_budget`로 먼저 예산 의견을 묻고(A2A `call_agent`),
  `execute("start_campaign")`로 실행한다. 재무는 예산을 승인·반려하지 않고 의견만 낸다(`decision`은
  늘 `approved`, 한도는 요청액, 의견은 `opinion`·`suggested_krw`). 의견이 "권하지 않음"이어도
  막지 않고 `output.review`에 그대로 실어 돌려준다. 막는 것은 safety 관문(건당 50만 원 이상·하루
  합계 100만 원 이상은 사람 승인, 0원 이하는 거부)뿐이고 관문 결과(executed·held·rejected 등)는
  있는 그대로 돌려준다. 여기서는 숫자를 만들지 않는다.
  옛 재무 답(`rejected`, 한도 미만 `conditional`)이 오면 관문을 부르지 않고 멈추는 분기는
  방어로 남겨 둔다(지금 재무는 보내지 않는다).
- 결과: 끝난 캠페인의 성과를 재무 `record_campaign_result`에 계약 모델(`CampaignResultInput`)로
  그대로 전달한다. 모르는 숫자(`gmv_krw` 등)는 비워 두고 채우지 않는다.
  매출 효과 숫자(기간·`gmv_krw`·`orders`·잰 값)를 호출자가 주지 않으면 데이터 `campaign_result`
  (view=finance)의 `finance_record`를 그대로 가져와 채운다. 집행액·이름·채널·쿠폰은 호출자 값이다.
  호출자가 준 값은 덮어쓰지 않는다. 데이터에 닿지 못하면 재무를 부르지 않고 실패로 올린다.
- 쿠폰 지급(`issue_coupon`)은 이 흐름과 별개다. 재무는 쿠폰을 판단하지 않는다.
- 재무에 닿지 못하면 승인으로 보지 않는다(fail-closed): 시작은 실행하지 않고 멈춘다.
"""

from __future__ import annotations

from datetime import UTC, date, datetime
from typing import Any

from pydantic import Field, StrictInt, ValidationError, model_validator

from ax_agent_template.client import call_agent
from ax_agent_template.execute import execute
from ax_contracts.schemas import (
    AgentRequest,
    AgentResult,
    CampaignResultOutput,
    Evidence,
    ExecuteDecision,
    HoldReason,
    InputReason,
    TaskStatus,
)
from ax_contracts.schemas.common import AxModel
from ax_contracts.schemas.finance import (
    CampaignBudgetReviewInput,
    CampaignBudgetReviewOutput,
    CampaignResultInput,
)

FINANCE = "finance"
REVIEW_SKILL = "review_campaign_budget"
RECORD_SKILL = "record_campaign_result"
DATA = "data"
DATA_RESULT_SKILL = "campaign_result"
START_ACTION = "start_campaign"


class StartCampaignInput(AxModel):
    """캠페인 시작 요청. 예산은 정수 원이며 0 이하는 받지 않는다."""

    campaign_id: str = Field(min_length=1)
    campaign_name: str = Field(min_length=1)
    channel: str = Field(min_length=1)
    budget_krw: StrictInt = Field(gt=0)


# 데이터가 재는 매출 효과 값 묶음. 호출자가 하나라도 주면 이 묶음은 데이터로 채우지 않는다
# (호출자 숫자와 데이터 숫자가 섞이면 gmv와 주문 수가 서로 다른 기준이 된다)
_MEASURE_FIELDS = (
    "gmv_krw",
    "orders",
    "measured_gmv_krw",
    "measured_orders",
    "effect_generated_at",
)
_PERIOD_FIELDS = ("period_from", "period_to")


class RecordCampaignResultInput(AxModel):
    """재무 `CampaignResultInput`과 같은 칸. 데이터가 아는 칸(기간·매출 효과·출처)은 비워도 된다."""

    campaign_id: str
    campaign_name: str
    channel: str
    spend_krw: int = Field(ge=0)
    coupon_krw: int = Field(default=0, ge=0)
    period_from: str | None = None
    period_to: str | None = None
    gmv_krw: int | None = Field(default=None, ge=0)
    impressions: int | None = Field(default=None, ge=0)
    clicks: int | None = Field(default=None, ge=0)
    orders: int | None = Field(default=None, ge=0)
    source: str | None = None
    measured_gmv_krw: int | None = None
    measured_orders: int | None = None
    effect_generated_at: str | None = None

    @model_validator(mode="after")
    def _period_in_order(self) -> RecordCampaignResultInput:
        if self.period_from is None and self.period_to is None:
            return self
        try:
            start = date.fromisoformat(self.period_from or "")
            end = date.fromisoformat(self.period_to or "")
        except ValueError as exc:
            raise ValueError("period_from·period_to는 YYYY-MM-DD여야 한다") from exc
        if start > end:
            raise ValueError("period_from이 period_to보다 늦다")
        return self

    @property
    def has_measurement(self) -> bool:
        return any(getattr(self, name) is not None for name in _MEASURE_FIELDS)

    @property
    def needs_data(self) -> bool:
        """호출자가 주지 않은 칸이 있어 데이터가 아는 값이 필요한가."""
        missing_period = self.period_from is None or self.period_to is None
        return missing_period or self.source is None or not self.has_measurement


def _now() -> datetime:
    return datetime.now(UTC)


def _evidence(queries: list[str], summary: str, ref_ids: list[str] | None = None) -> Evidence:
    return Evidence(
        queries=queries, data_summary=summary, collected_at=_now(), ref_ids=ref_ids or []
    )


def _result(request: AgentRequest, status: TaskStatus, **kwargs: Any) -> AgentResult:
    return AgentResult(meta=request.meta, status=status, **kwargs)


def _invalid(request: AgentRequest, error: ValidationError) -> AgentResult:
    return _result(
        request,
        TaskStatus.FAILED,
        outcome="invalid_input",
        output={"errors": [f"{'.'.join(map(str, e['loc']))}: {e['type']}" for e in error.errors()]},
    )


async def _ask_finance(request: AgentRequest, skill: str, payload: AxModel) -> AgentResult:
    """재무를 A2A로 부른다. 연결 오류는 `call_agent`가 결과(failed·canceled)로 돌려준다."""
    sent = AgentRequest(
        meta=request.meta, skill=skill, input=payload.model_dump(mode="json", exclude_none=True)
    )
    return await call_agent(FINANCE, sent)


def _finance_unusable(request: AgentRequest, answer: AgentResult, outcome: str) -> AgentResult:
    """재무 답이 없거나 쓸 수 없다. 받은 상태와 이유를 그대로 올리고 다음 단계로 가지 않는다."""
    return _result(
        request,
        answer.status if answer.status != TaskStatus.COMPLETED else TaskStatus.FAILED,
        outcome=outcome,
        output={"finance_status": answer.status.value, "finance_outcome": answer.outcome},
        reason_code=answer.reason_code,
        evidence=answer.evidence,
    )


def _gate_outcome(
    request: AgentRequest,
    decision: ExecuteDecision,
    review: CampaignBudgetReviewOutput,
    evidence: Evidence,
) -> AgentResult:
    """관문 판정을 결과로 옮긴다. 재무 판단은 항상 output에 같이 싣는다."""
    detail: dict[str, Any] = {
        "review": review.model_dump(mode="json"),
        "execution_id": decision.execution_id,
        "grade": decision.grade,
    }
    refs = [review.review_id, decision.execution_id]
    evidence = evidence.model_copy(update={"ref_ids": refs})
    if decision.status == "executed":
        return _result(
            request, TaskStatus.COMPLETED, outcome="executed", output=detail, evidence=evidence
        )
    if decision.status == "verifying":
        return _result(request, TaskStatus.WORKING, outcome="verifying", output=detail)
    if decision.status == "held":
        detail |= {"review_id_hold": decision.review_id, "hold_reason": decision.hold_reason}
        return _result(
            request,
            TaskStatus.INPUT_REQUIRED,
            reason_code=InputReason.HUMAN_INPUT,
            outcome="held",
            output=detail,
            evidence=evidence,
        )
    if decision.hold_reason == HoldReason.VERIFY_REJECTED:
        return _result(
            request,
            TaskStatus.COMPLETED,
            outcome="rejected_by_review",
            output=detail,
            evidence=evidence,
        )
    if decision.hold_reason == HoldReason.VERIFY_TIMEOUT:
        return _result(
            request, TaskStatus.CANCELED, outcome="TIMEOUT", output=detail, evidence=evidence
        )
    outcome = "execute_failed" if decision.status == "failed" else "execute_rejected"
    return _result(request, TaskStatus.FAILED, outcome=outcome, output=detail, evidence=evidence)


async def start_campaign(request: AgentRequest) -> AgentResult:
    try:
        data = StartCampaignInput.model_validate(request.input)
    except ValidationError as error:
        return _invalid(request, error)

    ask = CampaignBudgetReviewInput(
        campaign_id=data.campaign_id,
        campaign_name=data.campaign_name,
        channel=data.channel,
        requested_krw=data.budget_krw,
    )
    answer = await _ask_finance(request, REVIEW_SKILL, ask)
    if answer.status != TaskStatus.COMPLETED:
        return _finance_unusable(request, answer, "finance_review_unavailable")
    try:
        review = CampaignBudgetReviewOutput.model_validate(answer.output)
    except ValidationError:
        return _finance_unusable(request, answer, "finance_review_invalid")

    evidence = _evidence(
        [f"finance.{REVIEW_SKILL}", START_ACTION],
        f"{data.campaign_id} 재무 판단 {review.decision}, 요청 {data.budget_krw}원",
        [review.review_id],
    )
    summary = {"review": review.model_dump(mode="json")}
    if review.decision == "rejected":
        return _result(
            request,
            TaskStatus.COMPLETED,
            outcome="rejected_by_finance",
            output=summary,
            evidence=evidence,
        )
    if data.budget_krw > review.approved_limit_krw:
        # 옛 재무 답 방어: 한도보다 큰 요청은 관문을 부르지 않는다(지금 재무는 한도=요청액)
        return _result(
            request,
            TaskStatus.COMPLETED,
            outcome="over_approved_limit",
            output=summary,
            evidence=evidence,
        )

    # 금액이 키에 없으면 관문이 금액을 보지 않고 이전 판정을 돌려준다(태윤 님 검토)
    key = f"{request.meta.context_id}:{START_ACTION}:{data.campaign_id}:{data.budget_krw}"
    decision = await execute(
        START_ACTION,
        {
            "campaign_id": data.campaign_id,
            "campaign_name": data.campaign_name,
            "channel": data.channel,
            "budget_krw": data.budget_krw,
        },
        request.meta.model_copy(update={"idempotency_key": key}),
        evidence,
    )
    return _gate_outcome(request, decision, review, evidence)


def _data_unusable(request: AgentRequest, answer: AgentResult, outcome: str) -> AgentResult:
    """데이터 답이 없거나 쓸 수 없다. 재무를 부르지 않는다(숫자를 지어내지 않는다)."""
    return _result(
        request,
        answer.status if answer.status != TaskStatus.COMPLETED else TaskStatus.FAILED,
        outcome=outcome,
        output={"data_status": answer.status.value, "data_outcome": answer.outcome},
        reason_code=answer.reason_code,
        evidence=answer.evidence,
    )


async def _fill_from_data(
    request: AgentRequest, data: RecordCampaignResultInput
) -> RecordCampaignResultInput | AgentResult:
    """호출자가 주지 않은 칸을 데이터 `finance_record`로 채운다. 준 값은 덮어쓰지 않는다."""
    sent = AgentRequest(
        meta=request.meta,
        skill=DATA_RESULT_SKILL,
        input={"campaign_id": data.campaign_id, "view": "finance"},
    )
    answer = await call_agent(DATA, sent)
    if answer.status != TaskStatus.COMPLETED:
        return _data_unusable(request, answer, "data_unavailable")
    try:
        found = CampaignResultOutput.model_validate(answer.output)
    except ValidationError:
        return _data_unusable(request, answer, "data_result_invalid")
    record = found.finance_record
    if found.campaign_id != data.campaign_id or (record and record.campaign_id != data.campaign_id):
        return _data_unusable(request, answer, "data_result_invalid")
    if record is None:
        return data
    fill: dict[str, Any] = {}
    for name in _PERIOD_FIELDS:
        if getattr(data, name) is None and getattr(record, name) is not None:
            fill[name] = getattr(record, name)
    if not data.has_measurement:
        for name in _MEASURE_FIELDS:
            if getattr(record, name) is not None:
                fill[name] = getattr(record, name)
        if data.source is None:
            fill["source"] = record.source
    try:
        return RecordCampaignResultInput.model_validate(data.model_dump() | fill)
    except ValidationError:
        return _data_unusable(request, answer, "data_result_invalid")


async def record_campaign_result(request: AgentRequest) -> AgentResult:
    try:
        data = RecordCampaignResultInput.model_validate(request.input)
    except ValidationError as error:
        return _invalid(request, error)

    queries = [f"finance.{RECORD_SKILL}"]
    if data.needs_data:
        filled = await _fill_from_data(request, data)
        if isinstance(filled, AgentResult):
            return filled
        data = filled
        queries.insert(0, f"data.{DATA_RESULT_SKILL}")
        if data.period_from is None or data.period_to is None:
            # 기간을 모르면 측정 기간을 지어내지 않는다
            return _result(
                request,
                TaskStatus.FAILED,
                outcome="data_period_missing",
                output={"campaign_id": data.campaign_id},
            )

    # 계약 모델 그대로 보낸다. 값이 없는 선택 필드는 보내지 않아 재무가 0으로 채우지 못한다
    try:
        contract = CampaignResultInput.model_validate(data.model_dump())
    except ValidationError as error:
        return _invalid(request, error)
    answer = await _ask_finance(request, RECORD_SKILL, contract)
    return _result(
        request,
        answer.status,
        outcome=answer.outcome,
        output=answer.output,
        reason_code=answer.reason_code,
        evidence=answer.evidence
        if answer.evidence.data_summary
        else _evidence(queries, f"{data.campaign_id} 결과 전달"),
    )
