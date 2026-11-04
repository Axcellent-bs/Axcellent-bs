"""캠페인 시작·결과 전달 (skill `start_campaign`, `record_campaign_result`).

- 시작: 재무 `review_campaign_budget`로 먼저 예산을 묻고(A2A `call_agent`), 거절이면 관문을 부르지
  않고 멈춘다. 그 밖에는 `execute("start_campaign")`로 실행한다. 관문 결과(executed·held·
  rejected 등)는 있는 그대로 돌려준다. 승인 한도·보류 기준(50만 원 이상은 사람 승인)은 재무와
  safety가 정하고, 여기서는 숫자를 만들지 않는다.
- 결과: 끝난 캠페인의 성과를 재무 `record_campaign_result`에 계약 모델(`CampaignResultInput`)로
  그대로 전달한다. 모르는 숫자(`gmv_krw` 등)는 비워 두고 채우지 않는다.
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
START_ACTION = "start_campaign"


class StartCampaignInput(AxModel):
    """캠페인 시작 요청. 예산은 정수 원이며 0 이하는 받지 않는다."""

    campaign_id: str = Field(min_length=1)
    campaign_name: str = Field(min_length=1)
    channel: str = Field(min_length=1)
    budget_krw: StrictInt = Field(gt=0)


class RecordCampaignResultInput(CampaignResultInput):
    """재무 계약(`CampaignResultInput`)에 날짜 순서만 확인을 더한다. 필드는 그대로다."""

    @model_validator(mode="after")
    def _period_in_order(self) -> RecordCampaignResultInput:
        try:
            start = date.fromisoformat(self.period_from)
            end = date.fromisoformat(self.period_to)
        except ValueError as exc:
            raise ValueError("period_from·period_to는 YYYY-MM-DD여야 한다") from exc
        if start > end:
            raise ValueError("period_from이 period_to보다 늦다")
        return self


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
        # 조건부 한도를 넘는 요청은 재무 검증이 거절하므로 관문을 부르지 않는다
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


async def record_campaign_result(request: AgentRequest) -> AgentResult:
    try:
        data = RecordCampaignResultInput.model_validate(request.input)
    except ValidationError as error:
        return _invalid(request, error)

    # 계약 모델 그대로 보낸다. 값이 없는 선택 필드는 보내지 않아 재무가 0으로 채우지 못한다
    contract = CampaignResultInput.model_validate(data.model_dump())
    answer = await _ask_finance(request, RECORD_SKILL, contract)
    return _result(
        request,
        answer.status,
        outcome=answer.outcome,
        output=answer.output,
        reason_code=answer.reason_code,
        evidence=answer.evidence
        if answer.evidence.data_summary
        else _evidence([f"finance.{RECORD_SKILL}"], f"{data.campaign_id} 결과 전달"),
    )
