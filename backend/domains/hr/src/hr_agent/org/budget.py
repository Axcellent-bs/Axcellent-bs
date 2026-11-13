"""`confirm_team_budget`: 인사 담당자가 그 달 팀 예산을 확정하고 재무에 기록을 요청한다 (11/13).

팀 예산 확정은 인사 AI(사람)가 한다. 재무는 인사가 확정할 때 보던 숫자(`signature`)가
지금과 같은지만 보고 장부에 기록한다(재무 A2A `record_team_budget`).
숫자가 바뀌었거나(stale) 인원을 못 받았거나
예시 명부면 재무가 거절하고, 그 이유를 그대로 돌려준다(확정한 것처럼 보이게 하지 않는다).
같은 확정을 다시 보내면 재무가 `already_recorded`로 답한다.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from pydantic import ValidationError

from ax_agent_template.client import call_agent
from ax_contracts.schemas import (
    AgentRequest,
    AgentResult,
    ConfirmTeamBudgetInput,
    ConfirmTeamBudgetOutput,
    RequestMeta,
    TaskStatus,
)
from hr_agent.results import (
    completed,
    failed,
    gate_blocked,
    invalid_input,
    make_evidence,
    refused,
    with_key,
)
from hr_agent.services import Services

CALL_TIMEOUT_SECONDS = 20
SEND = call_agent  # 테스트에서 바꿀 수 있다

# 재무가 숫자나 인원 때문에 기록을 거절할 때 쓰는 코드와 사람이 읽을 안내
_REFUSED = {
    "stale": "그사이 인원이나 단가가 바뀌었어요. 다시 불러와 확인한 뒤 확정해 주세요",
    "team_headcount_unavailable": "인사 AI에서 팀 인원을 받지 못해 확정할 수 없어요",
    "example_headcount": "예시 인원이라 확정할 수 없어요. 전 직원 등록을 마친 뒤 확정해 주세요",
}


async def confirm_team_budget(svc: Services, request: AgentRequest) -> AgentResult:
    try:
        params = ConfirmTeamBudgetInput.model_validate(request.input)
    except ValidationError as exc:
        return invalid_input(request, exc)
    if not request.meta.idempotency_key:
        return invalid_input(
            request, "같은 요청을 두 번 처리하지 않도록 요청 번호(idempotency_key)가 필요해요"
        )

    summary = f"{params.month} 팀 예산 확정 ({params.confirmed_by})"
    evidence = make_evidence(svc.clock, summary, [f"team_budget:{params.month}"])
    target = f"{params.month}#{params.signature}"
    decision = await svc.gate.execute(
        "confirm_team_budget",
        {"month": params.month, "summary": summary, "target": target},
        with_key(request.meta, f"confirm_team_budget:{target}"),
        evidence,
    )
    if decision.status != "executed":
        return gate_blocked(request, decision)

    deadline = min(
        request.meta.deadline, datetime.now(UTC) + timedelta(seconds=CALL_TIMEOUT_SECONDS)
    )
    meta = RequestMeta(
        context_id=request.meta.context_id,
        tenant_id=request.meta.tenant_id,
        token="replaced-by-call_agent",
        path=list(request.meta.path),
        deadline=deadline,
        idempotency_key=f"record_team_budget:{target}",
    )
    sent = AgentRequest(
        meta=meta,
        skill="record_team_budget",
        input=params.model_dump(mode="json", exclude_none=True),
    )
    result = await SEND("finance", sent)

    if result.status == TaskStatus.COMPLETED:
        try:
            output = ConfirmTeamBudgetOutput.model_validate(result.output)
        except ValidationError:
            return failed(request, "FINANCE_BAD_RESPONSE", "재무 응답 형식이 맞지 않아요")
        svc.audit.record(
            request.meta.context_id,
            "team_budget.confirm",
            {"month": params.month, "by": params.confirmed_by, "status": output.status},
        )
        return completed(request, output, outcome=output.status, evidence=evidence)
    code = str(result.outcome or "")
    if code in _REFUSED:
        return refused(request, code.upper(), _REFUSED[code])
    return failed(
        request, "FINANCE_UNAVAILABLE", f"재무가 기록하지 못했어요 ({result.status.value} {code})"
    )
