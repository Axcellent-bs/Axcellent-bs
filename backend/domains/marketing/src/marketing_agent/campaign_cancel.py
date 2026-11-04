"""캠페인 취소 (skill `cancel_campaign`).

재무 `cancel_campaign`에 계약 모델(`CampaignCancelInput`)로 그대로 전달한다. 바뀌는 것은 **재무가
잡아 둔 예산 기록뿐**이다. 실제 광고를 멈추는 일은 마케팅도 재무도 하지 않으며, 요청한 쪽이 한다.
이 skill의 결과를 "광고를 멈췄다"로 읽으면 안 된다.

- 재무의 상태·결과·출력은 바꾸지 않고 돌려준다(`already_recorded`·`not_started`·
  `starts_unavailable`도 그대로). 결과가 이미 기록된 캠페인은 취소되지 않는다. 일부를 이미 썼으면
  취소 대신 `record_campaign_result`로 결과를 기록한다.
- 재무에 닿지 못하거나 답이 계약과 다르면 실패로 돌려준다(fail-closed). 취소됐다고 가정하지 않는다.
"""

from __future__ import annotations

from pydantic import Field, ValidationError, field_validator

from ax_contracts.schemas import AgentRequest, AgentResult, TaskStatus
from ax_contracts.schemas.finance import CampaignCancelInput
from marketing_agent.campaign import (
    FINANCE,
    _ask_finance,
    _evidence,
    _finance_unusable,
    _invalid,
    _result,
)

CANCEL_SKILL = "cancel_campaign"
CANCELED = "canceled"
NOTE = (
    "재무가 잡아 둔 예산 기록만 풀었다. 실제 광고를 멈춘 것이 아니다. 광고 중단은 요청한 쪽이 한다."
)


class CancelCampaignInput(CampaignCancelInput):
    """재무 계약(`CampaignCancelInput`)에 빈 문자열 거절만 더한다. 필드는 그대로다."""

    campaign_id: str = Field(min_length=1)

    @field_validator("campaign_id", "reason")
    @classmethod
    def _not_blank(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("빈 값은 받지 않는다")
        return value


async def cancel_campaign(request: AgentRequest) -> AgentResult:
    try:
        data = CancelCampaignInput.model_validate(request.input)
    except ValidationError as error:
        return _invalid(request, error)

    contract = CampaignCancelInput.model_validate(data.model_dump())
    answer = await _ask_finance(request, CANCEL_SKILL, contract)
    evidence = (
        answer.evidence
        if answer.evidence.data_summary
        else _evidence([f"{FINANCE}.{CANCEL_SKILL}"], f"{data.campaign_id} 취소 요청")
    )
    if answer.status == TaskStatus.COMPLETED:
        # 취소로 보려면 재무가 canceled를 분명히 답해야 한다. 아니면 취소됐다고 가정하지 않는다
        out = answer.output if isinstance(answer.output, dict) else {}
        released = out.get("released_krw")
        if (
            answer.outcome != CANCELED
            or out.get("status") != CANCELED
            or out.get("campaign_id") != data.campaign_id
            or not isinstance(released, int)
            or isinstance(released, bool)
            or released < 0
        ):
            return _finance_unusable(request, answer, "finance_cancel_invalid")
        return _result(
            request,
            TaskStatus.COMPLETED,
            outcome=answer.outcome,
            output=answer.output | {"note": NOTE},
            reason_code=answer.reason_code,
            evidence=evidence,
        )
    return _result(
        request,
        answer.status,
        outcome=answer.outcome,
        output=answer.output,
        reason_code=answer.reason_code,
        evidence=evidence,
    )
