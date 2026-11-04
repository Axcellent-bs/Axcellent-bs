"""캠페인 시작·결과 전달 (skill `start_campaign`, `record_campaign_result`).

- 시작: 재무 `review_campaign_budget`로 먼저 예산을 묻고(A2A `call_agent`), 거절이면 관문을 부르지
  않고 멈춘다. 그 밖에는 `execute("start_campaign")`로 실행한다. 관문 결과(executed·held·
  rejected 등)는 있는 그대로 돌려준다. 승인 한도·보류 기준(50만 원 이상은 사람 승인)은 재무와
  safety가 정하고, 여기서는 숫자를 만들지 않는다.
- 결과: 끝난 캠페인의 성과를 재무 `record_campaign_result`에 계약 모델(`CampaignResultInput`)로
  그대로 전달한다. 모르는 숫자(`gmv_krw` 등)는 비워 두고 채우지 않는다.
- 광고비: 마케팅에는 실제 집행액 출처가 없다. 호출자가 `spend_krw`를 주면(`source` 필수) 그대로
  전달한다. 생략하면 이 프로세스에서 `executed`된 캠페인의 예산(`budget_krw`)을 `source =
  "marketing.budget_krw"`로 대신 쓰고, 출력에 예산 기준 참고값(실제 집행액 아님)이라고 밝힌다.
  기억에 없으면(이 프로세스에서 시작한 적 없음·재시작) `spend_unknown`으로 멈추고 0을 보내지 않는다.
- 예산 기억(`BUDGETS`)은 메모리뿐이라 프로세스를 다시 띄우면 사라진다.
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
BUDGET_SOURCE = "marketing.budget_krw"
BUDGET_NOTE = "spend_krw는 실제 집행액이 아니라 시작 때의 예산(budget_krw) 기준 참고값이다"

# executed된 캠페인의 예산 {campaign_id: budget_krw}. 메모리뿐이라 재시작하면 사라진다.
BUDGETS: dict[str, int] = {}


def reset_budgets() -> None:
    BUDGETS.clear()


class StartCampaignInput(AxModel):
    """캠페인 시작 요청. 예산은 정수 원이며 0 이하는 받지 않는다."""

    campaign_id: str = Field(min_length=1)
    campaign_name: str = Field(min_length=1)
    channel: str = Field(min_length=1)
    budget_krw: StrictInt = Field(gt=0)


class RecordCampaignResultInput(CampaignResultInput):
    """재무 계약(`CampaignResultInput`)과 같되 `spend_krw`·`source`는 생략할 수 있다.

    생략하면 기억해 둔 예산으로 채운다. `spend_krw`를 주면 `source`도 있어야 하고,
    `source`만 주는 것은 받지 않는다. 날짜 순서도 확인한다.
    """

    spend_krw: int | None = Field(default=None, ge=0)
    source: str | None = None

    @model_validator(mode="after")
    def _spend_and_source_together(self) -> RecordCampaignResultInput:
        if self.spend_krw is not None and not self.source:
            raise ValueError("spend_krw를 주면 source(출처)도 줘야 한다")
        if self.spend_krw is None and self.source is not None:
            raise ValueError("source만 줄 수 없다(spend_krw와 함께 준다)")
        return self

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

    key = f"{request.meta.context_id}:{START_ACTION}:{data.campaign_id}"
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
    result = _gate_outcome(request, decision, review, evidence)
    if result.outcome == "executed":
        BUDGETS[data.campaign_id] = data.budget_krw
    return result


async def record_campaign_result(request: AgentRequest) -> AgentResult:
    try:
        data = RecordCampaignResultInput.model_validate(request.input)
    except ValidationError as error:
        return _invalid(request, error)

    fields = data.model_dump()
    budget_based = data.spend_krw is None
    if budget_based:
        budget = BUDGETS.get(data.campaign_id)
        if budget is None:
            # 광고비를 모르면 짐작하거나 0을 보내지 않는다
            return _result(
                request,
                TaskStatus.FAILED,
                outcome="spend_unknown",
                output={
                    "campaign_id": data.campaign_id,
                    "detail": "spend_krw가 없고 이 프로세스에서 시작한 캠페인 예산도 없다",
                },
            )
        fields |= {"spend_krw": budget, "source": BUDGET_SOURCE}

    # 계약 모델 그대로 보낸다. 값이 없는 선택 필드는 보내지 않아 재무가 0으로 채우지 못한다
    contract = CampaignResultInput.model_validate(fields)
    answer = await _ask_finance(request, RECORD_SKILL, contract)
    output = answer.output
    if budget_based:
        output = {**output, "spend_basis": "budget", "spend_note": BUDGET_NOTE}
    return _result(
        request,
        answer.status,
        outcome=answer.outcome,
        output=output,
        reason_code=answer.reason_code,
        evidence=answer.evidence
        if answer.evidence.data_summary
        else _evidence([f"finance.{RECORD_SKILL}"], f"{data.campaign_id} 결과 전달"),
    )
